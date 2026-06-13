const os = require("node:os");
const process = require("node:process");

function getDeviceName() {
  try {
    const hostname = os.hostname();
    if (hostname && hostname.length <= 96) return hostname;
    if (hostname) return hostname.slice(0, 96);
  } catch {
    // ignore
  }
  return `WenShape-${process.platform}`;
}

function getPlatformLabel() {
  switch (process.platform) {
    case "win32":
      return "windows";
    case "darwin":
      return "macos";
    case "linux":
      return "linux";
    default:
      return process.platform;
  }
}

function getArchLabel() {
  return process.arch === "x64" ? "x64" : process.arch;
}

function getAppVersion(manifest) {
  return (
    manifest?.product?.version ||
    manifest?.version ||
    process.env.WENSHAPE_DESKTOP_VERSION ||
    "0.0.0-dev"
  );
}

function buildDeviceInfo(manifest) {
  return {
    device_name: getDeviceName(),
    platform: getPlatformLabel(),
    app_version: getAppVersion(manifest),
  };
}

function buildVersionCheckPayload(manifest, channel = "stable") {
  return {
    current_version: getAppVersion(manifest),
    platform: getPlatformLabel(),
    arch: getArchLabel(),
    channel,
  };
}

module.exports = {
  getDeviceName,
  getPlatformLabel,
  getArchLabel,
  getAppVersion,
  buildDeviceInfo,
  buildVersionCheckPayload,
};
