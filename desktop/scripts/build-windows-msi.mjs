import { dirname, join, resolve } from "node:path";
import { existsSync } from "node:fs";

import { MSICreator } from "electron-wix-msi/lib/creator.js";
import { arrayToTree } from "electron-wix-msi/lib/utils/array-to-tree.js";

import {
  desktopPackageJson,
  desktopRoot,
  parseCliArgs,
  resolveReleaseTarget,
  runCommand,
  shellManifest,
  verifyPathExists
} from "./_shared.mjs";

function windowsArchForWix(targetArch) {
  if (targetArch === "x64") {
    return "x64";
  }

  if (targetArch === "ia32" || targetArch === "x86") {
    return "x86";
  }

  throw new Error(`unsupported WiX architecture: ${targetArch}`);
}

function patchWixTemplate(template) {
  return template
    .replace(/\s+xmlns:util="http:\/\/schemas\.microsoft\.com\/wix\/UtilExtension"/, "")
    .replace(/\n\s*<Property Id="ARPSYSTEMCOMPONENT" Value="1" \/>/, "")
    .replace(/\n\s*<!-- Lets cleanup any files that are were not part of the initial install[\s\S]*?<\/DirectoryRef>\n/, "\n")
    .replace(/\n\s*<ComponentRef Id="PurgeOnUninstall" \/>\n/, "\n");
}

function attachFilesToFlatTree(tree, files, specialFiles, registry, root, executableName) {
  const output = globalThis.structuredClone(tree);
  output.__ELECTRON_WIX_MSI_REGISTRY__ = registry;
  output.__ELECTRON_WIX_MSI_FILES__ = specialFiles;
  const specialFileNames = new Set((specialFiles || []).map((file) => file?.name).filter(Boolean));

  for (const filePath of files) {
    const relativePath = filePath.slice(root.length).replace(/^[/\\]/, "");
    const segments = relativePath.split(/[/\\]/).filter(Boolean);
    if (!segments.length) {
      continue;
    }

    if (segments.length === 1 && executableName && segments[0] === executableName && specialFileNames.has(executableName)) {
      continue;
    }

    let target = output;
    for (let index = 0; index < segments.length - 1; index += 1) {
      target = target[segments[index]];
      if (!target) {
        break;
      }
    }

    if (!target) {
      continue;
    }

    target.__ELECTRON_WIX_MSI_FILES__.push({
      name: segments[segments.length - 1],
      path: filePath
    });
  }

  return output;
}

async function main() {
  const args = parseCliArgs(process.argv.slice(2));
  const target = resolveReleaseTarget({
    platform: args.platform || "win32",
    arch: args.arch,
    channel: args.channel,
    version: args.version
  });
  const pkg = desktopPackageJson();
  const manifest = shellManifest();
  const packageDir = resolve(
    args["package-dir"] || join(desktopRoot, "out", `${pkg.name}-${target.platform}-${target.arch}`)
  );
  const outputDir = resolve(
    args["make-dir"] || join(desktopRoot, "out", "make", "wix", target.arch)
  );
  const vendorDir = join(desktopRoot, "node_modules", "electron-winstaller", "vendor");
  const candlePath = join(vendorDir, "candle.exe");
  const lightPath = join(vendorDir, "light.exe");
  const iconPath = join(desktopRoot, "resources", "icons", "wenshape.ico");
  const appName = manifest.product?.displayName || manifest.product?.name || "WenShape";
  const executableName = `${manifest.product?.executableName || "WenShape"}.exe`;

  verifyPathExists(packageDir, `packaged Electron app is missing: ${packageDir}`);
  verifyPathExists(vendorDir, `WiX tooling is missing: ${vendorDir}`);
  verifyPathExists(candlePath, `candle.exe is missing: ${candlePath}`);
  verifyPathExists(lightPath, `light.exe is missing: ${lightPath}`);
  verifyPathExists(iconPath, `Windows icon is missing: ${iconPath}`);

  const creator = new MSICreator({
    appDirectory: packageDir,
    outputDirectory: outputDir,
    exe: executableName,
    name: appName,
    description: pkg.description || "WenShape desktop shell",
    manufacturer: "WenShape Team",
    version: target.version,
    arch: windowsArchForWix(target.arch),
    icon: iconPath,
    programFilesFolderName: "WenShape",
    shortcutFolderName: "WenShape",
    shortcutName: appName,
    defaultInstallMode: "perMachine",
    upgradeCode: "AA2B94F1-3E2D-4A7B-8B64-04C5D2A4A291",
    // Note: ui is kept false because the WiX vendor from electron-winstaller
    // does not include WixUIExtension.dll.  The MSI will install silently
    // with a progress bar shown by Windows Installer.
    ui: false
  });

  creator.wixTemplate = patchWixTemplate(creator.wixTemplate);
  creator.getSpecialFiles = async function getNativeMainExecutableOnly() {
    return [];
  };
  creator.getTree = async function getFlatTree() {
    const root = this.appDirectory;
    const folderTree = arrayToTree(this.directories, root);
    return attachFilesToFlatTree(folderTree, this.files, this.specialFiles, this.registry, root, this.exeFilename);
  };

  console.log(`[desktop-msi] creating MSI sources from ${packageDir}`);
  await creator.create();

  const wixWorkingDir = dirname(creator.wxsFile);
  const wixBaseName = join(wixWorkingDir, manifest.product?.executableName || "WenShape");

  await runCommand(candlePath, ["-out", `${wixBaseName}.wixobj`, creator.wxsFile], {
    cwd: wixWorkingDir
  });
  await runCommand(lightPath, ["-out", `${wixBaseName}.msi`, `${wixBaseName}.wixobj`], {
    cwd: wixWorkingDir
  });

  const msiPath = `${wixBaseName}.msi`;
  if (!existsSync(msiPath)) {
    throw new Error(`MSI output is missing: ${msiPath}`);
  }

  console.log(`[desktop-msi] MSI ready -> ${msiPath}`);
}

main().catch((error) => {
  console.error("[desktop-msi] build failed", error);
  process.exit(1);
});
