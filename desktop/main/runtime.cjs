const fs = require("node:fs");
const path = require("node:path");
const process = require("node:process");

function desktopRoot() {
  return path.resolve(__dirname, "..");
}

function repoRoot() {
  return path.resolve(desktopRoot(), "..");
}

function readShellManifest() {
  const manifestPath = path.join(desktopRoot(), "config", "shell.manifest.json");
  return JSON.parse(fs.readFileSync(manifestPath, "utf8"));
}

function boolFromEnv(value) {
  return ["1", "true", "yes", "on"].includes(String(value || "").trim().toLowerCase());
}

function getShellMetadata() {
  const manifest = readShellManifest();
  const frontendPort = Number.parseInt(
    String(process.env.WENSHAPE_FRONTEND_PORT || process.env.VITE_DEV_PORT || "3000"),
    10
  ) || 3000;
  const frontendDevUrl = String(
    process.env.WENSHAPE_DESKTOP_FRONTEND_URL || `http://127.0.0.1:${frontendPort}`
  );
  const packagedByEnv = boolFromEnv(process.env.WENSHAPE_DESKTOP_PACKAGED);
  const packagedByAsar = __dirname.includes("app.asar");
  const isPackagedRuntime = packagedByEnv || packagedByAsar;
  const isDev = boolFromEnv(process.env.WENSHAPE_DESKTOP_DEV)
    || (!isPackagedRuntime && !process.env.APPIMAGE && process.env.NODE_ENV !== "production");

  return {
    manifest,
    isDev,
    isPackagedRuntime,
    frontendDevUrl,
    repoRoot: repoRoot(),
    desktopRoot: desktopRoot()
  };
}

function buildDesktopWindowTitle() {
  return "WenShape";
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll("\"", "&quot;")
    .replaceAll("'", "&#39;");
}

function buildDesktopPage({ title, heading, message, body, footer }) {
  return `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>${escapeHtml(title)}</title>
    <style>
      :root {
        color-scheme: dark;
        --bg-start: #0f172a;
        --bg-end: #111827;
        --surface: rgba(15, 23, 42, 0.9);
        --border: rgba(148, 163, 184, 0.22);
        --text: #e5eef8;
        --muted: #cbd5e1;
      }
      * {
        box-sizing: border-box;
      }
      body {
        margin: 0;
        min-height: 100vh;
        font-family: "Segoe UI", sans-serif;
        background:
          radial-gradient(circle at top left, rgba(56, 189, 248, 0.18), transparent 32%),
          linear-gradient(160deg, var(--bg-start), var(--bg-end) 64%, #1f2937);
        color: var(--text);
      }
      main {
        width: min(960px, calc(100vw - 48px));
        margin: 0 auto;
        padding: 56px 0 72px;
      }
      section {
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 20px;
        padding: 28px 28px 24px;
        box-shadow: 0 28px 80px rgba(15, 23, 42, 0.36);
        backdrop-filter: blur(14px);
      }
      .eyebrow {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        padding: 6px 12px;
        border-radius: 999px;
        background: rgba(56, 189, 248, 0.12);
        color: #bae6fd;
        font-size: 13px;
        letter-spacing: 0.04em;
      }
      h1 {
        margin: 18px 0 12px;
        font-size: clamp(28px, 5vw, 38px);
      }
      p {
        margin: 0;
        color: var(--muted);
        line-height: 1.75;
      }
      .panel {
        margin-top: 24px;
        padding: 18px 20px;
        border-radius: 16px;
        background: rgba(15, 23, 42, 0.92);
        border: 1px solid rgba(148, 163, 184, 0.16);
      }
      .panel strong {
        display: block;
        margin-bottom: 8px;
        font-size: 14px;
        color: #dbeafe;
      }
      .status {
        display: flex;
        flex-wrap: wrap;
        gap: 10px;
        margin-top: 24px;
      }
      .badge {
        padding: 8px 12px;
        border-radius: 999px;
        font-size: 13px;
        background: rgba(148, 163, 184, 0.14);
        color: #e2e8f0;
      }
      .footer {
        margin-top: 18px;
        font-size: 13px;
        color: #94a3b8;
      }
      pre {
        margin: 0;
        white-space: pre-wrap;
        word-break: break-word;
        color: #dbeafe;
        line-height: 1.65;
        font-family: "Cascadia Code", "Consolas", monospace;
      }
      dl {
        display: grid;
        grid-template-columns: minmax(120px, 160px) 1fr;
        gap: 10px 14px;
        margin: 0;
      }
      dt {
        color: #94a3b8;
      }
      dd {
        margin: 0;
        color: #dbeafe;
        word-break: break-word;
      }
    </style>
  </head>
  <body>
    <main>
      <section>
        <span class="eyebrow">${buildDesktopWindowTitle()}</span>
        <h1>${escapeHtml(heading)}</h1>
        <p>${escapeHtml(message)}</p>
        ${body}
        ${footer ? `<div class="footer">${escapeHtml(footer)}</div>` : ""}
      </section>
    </main>
  </body>
</html>`;
}

function buildStartupPageHtml() {
  const body = `
    <div class="status">
      <span class="badge" id="phase-label">Preparing</span>
      <span class="badge" id="severity-label">Info</span>
      <span class="badge" id="updated-at">Waiting for status</span>
    </div>
    <div class="panel">
      <strong id="message-label">Initializing desktop runtime</strong>
      <p id="detail-label">WenShape is preparing local services and loading the desktop workspace.</p>
    </div>
    <div class="panel">
      <strong>Runtime status</strong>
      <dl id="status-metadata">
        <dt>Status</dt>
        <dd>Waiting for main process</dd>
      </dl>
    </div>
    <script>
      const phaseLabel = document.getElementById("phase-label");
      const severityLabel = document.getElementById("severity-label");
      const updatedAtLabel = document.getElementById("updated-at");
      const messageLabel = document.getElementById("message-label");
      const detailLabel = document.getElementById("detail-label");
      const metadataList = document.getElementById("status-metadata");

      const phaseMap = {
        booting: "Booting",
        "startup-screen": "Loading startup screen",
        "starting-sidecar": "Starting local engine",
        "loading-renderer": "Loading renderer",
        ready: "Ready",
        "restarting-sidecar": "Recovering engine",
        "renderer-load-failed": "Renderer load failed",
        "renderer-crashed": "Renderer crashed",
        "shutting-down": "Shutting down",
        error: "Error"
      };

      const severityMap = {
        info: "Info",
        warn: "Warning",
        error: "Error"
      };

      function stringifyValue(value) {
        if (value === null || value === undefined || value === "") {
          return "-";
        }
        if (typeof value === "object") {
          try {
            return JSON.stringify(value, null, 2);
          } catch (_error) {
            return String(value);
          }
        }
        return String(value);
      }

      function renderDetails(details) {
        const entries = Object.entries(details || {});
        if (!entries.length) {
          metadataList.innerHTML = "<dt>Status</dt><dd>Waiting for main process</dd>";
          return;
        }
        metadataList.innerHTML = entries.map(([key, value]) => (
          "<dt>" + key + "</dt><dd>" + stringifyValue(value) + "</dd>"
        )).join("");
      }

      function renderStatus(payload) {
        if (!payload || typeof payload !== "object") {
          return;
        }
        phaseLabel.textContent = phaseMap[payload.phase] || payload.phase || "Booting";
        severityLabel.textContent = severityMap[payload.severity] || payload.severity || "Info";
        updatedAtLabel.textContent = payload.updatedAt ? "Updated " + payload.updatedAt : "Waiting for status";
        messageLabel.textContent = payload.message || "Initializing desktop runtime";
        detailLabel.textContent = payload.detail || "WenShape is preparing local runtime resources.";
        renderDetails(payload.details);
      }

      async function hydrateStatus() {
        try {
          if (window.wenshapeDesktop && window.wenshapeDesktop.getShellStatus) {
            const payload = await window.wenshapeDesktop.getShellStatus();
            renderStatus(payload);
          }
        } catch (_error) {
        }

        try {
          if (window.wenshapeDesktop && window.wenshapeDesktop.onShellStatus) {
            window.wenshapeDesktop.onShellStatus(renderStatus);
          }
        } catch (_error) {
        }
      }

      hydrateStatus();
    </script>
  `;

  return buildDesktopPage({
    title: "WenShape Startup",
    heading: "Starting WenShape",
    message: "The desktop shell is preparing local services before the main workspace is shown.",
    body,
    footer: "If this page does not progress, inspect desktop logs and share them with the engineering team."
  });
}

function buildFailurePageHtml(detail, options = {}) {
  const metadataHtml = options.metadata
    ? `<div class="panel">
        <strong>Runtime paths</strong>
        <dl>
          ${Object.entries(options.metadata).map(([key, value]) => (
            `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd>`
          )).join("")}
        </dl>
      </div>`
    : "";

  const body = `
    <div class="panel">
      <strong>Failure summary</strong>
      <p>${escapeHtml(options.message || "The desktop shell or local sidecar did not start correctly.")}</p>
    </div>
    <div class="panel">
      <strong>Diagnostic details</strong>
      <pre>${escapeHtml(detail)}</pre>
    </div>
    ${metadataHtml}
  `;

  return buildDesktopPage({
    title: options.title || "WenShape Startup Failed",
    heading: options.heading || "WenShape startup failed",
    message: options.summary || "Please share the diagnostic details and log paths with the engineering team.",
    body,
    footer: options.footer || ""
  });
}

module.exports = {
  buildDesktopWindowTitle,
  buildFailurePageHtml,
  buildStartupPageHtml,
  getShellMetadata,
  readShellManifest
};
