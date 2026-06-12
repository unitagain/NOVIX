import { existsSync, rmSync } from "node:fs";
import { join } from "node:path";

import {
  backendRoot,
  copyDirectory,
  ensureDirectory,
  frontendRoot,
  npmCommand,
  runCommand
} from "./_shared.mjs";

const frontendDistDir = join(frontendRoot, "dist");
const backendStaticDir = join(backendRoot, "static");

async function main() {
  console.log("[desktop-build] building frontend bundle");
  await runCommand(npmCommand(), ["run", "build"], {
    cwd: frontendRoot
  });

  if (!existsSync(frontendDistDir)) {
    throw new Error(`frontend dist directory was not produced: ${frontendDistDir}`);
  }

  console.log("[desktop-build] syncing frontend bundle into backend/static");
  rmSync(backendStaticDir, { recursive: true, force: true });
  ensureDirectory(backendStaticDir);
  copyDirectory(frontendDistDir, backendStaticDir);

  console.log("[desktop-build] frontend static assets are ready");
}

main().catch((error) => {
  console.error("[desktop-build] frontend build failed", error);
  process.exit(1);
});
