# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  对话历史持久化（Git-Native）—— 把"单输入框对话"落到
  `data/{project_id}/sessions/conversation.jsonl`，纯文本、可 Git 追踪、扛刷新/重启/清缓存/换机
  （取代脆弱的前端 localStorage 单点存储）。一个项目一份长青对话。

  能力：append（追加一条消息）/ load（读取，可限近 N 条）/ replace（整体重写）/ count /
  compact（长对话压缩：最旧的若干轮压成一条 summary 系统消息，保留头部既有摘要 + 近 keep_recent 条）。
  compact 的 summarizer 以参数注入（LLM 或规则压缩皆可），便于单测与能力降级。

  Git-native conversation persistence: one evergreen conversation per project, stored as JSONL so it
  survives refresh/restart/cache-clear and is versionable. compact() folds the oldest turns into a
  single summary message (summarizer injected) while keeping recent turns verbatim.
"""

import json
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.context_engine.compact_artifact import CompactArtifactV2, CompactVerifier
from app.error_contract import safe_error_code
from app.storage.base import BaseStorage
from app.storage.file_lock import get_file_lock
from app.utils.logger import get_logger

logger = get_logger(__name__)

_ALLOWED_ROLES = {"user", "assistant", "system"}


class SessionHistoryStorage(BaseStorage):
    """单项目一份长青对话，落 `sessions/conversation.jsonl`（每行一条消息）。"""

    def _path(self, project_id: str) -> Path:
        return self.get_project_path(project_id) / "sessions" / "conversation.jsonl"

    def _event_path(self, project_id: str) -> Path:
        return self.get_project_path(project_id) / "sessions" / "conversation.events.jsonl"

    def _compact_dir(self, project_id: str) -> Path:
        return self.get_project_path(project_id) / "sessions" / "compact"

    def _compact_state_path(self, project_id: str) -> Path:
        return self._compact_dir(project_id) / "state.json"

    @staticmethod
    def _normalize(message: Dict[str, Any]) -> Dict[str, Any]:
        """把一条消息规范化为 {role, content, ts, type?}。role 非法→user；ts 缺省→当前毫秒。"""
        role = str(message.get("role") or "user").strip().lower()
        if role not in _ALLOWED_ROLES:
            role = "user"
        item: Dict[str, Any] = {
            "role": role,
            "content": str(message.get("content") or ""),
            "ts": int(message.get("ts") or int(time.time() * 1000)),
            "event_id": str(message.get("event_id") or f"evt_{uuid.uuid4().hex}"),
        }
        mtype = message.get("type")
        if mtype:
            item["type"] = str(mtype)
        return item

    async def append(self, project_id: str, message: Dict[str, Any]) -> Dict[str, Any]:
        """追加一条消息并返回规范化后的条目（带锁，并发安全）。"""
        item = self._normalize(message)
        async with self.content_transaction(project_id):
            await self.append_jsonl(self._event_path(project_id), item)
            await self.append_jsonl(self._path(project_id), item)
        return item

    async def load(self, project_id: str, *, limit: int = 0) -> List[Dict[str, Any]]:
        """读取对话历史；limit>0 时只返回最近 limit 条。"""
        path = self._path(project_id)
        await self._repair_projection_from_archive(project_id)
        items = await self.read_jsonl(path)
        if limit and limit > 0:
            return items[-limit:]
        return items

    async def replace(self, project_id: str, messages: List[Dict[str, Any]]) -> None:
        """整体重写对话历史（原子，带锁）。"""
        normalized = [self._normalize(m) for m in messages]
        async with self.content_transaction(project_id):
            await self.write_jsonl(self._event_path(project_id), normalized)
            await self.write_jsonl(self._path(project_id), normalized)

    async def count(self, project_id: str) -> int:
        return len(await self.read_jsonl(self._path(project_id)))

    async def compact(
        self,
        project_id: str,
        summarizer: Callable[[List[Dict[str, Any]]], Awaitable[str]],
        *,
        keep_recent: int = 40,
        trigger_at: int = 120,
        trigger_tokens: int = 24000,
        provenance: Optional[Dict[str, Any]] = None,
        semantic_verifier: Optional[
            Callable[[CompactArtifactV2, List[Dict[str, Any]]], Awaitable[Dict[str, Any]]]
        ] = None,
    ) -> Dict[str, Any]:
        """长对话压缩。

        消息数 ≤ max(trigger_at, keep_recent) → 不动（返回 compacted=False）。否则把"最旧的、
        非近 keep_recent 条、且非既有摘要"的消息交给 summarizer 压成一条 summary 系统消息，重写为
        ``头部既有摘要 + 新摘要 + 近 keep_recent 条``。summarizer 失败/空摘要 → 安全不动（不丢数据）。

        Args:
            summarizer: async (old_messages) -> summary_text，由调用方注入（LLM 或规则压缩）。
        """
        path = self._path(project_id)
        items = await self.read_jsonl(path)
        token_estimate = sum(max(1, len(str(item.get("content") or "")) // 4) for item in items)
        count_pressure = len(items) > max(trigger_at, keep_recent)
        token_pressure = trigger_tokens > 0 and token_estimate > trigger_tokens
        if not count_pressure and not token_pressure:
            return {"compacted": False, "total": len(items), "token_estimate": token_estimate}

        split = len(items) - keep_recent
        materialized = [self._with_recovery_id(item, index) for index, item in enumerate(items)]
        older, recent = materialized[:split], materialized[split:]
        to_compress = older
        if not to_compress:
            return {"compacted": False, "total": len(items)}

        source_messages = to_compress
        await self._ensure_event_archive(project_id, items)

        try:
            summary = await summarizer(to_compress)
        except Exception as exc:
            logger.warning("conversation compact summarizer failed: %s", exc)
            return {"compacted": False, "total": len(items), "error": safe_error_code(exc)}
        if not summary or (isinstance(summary, str) and not summary.strip()):
            return {"compacted": False, "total": len(items)}

        state = await self._read_compact_state(project_id)
        parent_epoch = int(state.get("epoch") or 0) or None
        epoch = int(parent_epoch or 0) + 1
        artifact_id = f"compact_epoch_{epoch:06d}"
        recovery_refs = [str(item["event_id"]) for item in source_messages]
        artifact = CompactArtifactV2.from_summary(
            artifact_id=artifact_id,
            epoch=epoch,
            parent_epoch=parent_epoch,
            summary=summary,
            source_messages=source_messages,
            recovery_refs=recovery_refs,
            parent_artifact_id=str(state.get("artifact_id") or ""),
            provenance=provenance,
        )
        verification = CompactVerifier.verify(artifact, source_messages)
        if not verification["valid"]:
            return {
                "compacted": False,
                "total": len(items),
                "error": "compact_verification_failed",
                "verification": verification,
            }
        semantic_verification: Dict[str, Any] = {"available": False, "valid": True}
        if semantic_verifier is not None:
            try:
                semantic_verification = dict(await semantic_verifier(artifact, source_messages) or {})
            except Exception as exc:
                logger.warning("conversation compact semantic verifier failed: %s", exc)
                return {
                    "compacted": False,
                    "total": len(items),
                    "error": "compact_semantic_verifier_failed",
                    "retryable": True,
                }
            if semantic_verification.get("valid") is not True:
                return {
                    "compacted": False,
                    "total": len(items),
                    "error": "compact_semantic_verification_failed",
                    "semantic_verification": semantic_verification,
                }

        summary_msg = {
            "role": "system",
            "type": "summary",
            "content": self._render_artifact(artifact),
            "ts": int(time.time() * 1000),
            "event_id": f"summary_{artifact_id}",
            "compacted_count": len(to_compress),
            "compact_artifact_id": artifact.id,
            "context_epoch": artifact.epoch,
            "parent_epoch": artifact.parent_epoch,
            "source_sha256": artifact.source_range["sha256"],
            "recovery_refs": artifact.recovery_refs,
        }
        file_lock = get_file_lock()
        async with self.content_transaction(project_id):
            async with file_lock.lock(path):
                current = await self._read_jsonl_unlocked(path)
                if current[: len(items)] != items:
                    return {
                        "compacted": False,
                        "total": len(current),
                        "error": "concurrent_history_rewrite",
                        "retryable": True,
                    }
                appended = current[len(items) :]
                new_items = [summary_msg] + recent + appended
                await self._atomic_write(
                    self._compact_dir(project_id) / f"{artifact.id}.json",
                    json.dumps(artifact.to_dict(), ensure_ascii=False, indent=2) + "\n",
                )
                await self._atomic_write(
                    self._compact_state_path(project_id),
                    json.dumps({"epoch": epoch, "artifact_id": artifact.id}, ensure_ascii=False, indent=2) + "\n",
                )
                await self._write_jsonl_unlocked(path, new_items)
        return {
            "compacted": True,
            "before": len(current),
            "after": len(new_items),
            "summarized": len(to_compress),
            "preserved_concurrent_appends": len(appended),
            "context_epoch": epoch,
            "compact_artifact_id": artifact.id,
            "source_snapshot_sha256": artifact.source_range["sha256"],
            "verification": verification,
            "semantic_verification": semantic_verification,
            "token_estimate": token_estimate,
        }

    async def read_compact_artifact(self, project_id: str, artifact_id: str) -> Optional[Dict[str, Any]]:
        path = self._compact_dir(project_id) / f"{artifact_id}.json"
        if not path.exists():
            return None
        try:
            return json.loads(await self.read_text(path))
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    async def recover_compact_sources(self, project_id: str, artifact_id: str) -> List[Dict[str, Any]]:
        artifact = await self.read_compact_artifact(project_id, artifact_id)
        if not artifact:
            return []
        refs = set(str(item) for item in artifact.get("recovery_refs") or [])
        events = await self.read_jsonl(self._event_path(project_id))
        return [item for item in events if str(item.get("event_id") or "") in refs]

    async def current_context_epoch(self, project_id: str) -> int:
        return int((await self._read_compact_state(project_id)).get("epoch") or 0)

    async def _read_compact_state(self, project_id: str) -> Dict[str, Any]:
        path = self._compact_state_path(project_id)
        if not path.exists():
            return {}
        try:
            return dict(json.loads(await self.read_text(path)) or {})
        except (OSError, ValueError, json.JSONDecodeError):
            return {}

    async def _ensure_event_archive(self, project_id: str, active_items: List[Dict[str, Any]]) -> None:
        path = self._event_path(project_id)
        archived = await self.read_jsonl(path)
        known = {str(item.get("event_id") or "") for item in archived}
        additions = [
            self._with_recovery_id(item, index)
            for index, item in enumerate(active_items)
            if str(item.get("event_id") or "") not in known
        ]
        for item in additions:
            await self.append_jsonl(path, item)

    async def _repair_projection_from_archive(self, project_id: str) -> None:
        active_path = self._path(project_id)
        event_path = self._event_path(project_id)
        if not event_path.exists():
            return
        active = await self.read_jsonl(active_path)
        archived = await self.read_jsonl(event_path)
        active_ids = {str(item.get("event_id") or "") for item in active if item.get("event_id")}
        compacted_ids = set()
        directory = self._compact_dir(project_id)
        if directory.exists():
            for artifact_path in directory.glob("compact_epoch_*.json"):
                try:
                    artifact = json.loads(await self.read_text(artifact_path))
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                compacted_ids.update(str(item) for item in artifact.get("recovery_refs") or [])
        pending = [
            item
            for item in archived
            if str(item.get("event_id") or "")
            and str(item.get("event_id")) not in active_ids
            and str(item.get("event_id")) not in compacted_ids
        ]
        if not pending:
            return
        file_lock = get_file_lock()
        async with file_lock.lock(active_path):
            current = await self._read_jsonl_unlocked(active_path)
            current_ids = {str(item.get("event_id") or "") for item in current}
            repaired = current + [item for item in pending if str(item.get("event_id") or "") not in current_ids]
            await self._write_jsonl_unlocked(active_path, repaired)

    @staticmethod
    def _with_recovery_id(item: Dict[str, Any], index: int) -> Dict[str, Any]:
        if item.get("event_id"):
            return dict(item)
        normalized = dict(item)
        normalized["event_id"] = f"legacy_{int(item.get('ts') or 0)}_{index:06d}"
        return normalized

    @staticmethod
    def _render_artifact(artifact: CompactArtifactV2) -> str:
        sections = [("摘要", [artifact.recent_summary])]
        sections.extend(
            [
                ("决策", artifact.decisions),
                ("约束", artifact.constraints),
                ("实体状态", artifact.entity_state),
                ("未决事项", artifact.open_loops),
            ]
        )
        lines: List[str] = []
        for title, values in sections:
            if values:
                lines.append(f"[{title}]")
                lines.extend(f"- {value}" for value in values)
        return "\n".join(lines).strip()
