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

import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List

from app.storage.base import BaseStorage
from app.utils.logger import get_logger

logger = get_logger(__name__)

_ALLOWED_ROLES = {"user", "assistant", "system"}


class SessionHistoryStorage(BaseStorage):
    """单项目一份长青对话，落 `sessions/conversation.jsonl`（每行一条消息）。"""

    def _path(self, project_id: str) -> Path:
        return self.get_project_path(project_id) / "sessions" / "conversation.jsonl"

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
        }
        mtype = message.get("type")
        if mtype:
            item["type"] = str(mtype)
        return item

    async def append(self, project_id: str, message: Dict[str, Any]) -> Dict[str, Any]:
        """追加一条消息并返回规范化后的条目（带锁，并发安全）。"""
        item = self._normalize(message)
        await self.append_jsonl(self._path(project_id), item)
        return item

    async def load(self, project_id: str, *, limit: int = 0) -> List[Dict[str, Any]]:
        """读取对话历史；limit>0 时只返回最近 limit 条。"""
        items = await self.read_jsonl(self._path(project_id))
        if limit and limit > 0:
            return items[-limit:]
        return items

    async def replace(self, project_id: str, messages: List[Dict[str, Any]]) -> None:
        """整体重写对话历史（原子，带锁）。"""
        await self.write_jsonl(self._path(project_id), [self._normalize(m) for m in messages])

    async def count(self, project_id: str) -> int:
        return len(await self.read_jsonl(self._path(project_id)))

    async def compact(
        self,
        project_id: str,
        summarizer: Callable[[List[Dict[str, Any]]], Awaitable[str]],
        *,
        keep_recent: int = 40,
        trigger_at: int = 120,
    ) -> Dict[str, Any]:
        """长对话压缩。

        消息数 ≤ max(trigger_at, keep_recent) → 不动（返回 compacted=False）。否则把"最旧的、
        非近 keep_recent 条、且非既有摘要"的消息交给 summarizer 压成一条 summary 系统消息，重写为
        ``头部既有摘要 + 新摘要 + 近 keep_recent 条``。summarizer 失败/空摘要 → 安全不动（不丢数据）。

        Args:
            summarizer: async (old_messages) -> summary_text，由调用方注入（LLM 或规则压缩）。
        """
        items = await self.read_jsonl(self._path(project_id))
        if len(items) <= max(trigger_at, keep_recent):
            return {"compacted": False, "total": len(items)}

        split = len(items) - keep_recent
        older, recent = items[:split], items[split:]
        head_summaries = [m for m in older if m.get("type") == "summary"]
        to_compress = [m for m in older if m.get("type") != "summary"]
        if not to_compress:
            return {"compacted": False, "total": len(items)}

        try:
            summary_text = str(await summarizer(to_compress) or "").strip()
        except Exception as exc:
            logger.warning("conversation compact summarizer failed: %s", exc)
            return {"compacted": False, "total": len(items), "error": str(exc)}
        if not summary_text:
            return {"compacted": False, "total": len(items)}

        summary_msg = {
            "role": "system",
            "type": "summary",
            "content": summary_text,
            "ts": int(time.time() * 1000),
            "compacted_count": len(to_compress),
        }
        new_items = head_summaries + [summary_msg] + recent
        await self.write_jsonl(self._path(project_id), new_items)
        return {
            "compacted": True,
            "before": len(items),
            "after": len(new_items),
            "summarized": len(to_compress),
        }
