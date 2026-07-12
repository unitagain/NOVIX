const SESSION_HEADER = "X-WenShape-Session-Token";

function normalizeRequestOrigin(rawUrl) {
  const requestUrl = new URL(rawUrl);
  if (requestUrl.protocol === "ws:") requestUrl.protocol = "http:";
  if (requestUrl.protocol === "wss:") requestUrl.protocol = "https:";
  return requestUrl.origin;
}

function authenticatedHeaders(details, { sidecarState, frontendDevUrl = "" }) {
  const requestHeaders = { ...(details.requestHeaders || {}) };
  if (!sidecarState?.token || !sidecarState?.baseUrl) {
    return requestHeaders;
  }

  let requestOrigin;
  try {
    requestOrigin = normalizeRequestOrigin(details.url);
  } catch (_error) {
    return requestHeaders;
  }

  const allowedOrigins = new Set([sidecarState.baseUrl, frontendDevUrl].filter(Boolean));
  if (allowedOrigins.has(requestOrigin)) {
    requestHeaders[SESSION_HEADER] = sidecarState.token;
  }
  return requestHeaders;
}

function installSidecarAuthInterceptor(electronSession, getContext) {
  electronSession.webRequest.onBeforeSendHeaders({ urls: ["<all_urls>"] }, (details, callback) => {
    callback({ requestHeaders: authenticatedHeaders(details, getContext()) });
  });
}

module.exports = {
  SESSION_HEADER,
  authenticatedHeaders,
  installSidecarAuthInterceptor,
  normalizeRequestOrigin
};
