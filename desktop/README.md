# WenShape Desktop Shell

This directory contains the Electron desktop shell for WenShape.

Current baseline: phase 4.

## Responsibilities

- `config/`
  - shell manifest and release metadata inputs
- `main/`
  - Electron main-process code
- `preload/`
  - controlled renderer bridge
- `resources/`
  - icon and packaging assets
- `scripts/`
  - development, doctor, and release build scripts

## Main Commands

```bash
cd desktop
npm run doctor
npm run dev
npm run build:sidecar:isolated
npm run make:windows
npm run make:macos:x64
```

## Phase 4 Notes

Phase 4 focuses on packaging and delivery readiness:

- frontend static assets are synced into `backend/static`
- Python sidecar is built with PyInstaller
- Electron Forge consumes the packaged sidecar as an extra resource
- Windows releases are generated as WiX-based `.msi` installers via `scripts/build-windows-msi.mjs`
- release artifacts are emitted into `desktop/.artifacts/releases`

Signing, notarization, and auto-update remain phase 5 work.
