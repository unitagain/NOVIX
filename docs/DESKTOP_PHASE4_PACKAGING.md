# Desktop Phase 4 Packaging

This document records the phase 4 packaging and delivery baseline for WenShape desktop.

## Scope

Phase 4 establishes reproducible build outputs for the Electron shell and the Python sidecar.

Delivered areas:

- Unified build scripts for frontend, sidecar, and desktop shell
- Electron Forge packaging configuration for Windows and macOS
- WiX-based MSI installer output for Windows
- Sidecar build integration based on PyInstaller
- CI workflow that produces desktop artifacts on Windows and macOS

## Build Flow

The packaging pipeline now follows this order:

1. Build `frontend/dist`
2. Sync frontend assets into `backend/static`
3. Build the Python sidecar with PyInstaller
4. Package or make the Electron desktop shell
5. Emit release metadata into the release output directory

## Main Commands

From `desktop/`:

- `npm run build:frontend`
- `npm run build:sidecar`
- `npm run build:sidecar:isolated`
- `npm run package`
- `npm run make`
- `npm run make:windows`
- `npm run make:macos:x64`
- `npm run make:macos:arm64`

## Output Layout

Intermediate build assets:

- `desktop/.artifacts/build/<version-channel>/<platform-arch>/`

Final release outputs:

- `desktop/.artifacts/releases/<version-channel>/<platform-arch>/`
- Windows MSI artifacts under `forge-out/make/wix/<arch>/`
- macOS app archives under `forge-out/make/zip` and `forge-out/make/dmg`

Release metadata:

- `desktop/.artifacts/releases/.../wenshape-release.json`

## Sidecar Packaging

The sidecar build no longer depends on a machine-specific checked-in `.spec` file.

Instead:

- the desktop build script generates a temporary PyInstaller spec using current repository paths
- frontend static assets are embedded via `backend/static`
- packaged sidecar output is normalized to a `sidecar/` directory so Electron can load it via `resources/sidecar`
- CI uses `backend/requirements.runtime.txt` to avoid pulling development-only packages into release builds
- local development can opt into an isolated sidecar build venv through `build:sidecar:isolated`

## CI Baseline

The repository now includes a first desktop release workflow:

- Windows build on `windows-latest`
- macOS build on `macos-13`
- release artifacts uploaded through GitHub Actions artifacts

This phase deliberately stops before:

- signing
- notarization
- auto-update rollout
- public release channels

Those remain phase 5 work.
