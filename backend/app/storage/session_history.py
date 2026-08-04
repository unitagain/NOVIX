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
import re
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.context_engine.compact_artifact import (
    CompactArtifactV2,
    CompactVerifier,
    compact_summary_payload,
    stable_message_hash,
)
from app.context_engine.token_accounting import TokenAccounting, count_provider_payload, count_text_tokens
from app.error_contract import safe_error_code
from app.storage.base import BaseStorage
from app.storage.file_lock import get_file_lock
from app.utils.logger import get_logger

logger = get_logger(__name__)

_ALLOWED_ROLES = {"user", "assistant", "system", "tool"}


class SessionHistoryStorage(BaseStorage):
    """单项目一份长青对话，落 `sessions/conversation.jsonl`（每行一条消息）。"""

    _CONVERSATION_ID = re.compile(r"^[a-zA-Z0-9_-]{1,80}$")

    def _index_path(self, project_id: str) -> Path:
        return self.get_project_path(project_id) / "sessions" / "conversations" / "index.json"

    def _read_index_sync(self, project_id: str) -> Dict[str, Any]:
        path = self._index_path(project_id)
        if not path.exists():
            return {}
        try:
            return dict(json.loads(path.read_text(encoding="utf-8")) or {})
        except (OSError, ValueError, json.JSONDecodeError):
            return {}

    def active_conversation_id(self, project_id: str) -> str:
        value = str(self._read_index_sync(project_id).get("active_id") or "legacy")
        return value if self._CONVERSATION_ID.fullmatch(value) else "legacy"

    def _conversation_id(self, project_id: str, conversation_id: str = "") -> str:
        value = str(conversation_id or self.active_conversation_id(project_id))
        if not self._CONVERSATION_ID.fullmatch(value):
            raise ValueError("invalid_conversation_id")
        return value

    def _path(self, project_id: str, conversation_id: str = "") -> Path:
        cid = self._conversation_id(project_id, conversation_id)
        if cid == "legacy":
            return self.get_project_path(project_id) / "sessions" / "conversation.jsonl"
        return self.get_project_path(project_id) / "sessions" / "conversations" / cid / "conversation.jsonl"

    def _event_path(self, project_id: str, conversation_id: str = "") -> Path:
        cid = self._conversation_id(project_id, conversation_id)
        if cid == "legacy":
            return self.get_project_path(project_id) / "sessions" / "conversation.events.jsonl"
        return self.get_project_path(project_id) / "sessions" / "conversations" / cid / "conversation.events.jsonl"

    def _compact_dir(self, project_id: str) -> Path:
        cid = self.active_conversation_id(project_id)
        if cid == "legacy":
            return self.get_project_path(project_id) / "sessions" / "compact"
        return self.get_project_path(project_id) / "sessions" / "conversations" / cid / "compact"

    async def list_conversations(self, project_id: str) -> List[Dict[str, Any]]:
        index = self._read_index_sync(project_id)
        items = [dict(item) for item in index.get("items") or [] if isinstance(item, dict)]
        legacy_path = self.get_project_path(project_id) / "sessions" / "conversation.jsonl"
        if legacy_path.exists() and not any(item.get("id") == "legacy" for item in items):
            items.insert(0, {"id": "legacy", "title": "历史对话", "created_at": 0, "updated_at": 0})
        active_id = str(index.get("active_id") or ("legacy" if legacy_path.exists() else ""))
        return [{**item, "active": item.get("id") == active_id} for item in sorted(items, key=lambda x: int(x.get("updated_at") or 0), reverse=True)]

    async def create_conversation(self, project_id: str, *, title: str = "") -> Dict[str, Any]:
        now = int(time.time() * 1000)
        item = {
            "id": f"conv_{uuid.uuid4().hex[:16]}",
            "title": str(title or "新对话").strip()[:80] or "新对话",
            "created_at": now,
            "updated_at": now,
        }
        async with self.content_transaction(project_id):
            index = self._read_index_sync(project_id)
            items = [dict(row) for row in index.get("items") or [] if isinstance(row, dict)]
            items.append(item)
            await self._atomic_write(
                self._index_path(project_id),
                json.dumps({"active_id": item["id"], "items": items}, ensure_ascii=False, indent=2) + "\n",
            )
        return {**item, "active": True}

    async def activate_conversation(self, project_id: str, conversation_id: str) -> Dict[str, Any]:
        cid = self._conversation_id(project_id, conversation_id)
        async with self.content_transaction(project_id):
            index = self._read_index_sync(project_id)
            items = [dict(row) for row in index.get("items") or [] if isinstance(row, dict)]
            known = {str(row.get("id") or "") for row in items}
            legacy_exists = (self.get_project_path(project_id) / "sessions" / "conversation.jsonl").exists()
            if cid not in known and not (cid == "legacy" and legacy_exists):
                raise FileNotFoundError("conversation_not_found")
            await self._atomic_write(
                self._index_path(project_id),
                json.dumps({"active_id": cid, "items": items}, ensure_ascii=False, indent=2) + "\n",
            )
        return {"id": cid, "active": True}

    async def delete_conversation(self, project_id: str, conversation_id: str) -> Dict[str, Any]:
        """删除独立会话；legacy 是兼容历史数据，不允许误删。"""
        cid = self._conversation_id(project_id, conversation_id)
        if cid == "legacy":
            raise ValueError("legacy_conversation_protected")
        async with self.content_transaction(project_id):
            index = self._read_index_sync(project_id)
            items = [dict(row) for row in index.get("items") or [] if isinstance(row, dict)]
            if cid not in {str(row.get("id") or "") for row in items}:
                raise FileNotFoundError("conversation_not_found")
            path = self.get_project_path(project_id) / "sessions" / "conversations" / cid
            if path.exists():
                import shutil
                shutil.rmtree(path)
            remaining = [row for row in items if str(row.get("id") or "") != cid]
            active = str(index.get("active_id") or "")
            if active == cid:
                active = str(remaining[-1].get("id") or "") if remaining else ""
            await self._atomic_write(self._index_path(project_id), json.dumps({"active_id": active, "items": remaining}, ensure_ascii=False, indent=2) + "\n")
        return {"id": cid, "active_id": active}

    async def rollback_last_turn(self, project_id: str, conversation_id: str = "") -> Dict[str, Any]:
        cid = self._conversation_id(project_id, conversation_id)
        messages = await self.load(project_id, conversation_id=cid)
        cut = next((i for i in range(len(messages) - 1, -1, -1) if messages[i].get("role") == "user"), None)
        if cut is None:
            return {"removed": 0, "conversation_id": cid, "restored_input": ""}
        path = self._path(project_id, cid)
        event_path = self._event_path(project_id, cid)
        removed_messages = messages[cut:]
        removed_event_ids = {str(item.get("event_id") or "") for item in removed_messages if item.get("event_id")}
        async with self.content_transaction(project_id):
            kept = messages[:cut]
            await self._atomic_write(path, "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in kept))
            if removed_event_ids:
                archived = await self.read_jsonl(event_path)
                kept_events = [
                    item for item in archived if str(item.get("event_id") or "") not in removed_event_ids
                ]
                await self._atomic_write(
                    event_path,
                    "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in kept_events),
                )
        restored_input = str(removed_messages[0].get("content") or "") if removed_messages else ""
        return {
            "removed": len(removed_messages),
            "conversation_id": cid,
            "restored_input": restored_input,
        }

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
        for key in ("turn_id", "tool_call_id", "name"):
            if message.get(key):
                item[key] = str(message[key])
        if isinstance(message.get("tool_calls"), list):
            item["tool_calls"] = list(message["tool_calls"])
        return item

    async def append(self, project_id: str, message: Dict[str, Any], *, conversation_id: str = "") -> Dict[str, Any]:
        """追加一条消息并返回规范化后的条目（带锁，并发安全）。"""
        item = self._normalize(message)
        cid = self._conversation_id(project_id, conversation_id)
        async with self.content_transaction(project_id):
            await self.append_jsonl(self._event_path(project_id, cid), item)
            await self.append_jsonl(self._path(project_id, cid), item)
            if cid != "legacy":
                index = self._read_index_sync(project_id)
                items = [dict(row) for row in index.get("items") or [] if isinstance(row, dict)]
                for row in items:
                    if str(row.get("id") or "") != cid:
                        continue
                    row["updated_at"] = int(item["ts"])
                    if row.get("title") == "新对话" and item["role"] == "user" and item["content"].strip():
                        row["title"] = item["content"].strip().replace("\n", " ")[:32]
                await self._atomic_write(
                    self._index_path(project_id),
                    json.dumps({"active_id": cid, "items": items}, ensure_ascii=False, indent=2) + "\n",
                )
        return item

    async def load(self, project_id: str, *, limit: int = 0, conversation_id: str = "") -> List[Dict[str, Any]]:
        """读取对话历史；limit>0 时只返回最近 limit 条。"""
        path = self._path(project_id, conversation_id)
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

    async def count(self, project_id: str, *, conversation_id: str = "") -> int:
        return len(await self.read_jsonl(self._path(project_id, conversation_id)))

    async def compact(
        self,
        project_id: str,
        summarizer: Callable[[List[Dict[str, Any]]], Awaitable[str]],
        *,
        keep_recent: int = 40,
        trigger_at: int = 120,
        trigger_tokens: int = 24000,
        recent_token_budget: int = 8000,
        provider: str = "",
        model: str = "",
        provenance: Optional[Dict[str, Any]] = None,
        semantic_verifier: Optional[
            Callable[[CompactArtifactV2, List[Dict[str, Any]]], Awaitable[Dict[str, Any]]]
        ] = None,
    ) -> Dict[str, Any]:
        """Compact complete turns while preserving a count- and token-bounded raw tail."""

        path = self._path(project_id)
        items = await self.read_jsonl(path)
        materialized = [self._with_recovery_id(item, index) for index, item in enumerate(items)]
        previous_compact = [item for item in materialized if item.get("type") == "summary"]
        raw_messages = [item for item in materialized if item.get("type") != "summary"]
        turns = self._group_complete_turns(raw_messages)
        total_accounting = count_provider_payload(materialized, provider=provider, model=model)
        count_pressure = len(turns) > max(1, int(trigger_at))
        token_pressure = trigger_tokens > 0 and total_accounting.upper_bound_tokens > trigger_tokens
        if not count_pressure and not token_pressure:
            return {
                "compacted": False,
                "total": len(items),
                "turns": len(turns),
                "accounting": {"total": total_accounting.to_dict()},
            }

        recent_turns, tail_accounting, tail_overflow = self._select_recent_turns(
            turns,
            keep_recent=max(1, int(keep_recent)),
            token_budget=max(0, int(recent_token_budget)),
            provider=provider,
            model=model,
        )
        older_turns = turns[: len(turns) - len(recent_turns)]
        recent = [message for turn in recent_turns for message in turn["messages"]]
        source_messages = [message for turn in older_turns for message in turn["messages"]]
        if not source_messages:
            return {
                "compacted": False,
                "total": len(items),
                "turns": len(turns),
                "error": "recent_tail_consumes_all_source",
                "tail_budget_overflow": tail_overflow,
                "accounting": {
                    "total": total_accounting.to_dict(),
                    "recent_tail": tail_accounting.to_dict(),
                },
            }

        await self._ensure_event_archive(project_id, raw_messages)
        summary_inputs = [*previous_compact, *source_messages]

        try:
            summary = await summarizer(summary_inputs)
        except Exception as exc:
            logger.warning("conversation compact summarizer failed: %s", exc)
            return {
                "compacted": False,
                "total": len(items),
                "error": safe_error_code(exc),
                "accounting": {"total": total_accounting.to_dict(), "recent_tail": tail_accounting.to_dict()},
            }
        if not summary or (isinstance(summary, str) and not summary.strip()):
            return {"compacted": False, "total": len(items), "error": "compact_empty_summary"}

        effective_provider = str((provenance or {}).get("provider") or provider or "")
        effective_model = str((provenance or {}).get("model") or model or "")
        source_accounting = count_provider_payload(
            source_messages,
            provider=effective_provider,
            model=effective_model,
        )
        summary_accounting = count_text_tokens(
            compact_summary_payload(summary),
            provider=effective_provider,
            model=effective_model,
        )
        if summary_accounting.upper_bound_tokens >= source_accounting.upper_bound_tokens:
            return {
                "compacted": False,
                "total": len(items),
                "error": "compact_summary_overflow",
                "accounting": {
                    "source": source_accounting.to_dict(),
                    "summary": summary_accounting.to_dict(),
                    "recent_tail": tail_accounting.to_dict(),
                },
            }

        state = await self._read_compact_state(project_id)
        parent_epoch = int(state.get("epoch") or 0) or None
        epoch = int(parent_epoch or 0) + 1
        artifact_id = f"compact_epoch_{epoch:06d}"
        recovery_refs = [str(item["event_id"]) for item in source_messages]
        selected_turn_ids = [str(turn["turn_id"]) for turn in older_turns]
        preserved_tail_id = str(recent_turns[0]["turn_id"]) if recent_turns else ""
        parent_artifact_id = str(state.get("artifact_id") or "")
        parent_artifact = await self.read_compact_artifact(project_id, parent_artifact_id) if parent_artifact_id else None
        artifact = CompactArtifactV2.from_summary(
            artifact_id=artifact_id,
            epoch=epoch,
            parent_epoch=parent_epoch,
            summary=summary,
            source_messages=source_messages,
            recovery_refs=recovery_refs,
            parent_artifact_id=parent_artifact_id,
            provenance=provenance,
            selected_turn_ids=selected_turn_ids,
            preserved_tail_id=preserved_tail_id,
            source_accounting=source_accounting.to_dict(),
            summary_accounting=summary_accounting.to_dict(),
        )
        verification = CompactVerifier.verify(artifact, source_messages, parent_artifact=parent_artifact)
        if not verification["valid"]:
            return {
                "compacted": False,
                "total": len(items),
                "error": "compact_verification_failed",
                "verification": verification,
            }
        lineage_verification = await self._verify_compact_lineage(project_id, artifact)
        if lineage_verification.get("valid") is not True:
            return {
                "compacted": False,
                "total": len(items),
                "error": "compact_lineage_verification_failed",
                "lineage_verification": lineage_verification,
            }
        semantic_verification: Dict[str, Any] = {"available": False, "valid": True}
        if semantic_verifier is not None:
            try:
                semantic_verification = dict(await semantic_verifier(artifact, summary_inputs) or {})
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
            "compacted_count": len(source_messages),
            "compacted_turn_count": len(older_turns),
            "compact_artifact_id": artifact.id,
            "context_epoch": artifact.epoch,
            "parent_epoch": artifact.parent_epoch,
            "source_sha256": artifact.source_range["sha256"],
            "recovery_refs": artifact.recovery_refs,
            "selected_turn_ids": artifact.selected_turn_ids,
            "preserved_tail_id": artifact.preserved_tail_id,
            "source_token_estimate": artifact.source_token_estimate,
            "summary_token_estimate": artifact.summary_token_estimate,
            "accounting_estimator": artifact.accounting_estimator,
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
            "summarized": len(source_messages),
            "summarized_turns": len(older_turns),
            "preserved_tail_turns": len(recent_turns),
            "preserved_tail_id": preserved_tail_id,
            "tail_budget_overflow": tail_overflow,
            "preserved_concurrent_appends": len(appended),
            "context_epoch": epoch,
            "compact_artifact_id": artifact.id,
            "source_snapshot_sha256": artifact.source_range["sha256"],
            "verification": verification,
            "lineage_verification": lineage_verification,
            "semantic_verification": semantic_verification,
            "layers": {
                "previous_compact_messages": len(previous_compact),
                "previous_compact_artifact_id": parent_artifact_id,
                "selected_raw_messages": len(source_messages),
                "selected_raw_turns": len(older_turns),
                "recent_tail_messages": len(recent),
                "recent_tail_turns": len(recent_turns),
            },
            "accounting": {
                "total": total_accounting.to_dict(),
                "source": source_accounting.to_dict(),
                "summary": summary_accounting.to_dict(),
                "recent_tail": tail_accounting.to_dict(),
            },
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
        refs = {str(item) for item in artifact.get("recovery_refs") or []}
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

    @staticmethod
    def _group_complete_turns(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Group legacy or explicit-turn messages without splitting tool exchanges."""

        turns: List[Dict[str, Any]] = []
        current: Optional[Dict[str, Any]] = None
        for message in messages:
            explicit_turn_id = str(message.get("turn_id") or "")
            role = str(message.get("role") or "user")
            starts_turn = current is None or role == "user"
            if explicit_turn_id and current is not None and explicit_turn_id != current["turn_id"]:
                starts_turn = True
            if starts_turn:
                event_id = str(message.get("event_id") or f"legacy_{len(turns):06d}")
                current = {
                    "turn_id": explicit_turn_id or f"turn_{event_id}",
                    "messages": [],
                }
                turns.append(current)
            current["messages"].append(message)
        return turns

    @staticmethod
    def _select_recent_turns(
        turns: List[Dict[str, Any]],
        *,
        keep_recent: int,
        token_budget: int,
        provider: str,
        model: str,
    ) -> tuple[List[Dict[str, Any]], TokenAccounting, bool]:
        """Select a deterministic newest suffix constrained by turns and tokens."""

        candidates = turns[-max(1, keep_recent) :]
        selected: List[Dict[str, Any]] = []
        accounting = count_provider_payload([], provider=provider, model=model)
        for turn in reversed(candidates):
            proposed = [turn, *selected]
            proposed_messages = [message for item in proposed for message in item["messages"]]
            proposed_accounting = count_provider_payload(proposed_messages, provider=provider, model=model)
            if selected and token_budget > 0 and proposed_accounting.upper_bound_tokens > token_budget:
                break
            selected = proposed
            accounting = proposed_accounting
        overflow = bool(token_budget > 0 and accounting.upper_bound_tokens > token_budget)
        return selected, accounting, overflow

    async def _verify_compact_lineage(
        self,
        project_id: str,
        artifact: CompactArtifactV2,
    ) -> Dict[str, Any]:
        """Verify parent availability, acyclic epochs, and archived source hashes."""

        errors: List[str] = []
        seen = {artifact.id}
        parent_id = str(artifact.parent_artifact_id or "")
        expected_epoch = int(artifact.parent_epoch or 0)
        events = await self.read_jsonl(self._event_path(project_id))
        events_by_id = {str(item.get("event_id") or ""): item for item in events if item.get("event_id")}
        depth = 0
        while parent_id:
            depth += 1
            if parent_id in seen:
                errors.append("lineage_cycle")
                break
            seen.add(parent_id)
            parent = await self.read_compact_artifact(project_id, parent_id)
            if not parent:
                errors.append("parent_artifact_missing")
                break
            if int(parent.get("epoch") or 0) != expected_epoch:
                errors.append("parent_epoch_mismatch")
                break
            refs = [str(item) for item in parent.get("recovery_refs") or []]
            missing = [ref for ref in refs if ref not in events_by_id]
            if missing:
                errors.append("parent_recovery_ref_missing")
                break
            recovered = [events_by_id[ref] for ref in refs]
            if stable_message_hash(recovered) != str((parent.get("source_range") or {}).get("sha256") or ""):
                errors.append("parent_source_hash_mismatch")
                break
            parent_id = str(parent.get("parent_artifact_id") or "")
            expected_epoch = int(parent.get("parent_epoch") or 0)
        return {"valid": not errors, "errors": errors, "depth": depth, "artifacts": sorted(seen)}

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
