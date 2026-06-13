const { ipcMain } = require("electron");
const path = require("node:path");
const { createCloudClient, resolveCloudBaseUrl, CloudApiError } = require("./cloud-client.cjs");
const { loadTokens, saveTokens, clearTokens } = require("./auth-store.cjs");
const { buildDeviceInfo, buildVersionCheckPayload, getAppVersion } = require("./desktop-info.cjs");
const { downloadAndVerify, installAndQuit, cleanupDir } = require("./updater.cjs");
const { getUpdateChannel, setUpdateChannel } = require("./update-prefs.cjs");

const VERSION_CHECK_DELAY_MS = 2500;
const VERSION_CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000; // 6h

function serializeError(error) {
  if (error instanceof CloudApiError) {
    return { name: error.name, status: error.status, message: error.message, data: error.data };
  }
  return { name: "Error", status: 0, message: error?.message || String(error) };
}

function createCloudController({ desktopPaths, manifest, emitToRenderer }) {
  const baseUrl = resolveCloudBaseUrl(manifest);
  let tokensCache = null;
  let userCache = null;
  let lastVersionCheckResult = null;
  let lastVersionCheckAt = 0;
  let registeredDeviceForUserId = null;
  let versionTimer = null;

  function getTokens() {
    return tokensCache;
  }

  async function persistTokens(access, refresh) {
    tokensCache = { access_token: access, refresh_token: refresh };
    try {
      saveTokens(desktopPaths, access, refresh);
    } catch (error) {
      console.warn("[cloud-controller] persist tokens failed", error);
    }
    publishAuthState();
  }

  function clearLocalSession({ silent = false } = {}) {
    tokensCache = null;
    userCache = null;
    registeredDeviceForUserId = null;
    try {
      clearTokens(desktopPaths);
    } catch (error) {
      console.warn("[cloud-controller] clear tokens failed", error);
    }
    if (!silent) {
      publishAuthState();
    }
  }

  function publishAuthState() {
    emitToRenderer?.("wenshape:cloud-auth-state", {
      authenticated: !!tokensCache,
      user: userCache,
      baseUrl,
      updatedAt: new Date().toISOString(),
    });
  }

  const client = createCloudClient({
    getBaseUrl: () => baseUrl,
    getTokens,
    onTokenRefresh: persistTokens,
    onUnauthorized: async () => {
      clearLocalSession();
    },
  });

  async function rehydrate() {
    try {
      const stored = loadTokens(desktopPaths);
      if (!stored) {
        publishAuthState();
        return;
      }
      tokensCache = { access_token: stored.access_token, refresh_token: stored.refresh_token };
      try {
        const me = await client.me();
        userCache = me;
        publishAuthState();
        // best-effort device sync without blocking
        registerDeviceIfNeeded().catch(() => {});
      } catch (error) {
        if (error instanceof CloudApiError && (error.status === 401 || error.status === 403)) {
          clearLocalSession();
        } else {
          // network failure — keep tokens, mark offline
          publishAuthState();
        }
      }
    } catch (error) {
      console.warn("[cloud-controller] rehydrate failed", error);
      publishAuthState();
    }
  }

  async function registerDeviceIfNeeded() {
    if (!tokensCache || !userCache) return;
    if (registeredDeviceForUserId === userCache.id) return;
    try {
      const info = buildDeviceInfo(manifest);
      await client.registerDevice(info);
      registeredDeviceForUserId = userCache.id;
    } catch (error) {
      console.warn("[cloud-controller] device register failed", serializeError(error));
    }
  }

  async function login({ email, password }) {
    if (!email || !password) {
      throw new CloudApiError(400, "请输入邮箱与密码");
    }
    const tokens = await client.login({ email, password });
    if (!tokens?.access_token || !tokens?.refresh_token) {
      throw new CloudApiError(0, "登录响应无效");
    }
    await persistTokens(tokens.access_token, tokens.refresh_token);
    try {
      userCache = await client.me();
    } catch (error) {
      // login succeeded but /me failed — keep session, surface error
      console.warn("[cloud-controller] /me after login failed", serializeError(error));
      userCache = null;
    }
    publishAuthState();
    registerDeviceIfNeeded().catch(() => {});
    return { user: userCache };
  }

  async function logout() {
    if (!tokensCache) {
      clearLocalSession();
      return;
    }
    try {
      await client.logout(tokensCache.refresh_token);
    } catch (error) {
      // even if server rejects, drop local state
      console.warn("[cloud-controller] logout failed", serializeError(error));
    } finally {
      clearLocalSession();
    }
  }

  async function register(body) {
    return client.register(body);
  }

  async function fetchMe() {
    if (!tokensCache) return null;
    const me = await client.me();
    userCache = me;
    publishAuthState();
    return me;
  }

  function getStatus() {
    return {
      authenticated: !!tokensCache,
      user: userCache,
      baseUrl,
      lastVersionCheck: lastVersionCheckResult,
      lastVersionCheckAt: lastVersionCheckAt ? new Date(lastVersionCheckAt).toISOString() : null,
    };
  }

  let lastUpdateAvailablePayload = null;

  async function checkVersion({ silent = false } = {}) {
    try {
      const channel = getUpdateChannel(desktopPaths, manifest?.cloud?.defaultChannel || "stable");
      const payload = buildVersionCheckPayload(manifest, channel);
      const result = await client.checkVersion(payload);
      lastVersionCheckResult = result;
      lastVersionCheckAt = Date.now();
      if (result?.has_update && result.latest) {
        const updatePayload = {
          current: payload.current_version,
          latest: result.latest,
          checkedAt: new Date(lastVersionCheckAt).toISOString(),
          silent,
        };
        lastUpdateAvailablePayload = updatePayload;
        emitToRenderer?.("wenshape:cloud-update-available", updatePayload);
      } else {
        lastUpdateAvailablePayload = null;
      }
      return result;
    } catch (error) {
      if (!silent) {
        console.warn("[cloud-controller] version check failed", serializeError(error));
      }
      return null;
    }
  }

  let downloadedUpdateFile = null;

  function updatesDir() {
    return path.join(desktopPaths.cacheDir, "updates");
  }

  async function downloadUpdate() {
    const latest = lastUpdateAvailablePayload?.latest;
    if (!latest?.download_url) {
      throw new CloudApiError(0, "当前没有可下载的更新");
    }
    if (!latest.sha256) {
      throw new CloudApiError(0, "该版本缺少校验值（sha256），请前往官网手动下载");
    }
    const allowHosts = manifest?.cloud?.downloadHosts || [];
    const dir = updatesDir();
    await cleanupDir(dir); // 清掉历史下载，避免堆积
    const result = await downloadAndVerify({
      url: latest.download_url,
      sha256: latest.sha256,
      allowHosts,
      destDir: dir,
      onProgress: (p) => emitToRenderer?.("wenshape:cloud-update-progress", p),
    });
    downloadedUpdateFile = result.file;
    return { file: result.file, size: result.size };
  }

  async function installUpdate() {
    if (!downloadedUpdateFile) {
      throw new CloudApiError(0, "请先下载更新");
    }
    await installAndQuit(downloadedUpdateFile);
    return { ok: true };
  }

  function replayLastUpdate() {
    if (lastUpdateAvailablePayload) {
      emitToRenderer?.("wenshape:cloud-update-available", lastUpdateAvailablePayload);
    }
  }

  function startVersionCheckLoop() {
    setTimeout(() => {
      checkVersion({ silent: true });
    }, VERSION_CHECK_DELAY_MS);

    versionTimer = setInterval(() => {
      checkVersion({ silent: true });
    }, VERSION_CHECK_INTERVAL_MS);
  }

  function stop() {
    if (versionTimer) {
      clearInterval(versionTimer);
      versionTimer = null;
    }
  }

  function registerIpc() {
    const handlers = {
      "wenshape:cloud-status": async () => getStatus(),
      "wenshape:cloud-login": async (_e, payload) => {
        try {
          return { ok: true, data: await login(payload || {}) };
        } catch (error) {
          return { ok: false, error: serializeError(error) };
        }
      },
      "wenshape:cloud-register": async (_e, payload) => {
        try {
          return { ok: true, data: await register(payload || {}) };
        } catch (error) {
          return { ok: false, error: serializeError(error) };
        }
      },
      "wenshape:cloud-logout": async () => {
        try {
          await logout();
          return { ok: true };
        } catch (error) {
          return { ok: false, error: serializeError(error) };
        }
      },
      "wenshape:cloud-me": async () => {
        try {
          return { ok: true, data: await fetchMe() };
        } catch (error) {
          return { ok: false, error: serializeError(error) };
        }
      },
      "wenshape:cloud-list-devices": async () => {
        try {
          return { ok: true, data: await client.listDevices() };
        } catch (error) {
          return { ok: false, error: serializeError(error) };
        }
      },
      "wenshape:cloud-remove-device": async (_e, deviceId) => {
        try {
          await client.removeDevice(String(deviceId));
          return { ok: true };
        } catch (error) {
          return { ok: false, error: serializeError(error) };
        }
      },
      "wenshape:cloud-request-password-reset": async (_e, email) => {
        try {
          return { ok: true, data: await client.requestPasswordReset(String(email || "")) };
        } catch (error) {
          return { ok: false, error: serializeError(error) };
        }
      },
      "wenshape:cloud-confirm-password-reset": async (_e, payload) => {
        try {
          return { ok: true, data: await client.confirmPasswordReset(payload || {}) };
        } catch (error) {
          return { ok: false, error: serializeError(error) };
        }
      },
      "wenshape:cloud-check-version": async (_e, options) => {
        const result = await checkVersion({ silent: false, ...(options || {}) });
        return { ok: true, data: result };
      },
      "wenshape:cloud-download-update": async () => {
        try {
          return { ok: true, data: await downloadUpdate() };
        } catch (error) {
          return { ok: false, error: serializeError(error) };
        }
      },
      "wenshape:cloud-install-update": async () => {
        try {
          return { ok: true, data: await installUpdate() };
        } catch (error) {
          return { ok: false, error: serializeError(error) };
        }
      },
      "wenshape:cloud-get-update-channel": async () => {
        const channel = getUpdateChannel(desktopPaths, manifest?.cloud?.defaultChannel || "stable");
        return { ok: true, data: { channel } };
      },
      "wenshape:cloud-set-update-channel": async (_e, channel) => {
        try {
          setUpdateChannel(desktopPaths, String(channel));
          const result = await checkVersion({ silent: false }); // 切换后立即检查一次
          return { ok: true, data: { channel: String(channel), check: result } };
        } catch (error) {
          return { ok: false, error: serializeError(error) };
        }
      },
      "wenshape:cloud-app-info": async () => ({
        ok: true,
        data: {
          baseUrl,
          appVersion: getAppVersion(manifest),
          deviceInfo: buildDeviceInfo(manifest),
        },
      }),
    };

    for (const [channel, handler] of Object.entries(handlers)) {
      ipcMain.handle(channel, handler);
    }
  }

  return {
    rehydrate,
    startVersionCheckLoop,
    stop,
    registerIpc,
    publishAuthState,
    replayLastUpdate,
    checkVersion,
    downloadUpdate,
    installUpdate,
    getStatus,
  };
}

module.exports = { createCloudController };
