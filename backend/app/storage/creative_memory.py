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

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.storage.base import BaseStorage
from app.utils.logger import get_logger

logger = get_logger(__name__)

# 合法记忆类型 / Allowed memory types（对应完整构想 §6）。
MEMORY_TYPES = ("preference", "progress", "decision")

_INDEX_NAME = "MEMORY.md"
_SLUG_RE = re.compile(r'[\\/:*?"<>|\s]+')


def _safe_slug(slug: str) -> str:
    """把 slug 规范为安全文件名（去路径分隔符/空白，保留中文与字母数字，限长）。"""
    s = _SLUG_RE.sub("-", str(slug or "").strip()).strip("-")
    return s[:80] or "memory"


def _one_line(text: str) -> str:
    """description 压成单行（去换行，避免破坏 frontmatter）。"""
    return " ".join(str(text or "").split())


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


def _parse_memory(text: str) -> Dict[str, str]:
    """解析 frontmatter + body：返回 {name, description, type, body}。"""
    meta = {"name": "", "description": "", "type": "preference", "body": ""}
    raw = str(text or "")
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            header_block, body = parts[1], parts[2]
            for line in header_block.splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    key = key.strip().lower()
                    if key in ("name", "description", "type"):
                        meta[key] = value.strip()
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
    ) -> str:
        """写入（覆盖）一条记忆并重建索引；返回规范化后的 slug（Upsert 语义）。"""
        safe = _safe_slug(slug)
        mem_type = mem_type if mem_type in MEMORY_TYPES else "preference"
        content = (
            "---\n"
            f"name: {safe}\n"
            f"description: {_one_line(description)}\n"
            f"type: {mem_type}\n"
            "---\n"
            f"{str(body or '').strip()}\n"
        )
        await self._atomic_write(self._memory_path(project_id, safe), content)
        await self._rebuild_index(project_id)
        return safe

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
        return meta

    async def list_headers(self, project_id: str) -> List[Dict[str, str]]:
        """列出所有记忆的 header（name/description/type/slug），**不含 body** —— JIT 索引层。"""
        directory = self._memory_dir(project_id)
        if not directory.exists():
            return []
        out: List[Dict[str, str]] = []
        for path in sorted(directory.glob("*.md")):
            if path.name == _INDEX_NAME:
                continue
            try:
                text = await self.read_text(path)
            except Exception:
                continue
            meta = _parse_memory(text)
            out.append(
                {
                    "name": meta.get("name") or path.stem,
                    "description": meta.get("description", ""),
                    "type": meta.get("type", "preference"),
                    "slug": path.stem,
                }
            )
        return out

    async def recall(self, project_id: str, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """按 query 词法匹配 header，返回 top_k 条**完整记忆**（含 body）。无命中返回 []。"""
        top_k = max(1, int(top_k or 1))
        headers = await self.list_headers(project_id)
        if not headers:
            return []
        scored = []
        for header in headers:
            score = _recall_score(query, header["name"], header["description"])
            if score > 0:
                scored.append((score, header))
        scored.sort(key=lambda item: item[0], reverse=True)
        out: List[Dict[str, Any]] = []
        for _, header in scored[:top_k]:
            full = await self.read_memory(project_id, header["slug"])
            if full:
                out.append(full)
        return out

    async def read_index(self, project_id: str) -> str:
        """读取 MEMORY.md 索引文本（常驻上下文用）；不存在返回空串。"""
        path = self._index_path(project_id)
        if not path.exists():
            return ""
        try:
            return await self.read_text(path)
        except Exception:
            return ""

    async def delete_memory(self, project_id: str, slug: str) -> bool:
        """删除一条记忆并重建索引。"""
        path = self._memory_path(project_id, slug)
        if path.exists():
            try:
                path.unlink()
            except OSError:
                return False
            await self._rebuild_index(project_id)
            return True
        return False

    async def _rebuild_index(self, project_id: str) -> None:
        """扫描所有记忆 header，重建 MEMORY.md 索引（每条一行，符合 JIT 目录原则）。"""
        headers = await self.list_headers(project_id)
        lines = ["# 创作记忆索引 / Creative Memory Index", ""]
        for header in headers:
            lines.append(f"- [{header['name']}] {header['description']} ({header['type']})")
        await self._atomic_write(self._index_path(project_id), "\n".join(lines) + "\n")
