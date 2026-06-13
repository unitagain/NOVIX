import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const desktopRoot = resolve(__dirname, "..");
const repoRoot = resolve(desktopRoot, "..");

const requiredDirs = [
  "frontend",
  "backend",
  "desktop",
  "desktop/config",
  "desktop/main",
  "desktop/preload",
  "desktop/resources",
  "desktop/resources/icons",
  "desktop/scripts"
];

function parseVersion(value) {
  return String(value || "")
    .replace(/^v/i, "")
    .split(".")
    .map((part) => Number.parseInt(part, 10) || 0);
}

function versionAtLeast(current, expected) {
  const left = parseVersion(current);
  const right = parseVersion(expected);
  const max = Math.max(left.length, right.length);

  for (let index = 0; index < max; index += 1) {
    const a = left[index] || 0;
    const b = right[index] || 0;
    if (a > b) return true;
    if (a < b) return false;
  }

  return true;
}

function fail(message) {
  console.error(`[desktop-doctor] ${message}`);
  process.exitCode = 1;
}

for (const relativeDir of requiredDirs) {
  const absoluteDir = join(repoRoot, relativeDir);
  if (!existsSync(absoluteDir)) {
    fail(`missing directory: ${relativeDir}`);
  }
}

const manifestPath = join(desktopRoot, "config", "shell.manifest.json");
if (!existsSync(manifestPath)) {
  fail("missing desktop/config/shell.manifest.json");
} else {
  const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));

  if (!["phase0", "phase1", "phase2", "phase3", "phase4"].includes(String(manifest.phase || ""))) {
    fail(`manifest phase must be phase0, phase1, phase2, phase3, or phase4, received ${manifest.phase}`);
  }

  if (manifest.toolchain?.shell !== "electron") {
    fail("manifest toolchain.shell must remain electron");
  }

  if (manifest.toolchain?.sidecar !== "python-fastapi") {
    fail("manifest toolchain.sidecar must remain python-fastapi");
  }

  if (!versionAtLeast(process.versions.node, manifest.toolchain.minimumNode)) {
    fail(`Node.js version is too low: current ${process.versions.node}, expected >= ${manifest.toolchain.minimumNode}`);
  }

  if (!manifest.paths?.dataDirName) {
    fail("manifest paths.dataDirName is required for desktop runtime path management");
  }
}

if (process.exitCode && process.exitCode !== 0) {
  process.exit(process.exitCode);
}

const phase = JSON.parse(readFileSync(manifestPath, "utf8")).phase;
console.log(`[desktop-${phase}] desktop baseline checks passed`);
