#!/usr/bin/env python3
"""Assemble revision-bound W8 production evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.ops.production_evidence import ProductionEvidenceBundle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, default=BACKEND_ROOT / ".runtime" / "w8")
    parser.add_argument("--output", type=Path, default=BACKEND_ROOT / ".runtime" / "w8" / "production_manifest.json")
    parser.add_argument("--runtime-smoke", type=Path)
    args = parser.parse_args()
    required_mapping = {
        "crash_matrix": "crash_matrix.json",
        "migration_matrix": "migration_matrix.json",
        "telemetry_budget": "telemetry_budget.json",
        "package_security": "package_security.json",
    }
    checks = {}
    artifacts = []
    for name, filename in required_mapping.items():
        path = args.evidence_dir / filename
        if path.is_file():
            checks[name] = json.loads(path.read_text(encoding="utf-8-sig"))
            artifacts.append(path)
        else:
            checks[name] = {"success": False, "reason": "evidence_missing"}
    soak_path = args.evidence_dir / "soak.json"
    if soak_path.is_file():
        checks["soak"] = json.loads(soak_path.read_text(encoding="utf-8-sig"))
        artifacts.append(soak_path)
    if args.runtime_smoke and args.runtime_smoke.is_file():
        runtime_smoke = json.loads(args.runtime_smoke.read_text(encoding="utf-8-sig"))
        checks["runtime_smoke"] = {
            "runtime_smoke": runtime_smoke,
            "success": bool(runtime_smoke.get("success")),
        }
        artifacts.append(args.runtime_smoke)
    for filename in ("sbom.cyclonedx.json", "package-tree.json", "package_runtime_smoke.json"):
        path = args.evidence_dir / filename
        if path.is_file() and path not in artifacts:
            artifacts.append(path)
    result = ProductionEvidenceBundle(REPO_ROOT).build(checks=checks, artifacts=artifacts, output=args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
