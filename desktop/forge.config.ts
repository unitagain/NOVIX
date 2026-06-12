import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";

const desktopRoot = resolve(__dirname);
const manifest = JSON.parse(readFileSync(join(desktopRoot, "config", "shell.manifest.json"), "utf8"));

function optionalIcon() {
  if (process.platform === "win32") {
    const iconPath = join(desktopRoot, "resources", "icons", "wenshape.ico");
    return existsSync(iconPath) ? iconPath : undefined;
  }

  if (process.platform === "darwin") {
    const iconPath = join(desktopRoot, "resources", "icons", "wenshape.icns");
    return existsSync(iconPath) ? iconPath : undefined;
  }

  return undefined;
}

function extraResources() {
  const sidecarDir = process.env.WENSHAPE_DESKTOP_SIDECAR_DIR;
  if (!sidecarDir || !existsSync(sidecarDir)) {
    return [];
  }
  return [sidecarDir];
}

const config = {
  packagerConfig: {
    executableName: manifest.product?.executableName || "WenShape",
    appBundleId: manifest.product?.bundleId || "com.wenshape.desktop",
    appCategoryType: "public.app-category.productivity",
    asar: true,
    appVersion: process.env.WENSHAPE_DESKTOP_BUILD_VERSION,
    icon: optionalIcon(),
    extraResource: extraResources(),
    protocols: [
      {
        name: "WenShape Deep Link",
        schemes: [manifest.product?.protocol || "wenshape"]
      }
    ],
    ignore: [
      /^\/node_modules\/\.cache/,
      /^\/out/,
      /^\/release/,
      /^\/\.artifacts/,
      /^\/scripts/,
      /^\/\.git/,
      /^\/tsconfig\.json$/,
      /^\/forge\.config\.ts$/,
      /^\/index\.cjs$/,
      /\.md$/
    ]
  },
  rebuildConfig: {},
  makers: [
    {
      name: "@electron-forge/maker-zip",
      platforms: ["darwin"]
    },
    {
      name: "@electron-forge/maker-dmg",
      platforms: ["darwin"],
      config: {
        name: "WenShape"
      }
    }
  ],
  plugins: []
};

export default config;
