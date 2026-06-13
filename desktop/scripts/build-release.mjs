import { join, extname } from "node:path";
import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";

import {
  copyDirectory,
  desktopPackageJson,
  desktopRoot,
  electronForgeCommand,
  parseCliArgs,
  resetDirectory,
  resolveBuildRoot,
  resolveReleaseTarget,
  runCommand,
  shellManifest,
  shellManifestPath,
  verifyPathExists,
  writeTextFile
} from "./_shared.mjs";

// 安装包扩展名（自动更新分发的产物）；解包目录里的 WenShape.exe 不在 make/ 下，不会被误抓。
const ARTIFACT_EXTS = new Set([".msi", ".exe", ".dmg", ".zip", ".nupkg"]);

function sha256File(filePath) {
  return createHash("sha256").update(readFileSync(filePath)).digest("hex");
}

// 递归扫描 make 产物目录，对安装包计算 SHA256 + 大小（供发布登记直接复制到 admin 后台）
function collectArtifacts(rootDir) {
  if (!existsSync(rootDir)) return [];
  const results = [];
  const walk = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = join(dir, entry.name);
      if (entry.isDirectory()) {
        walk(full);
      } else if (ARTIFACT_EXTS.has(extname(entry.name).toLowerCase())) {
        results.push({
          file: entry.name,
          relativePath: full.slice(rootDir.length).replace(/^[/\\]/, ""),
          size: statSync(full).size,
          sha256: sha256File(full)
        });
      }
    }
  };
  walk(rootDir);
  return results;
}

async function main() {
  const args = parseCliArgs(process.argv.slice(2));
  const mode = args.positional[0] || "make";
  if (!["make", "package"].includes(mode)) {
    throw new Error(`unsupported release mode: ${mode}`);
  }

  const target = resolveReleaseTarget({
    platform: args.platform,
    arch: args.arch,
    channel: args.channel,
    version: args.version,
    outDir: args["out-dir"]
  });
  const buildRoot = resolveBuildRoot(target);
  const sidecarDir = join(buildRoot, "dist", "sidecar");
  const releaseMetadataPath = join(target.outDir, "wenshape-release.json");
  const forgeOutDir = join(desktopRoot, "out");
  const bundledOutDir = join(target.outDir, "forge-out");
  const manifest = shellManifest();
  const pkg = desktopPackageJson();
  const releaseEnv = {
    ...process.env,
    WENSHAPE_DESKTOP_RELEASE_CHANNEL: target.channel,
    WENSHAPE_DESKTOP_BUILD_VERSION: target.version,
    WENSHAPE_DESKTOP_SIDECAR_DIR: sidecarDir,
    WENSHAPE_DESKTOP_RELEASE_OUT_DIR: target.outDir
  };

  console.log(`[desktop-release] target=${target.targetId} channel=${target.channel} version=${target.version}`);

  if (args["skip-frontend"] !== "true") {
    await runCommand("node", ["./scripts/build-frontend.mjs"], {
      cwd: desktopRoot
    });
  }

  if (args["skip-sidecar"] !== "true") {
    await runCommand("node", ["./scripts/build-sidecar.mjs", "--platform", target.platform, "--arch", target.arch, "--channel", target.channel, "--version", target.version], {
      cwd: desktopRoot,
      env: {
        ...process.env,
        WENSHAPE_DESKTOP_BUILD_VERSION: target.version
      }
    });
  }

  verifyPathExists(sidecarDir, "sidecar build output is missing. The sidecar build step must succeed before packaging the desktop shell.");
  resetDirectory(target.outDir);
  if (mode === "make") {
    resetDirectory(join(forgeOutDir, "make"));
  }

  // 把构建版本号注入打包用的 manifest（运行时 getAppVersion 读 manifest.product.version，
  // 它会被 asar 打包进安装包）。构建结束后恢复源文件，避免污染 git 工作区。
  const originalManifestText = readFileSync(shellManifestPath, "utf8");
  let manifestPatched = false;
  if (manifest.product?.version !== target.version) {
    const patched = { ...manifest, product: { ...manifest.product, version: target.version } };
    writeFileSync(shellManifestPath, `${JSON.stringify(patched, null, 2)}\n`, "utf8");
    manifestPatched = true;
    console.log(`[desktop-release] manifest version -> ${target.version} (will restore after build)`);
  }

  try {
    const shouldRunPackageStep = mode === "package" || args["skip-package"] !== "true";
    if (shouldRunPackageStep) {
      await runCommand(electronForgeCommand(), ["package", "--platform", target.platform, "--arch", target.arch], {
        cwd: desktopRoot,
        env: releaseEnv
      });
    }

    if (mode === "make") {
      if (target.platform === "win32") {
        await runCommand("node", ["./scripts/build-windows-msi.mjs", "--platform", target.platform, "--arch", target.arch, "--channel", target.channel, "--version", target.version], {
          cwd: desktopRoot,
          env: releaseEnv
        });
      } else {
        await runCommand(electronForgeCommand(), ["make", "--platform", target.platform, "--arch", target.arch, "--skip-package"], {
          cwd: desktopRoot,
          env: releaseEnv
        });
      }
    }
  } finally {
    if (manifestPatched) {
      writeFileSync(shellManifestPath, originalManifestText, "utf8");
      console.log("[desktop-release] manifest restored");
    }
  }

  verifyPathExists(forgeOutDir, `electron forge output directory is missing: ${forgeOutDir}`);
  copyDirectory(forgeOutDir, bundledOutDir);

  const artifacts = mode === "make" ? collectArtifacts(join(forgeOutDir, "make")) : [];
  for (const item of artifacts) {
    console.log(`[desktop-release] artifact ${item.file} size=${item.size} sha256=${item.sha256}`);
  }

  const metadata = {
    product: { ...manifest.product, version: target.version },
    mode,
    target,
    shellVersion: pkg.version,
    sidecarDir,
    outDir: target.outDir,
    forgeOutDir: bundledOutDir,
    artifacts,
    builtAt: new Date().toISOString()
  };
  writeTextFile(releaseMetadataPath, `${JSON.stringify(metadata, null, 2)}\n`);
  console.log(`[desktop-release] release metadata -> ${releaseMetadataPath}`);
}

main().catch((error) => {
  console.error("[desktop-release] build failed", error);
  process.exit(1);
});
