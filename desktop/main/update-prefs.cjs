// 阶段 9：更新通道偏好持久化。结构 { channel: "dev" | "beta" | "stable" }，
// 存于 <cacheDir>/update-prefs.json（与 auth-store 同目录约定）。
const fs = require("node:fs");
const path = require("node:path");

const PREFS_FILE_NAME = "update-prefs.json";
const VALID_CHANNELS = ["dev", "beta", "stable"];

function prefsFilePath(paths) {
  if (!paths || !paths.cacheDir) {
    throw new Error("update-prefs: cacheDir is not initialized");
  }
  return path.join(paths.cacheDir, PREFS_FILE_NAME);
}

function getUpdatePrefs(paths) {
  try {
    const file = prefsFilePath(paths);
    if (!fs.existsSync(file)) return {};
    const data = JSON.parse(fs.readFileSync(file, "utf8"));
    return data && typeof data === "object" ? data : {};
  } catch (error) {
    console.warn("[update-prefs] read failed", error);
    return {};
  }
}

// 读当前通道；非法/缺失时回退 fallback（通常为 manifest.cloud.defaultChannel）
function getUpdateChannel(paths, fallback = "stable") {
  const ch = getUpdatePrefs(paths).channel;
  return VALID_CHANNELS.includes(ch) ? ch : fallback;
}

function setUpdateChannel(paths, channel) {
  if (!VALID_CHANNELS.includes(channel)) {
    throw new Error(`无效的更新通道：${channel}`);
  }
  const file = prefsFilePath(paths);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify({ channel }, null, 2), "utf8");
  return channel;
}

module.exports = { getUpdatePrefs, getUpdateChannel, setUpdateChannel, VALID_CHANNELS };
