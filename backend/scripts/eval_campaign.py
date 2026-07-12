"""Run, resume and inspect real-API evaluation campaigns."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parents[1]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.eval.campaign_models import EvalCampaign
from app.eval.campaign_privacy import export_p12_cases_from_traces
from app.eval.campaign_runner import CampaignRunner
from app.eval.campaign_store import CampaignStore


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--root", default="benchmarks")
    sub = root.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Run or resume a campaign")
    run.add_argument("--config", required=True)
    status = sub.add_parser("status", help="Read campaign state")
    status.add_argument("--campaign-id", required=True)
    resolve = sub.add_parser("resolve-job", help="Resolve an uncertain running job before resume")
    resolve.add_argument("--campaign-id", required=True)
    resolve.add_argument("--job-id", required=True)
    resolve.add_argument("--resolution", choices=["charged", "not-charged", "failed"], required=True)
    export = sub.add_parser("export-p12-cases", help="Export explicit privacy-reviewed P12 cases from traces")
    export.add_argument("--output", required=True)
    export.add_argument("--trace", action="append", required=True)
    export.add_argument("--allow-content", action="store_true")
    export.add_argument("--redact-field", action="append", default=[])
    return root


async def main() -> None:
    args = parser().parse_args()
    root = Path(args.root)
    if args.command == "run":
        campaign = EvalCampaign.from_dict(json.loads(Path(args.config).read_text(encoding="utf-8-sig")))
        result = await CampaignRunner(root, campaign).run()
    elif args.command == "status":
        store = CampaignStore(root, args.campaign_id)
        result = {"state": store.load_state(), "jobs": store.jobs()}
    elif args.command == "resolve-job":
        store = CampaignStore(root, args.campaign_id)
        status = {"charged": "skipped", "not-charged": "retryable", "failed": "failed"}[args.resolution]
        store.append_jsonl(
            store.jobs_path,
            {
                "job_id": args.job_id,
                "status": status,
                "resolution": args.resolution,
                "resolved_manually": True,
            },
        )
        state = store.load_state()
        state["stop_reasons"] = [
            reason
            for reason in state.get("stop_reasons") or []
            if not str(reason).startswith(f"uncertain_job_requires_manual_resolution:{args.job_id}")
        ]
        store.save_json(store.state_path, state)
        result = {"success": True, "job_id": args.job_id, "status": status}
    else:
        result = export_p12_cases_from_traces(
            args.trace,
            output_path=Path(args.output),
            allow_content=args.allow_content,
            redact_fields=args.redact_field
            or ["content", "prompt", "messages", "candidate_text", "chapter_text", "body"],
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
