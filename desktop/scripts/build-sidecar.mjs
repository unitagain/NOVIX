import { existsSync } from "node:fs";
import { join } from "node:path";

import {
  artifactsRoot,
  backendRoot,
  parseCliArgs,
  pythonInvocation,
  readJson,
  releaseRoot,
  resolveBuildRoot,
  resolveReleaseTarget,
  resetDirectory,
  runCommand,
  shellManifest,
  verifyPathExists,
  writeTextFile
} from "./_shared.mjs";

function normalizeForPython(filePath) {
  return String(filePath).replaceAll("\\", "\\\\");
}

function buildPyInstallerSpec({ backendStaticDir, buildRoot, platform, arch }) {
  const configYaml = join(backendRoot, "config.yaml");
  const stopwordsYaml = join(backendRoot, "stopwords.yaml");
  const mainPy = join(backendRoot, "app", "main.py");
  const specDir = join(buildRoot, "spec");

  // macOS: console must be True to produce a plain executable (not a .app
  // bundle).  Windows: console=False hides the console window.
  const consoleFlag = platform === "darwin" ? "True" : "False";

  // UPX is unreliable on macOS (code-signing breaks, some dylibs crash).
  const upxFlag = platform === "darwin" ? "False" : "True";

  // Map Electron/Node arch names to PyInstaller target_arch values.
  let targetArchPy = "None";
  if (platform === "darwin") {
    if (arch === "arm64") targetArchPy = "'arm64'";
    else if (arch === "x64") targetArchPy = "'x86_64'";
  }

  const specText = `# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files

backend_root = r"${normalizeForPython(backendRoot)}"
main_py = r"${normalizeForPython(mainPy)}"
static_dir = r"${normalizeForPython(backendStaticDir)}"
config_yaml = r"${normalizeForPython(configYaml)}"
stopwords_yaml = r"${normalizeForPython(stopwordsYaml)}"

datas = [
    (static_dir, "static"),
    (config_yaml, "."),
    (stopwords_yaml, "."),
]
datas += collect_data_files("tiktoken_ext.openai_public")
datas += collect_data_files("tiktoken_ext")

a = Analysis(
    [main_py],
    pathex=[backend_root],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "tiktoken",
        "tiktoken_ext.openai_public",
        "tiktoken_ext",
        "aiohttp",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "IPython",
        "altair",
        "anaconda_project",
        "astropy",
        "bokeh",
        "boto3",
        "botocore",
        "bs4.tests",
        "black",
        "dask",
        "distributed",
        "docutils",
        "flask",
        "huggingface_hub",
        "imagecodecs",
        "isort",
        "jedi",
        "jsonschema.tests",
        "jupyterlab",
        "llvmlite",
        "matplotlib",
        "numba",
        "notebook",
        "pandas",
        "PIL.ImageQt",
        "pytest",
        "scipy",
        "sklearn",
        "sympy",
        "tables",
        "tensorflow",
        "torch",
        "xarray",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WenShapeBackend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=${upxFlag},
    console=${consoleFlag},
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=${targetArchPy},
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=${upxFlag},
    upx_exclude=[],
    name="sidecar",
)
`;

  return writeTextFile(join(specDir, "sidecar.spec"), specText);
}

async function ensureIsolatedPython(target) {
  const venvRoot = join(artifactsRoot, "venvs", `sidecar-${target.targetId}`);
  const pythonPath = process.platform === "win32"
    ? join(venvRoot, "Scripts", "python.exe")
    : join(venvRoot, "bin", "python");

  if (!existsSync(pythonPath)) {
    console.log(`[desktop-build] creating isolated venv -> ${venvRoot}`);
    const python = pythonInvocation();
    await runCommand(python.command, [...python.argsPrefix, "-m", "venv", venvRoot], {
      cwd: backendRoot
    });
  }

  console.log("[desktop-build] installing runtime dependencies into isolated venv");
  await runCommand(pythonPath, ["-m", "pip", "install", "--upgrade", "pip"], {
    cwd: backendRoot
  });
  await runCommand(pythonPath, ["-m", "pip", "install", "-r", "requirements.runtime.txt", "pyinstaller"], {
    cwd: backendRoot
  });

  return pythonPath;
}

async function main() {
  const args = parseCliArgs(process.argv.slice(2));
  const target = resolveReleaseTarget({
    platform: args.platform,
    arch: args.arch,
    channel: args.channel,
    version: args.version,
    outDir: args["out-dir"]
  });
  const buildRoot = resolveBuildRoot(target);
  const backendStaticDir = join(backendRoot, "static");
  const distRoot = join(buildRoot, "dist");
  const workRoot = join(buildRoot, "work");
  const useIsolatedVenv = args["isolated-venv"] === "true" || process.env.WENSHAPE_DESKTOP_ISOLATED_VENV === "1";

  verifyPathExists(backendStaticDir, "backend/static is missing. Run the frontend build step before packaging the sidecar.");
  verifyPathExists(join(backendRoot, "config.yaml"), "backend/config.yaml is missing");
  verifyPathExists(join(backendRoot, "stopwords.yaml"), "backend/stopwords.yaml is missing");

  if (process.env.CONDA_PREFIX && !process.env.WENSHAPE_DESKTOP_PYTHON) {
    console.warn("[desktop-build] detected a Conda environment. For leaner release artifacts, prefer a clean runtime venv and point WENSHAPE_DESKTOP_PYTHON to it.");
  }

  resetDirectory(distRoot);
  resetDirectory(workRoot);

  const specPath = buildPyInstallerSpec({
    backendStaticDir,
    buildRoot,
    platform: target.platform,
    arch: target.arch
  });
  const python = pythonInvocation();
  const pythonExecutable = useIsolatedVenv
    ? await ensureIsolatedPython(target)
    : python.command;
  const pythonExecutableArgsPrefix = useIsolatedVenv ? [] : python.argsPrefix;

  console.log(`[desktop-build] building sidecar for ${target.targetId}`);
  await runCommand(pythonExecutable, [...pythonExecutableArgsPrefix, "-m", "PyInstaller", "--noconfirm", "--clean", specPath, "--distpath", distRoot, "--workpath", workRoot], {
    cwd: backendRoot
  });

  const outputDir = join(distRoot, "sidecar");
  verifyPathExists(outputDir, `sidecar build output is missing: ${outputDir}`);

  const metadata = {
    target,
    sidecarDir: outputDir,
    staticSource: backendStaticDir,
    builtAt: new Date().toISOString()
  };
  writeTextFile(join(buildRoot, "sidecar-build.json"), `${JSON.stringify(metadata, null, 2)}\n`);
  console.log(`[desktop-build] sidecar ready -> ${outputDir}`);
}

main().catch((error) => {
  console.error("[desktop-build] sidecar build failed", error);
  process.exit(1);
});
