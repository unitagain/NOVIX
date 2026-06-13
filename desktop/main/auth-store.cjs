const fs = require("node:fs");
const path = require("node:path");
const { safeStorage } = require("electron");

const TOKEN_FILE_NAME = "auth.dat";

function tokenFilePath(paths) {
  if (!paths || !paths.cacheDir) {
    throw new Error("auth-store: cacheDir is not initialized");
  }
  return path.join(paths.cacheDir, TOKEN_FILE_NAME);
}

function canEncrypt() {
  try {
    return typeof safeStorage.isEncryptionAvailable === "function" && safeStorage.isEncryptionAvailable();
  } catch {
    return false;
  }
}

function readRawTokenFile(paths) {
  const file = tokenFilePath(paths);
  if (!fs.existsSync(file)) {
    return null;
  }
  try {
    return fs.readFileSync(file);
  } catch (error) {
    console.warn("[auth-store] failed to read token file", error);
    return null;
  }
}

function decodePayload(buffer) {
  if (!buffer || buffer.length === 0) {
    return null;
  }
  // Convention: first byte is a marker.
  // 0x01 = safeStorage encrypted; 0x00 = plain UTF-8 JSON.
  const marker = buffer[0];
  const body = buffer.subarray(1);
  try {
    if (marker === 0x01) {
      if (!canEncrypt()) {
        // Encrypted blob present but platform refuses to decrypt — drop it.
        return null;
      }
      const decrypted = safeStorage.decryptString(body);
      return JSON.parse(decrypted);
    }
    if (marker === 0x00) {
      return JSON.parse(body.toString("utf8"));
    }
  } catch (error) {
    console.warn("[auth-store] failed to decode token payload", error);
  }
  return null;
}

function encodePayload(payload) {
  const json = JSON.stringify(payload);
  if (canEncrypt()) {
    const encrypted = safeStorage.encryptString(json);
    return Buffer.concat([Buffer.from([0x01]), encrypted]);
  }
  return Buffer.concat([Buffer.from([0x00]), Buffer.from(json, "utf8")]);
}

function loadTokens(paths) {
  const buffer = readRawTokenFile(paths);
  const decoded = decodePayload(buffer);
  if (!decoded || typeof decoded !== "object") {
    return null;
  }
  if (typeof decoded.access_token !== "string" || typeof decoded.refresh_token !== "string") {
    return null;
  }
  return {
    access_token: decoded.access_token,
    refresh_token: decoded.refresh_token,
    saved_at: typeof decoded.saved_at === "string" ? decoded.saved_at : null,
  };
}

function saveTokens(paths, accessToken, refreshToken) {
  if (!accessToken || !refreshToken) {
    throw new Error("auth-store: tokens are required");
  }
  const file = tokenFilePath(paths);
  fs.mkdirSync(path.dirname(file), { recursive: true });

  const payload = {
    access_token: accessToken,
    refresh_token: refreshToken,
    saved_at: new Date().toISOString(),
  };

  const buffer = encodePayload(payload);
  fs.writeFileSync(file, buffer, { mode: 0o600 });
}

function clearTokens(paths) {
  const file = tokenFilePath(paths);
  try {
    if (fs.existsSync(file)) {
      fs.unlinkSync(file);
    }
  } catch (error) {
    console.warn("[auth-store] failed to remove token file", error);
  }
}

module.exports = {
  loadTokens,
  saveTokens,
  clearTokens,
  isEncryptionAvailable: canEncrypt,
};
