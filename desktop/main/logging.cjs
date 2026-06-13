const fs = require("node:fs");
const path = require("node:path");
const util = require("node:util");

let installedLogger = null;

// 10 MB per log file, keep 3 rotated backups
const DEFAULT_MAX_BYTES = 10 * 1024 * 1024;
const DEFAULT_BACKUP_COUNT = 3;

function createLine(level, args) {
  return `${new Date().toISOString()} level=${level} ${util.format(...args)}`;
}

/**
 * Rotating log writer.
 *
 * When the current file exceeds `maxBytes`, it is renamed to `<name>.1`,
 * previous backups are shifted (`1` -> `2`, ..., oldest is deleted), and a
 * fresh file is opened.  This mirrors Python's RotatingFileHandler behaviour
 * and caps total disk usage at roughly `maxBytes * (backupCount + 1)`.
 */
function createRotatingWriter(logFilePath, maxBytes, backupCount) {
  let stream = null;
  let bytesWritten = 0;

  function openStream() {
    stream = fs.createWriteStream(logFilePath, {
      flags: "a",
      encoding: "utf8"
    });
    try {
      const stat = fs.statSync(logFilePath);
      bytesWritten = stat.size;
    } catch (_error) {
      bytesWritten = 0;
    }
  }

  function rotate() {
    if (stream && !stream.destroyed && !stream.closed) {
      try { stream.end(); } catch (_error) { /* ignore */ }
    }

    // Shift existing backups: .3 -> delete, .2 -> .3, .1 -> .2
    for (let i = backupCount; i >= 1; i -= 1) {
      const src = i === 1 ? logFilePath : `${logFilePath}.${i - 1}`;
      const dst = `${logFilePath}.${i}`;
      try {
        if (i === backupCount) {
          fs.unlinkSync(dst);
        }
      } catch (_error) { /* ignore */ }
      try {
        fs.renameSync(src, dst);
      } catch (_error) { /* ignore */ }
    }

    openStream();
  }

  function write(line) {
    if (!stream || stream.destroyed || stream.closed) {
      openStream();
    }

    const data = `${line}\n`;
    const dataBytes = Buffer.byteLength(data, "utf8");

    if (bytesWritten + dataBytes > maxBytes && bytesWritten > 0) {
      rotate();
    }

    try {
      stream.write(data);
      bytesWritten += dataBytes;
    } catch (_error) { /* ignore */ }
  }

  function close() {
    if (stream && !stream.closed && !stream.destroyed) {
      return new Promise((resolve) => stream.end(resolve));
    }
    return Promise.resolve();
  }

  openStream();
  return { write, close };
}

function installMainLogger(options = {}) {
  if (installedLogger) {
    return installedLogger;
  }

  const logFilePath = path.resolve(String(options.logFilePath));
  const maxBytes = options.maxBytes || DEFAULT_MAX_BYTES;
  const backupCount = options.backupCount || DEFAULT_BACKUP_COUNT;

  fs.mkdirSync(path.dirname(logFilePath), { recursive: true });

  const writer = createRotatingWriter(logFilePath, maxBytes, backupCount);

  function write(level, args) {
    writer.write(createLine(level, args));
  }

  console.debug = (...args) => write("DEBUG", args);
  console.info = (...args) => write("INFO", args);
  console.log = (...args) => write("INFO", args);
  console.warn = (...args) => write("WARN", args);
  console.error = (...args) => write("ERROR", args);

  process.on("uncaughtException", (error) => {
    writer.write(createLine("ERROR", ["[desktop-main] uncaughtException", error]));
  });

  process.on("unhandledRejection", (reason) => {
    writer.write(createLine("ERROR", ["[desktop-main] unhandledRejection", reason]));
  });

  installedLogger = {
    logFilePath,
    async close() {
      await writer.close();
    }
  };

  console.info(`[desktop-main] logging initialized -> ${logFilePath} (maxBytes=${maxBytes}, backupCount=${backupCount})`);
  return installedLogger;
}

module.exports = {
  installMainLogger
};
