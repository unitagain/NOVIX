// 阶段 9：辅助更新器。职责：① 校验下载源在白名单内 ② 流式下载并边下边算 SHA256
// ③ 校验通过后启动安装包（Windows 走 msiexec，过一次 UAC/SmartScreen 由用户确认）。
// 无证书场景下，SHA256 是"安装包未被篡改/损坏"的唯一可靠保证。
const { app, shell } = require("electron");
const { createWriteStream } = require("node:fs");
const fsp = require("node:fs/promises");
const { createHash } = require("node:crypto");
const path = require("node:path");
const { spawn } = require("node:child_process");

function assertTrustedHost(url, allowHosts) {
  let host;
  try {
    host = new URL(url).host;
  } catch {
    throw new Error(`下载地址无效：${url}`);
  }
  if (!Array.isArray(allowHosts) || allowHosts.length === 0) {
    throw new Error("未配置受信任的下载域名（manifest.cloud.downloadHosts）");
  }
  if (!allowHosts.includes(host)) {
    throw new Error(`下载源不受信任：${host}`);
  }
  return host;
}

function fileNameFromUrl(url) {
  try {
    const base = decodeURIComponent(new URL(url).pathname.split("/").pop() || "");
    if (base) return base.replace(/[^\w.\-]/g, "_");
  } catch {
    /* ignore */
  }
  return `wenshape-update-${Date.now()}.bin`;
}

// 下载到 destDir 并校验。失败时清掉半成品文件再抛错。
async function downloadAndVerify({ url, sha256, allowHosts, destDir, onProgress, signal }) {
  assertTrustedHost(url, allowHosts);
  await fsp.mkdir(destDir, { recursive: true });
  const file = path.join(destDir, fileNameFromUrl(url));

  const res = await fetch(url, { signal });
  if (!res.ok || !res.body) {
    throw new Error(`下载失败：HTTP ${res.status}`);
  }
  const total = Number(res.headers.get("content-length")) || 0;

  const hash = createHash("sha256");
  const out = createWriteStream(file);
  let received = 0;
  try {
    for await (const chunk of res.body) {
      hash.update(chunk);
      received += chunk.length;
      if (!out.write(chunk)) {
        await new Promise((resolve) => out.once("drain", resolve)); // 背压：等缓冲清空
      }
      onProgress?.({
        phase: "downloading",
        received,
        total,
        percent: total ? Math.round((received / total) * 100) : 0,
      });
    }
    await new Promise((resolve, reject) => out.end((err) => (err ? reject(err) : resolve())));
  } catch (error) {
    out.destroy();
    await fsp.rm(file, { force: true }).catch(() => {});
    throw error;
  }

  const actual = hash.digest("hex").toLowerCase();
  if (sha256 && actual !== String(sha256).toLowerCase()) {
    await fsp.rm(file, { force: true }).catch(() => {});
    throw new Error("安装包校验失败（SHA256 不匹配），已删除下载文件");
  }
  onProgress?.({ phase: "verified", received, total, percent: 100, file });
  return { file, sha256: actual, size: received };
}

// 清理下载目录（启动新一轮下载前调用，避免历史安装包堆积）
async function cleanupDir(destDir) {
  await fsp.rm(destDir, { recursive: true, force: true }).catch(() => {});
}

// 启动安装并退出当前应用。Windows: msiexec /i（detached，走 UAC/SmartScreen）；macOS: 打开 dmg。
async function installAndQuit(file) {
  if (process.platform === "win32") {
    const child = spawn("msiexec", ["/i", file], { detached: true, stdio: "ignore" });
    child.unref();
  } else {
    await shell.openPath(file);
  }
  setTimeout(() => app.quit(), 1200); // 给安装程序拉起的时间，再退出旧实例
}

module.exports = { downloadAndVerify, installAndQuit, cleanupDir };
