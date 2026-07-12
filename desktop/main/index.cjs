const { app, BrowserWindow, Menu, dialog, ipcMain, shell } = require("electron");
const path = require("node:path");
const process = require("node:process");

const { openPath, pickExportFilePath, pickImportTextFile, registerDesktopDialogIpc, revealPath } = require("./dialogs.cjs");
const { installMainLogger } = require("./logging.cjs");
const { createApplicationMenu } = require("./menu.cjs");
const { ensureShellPage } = require("./page_store.cjs");
const { applyElectronPathOverrides, ensureDesktopPaths, resolveDesktopPaths } = require("./paths.cjs");
const { createProtocolBridge } = require("./protocol.cjs");
const { buildDesktopWindowTitle, buildFailurePageHtml, buildStartupPageHtml, getShellMetadata } = require("./runtime.cjs");
const { startSidecar, stopSidecar } = require("./sidecar.cjs");
const { installSidecarAuthInterceptor } = require("./sidecar-auth.cjs");
const { createTrayController } = require("./tray.cjs");
const { createCloudController } = require("./cloud-controller.cjs");

let shellMetadata;
try {
  shellMetadata = getShellMetadata();
} catch (error) {
  // If the manifest cannot be read the process would silently exit.
  // Show a visible error so the user knows what went wrong.
  app.whenReady().then(() => {
    dialog.showErrorBox(
      "WenShape startup failed",
      `Failed to read shell manifest:\n${error.message || error}\n\n__dirname: ${__dirname}`
    );
    app.quit();
  });
  // Return a stub so the rest of module-level code does not crash.
  shellMetadata = { manifest: {}, isDev: false, isPackagedRuntime: true, frontendDevUrl: "", repoRoot: "", desktopRoot: "" };
}

const SIDECAR_MAX_RECOVERY_ATTEMPTS = 3;

function isRuntimeDev() {
  return !app.isPackaged && shellMetadata.isDev;
}

let mainWindow = null;
let sidecarState = null;
let desktopPaths = null;
let mainLogger = null;
let canQuit = false;
let quitRequested = false;
let isShuttingDown = false;
let recoveryPromise = null;
let startupScreenLoaded = false;
let trayController = null;
let protocolBridge = null;
let cloudController = null;
let shellStatus = {
  phase: "booting",
  severity: "info",
  message: "Desktop shell is booting",
  detail: "Waiting for the main process to initialize.",
  details: {},
  updatedAt: new Date().toISOString()
};

function resolvePreloadPath() {
  return path.join(__dirname, "..", "preload", "index.cjs");
}

function getDesktopPaths() {
  return desktopPaths;
}

function getLiveWindow() {
  const window = mainWindow;
  if (!window || window.isDestroyed()) {
    return null;
  }
  if (!window.webContents || window.webContents.isDestroyed()) {
    return null;
  }
  return window;
}

function emitToRenderer(channel, payload) {
  const window = getLiveWindow();
  if (!window) {
    return false;
  }

  try {
    window.webContents.send(channel, payload);
    return true;
  } catch (_error) {
    return false;
  }
}

function setShellStatus(nextStatus) {
  shellStatus = {
    ...shellStatus,
    ...nextStatus,
    details: Object.prototype.hasOwnProperty.call(nextStatus || {}, "details")
      ? (nextStatus.details || {})
      : (shellStatus.details || {}),
    updatedAt: new Date().toISOString()
  };

  console.info("[desktop-main] shell status ->", shellStatus.phase, shellStatus.message);
  trayController?.setStatus(shellStatus);
  emitToRenderer("wenshape:shell-status", shellStatus);
}

async function safeLoadUrl(window, targetUrl) {
  if (!window || window.isDestroyed() || window.webContents.isDestroyed()) {
    return false;
  }

  await window.loadURL(targetUrl);
  return !(window.isDestroyed() || window.webContents.isDestroyed());
}

async function safeLoadFile(window, filePath) {
  if (!window || window.isDestroyed() || window.webContents.isDestroyed()) {
    return false;
  }

  await window.loadFile(filePath);
  return !(window.isDestroyed() || window.webContents.isDestroyed());
}

function getRendererEntry() {
  if (isRuntimeDev()) {
    return shellMetadata.frontendDevUrl;
  }
  return sidecarState.baseUrl;
}

function hideWindowToTray() {
  const window = getLiveWindow();
  if (!window) {
    return false;
  }

  window.hide();
  setShellStatus({
    phase: sidecarState ? "ready" : shellStatus.phase,
    severity: "info",
    message: "WenShape is running in the tray",
    detail: "The desktop shell remains active in the background. Use the tray icon or menu to restore it."
  });
  return true;
}

async function showMainWindow() {
  const window = await ensureWindow();
  if (window.isMinimized()) {
    window.restore();
  }
  window.show();
  window.focus();
  return window;
}

async function reloadRendererWindow() {
  const window = getLiveWindow();
  if (!window) {
    return false;
  }

  if (startupScreenLoaded) {
    await loadRenderer();
    return true;
  }

  window.webContents.reloadIgnoringCache();
  return true;
}

function toggleMainWindowDevTools() {
  const window = getLiveWindow();
  if (!window) {
    return false;
  }

  if (window.webContents.isDevToolsOpened()) {
    window.webContents.closeDevTools();
  } else {
    window.webContents.openDevTools({ mode: "detach" });
  }
  return true;
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 1180,
    minHeight: 760,
    show: false,
    title: buildDesktopWindowTitle(),
    backgroundColor: "#0f172a",
    autoHideMenuBar: false,
    webPreferences: {
      preload: resolvePreloadPath(),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      devTools: true
    }
  });

  installSidecarAuthInterceptor(mainWindow.webContents.session, () => {
    return {
      sidecarState,
      frontendDevUrl: isRuntimeDev() ? shellMetadata.frontendDevUrl : ""
    };
  });

  mainWindow.once("ready-to-show", () => {
    mainWindow?.show();
  });

  mainWindow.on("close", (event) => {
    if (quitRequested || canQuit || isShuttingDown || !trayController) {
      return;
    }

    event.preventDefault();
    hideWindowToTray();
  });

  mainWindow.on("minimize", (event) => {
    if (quitRequested || canQuit || isShuttingDown || !trayController) {
      return;
    }

    event.preventDefault();
    hideWindowToTray();
  });

  mainWindow.on("closed", () => {
    startupScreenLoaded = false;
    mainWindow = null;
  });

  mainWindow.on("unresponsive", () => {
    console.warn("[desktop-main] renderer became unresponsive");
    setShellStatus({
      phase: "renderer-crashed",
      severity: "warn",
      message: "Renderer became unresponsive",
      detail: "The window stopped responding. Check logs if it does not recover."
    });
  });

  mainWindow.on("responsive", () => {
    console.info("[desktop-main] renderer became responsive again");
    setShellStatus({
      phase: sidecarState ? "ready" : shellStatus.phase,
      severity: "info",
      message: sidecarState ? "Renderer recovered" : shellStatus.message,
      detail: sidecarState ? "The desktop window is responsive again." : shellStatus.detail
    });
  });

  mainWindow.webContents.on("dom-ready", () => {
    emitToRenderer("wenshape:shell-status", shellStatus);
    protocolBridge?.flush();
    cloudController?.publishAuthState();
    cloudController?.replayLastUpdate();
  });

  mainWindow.webContents.on("did-fail-load", async (_event, errorCode, errorDescription, validatedURL, isMainFrame) => {
    if (!isMainFrame || errorCode === -3 || isShuttingDown) {
      return;
    }

    console.error("[desktop-main] renderer failed to load", {
      errorCode,
      errorDescription,
      validatedURL
    });

    setShellStatus({
      phase: "renderer-load-failed",
      severity: "error",
      message: "Renderer failed to load",
      detail: errorDescription || "Unknown renderer load failure.",
      details: {
        rendererUrl: validatedURL,
        errorCode
      }
    });

    await showFailurePage(new Error(`renderer load failed: ${errorDescription || errorCode}`), {
      heading: "Renderer failed to load",
      message: "The desktop window started, but the target page could not be loaded."
    });
  });

  mainWindow.webContents.on("render-process-gone", async (_event, details) => {
    if (isShuttingDown) {
      return;
    }

    console.error("[desktop-main] renderer process gone", details);
    setShellStatus({
      phase: "renderer-crashed",
      severity: "error",
      message: "Renderer process exited",
      detail: "The renderer process terminated unexpectedly.",
      details: {
        reason: details.reason,
        exitCode: details.exitCode
      }
    });

    await showFailurePage(new Error(`renderer process gone: ${details.reason}`), {
      heading: "Renderer process exited",
      message: "The desktop UI process terminated unexpectedly."
    });
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url).catch(() => {});
    return { action: "deny" };
  });

  mainWindow.webContents.on("will-navigate", (event, url) => {
    let allowed = false;
    try {
      const target = new URL(url);
      const allowedOrigins = new Set(
        [sidecarState?.baseUrl, isRuntimeDev() ? shellMetadata.frontendDevUrl : ""].filter(Boolean)
      );
      allowed = target.protocol === "file:" || allowedOrigins.has(target.origin);
    } catch (_error) {
      allowed = false;
    }
    if (!allowed) {
      event.preventDefault();
      shell.openExternal(url).catch(() => {});
    }
  });

  if (isRuntimeDev()) {
    mainWindow.webContents.openDevTools({ mode: "detach" });
  }

  return mainWindow;
}

async function ensureWindow() {
  const current = getLiveWindow();
  if (current) {
    return current;
  }

  const window = createWindow();
  startupScreenLoaded = false;
  return window;
}

async function showStartupScreen(detail, extraDetails = {}) {
  const window = await ensureWindow();
  setShellStatus({
    phase: "startup-screen",
    severity: "info",
    message: "Preparing desktop runtime",
    detail,
    details: extraDetails
  });

  if (!startupScreenLoaded) {
    const startupPagePath = ensureShellPage(
      desktopPaths.cacheDir,
      "startup.html",
      buildStartupPageHtml()
    );
    const loaded = await safeLoadFile(window, startupPagePath);
    startupScreenLoaded = loaded;
  }
}

async function loadRenderer() {
  const window = await ensureWindow();
  const rendererEntry = getRendererEntry();

  setShellStatus({
    phase: "loading-renderer",
    severity: "info",
    message: "Loading renderer",
    detail: "The desktop shell is switching from the startup page to the main workspace.",
    details: {
      rendererEntry
    }
  });

  await safeLoadUrl(window, rendererEntry);
  startupScreenLoaded = false;
  protocolBridge?.flush();

  setShellStatus({
    phase: "ready",
    severity: "info",
    message: "WenShape is ready",
    detail: "The local engine is connected and the workspace is ready.",
    details: {
      rendererEntry,
      sidecarPort: sidecarState?.port,
      dataDir: desktopPaths?.dataDir,
      logsDir: desktopPaths?.logsDir
    }
  });
}

function attachSidecarLifecycle(state) {
  state.child.once("exit", (code, signal) => {
    const isExpectedExit = Boolean(state.expectedExit) || canQuit || isShuttingDown;
    console.warn("[desktop-main] sidecar exited", { code, signal, isExpectedExit });

    if (sidecarState === state) {
      sidecarState = null;
    }

    if (isExpectedExit) {
      return;
    }

    if (!recoveryPromise) {
      recoveryPromise = recoverSidecar({ code, signal }).finally(() => {
        recoveryPromise = null;
      });
    }
  });
}

async function launchSidecar() {
  setShellStatus({
    phase: "starting-sidecar",
    severity: "info",
    message: "Starting local engine",
    detail: "The desktop shell is starting the local Python sidecar and waiting for health checks.",
    details: {
      dataDir: desktopPaths?.dataDir,
      logsDir: desktopPaths?.logsDir
    }
  });

  const nextSidecar = await startSidecar({
    isPackaged: app.isPackaged,
    userDataDir: desktopPaths?.runtimeRoot,
    paths: desktopPaths,
    frontendDevUrl: isRuntimeDev() ? shellMetadata.frontendDevUrl : "",
    // PyInstaller first-run extraction can be slow on some disks; give it
    // plenty of time in packaged mode.
    startupTimeoutMs: app.isPackaged ? 90000 : 45000
  });

  sidecarState = nextSidecar;
  attachSidecarLifecycle(nextSidecar);
  console.info("[desktop-main] sidecar ready", {
    port: nextSidecar.port,
    baseUrl: nextSidecar.baseUrl
  });
}

async function bootstrap() {
  await showStartupScreen("Initializing runtime directories and startup window.", {
    runtimeRoot: desktopPaths.runtimeRoot,
    dataDir: desktopPaths.dataDir,
    logsDir: desktopPaths.logsDir,
    cacheDir: desktopPaths.cacheDir,
    seededDataFrom: desktopPaths.seededDataFrom || "-"
  });

  if (!sidecarState) {
    await launchSidecar();
  }

  await loadRenderer();
}

async function recoverSidecar(exitInfo) {
  let lastError = new Error("sidecar recovery failed");

  for (let attempt = 1; attempt <= SIDECAR_MAX_RECOVERY_ATTEMPTS; attempt += 1) {
    const delayMs = Math.min(1500 * attempt, 5000);

    setShellStatus({
      phase: "restarting-sidecar",
      severity: "warn",
      message: `Local engine exited unexpectedly, retrying (${attempt}/${SIDECAR_MAX_RECOVERY_ATTEMPTS})`,
      detail: "If recovery succeeds, the workspace will reconnect automatically.",
      details: {
        lastExitCode: exitInfo.code,
        lastExitSignal: exitInfo.signal || "none",
        retryDelayMs: delayMs
      }
    });

    if (!isRuntimeDev()) {
      await showStartupScreen("Recovering local engine.", {
        lastExitCode: exitInfo.code,
        lastExitSignal: exitInfo.signal || "none",
        recoveryAttempt: `${attempt}/${SIDECAR_MAX_RECOVERY_ATTEMPTS}`
      });
    }

    await new Promise((resolve) => setTimeout(resolve, delayMs));

    try {
      await launchSidecar();
      await loadRenderer();
      return;
    } catch (error) {
      lastError = error;
      console.error("[desktop-main] sidecar recovery attempt failed", {
        attempt,
        error
      });
    }
  }

  setShellStatus({
    phase: "error",
    severity: "error",
    message: "Local engine recovery failed",
    detail: "Automatic recovery reached the retry limit.",
    details: {
      logsDir: desktopPaths?.logsDir,
      crashDir: desktopPaths?.crashDir
    }
  });

  await showFailurePage(lastError, {
    heading: "Runtime interrupted",
    message: "The local engine could not be recovered after multiple attempts."
  });

  dialog.showErrorBox(
    "WenShape runtime interrupted",
    `The local engine could not be recovered.\n\nLog directory: ${desktopPaths?.logsDir || "-"}`
  );
}

async function showFailurePage(error, options = {}) {
  const detail = error instanceof Error ? (error.stack || error.message) : String(error);
  const window = await ensureWindow();

  const failurePagePath = ensureShellPage(
    desktopPaths.cacheDir,
    "failure.html",
    buildFailurePageHtml(detail, {
      ...options,
      metadata: {
        runtimeRoot: desktopPaths?.runtimeRoot || "-",
        dataDir: desktopPaths?.dataDir || "-",
        logsDir: desktopPaths?.logsDir || "-"
      }
    })
  );

  await safeLoadFile(window, failurePagePath);
  startupScreenLoaded = false;
}

async function shutdownRuntime() {
  setShellStatus({
    phase: "shutting-down",
    severity: "info",
    message: "Shutting down WenShape",
    detail: "Stopping local services and releasing runtime resources."
  });

  trayController?.destroy();
  trayController = null;

  cloudController?.stop();

  if (sidecarState) {
    await stopSidecar(sidecarState);
    sidecarState = null;
  }

  if (mainLogger) {
    await mainLogger.close();
  }
}

async function dispatchDesktopCommand(type, payload) {
  const commandPayload = {
    type,
    payload,
    updatedAt: new Date().toISOString()
  };

  if (emitToRenderer("wenshape:desktop-command", commandPayload)) {
    return true;
  }

  return false;
}

async function handleImportTextFileCommand() {
  const result = await pickImportTextFile({
    browserWindow: getLiveWindow()
  });

  if (result.canceled) {
    return result;
  }

  const dispatched = await dispatchDesktopCommand("import-text-file", result);
  if (!dispatched) {
    dialog.showMessageBox({
      type: "info",
      message: "Text file loaded",
      detail: `${result.name}\n${result.size} bytes`
    }).catch(() => {});
  }

  return result;
}

async function handleChooseExportPathCommand() {
  const result = await pickExportFilePath({
    browserWindow: getLiveWindow(),
    defaultFileName: "wenshape-export.txt"
  });

  if (result.canceled) {
    return result;
  }

  const dispatched = await dispatchDesktopCommand("choose-export-path", result);
  if (!dispatched) {
    dialog.showMessageBox({
      type: "info",
      message: "Export location selected",
      detail: result.filePath
    }).catch(() => {});
  }

  return result;
}

async function requestQuit(options = {}) {
  if (canQuit) {
    app.quit();
    return true;
  }

  if (isShuttingDown) {
    return false;
  }

  if (!options.skipConfirmation) {
    const response = await dialog.showMessageBox(getLiveWindow() || undefined, {
      type: "question",
      buttons: ["Quit", "Cancel"],
      defaultId: 0,
      cancelId: 1,
      title: "Quit WenShape",
      message: "Quit WenShape?",
      detail: "The local writing engine will stop and the desktop shell will exit."
    });

    if (response.response !== 0) {
      return false;
    }
  }

  quitRequested = true;
  app.quit();
  return true;
}

ipcMain.handle("wenshape:get-shell-status", async () => shellStatus);

registerDesktopDialogIpc({
  ipcMain,
  getWindow: getLiveWindow,
  getDesktopPaths,
  getShellMetadata
});

const shouldUseSingleInstanceLock = !isRuntimeDev();
if (shouldUseSingleInstanceLock && !app.requestSingleInstanceLock()) {
  app.quit();
} else if (shouldUseSingleInstanceLock) {
  app.on("second-instance", async (_event, argv) => {
    if (protocolBridge?.handleSecondInstance(argv)) {
      return;
    }

    // If the window was hidden to tray or is still alive, just show it.
    const window = getLiveWindow();
    if (window) {
      if (window.isMinimized()) {
        window.restore();
      }
      window.show();
      window.focus();
      return;
    }

    // No live window — re-bootstrap so the user sees something instead
    // of a silent "nothing happened" experience.
    try {
      await showMainWindow();
    } catch (error) {
      console.error("[desktop-main] second-instance: failed to restore window", error);
    }
  });
}

app.on("open-url", (event, targetUrl) => {
  event.preventDefault();
  protocolBridge?.handleOpenUrl(targetUrl, "open-url");
});

app.on("window-all-closed", () => {
  if (process.platform === "darwin") {
    // macOS: keep running in the dock / tray
    return;
  }

  if (quitRequested || !trayController) {
    // User explicitly quit, or tray is not available — exit the app.
    // Without this, a closed window + active single-instance lock leaves a
    // zombie process that blocks subsequent launches ("click does nothing").
    app.quit();
  }
});

app.on("before-quit", (event) => {
  if (canQuit) {
    return;
  }

  event.preventDefault();
  if (isShuttingDown) {
    return;
  }

  isShuttingDown = true;
  shutdownRuntime()
    .catch((error) => {
      console.error("[desktop-main] shutdown failed", error);
    })
    .finally(() => {
      canQuit = true;
      app.quit();
    });
});

app.whenReady().then(async () => {
  desktopPaths = ensureDesktopPaths(resolveDesktopPaths({
    isPackaged: app.isPackaged,
    userDataDir: app.getPath("userData")
  }));
  applyElectronPathOverrides(app, desktopPaths);
  mainLogger = installMainLogger({ logFilePath: desktopPaths.mainLogPath });

  console.info("[desktop-main] runtime directories", desktopPaths);
  app.setAppUserModelId("com.wenshape.desktop");

  protocolBridge = createProtocolBridge({
    app,
    protocolName: shellMetadata.manifest.product?.protocol || "wenshape",
    emit: (payload) => emitToRenderer("wenshape:deep-link", payload),
    showWindow: showMainWindow,
    log: console
  });
  protocolBridge.register();

  trayController = createTrayController({
    showWindow: showMainWindow,
    hideWindow: hideWindowToTray,
    openLogsDirectory: () => openPath(desktopPaths?.logsDir),
    openDataDirectory: () => openPath(desktopPaths?.dataDir),
    requestQuit
  });
  trayController.setStatus(shellStatus);

  cloudController = createCloudController({
    desktopPaths,
    manifest: shellMetadata.manifest,
    emitToRenderer
  });
  cloudController.registerIpc();
  // Rehydrate session and kick off background version check; never block boot.
  cloudController.rehydrate().catch((error) => {
    console.warn("[desktop-main] cloud rehydrate failed", error);
  });
  cloudController.startVersionCheckLoop();

  Menu.setApplicationMenu(createApplicationMenu({
    isDev: isRuntimeDev(),
    showWindow: showMainWindow,
    hideWindow: hideWindowToTray,
    reloadWindow: reloadRendererWindow,
    toggleDevTools: toggleMainWindowDevTools,
    requestQuit,
    openLogsDirectory: () => openPath(desktopPaths?.logsDir),
    openDataDirectory: () => openPath(desktopPaths?.dataDir),
    openRuntimeDirectory: () => openPath(desktopPaths?.runtimeRoot),
    openMainLog: () => revealPath(desktopPaths?.mainLogPath),
    importTextFile: handleImportTextFileCommand,
    chooseExportPath: handleChooseExportPathCommand
  }));

  try {
    await bootstrap();
  } catch (error) {
    console.error("[desktop-main] bootstrap failed", error);
    await showFailurePage(error, {
      heading: "Startup failed",
      message: "The desktop shell did not finish initialization."
    });
    dialog.showErrorBox(
      "WenShape startup failed",
      `Check the desktop logs for details.\n\n${desktopPaths.logsDir}`
    );
  }
}).catch((error) => {
  dialog.showErrorBox("WenShape startup failed", String(error));
  app.quit();
});

app.on("activate", async () => {
  try {
    if (!getLiveWindow()) {
      if (!sidecarState) {
        await bootstrap();
        return;
      }

      await ensureWindow();
      await loadRenderer();
      return;
    }

    await showMainWindow();
  } catch (error) {
    console.error("[desktop-main] activate failed", error);
    await showFailurePage(error, {
      heading: "Activation failed",
      message: "The desktop shell could not restore the main window."
    });
  }
});
