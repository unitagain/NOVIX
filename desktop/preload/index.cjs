const { contextBridge, ipcRenderer } = require("electron");

function makeListenerBinder(channel) {
  return (listener) => {
    if (typeof listener !== "function") {
      return () => {};
    }
    const wrapped = (_event, payload) => listener(payload);
    ipcRenderer.on(channel, wrapped);
    return () => ipcRenderer.removeListener(channel, wrapped);
  };
}

contextBridge.exposeInMainWorld("wenshapeDesktop", {
  isDesktop: true,
  platform: process.platform,
  getShellStatus() {
    return ipcRenderer.invoke("wenshape:get-shell-status");
  },
  getRuntimeInfo() {
    return ipcRenderer.invoke("wenshape:desktop-get-runtime-info");
  },
  onShellStatus: makeListenerBinder("wenshape:shell-status"),
  onCommand: makeListenerBinder("wenshape:desktop-command"),
  onDeepLink: makeListenerBinder("wenshape:deep-link"),
  openLogsDirectory() {
    return ipcRenderer.invoke("wenshape:desktop-open-logs-dir");
  },
  openDataDirectory() {
    return ipcRenderer.invoke("wenshape:desktop-open-data-dir");
  },
  openRuntimeDirectory() {
    return ipcRenderer.invoke("wenshape:desktop-open-runtime-dir");
  },
  openMainLog() {
    return ipcRenderer.invoke("wenshape:desktop-open-main-log");
  },
  importTextFile() {
    return ipcRenderer.invoke("wenshape:desktop-import-text-file");
  },
  chooseExportPath(options) {
    return ipcRenderer.invoke("wenshape:desktop-choose-export-path", options || {});
  },
  revealPath(targetPath) {
    return ipcRenderer.invoke("wenshape:desktop-reveal-path", { path: targetPath });
  },

  // ── Cloud (阶段 7) ──
  cloud: {
    getStatus() {
      return ipcRenderer.invoke("wenshape:cloud-status");
    },
    getAppInfo() {
      return ipcRenderer.invoke("wenshape:cloud-app-info");
    },
    login(payload) {
      return ipcRenderer.invoke("wenshape:cloud-login", payload || {});
    },
    register(payload) {
      return ipcRenderer.invoke("wenshape:cloud-register", payload || {});
    },
    logout() {
      return ipcRenderer.invoke("wenshape:cloud-logout");
    },
    me() {
      return ipcRenderer.invoke("wenshape:cloud-me");
    },
    listDevices() {
      return ipcRenderer.invoke("wenshape:cloud-list-devices");
    },
    removeDevice(deviceId) {
      return ipcRenderer.invoke("wenshape:cloud-remove-device", deviceId);
    },
    requestPasswordReset(email) {
      return ipcRenderer.invoke("wenshape:cloud-request-password-reset", email);
    },
    confirmPasswordReset(payload) {
      return ipcRenderer.invoke("wenshape:cloud-confirm-password-reset", payload || {});
    },
    checkVersion(options) {
      return ipcRenderer.invoke("wenshape:cloud-check-version", options || {});
    },
    downloadUpdate() {
      return ipcRenderer.invoke("wenshape:cloud-download-update");
    },
    installUpdate() {
      return ipcRenderer.invoke("wenshape:cloud-install-update");
    },
    getUpdateChannel() {
      return ipcRenderer.invoke("wenshape:cloud-get-update-channel");
    },
    setUpdateChannel(channel) {
      return ipcRenderer.invoke("wenshape:cloud-set-update-channel", channel);
    },
    onAuthState: makeListenerBinder("wenshape:cloud-auth-state"),
    onUpdateAvailable: makeListenerBinder("wenshape:cloud-update-available"),
    onUpdateProgress: makeListenerBinder("wenshape:cloud-update-progress"),
  },
});
