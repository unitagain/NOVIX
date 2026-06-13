const process = require("node:process");

const DEFAULT_API_BASE = "https://api.wenshape.cn";
const TIMEOUT_MS = 12000;

function normalizeBaseUrl(value) {
  return String(value || "").replace(/\/+$/, "");
}

function resolveCloudBaseUrl(manifest) {
  const fromEnv = process.env.WENSHAPE_CLOUD_API_URL;
  if (fromEnv) return normalizeBaseUrl(fromEnv);
  const fromManifest = manifest?.cloud?.apiBaseUrl;
  if (fromManifest) return normalizeBaseUrl(fromManifest);
  return DEFAULT_API_BASE;
}

class CloudApiError extends Error {
  constructor(status, message, data) {
    super(message);
    this.name = "CloudApiError";
    this.status = status;
    this.data = data;
  }
}

function extractErrorMessage(payload, fallback) {
  if (!payload) return fallback;
  const detail = payload.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail.map((entry) => entry?.msg).filter(Boolean);
    if (parts.length) return parts.join("; ");
  }
  if (typeof payload.message === "string") return payload.message;
  return fallback;
}

async function readResponseBody(response) {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function withTimeout(promise, ms, abort) {
  let timer;
  const timeout = new Promise((_resolve, reject) => {
    timer = setTimeout(() => {
      abort.abort();
      reject(new CloudApiError(0, "请求超时，请检查网络。"));
    }, ms);
  });
  return Promise.race([promise, timeout]).finally(() => {
    clearTimeout(timer);
  });
}

function createCloudClient({ getBaseUrl, getTokens, onTokenRefresh, onUnauthorized }) {
  let refreshing = null;

  async function rawFetch(path, init = {}) {
    const baseUrl = normalizeBaseUrl(getBaseUrl());
    const url = `${baseUrl}${path}`;
    const headers = {
      Accept: "application/json",
      ...(init.headers || {}),
    };
    if (init.body !== undefined && headers["Content-Type"] === undefined) {
      headers["Content-Type"] = "application/json";
    }

    const abort = new AbortController();
    const fetchPromise = fetch(url, {
      ...init,
      headers,
      body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
      signal: abort.signal,
    });

    let response;
    try {
      response = await withTimeout(fetchPromise, TIMEOUT_MS, abort);
    } catch (error) {
      if (error instanceof CloudApiError) throw error;
      throw new CloudApiError(0, error?.message || "无法连接到云端服务", { cause: String(error) });
    }

    if (response.status === 204) {
      return undefined;
    }

    const data = await readResponseBody(response);

    if (!response.ok) {
      throw new CloudApiError(
        response.status,
        extractErrorMessage(data, response.statusText || "请求失败"),
        data
      );
    }

    return data;
  }

  async function authedFetch(path, init = {}) {
    const tokens = getTokens();
    const headers = { ...(init.headers || {}) };
    if (tokens?.access_token) {
      headers.Authorization = `Bearer ${tokens.access_token}`;
    }

    try {
      return await rawFetch(path, { ...init, headers });
    } catch (error) {
      if (!(error instanceof CloudApiError) || error.status !== 401) {
        throw error;
      }

      // Try one refresh, then replay.
      const refreshed = await refreshTokens();
      if (!refreshed) {
        await onUnauthorized?.();
        throw error;
      }

      const retryHeaders = { ...(init.headers || {}) };
      retryHeaders.Authorization = `Bearer ${refreshed.access_token}`;
      try {
        return await rawFetch(path, { ...init, headers: retryHeaders });
      } catch (replayError) {
        if (replayError instanceof CloudApiError && replayError.status === 401) {
          await onUnauthorized?.();
        }
        throw replayError;
      }
    }
  }

  async function refreshTokens() {
    if (refreshing) return refreshing;
    const tokens = getTokens();
    if (!tokens?.refresh_token) return null;

    refreshing = (async () => {
      try {
        const data = await rawFetch("/api/v1/auth/refresh", {
          method: "POST",
          body: { refresh_token: tokens.refresh_token },
        });
        if (!data?.access_token || !data?.refresh_token) {
          return null;
        }
        await onTokenRefresh?.(data.access_token, data.refresh_token);
        return { access_token: data.access_token, refresh_token: data.refresh_token };
      } catch (error) {
        if (error instanceof CloudApiError && error.status === 401) {
          await onUnauthorized?.();
        }
        return null;
      } finally {
        refreshing = null;
      }
    })();

    return refreshing;
  }

  return {
    rawFetch,
    authedFetch,
    refreshTokens,

    // ── Auth ──
    register: (body) => rawFetch("/api/v1/auth/register", { method: "POST", body }),
    login: (body) => rawFetch("/api/v1/auth/login", { method: "POST", body }),
    logout: (refreshToken) =>
      authedFetch("/api/v1/auth/logout", {
        method: "POST",
        body: { refresh_token: refreshToken },
      }),
    me: () => authedFetch("/api/v1/auth/me"),
    requestPasswordReset: (email) =>
      rawFetch("/api/v1/auth/password-reset/request", { method: "POST", body: { email } }),
    confirmPasswordReset: (body) =>
      rawFetch("/api/v1/auth/password-reset/confirm", { method: "POST", body }),

    // ── Devices ──
    registerDevice: (body) =>
      authedFetch("/api/v1/devices/register", { method: "POST", body }),
    listDevices: () => authedFetch("/api/v1/devices"),
    removeDevice: (deviceId) =>
      authedFetch(`/api/v1/devices/${encodeURIComponent(deviceId)}`, { method: "DELETE" }),

    // ── Releases ──
    listReleases: ({ platform, channel } = {}) => {
      const search = new URLSearchParams();
      if (platform) search.set("platform", platform);
      if (channel) search.set("channel", channel);
      const qs = search.toString();
      return rawFetch(`/api/v1/releases${qs ? `?${qs}` : ""}`);
    },
    checkVersion: (body) =>
      rawFetch("/api/v1/releases/check", { method: "POST", body }),
  };
}

module.exports = {
  CloudApiError,
  createCloudClient,
  resolveCloudBaseUrl,
  DEFAULT_API_BASE,
};
