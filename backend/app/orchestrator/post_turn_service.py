"""Post-turn compaction and candidate memory extraction."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List

from app.utils.logger import get_logger

logger = get_logger(__name__)


class PostTurnService:
    def __init__(
        self,
        *,
        session_history: Any,
        archivist: Any,
        creative_memory_storage: Any,
        summarize_conversation: Callable[[str], Awaitable[Any]],
        verify_compact: Callable[[Any, List[Dict[str, Any]]], Awaitable[Dict[str, Any]]] | None = None,
    ):
        self.session_history = session_history
        self.archivist = archivist
        self.creative_memory_storage = creative_memory_storage
        self.summarize_conversation = summarize_conversation
        self.verify_compact = verify_compact

    async def compact_conversation(
        self,
        project_id: str,
        *,
        keep_recent: int = 40,
        trigger_at: int = 120,
        trigger_tokens: int = 24000,
    ) -> Dict[str, Any]:
        extracted: List[str] = []
        pending_memories: List[Dict[str, Any]] = []
        provenance: Dict[str, Any] = {}

        async def summarizer(old_messages: List[Dict[str, Any]]) -> Any:
            conversation = "\n".join(
                f"{message.get('role', 'user')}: {str(message.get('content') or '').strip()}"
                for message in old_messages
                if str(message.get("content") or "").strip()
            )
            summary = await self.summarize_conversation(conversation)
            if isinstance(summary, dict):
                provenance.update(dict(summary.pop("_provenance", {}) or {}))
            try:
                user_text = "\n".join(
                    str(message.get("content") or "").strip()
                    for message in old_messages
                    if message.get("role") == "user" and str(message.get("content") or "").strip()
                )
                if user_text:
                    memories = await self.archivist.extract_creative_memory(final_draft="", user_feedback=user_text)
                    pending_memories.extend(dict(memory) for memory in memories)
            except Exception as exc:
                logger.warning("preference extraction during compact failed: %s", exc)
            return summary

        result = await self.session_history.compact(
            project_id,
            summarizer,
            keep_recent=keep_recent,
            trigger_at=trigger_at,
            trigger_tokens=trigger_tokens,
            provenance=provenance,
            semantic_verifier=self.verify_compact,
        )
        from app.observability.runtime_metrics import runtime_metrics

        runtime_metrics.increment("compact.accepted" if result.get("compacted") else "compact.rejected")
        if result.get("compacted"):
            artifact_id = str(result.get("compact_artifact_id") or "")
            epoch = int(result.get("context_epoch") or 0)
            source_sha256 = str(result.get("source_snapshot_sha256") or "")
            version_refs = [
                {
                    "asset_type": "compact",
                    "artifact_id": artifact_id,
                    "revision": source_sha256,
                    "context_epoch": epoch,
                }
            ]
            for memory in pending_memories:
                try:
                    slug = await self.creative_memory_storage.write_candidate_memory(
                        project_id,
                        memory["slug"],
                        memory["description"],
                        memory.get("body", ""),
                        memory.get("type", "preference"),
                        source="session_compact",
                        confidence=0.6,
                        source_refs=[f"compact:{artifact_id}"],
                        version_refs=version_refs,
                    )
                    extracted.append(slug)
                except Exception as exc:
                    logger.warning("preference persistence after compact failed: %s", exc)
        result["preferences_extracted"] = extracted
        return result
