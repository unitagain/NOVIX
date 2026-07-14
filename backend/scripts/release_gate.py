"""Run the complete WenShape backend release gate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parents[1]
repo_root = backend_dir.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.ops.release_gate import ReleaseGate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-manifest", action="append", default=[])
    parser.add_argument("--production-manifest", action="append", default=[])
    parser.add_argument("--require-global-campaign", action="store_true")
    parser.add_argument("--engineering-only", action="store_true")
    parser.add_argument(
        "--skip-campaign-evidence",
        action="store_true",
        help="Skip optional model-quality campaign evidence for the non-commercial release profile",
    )
    parser.add_argument("--allow-dirty-worktree", action="store_true")
    parser.add_argument("--max-evidence-age-days", type=float, default=30.0)
    parser.add_argument("--output", default="backend/release_gate_report.json")
    args = parser.parse_args()
    result = ReleaseGate(repo_root).run(
        campaign_manifests=args.campaign_manifest,
        production_manifests=args.production_manifest,
        require_global_campaign=args.require_global_campaign,
        require_campaign_evidence=not (args.engineering_only or args.skip_campaign_evidence),
        require_production_evidence=not args.engineering_only,
        require_clean_worktree=not args.allow_dirty_worktree,
        max_evidence_age_seconds=max(1.0, args.max_evidence_age_days * 86400.0),
        output=repo_root / args.output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["success"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
