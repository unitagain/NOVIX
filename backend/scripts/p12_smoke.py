"""Real-provider smoke for P12 structured compaction and independent verification."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

backend_dir = Path(__file__).resolve().parents[1]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.orchestrator.orchestrator import Orchestrator


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="wenshape-p12-") as data_dir:
        orchestrator = Orchestrator(data_dir=data_dir)
        project_id = "p12-smoke"
        messages = [
            ("user", "第三章改成雨夜开场，保持冷静克制的叙述。"),
            ("assistant", "已记录雨夜开场和克制叙述。"),
            ("user", "钥匙仍在林岚手中，不得让凶手提前现身。"),
            ("assistant", "后续场景会维持钥匙状态并隐藏凶手。"),
            ("user", "码头会面的具体时间暂时不确定。"),
            ("assistant", "将其保留为未决事项。"),
            ("user", "第三章先写到林岚抵达旧仓库。"),
            ("assistant", "本轮目标已明确。"),
        ]
        for role, content in messages:
            await orchestrator.application.conversation.append(project_id, {"role": role, "content": content})

        result = await orchestrator.application.commands.run(
            project_id=project_id,
            chapter="",
            intent="compact",
            route_path="compress",
            target_word_count=512,
            operation=lambda: orchestrator.application.conversation.compact(
                project_id,
                keep_recent=2,
                trigger_at=5,
            ),
        )
        artifact_id = str(result.get("compact_artifact_id") or "")
        artifact = await orchestrator.session_history.read_compact_artifact(project_id, artifact_id)
        recovered = await orchestrator.session_history.recover_compact_sources(project_id, artifact_id)
        output = {
            "success": bool(result.get("compacted")),
            "context_epoch": result.get("context_epoch"),
            "artifact_id": artifact_id,
            "deterministic_valid": (result.get("verification") or {}).get("valid"),
            "semantic_valid": (result.get("semantic_verification") or {}).get("valid"),
            "semantic_provider": (result.get("semantic_verification") or {}).get("provider"),
            "semantic_model": (result.get("semantic_verification") or {}).get("model"),
            "semantic_reason": (result.get("semantic_verification") or {}).get("reason"),
            "structured_sections": {
                key: len((artifact or {}).get(key) or [])
                for key in ("decisions", "constraints", "entity_state", "open_loops")
            },
            "source_count": (artifact or {}).get("source_range", {}).get("count"),
            "recovered_count": len(recovered),
            "runtime": (result.get("runtime") or {}).get("state"),
            "context_plan_version": (result.get("context_plan") or {}).get("version"),
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        if not all(
            [
                output["success"],
                output["deterministic_valid"],
                output["semantic_valid"],
                output["source_count"] == output["recovered_count"],
            ]
        ):
            raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
