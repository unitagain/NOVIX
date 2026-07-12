const assert = require("node:assert/strict");
const test = require("node:test");

const { SESSION_HEADER, authenticatedHeaders, normalizeRequestOrigin } = require("../main/sidecar-auth.cjs");

const context = {
  sidecarState: {
    baseUrl: "http://127.0.0.1:8123",
    token: "desktop-token"
  },
  frontendDevUrl: "http://127.0.0.1:3000"
};

test("normalizes websocket origins to their HTTP equivalent", () => {
  assert.equal(normalizeRequestOrigin("ws://127.0.0.1:8123/ws/trace"), "http://127.0.0.1:8123");
  assert.equal(normalizeRequestOrigin("wss://example.test/ws"), "https://example.test");
});

test("injects the token only into sidecar and configured dev renderer requests", () => {
  const sidecar = authenticatedHeaders({ url: "http://127.0.0.1:8123/api/projects", requestHeaders: {} }, context);
  const websocket = authenticatedHeaders({ url: "ws://127.0.0.1:8123/ws/trace", requestHeaders: {} }, context);
  const devProxy = authenticatedHeaders({ url: "http://127.0.0.1:3000/api/projects", requestHeaders: {} }, context);
  const wrongPort = authenticatedHeaders({ url: "http://127.0.0.1:8124/api/projects", requestHeaders: {} }, context);
  const external = authenticatedHeaders({ url: "https://example.test/", requestHeaders: {} }, context);

  assert.equal(sidecar[SESSION_HEADER], "desktop-token");
  assert.equal(websocket[SESSION_HEADER], "desktop-token");
  assert.equal(devProxy[SESSION_HEADER], "desktop-token");
  assert.equal(wrongPort[SESSION_HEADER], undefined);
  assert.equal(external[SESSION_HEADER], undefined);
});
