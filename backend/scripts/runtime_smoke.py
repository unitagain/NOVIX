"""Run one production chat turn and print metadata without prose content."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parents[1]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.orchestrator import Orchestrator


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="benchmarks/runtime_smoke")
    parser.add_argument("--project-id", default="runtime_smoke")
    parser.add_argument("--chapter", default="V1C001")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--target-word-count", type=int, default=300)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    orchestrator = Orchestrator(data_dir=args.data_dir)
    result = await orchestrator.run_chat_turn(
        args.project_id,
        args.chapter,
        args.prompt,
        target_word_count=args.target_word_count,
    )
    context_plan = result.get("context_plan") or {}
    payload = {
                "success": result.get("success"),
                "action": result.get("action"),
                "fallback": result.get("fallback", False),
                "runtime_state": (result.get("runtime") or {}).get("state"),
                "route": (result.get("route_contract") or {}).get("path"),
                "plan_version": context_plan.get("version"),
                "assembly_fingerprint": result.get("assembly_fingerprint"),
                "llm_requests": (context_plan.get("actual") or {}).get("llm_requests"),
            }
    text = json.dumps(payload, ensure_ascii=False)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    asyncio.run(main())
