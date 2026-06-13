import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";

const __dirname = dirname(fileURLToPath(import.meta.url));

export const desktopRoot = resolve(__dirname, "..");
export const repoRoot = resolve(desktopRoot, "..");
export const frontendRoot = join(repoRoot, "frontend");
export const backendRoot = join(repoRoot, "backend");
export const artifactsRoot = join(desktopRoot, ".artifacts");
export const releaseRoot = join(artifactsRoot, "releases");
export const shellManifestPath = join(desktopRoot, "config", "shell.manifest.json");
export const desktopPackageJsonPath = join(desktopRoot, "package.json");

export function readJson(filePath) {
  return JSON.parse(readFileSync(filePath, "utf8"));
}

export function shellManifest() {
  return readJson(shellManifestPath);
}

export function desktopPackageJson() {
  return readJson(desktopPackageJsonPath);
}

export function npmCommand() {
  return process.platform === "win32" ? "npm.cmd" : "npm";
}

export function electronForgeCommand() {
  return process.platform === "win32"
    ? join(desktopRoot, "node_modules", ".bin", "electron-forge.cmd")
    : join(desktopRoot, "node_modules", ".bin", "electron-forge");
}

export function pythonInvocation() {
  if (process.env.WENSHAPE_DESKTOP_PYTHON) {
    return {
      command: process.env.WENSHAPE_DESKTOP_PYTHON,
      argsPrefix: []
    };
  }
  if (process.platform === "win32") {
    return {
      command: "py",
      argsPrefix: ["-3.12"]
    };
  }
  return {
    command: "python3",
    argsPrefix: []
  };
}

export function pythonCommand() {
  return pythonInvocation().command;
}

export function startCommand(command, args, options = {}) {
  if (process.platform === "win32") {
    return spawn(process.env.ComSpec || "cmd.exe", ["/d", "/s", "/c", command, ...args], {
      windowsHide: false,
      ...options
    });
  }

  return spawn(command, args, {
    windowsHide: false,
    ...options
  });
}

export function runCommand(command, args, options = {}) {
  return new Promise((resolvePromise, rejectPromise) => {
    const child = startCommand(command, args, {
      stdio: options.stdio || "inherit",
      cwd: options.cwd,
      env: options.env
    });

    child.once("error", rejectPromise);
    child.once("exit", (code) => {
      if (code === 0) {
        resolvePromise();
        return;
      }
      rejectPromise(new Error(`command failed (${command} ${args.join(" ")}), exit code=${code}`));
    });
  });
}

export function ensureDirectory(dirPath) {
  mkdirSync(dirPath, { recursive: true });
  return dirPath;
}

export function resetDirectory(dirPath) {
  rmSync(dirPath, { recursive: true, force: true });
  mkdirSync(dirPath, { recursive: true });
  return dirPath;
}

export function copyDirectory(sourceDir, targetDir) {
  rmSync(targetDir, { recursive: true, force: true });
  mkdirSync(dirname(targetDir), { recursive: true });
  cpSync(sourceDir, targetDir, {
    recursive: true,
    force: true
  });
}

export function writeTextFile(filePath, content) {
  mkdirSync(dirname(filePath), { recursive: true });
  writeFileSync(filePath, content, "utf8");
  return filePath;
}

export function parseCliArgs(argv) {
  const result = {
    positional: []
  };

  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (!token.startsWith("--")) {
      result.positional.push(token);
      continue;
    }

    const key = token.slice(2);
    const next = argv[index + 1];
    if (!next || next.startsWith("--")) {
      result[key] = "true";
      continue;
    }

    result[key] = next;
    index += 1;
  }

  return result;
}

export function resolveReleaseTarget(overrides = {}) {
  const manifest = shellManifest();
  const pkg = desktopPackageJson();
  const platform = String(overrides.platform || process.platform);
  const arch = String(overrides.arch || process.arch);
  const channel = String(
    overrides.channel
    || process.env.WENSHAPE_DESKTOP_RELEASE_CHANNEL
    || manifest.releaseChannels?.[0]
    || "dev"
  );
  const version = String(
    overrides.version
    || process.env.WENSHAPE_DESKTOP_BUILD_VERSION
    || pkg.version
  );
  const targetId = `${platform}-${arch}`;
  const outDir = resolve(
    String(overrides.outDir || join(releaseRoot, `${version}-${channel}`, targetId))
  );

  return {
    platform,
    arch,
    channel,
    version,
    targetId,
    outDir
  };
}

export function resolveBuildRoot(target) {
  return join(artifactsRoot, "build", `${target.version}-${target.channel}`, target.targetId);
}

export function verifyPathExists(targetPath, message) {
  if (!existsSync(targetPath)) {
    throw new Error(message || `required path not found: ${targetPath}`);
  }
}
