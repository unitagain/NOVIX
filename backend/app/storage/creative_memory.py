# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  Phase 10 · 创作 Memory 持久层（单对话脊椎）。
  仿 Claude Code memory：每条记忆是一个带 frontmatter 的 .md 文件（header=name+description+type，
  body=正文），`MEMORY.md` 是常驻索引。承载**跨会话**的「作者偏好 / 项目进度 / 关键决策」软知识，
  让对话历史可弃、真相在文件（设计红线 2）。

  与既有概念的边界（前置核查结论，避免重叠）：
  - `memory_pack`（memory_packs/{chapter}.json）：**per-章检索快照**（working_memory/evidence），写作/编辑复用。
  - `canon`（facts/relations.jsonl）：**故事客观事实**，强一致、受护栏约束。
  - 本模块 `memory/`：**跨会话创作软知识**（怎么写、作者喜欢什么、进行到哪），弱约束、JIT 召回。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.storage.base import BaseStorage
from app.storage.file_lock import get_file_lock
from app.config import get_config
from app.context_engine.memory_record import (
    MEMORY_STATUSES_V2,
    MemoryRecordV2,
    encode_string_list,
    memory_transition_allowed,
    parse_datetime,
    parse_string_list,
    parse_version_refs,
)
from app.utils.logger import get_logger
from app.utils.trust import is_untrusted_source, trust_metadata

logger = get_logger(__name__)

# 合法记忆类型 / Allowed memory types（对应 RFC MemoryRecord）。
MEMORY_TYPES = ("preference", "progress", "decision", "constraint")
MEMORY_SCOPES = ("user", "project", "volume", "chapter")
MEMORY_STATUSES = MEMORY_STATUSES_V2

_INDEX_NAME = "MEMORY.md"
_SLUG_RE = re.compile(r'[\\/:*?"<>|\s]+')


def _safe_slug(slug: str) -> str:
    """把 slug 规范为安全文件名（去路径分隔符/空白，保留中文与字母数字，限长）。"""
    s = _SLUG_RE.sub("-", str(slug or "").strip()).strip("-")
    return s[:80] or "memory"


def _one_line(text: str) -> str:
    """description 压成单行（去换行，避免破坏 frontmatter）。"""
    return " ".join(str(text or "").split())


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp_confidence(value: Any, default: float = 1.0) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = default
    return max(0.0, min(1.0, score))


def _bigrams(s: str) -> set:
    """中文 2-gram（无空格分词的轻量召回信号）。"""
    s = re.sub(r"[^一-鿿]", "", s)
    if len(s) >= 2:
        return {s[i : i + 2] for i in range(len(s) - 1)}
    return {s} if s else set()


def _recall_score(query: str, name: str, description: str) -> int:
    """轻量词法召回打分：整串子串命中 + 英文词命中 + 中文 2-gram 命中。

    Phase 10 召回求「能跨会话复用」即可；真正的语义 reranker 在 Phase 13。
    """
    hay = f"{name} {description}".lower()
    q = str(query or "").lower()
    if not q or not hay.strip():
        return 0
    score = 0
    if q in hay:
        score += 10
    tokens = set(re.findall(r"[a-z0-9]+", q)) | _bigrams(q)
    for t in tokens:
        if t and t in hay:
            score += 1
    return score


def _parse_memory(text: str) -> Dict[str, Any]:
    """解析 frontmatter + body，兼容旧记忆缺元数据的情况。"""
    meta: Dict[str, Any] = {
        "name": "",
        "description": "",
        "type": "preference",
        "scope": "project",
        "source": "legacy",
        "confidence": 1.0,
        "status": "active",
        "updated_at": "",
        "expires_at": "",
        "source_type": "internal",
        "trust_label": "trusted",
        "activation": "legacy",
        "schema_version": 1,
        "id": "",
        "source_refs": [],
        "evidence_refs": [],
        "confidence_method": "legacy",
        "valid_from": "",
        "supersedes": [],
        "conflicts_with": [],
        "created_by": "legacy",
        "confirmed_by": "",
        "created_at": "",
        "last_recalled_at": "",
        "version_refs": [],
        "body": "",
    }
    raw = str(text or "")
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            header_block, body = parts[1], parts[2]
            for line in header_block.splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    key = key.strip().lower()
                    if key in (
                        "name",
                        "description",
                        "type",
                        "scope",
                        "source",
                        "confidence",
                        "status",
                        "updated_at",
                        "expires_at",
                        "source_type",
                        "trust_label",
                        "activation",
                        "schema_version",
                        "id",
                        "source_refs",
                        "evidence_refs",
                        "confidence_method",
                        "valid_from",
                        "supersedes",
                        "conflicts_with",
                        "created_by",
                        "confirmed_by",
                        "created_at",
                        "last_recalled_at",
                        "version_refs",
                    ):
                        meta[key] = value.strip()
            meta["type"] = meta["type"] if meta["type"] in MEMORY_TYPES else "preference"
            meta["scope"] = meta["scope"] if meta["scope"] in MEMORY_SCOPES else "project"
            meta["status"] = meta["status"] if meta["status"] in MEMORY_STATUSES else "active"
            meta["confidence"] = _clamp_confidence(meta.get("confidence"), default=1.0)
            for key in ("source_refs", "evidence_refs", "supersedes", "conflicts_with"):
                meta[key] = parse_string_list(meta.get(key))
            meta["version_refs"] = parse_version_refs(meta.get("version_refs"))
            try:
                meta["schema_version"] = int(meta.get("schema_version") or 1)
            except (TypeError, ValueError):
                meta["schema_version"] = 1
            meta["body"] = body.lstrip("\n").strip()
            return meta
    meta["body"] = raw.strip()
    return meta


class CreativeMemoryStorage(BaseStorage):
    """创作记忆文件存储（memory/ 目录 + MEMORY.md 索引）。"""

    def _memory_dir(self, project_id: str) -> Path:
        return self.get_project_path(project_id) / "memory"

    def _memory_path(self, project_id: str, slug: str) -> Path:
        return self._memory_dir(project_id) / f"{_safe_slug(slug)}.md"

    def _index_path(self, project_id: str) -> Path:
        return self._memory_dir(project_id) / _INDEX_NAME

    async def write_memory(
        self,
        project_id: str,
        slug: str,
        description: str,
        body: str,
        mem_type: str = "preference",
        *,
        scope: str = "project",
        source: Optional[str] = None,
        confidence: float = 1.0,
        status: str = "active",
        expires_at: Optional[str] = None,
        updated_at: Optional[str] = None,
        source_type: Optional[str] = None,
        trust_label: Optional[str] = None,
        activation: str = "manual",
        source_refs: Optional[List[str]] = None,
        evidence_refs: Optional[List[str]] = None,
        confidence_method: str = "declared",
        valid_from: Optional[str] = None,
        supersedes: Optional[List[str]] = None,
        conflicts_with: Optional[List[str]] = None,
        created_by: str = "user",
        confirmed_by: Optional[str] = None,
        created_at: Optional[str] = None,
        last_recalled_at: Optional[str] = None,
        version_refs: Optional[List[Dict[str, Any]]] = None,
        enforce_trust_policy: bool = True,
    ) -> str:
        """写入（覆盖）一条记忆并重建索引；返回规范化后的 slug（Upsert 语义）。"""
        safe = _safe_slug(slug)
        mem_type = mem_type if mem_type in MEMORY_TYPES else "preference"
        scope = scope if scope in MEMORY_SCOPES else "project"
        trust = trust_metadata(source=source, source_type=source_type, trust_label=trust_label)
        status = status if status in MEMORY_STATUSES else "active"
        if enforce_trust_policy and trust["trust_label"] == "untrusted" and status == "active":
            status = "needs_review"
        confidence = _clamp_confidence(confidence, default=1.0)
        timestamp = _one_line(updated_at or _utc_now())
        content = (
            "---\n"
            "schema_version: 2\n"
            f"id: {safe}\n"
            f"name: {safe}\n"
            f"description: {_one_line(description)}\n"
            f"type: {mem_type}\n"
            f"scope: {scope}\n"
            f"source: {_one_line(source or 'manual')}\n"
            f"confidence: {confidence:.3f}\n"
            f"status: {status}\n"
            f"source_refs: {encode_string_list(source_refs or [])}\n"
            f"evidence_refs: {encode_string_list(evidence_refs or [])}\n"
            f"confidence_method: {_one_line(confidence_method or 'declared')}\n"
            f"valid_from: {_one_line(valid_from or '')}\n"
            f"supersedes: {encode_string_list(supersedes or [])}\n"
            f"conflicts_with: {encode_string_list(conflicts_with or [])}\n"
            f"created_by: {_one_line(created_by or 'user')}\n"
            f"confirmed_by: {_one_line(confirmed_by or '')}\n"
            f"created_at: {_one_line(created_at or timestamp)}\n"
            f"updated_at: {timestamp}\n"
            f"last_recalled_at: {_one_line(last_recalled_at or '')}\n"
            f"version_refs: {json.dumps(version_refs or [], ensure_ascii=False, separators=(',', ':'))}\n"
            f"expires_at: {_one_line(expires_at or '')}\n"
            f"source_type: {_one_line(trust['source_type'])}\n"
            f"trust_label: {_one_line(trust['trust_label'])}\n"
            f"activation: {_one_line(activation or 'manual')}\n"
            "---\n"
            f"{str(body or '').strip()}\n"
        )
        transaction_path = self._index_path(project_id)
        file_lock = get_file_lock()
        async with self.content_transaction(project_id):
            async with file_lock.lock(transaction_path):
                await self._atomic_write(self._memory_path(project_id, safe), content)
                await self._rebuild_index_unlocked(project_id)
        return safe

    async def write_candidate_memory(
        self,
        project_id: str,
        slug: str,
        description: str,
        body: str,
        mem_type: str = "preference",
        *,
        scope: str = "project",
        source: Optional[str] = None,
        confidence: float = 0.6,
        source_type: Optional[str] = None,
        trust_label: Optional[str] = None,
        source_refs: Optional[List[str]] = None,
        version_refs: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """写入 AI 推断记忆；低风险高置信可直接激活，其余待审。"""
        status, activation = self._activation_decision(
            mem_type=mem_type,
            scope=scope,
            source=source or "ai_extract",
            source_type=source_type,
            trust_label=trust_label,
            confidence=confidence,
        )
        return await self.write_memory(
            project_id,
            slug,
            description,
            body,
            mem_type,
            scope=scope,
            source=source or "ai_extract",
            confidence=confidence,
            status=status,
            source_type=source_type,
            trust_label=trust_label,
            activation=activation,
            source_refs=source_refs if source_refs is not None else ([source] if source else []),
            version_refs=version_refs,
            confidence_method="model_declared",
            created_by="archivist",
        )

    async def read_memory(self, project_id: str, slug: str) -> Optional[Dict[str, Any]]:
        """读取单条记忆（含 body）；不存在返回 None。"""
        path = self._memory_path(project_id, slug)
        if not path.exists():
            return None
        try:
            text = await self.read_text(path)
        except Exception as exc:
            logger.warning("Read memory failed (%s): %s", path, exc)
            return None
        meta = _parse_memory(text)
        if not meta.get("name"):
            meta["name"] = path.stem
        meta["slug"] = path.stem
        meta["id"] = meta.get("id") or path.stem
        return meta

    async def list_headers(self, project_id: str, statuses: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """列出所有记忆的 header（name/description/type/slug），**不含 body** —— JIT 索引层。"""
        directory = self._memory_dir(project_id)
        if not directory.exists():
            return []
        if statuses is None:
            allowed_statuses = {"active"}
        elif "*" in statuses:
            allowed_statuses = set(MEMORY_STATUSES)
        else:
            allowed_statuses = {s for s in statuses if s in MEMORY_STATUSES}
        out: List[Dict[str, str]] = []
        for path in sorted(directory.glob("*.md")):
            if path.name == _INDEX_NAME:
                continue
            try:
                text = await self.read_text(path)
            except (OSError, UnicodeError):
                continue
            meta = _parse_memory(text)
            if meta.get("status", "active") not in allowed_statuses:
                continue
            out.append(
                {
                    "name": meta.get("name") or path.stem,
                    "description": meta.get("description", ""),
                    "type": meta.get("type", "preference"),
                    "scope": meta.get("scope", "project"),
                    "source": meta.get("source", "legacy"),
                    "confidence": meta.get("confidence", 1.0),
                    "status": meta.get("status", "active"),
                    "updated_at": meta.get("updated_at", ""),
                    "expires_at": meta.get("expires_at", ""),
                    "source_type": meta.get("source_type", "internal"),
                    "trust_label": meta.get("trust_label", "trusted"),
                    "activation": meta.get("activation", "legacy"),
                    "schema_version": meta.get("schema_version", 1),
                    "id": meta.get("id") or path.stem,
                    "source_refs": parse_string_list(meta.get("source_refs")),
                    "evidence_refs": parse_string_list(meta.get("evidence_refs")),
                    "confidence_method": meta.get("confidence_method", "legacy"),
                    "valid_from": meta.get("valid_from", ""),
                    "supersedes": parse_string_list(meta.get("supersedes")),
                    "conflicts_with": parse_string_list(meta.get("conflicts_with")),
                    "created_by": meta.get("created_by", "legacy"),
                    "confirmed_by": meta.get("confirmed_by", ""),
                    "created_at": meta.get("created_at", ""),
                    "last_recalled_at": meta.get("last_recalled_at", ""),
                    "slug": path.stem,
                }
            )
        return out

    async def list_review_items(self, project_id: str) -> List[Dict[str, Any]]:
        """列出待作者确认的候选记忆（含 body，供治理 UI/API 展示）。"""
        headers = await self.list_headers(project_id, statuses=["needs_review"])
        items: List[Dict[str, Any]] = []
        for header in headers:
            full = await self.read_memory(project_id, header["slug"])
            if full:
                items.append(full)
        return items

    async def recall(
        self,
        project_id: str,
        query: str,
        top_k: int = 5,
        *,
        scopes: Optional[List[str]] = None,
        as_of: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Recall valid, trusted and conflict-free memories with explainable ranking."""
        top_k = max(1, int(top_k or 1))
        await self.expire_due_memories(project_id)
        headers = await self.list_headers(project_id, statuses=["*"])
        if not headers:
            return []
        active_ids = {str(item.get("id") or item.get("slug")) for item in headers if item.get("status") == "active"}
        conflict_participants = set()
        superseded_ids = set()
        for item in headers:
            if item.get("status") != "active":
                continue
            item_id = str(item.get("id") or item.get("slug"))
            active_conflicts = set(parse_string_list(item.get("conflicts_with"))).intersection(active_ids)
            if active_conflicts:
                conflict_participants.add(item_id)
                conflict_participants.update(active_conflicts)
            superseded_ids.update(parse_string_list(item.get("supersedes")))
        allowed_scopes = set(scopes or MEMORY_SCOPES)
        scored = []
        for header in headers:
            record = MemoryRecordV2.from_mapping(header)
            if record.scope not in allowed_scopes:
                continue
            if as_of and record.scope == "chapter" and record.valid_from and str(record.valid_from) > str(as_of):
                continue
            if record.recall_block_reasons(()) or record.id in conflict_participants or record.id in superseded_ids:
                continue
            lexical = _recall_score(query, header["name"], header["description"])
            if lexical <= 0:
                continue
            score = float(lexical) + 2.0 * record.confidence
            scored.append((score, lexical, header))
        scored.sort(key=lambda item: (item[0], str(item[2].get("updated_at") or "")), reverse=True)
        out: List[Dict[str, Any]] = []
        recalled_at = _utc_now()
        for score, lexical, header in scored[:top_k]:
            full = await self.read_memory(project_id, header["slug"])
            if full:
                full["recall_reason"] = (
                    f"eligible(status=active,scope={full.get('scope')},trusted=true,conflict=false);"
                    f"lexical={lexical};confidence={float(full.get('confidence') or 0.0):.3f}"
                )
                full["recall_score"] = round(score, 6)
                full["last_recalled_at"] = recalled_at
                out.append(full)
        return out

    async def expire_due_memories(self, project_id: str) -> List[str]:
        """Persist deterministic expiration transitions before active recall."""
        headers = await self.list_headers(project_id, statuses=["active"])
        expired = [item["slug"] for item in headers if parse_datetime(item.get("expires_at")) and MemoryRecordV2.from_mapping(item).is_expired()]
        for slug in expired:
            await self.set_memory_status(project_id, slug, "expired")
        return expired

    async def read_index(self, project_id: str) -> str:
        """读取 MEMORY.md 索引文本（常驻上下文用）；不存在返回空串。"""
        await self.expire_due_memories(project_id)
        path = self._index_path(project_id)
        if self._index_is_stale(project_id):
            await self._rebuild_index(project_id)
        if not path.exists():
            return ""
        try:
            return await self.read_text(path)
        except Exception:
            return ""

    async def delete_memory(self, project_id: str, slug: str) -> bool:
        """删除一条记忆并重建索引。"""
        path = self._memory_path(project_id, slug)
        file_lock = get_file_lock()
        async with file_lock.lock(self._index_path(project_id)):
            if path.exists():
                try:
                    path.unlink()
                except OSError:
                    return False
                await self._rebuild_index_unlocked(project_id)
                return True
        return False

    async def set_memory_status(self, project_id: str, slug: str, status: str) -> bool:
        """更新记忆状态；用于 confirm/reject/supersede，保留来源链和正文。"""
        if status not in MEMORY_STATUSES:
            return False
        existing = await self.read_memory(project_id, slug)
        if not existing:
            return False
        if not memory_transition_allowed(str(existing.get("status") or "active"), status):
            return False
        await self.write_memory(
            project_id,
            slug=existing.get("slug") or slug,
            description=existing.get("description", ""),
            body=existing.get("body", ""),
            mem_type=existing.get("type", "preference"),
            scope=existing.get("scope", "project"),
            source=existing.get("source") or "legacy",
            confidence=_clamp_confidence(existing.get("confidence"), default=1.0),
            status=status,
            expires_at=existing.get("expires_at") or None,
            source_type=existing.get("source_type") or None,
            trust_label=existing.get("trust_label") or None,
            activation=existing.get("activation") or "manual",
            source_refs=parse_string_list(existing.get("source_refs")),
            evidence_refs=parse_string_list(existing.get("evidence_refs")),
            confidence_method=existing.get("confidence_method") or "legacy",
            valid_from=existing.get("valid_from") or None,
            supersedes=parse_string_list(existing.get("supersedes")),
            conflicts_with=parse_string_list(existing.get("conflicts_with")),
            created_by=existing.get("created_by") or "legacy",
            confirmed_by=existing.get("confirmed_by") or None,
            created_at=existing.get("created_at") or None,
            last_recalled_at=existing.get("last_recalled_at") or None,
            enforce_trust_policy=False,
        )
        return True

    async def supersede_memory(self, project_id: str, old_slug: str, new_slug: str, **new_record: Any) -> str:
        """Create the replacement and retire the previous record under one project lock."""
        old = await self.read_memory(project_id, old_slug)
        if not old:
            raise ValueError("memory_not_found")
        replacement = await self.write_memory(
            project_id,
            new_slug,
            str(new_record.get("description") or old.get("description") or ""),
            str(new_record.get("body") or old.get("body") or ""),
            str(new_record.get("mem_type") or old.get("type") or "preference"),
            scope=str(new_record.get("scope") or old.get("scope") or "project"),
            source=str(new_record.get("source") or f"supersedes:{old_slug}"),
            confidence=float(new_record.get("confidence") or old.get("confidence") or 1.0),
            status="active",
            supersedes=[old_slug, *parse_string_list(new_record.get("supersedes"))],
            conflicts_with=parse_string_list(new_record.get("conflicts_with")),
            confirmed_by=str(new_record.get("confirmed_by") or "user"),
            confidence_method=str(new_record.get("confidence_method") or "confirmed"),
            enforce_trust_policy=False,
        )
        await self.set_memory_status(project_id, old_slug, "superseded")
        return replacement

    async def set_memory_conflicts(self, project_id: str, slug: str, conflicts_with: List[str]) -> bool:
        existing = await self.read_memory(project_id, slug)
        if not existing:
            return False
        await self.write_memory(
            project_id,
            slug,
            existing.get("description", ""),
            existing.get("body", ""),
            existing.get("type", "preference"),
            scope=existing.get("scope", "project"),
            source=existing.get("source", "legacy"),
            confidence=existing.get("confidence", 1.0),
            status=existing.get("status", "active"),
            expires_at=existing.get("expires_at") or None,
            source_type=existing.get("source_type") or None,
            trust_label=existing.get("trust_label") or None,
            activation=existing.get("activation") or "manual",
            source_refs=parse_string_list(existing.get("source_refs")),
            evidence_refs=parse_string_list(existing.get("evidence_refs")),
            confidence_method=existing.get("confidence_method") or "legacy",
            valid_from=existing.get("valid_from") or None,
            supersedes=parse_string_list(existing.get("supersedes")),
            conflicts_with=conflicts_with,
            created_by=existing.get("created_by") or "legacy",
            confirmed_by=existing.get("confirmed_by") or None,
            created_at=existing.get("created_at") or None,
            enforce_trust_policy=False,
        )
        return True

    async def confirm_memory(self, project_id: str, slug: str) -> bool:
        """把候选记忆确认为 active。"""
        return await self.set_memory_status(project_id, slug, "active")

    async def reject_memory(self, project_id: str, slug: str) -> bool:
        """拒绝候选记忆，保留文件以便追溯。"""
        return await self.set_memory_status(project_id, slug, "rejected")

    async def governance_metrics(self, project_id: str) -> Dict[str, Any]:
        """Return memory governance health metrics for product/ops surfaces."""
        expired_transitions = await self.expire_due_memories(project_id)
        headers = await self.list_headers(project_id, statuses=["*"])
        review_items = [item for item in headers if item.get("status") == "needs_review"]
        auto_active = [item for item in headers if item.get("activation") == "auto_active"]
        auto_rejected = [
            item
            for item in headers
            if item.get("activation") == "auto_active" and item.get("status") in {"rejected", "superseded"}
        ]
        ages = []
        now = datetime.now(timezone.utc)
        for item in review_items:
            try:
                updated = datetime.fromisoformat(str(item.get("updated_at") or ""))
            except (TypeError, ValueError):
                continue
            ages.append(max(0.0, (now - updated).total_seconds()))
        ages.sort()
        median_age = 0.0
        if ages:
            mid = len(ages) // 2
            median_age = ages[mid] if len(ages) % 2 else (ages[mid - 1] + ages[mid]) / 2
        calibration: Dict[str, Dict[str, Any]] = {}
        for item in headers:
            method = str(item.get("confidence_method") or "legacy")
            bucket = calibration.setdefault(method, {"count": 0, "active": 0, "reverted": 0, "confirmed": 0})
            bucket["count"] += 1
            bucket["active"] += int(item.get("status") == "active")
            bucket["reverted"] += int(item.get("status") in {"superseded", "expired", "rejected"})
            bucket["confirmed"] += int(bool(item.get("confirmed_by")))
        for bucket in calibration.values():
            bucket["revert_rate"] = bucket["reverted"] / bucket["count"] if bucket["count"] else 0.0
        return {
            "review_backlog": len(review_items),
            "review_median_age_seconds": median_age,
            "auto_active_count": len(auto_active),
            "auto_active_reverted": len(auto_rejected),
            "auto_active_revert_rate": (len(auto_rejected) / len(auto_active)) if auto_active else 0.0,
            "expired_transitions": len(expired_transitions),
            "confidence_calibration": calibration,
        }

    @staticmethod
    def _activation_config() -> Dict[str, Any]:
        cfg = get_config().get("governance", {}).get("memory_auto_active", {})
        return {
            "enabled": bool(cfg.get("enabled", True)),
            "min_confidence": float(cfg.get("min_confidence", 0.85)),
            "allowed_types": set(cfg.get("allowed_types", ["preference", "progress"])),
            "allowed_scopes": set(cfg.get("allowed_scopes", ["project", "chapter"])),
        }

    def _activation_decision(
        self,
        *,
        mem_type: str,
        scope: str,
        source: Optional[str],
        source_type: Optional[str],
        trust_label: Optional[str],
        confidence: float,
    ) -> tuple[str, str]:
        trust = trust_metadata(source=source, source_type=source_type, trust_label=trust_label)
        cfg = self._activation_config()
        if trust["trust_label"] == "untrusted" or is_untrusted_source(source, source_type):
            return "needs_review", "review_required"
        if not cfg["enabled"]:
            return "needs_review", "review_required"
        if mem_type not in cfg["allowed_types"] or scope not in cfg["allowed_scopes"]:
            return "needs_review", "review_required"
        if _clamp_confidence(confidence, default=0.0) < cfg["min_confidence"]:
            return "needs_review", "review_required"
        return "active", "auto_active"

    async def _rebuild_index(self, project_id: str) -> None:
        """扫描所有记忆 header，重建 MEMORY.md 索引（每条一行，符合 JIT 目录原则）。"""
        file_lock = get_file_lock()
        async with file_lock.lock(self._index_path(project_id)):
            await self._rebuild_index_unlocked(project_id)

    async def _rebuild_index_unlocked(self, project_id: str) -> None:
        """Rebuild the derived index while the transaction lock is held."""

        headers = await self.list_headers(project_id)
        lines = ["# 创作记忆索引 / Creative Memory Index", ""]
        for header in headers:
            lines.append(f"- [{header['name']}] {header['description']} ({header['type']})")
        await self._atomic_write(self._index_path(project_id), "\n".join(lines) + "\n")

    def _index_is_stale(self, project_id: str) -> bool:
        index_path = self._index_path(project_id)
        directory = self._memory_dir(project_id)
        if not directory.exists():
            return False
        if not index_path.exists():
            return any(path.name != _INDEX_NAME for path in directory.glob("*.md"))
        try:
            index_mtime = index_path.stat().st_mtime_ns
            return any(
                path.name != _INDEX_NAME and path.stat().st_mtime_ns > index_mtime
                for path in directory.glob("*.md")
            )
        except OSError:
            return True
