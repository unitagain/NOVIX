# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  Writer 检索工具集（Phase 3）—— 把"设定库"（角色/世界卡、动态事实、已写正文）
  封装为 LLM 可调用的工具，实现"按需取设定"的即时检索（just-in-time retrieval），
  替代"一次性塞满上下文"。
  Writer retrieval toolset: turns the canon (cards / facts / chapters / prose) into
  LLM-callable tools so the Writer can pull only what it needs, just in time.
"""

import asyncio
import json
from typing import Any, Dict, List

from app.utils.logger import get_logger

logger = get_logger(__name__)

_MAX_TOOL_RESULT_CHARS = 4000


def _truncate(text: str, limit: int = _MAX_TOOL_RESULT_CHARS) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n…(已截断)"


def _format_card(card: Any) -> str:
    """把卡片（pydantic 模型或 dict）格式化为可读文本。"""
    if card is None:
        return ""
    if hasattr(card, "model_dump"):
        try:
            data = card.model_dump(exclude_none=True)
            if isinstance(data, dict):
                return "\n".join(f"{k}: {v}" for k, v in data.items() if v)
        except Exception:
            pass
    if isinstance(card, dict):
        return "\n".join(f"{k}: {v}" for k, v in card.items() if v)
    return str(card)


def writer_tool_schemas() -> List[Dict[str, Any]]:
    """返回 OpenAI function-tool 风格的工具定义列表。"""
    return [
        {
            "type": "function",
            "function": {
                "name": "lookup_card",
                "description": "按名称精确查询某个角色卡或世界观设定卡，返回其完整设定字段。当你需要某个具体人物/地点/势力/物品的设定细节时调用。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "角色或世界观设定的名称（尽量与设定库中的名称一致）"}
                    },
                    "required": ["name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "query_canon",
                "description": "按语义查询动态事实表与设定卡，返回与查询最相关的若干条已确立事实/设定。用于核对人物关系、历史事件、伏笔、世界规则，避免与既有设定矛盾。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "要查询的内容，如『张三与李四的关系』『迷雾森林的规则』",
                        },
                        "top_k": {"type": "integer", "description": "返回条数，默认 8"},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "query_relations",
                "description": (
                    "沿关系图查询某个实体的关系网，或两个实体之间的全部关系/恩怨（含演变与章节出处）。"
                    "用于回答『张三与李四的全部恩怨』『某势力都和谁有关联』这类纯向量/词法答不准的"
                    "『连点成线』问题。本地遍历、确定性、不调用模型。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "entity": {"type": "string", "description": "主体实体名（人物/势力/地点），必填"},
                        "other": {
                            "type": "string",
                            "description": "可选的第二个实体；提供时只返回两者之间的关系",
                        },
                    },
                    "required": ["entity"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_chapter",
                "description": "读取某一章节的正文（默认返回开头与结尾片段）。用于衔接上一章结尾、回看伏笔的具体写法。",
                "parameters": {
                    "type": "object",
                    "properties": {"chapter_id": {"type": "string", "description": "章节 ID，如 V1C010"}},
                    "required": ["chapter_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_prose",
                "description": "在已写正文中按关键词检索片段。用于查找某场景/对白/描写的原文出处。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "检索关键词"},
                        "top_k": {"type": "integer", "description": "返回条数，默认 5"},
                    },
                    "required": ["query"],
                },
            },
        },
    ]


class WriterToolset:
    """把 storage_adapter + select_engine 封装为 Writer 可调用的检索工具。"""

    def __init__(
        self, project_id, storage_adapter, select_engine, *, current_chapter: str = "", total_chapters: int = 0
    ):
        self.project_id = project_id
        self.adapter = storage_adapter
        self.select_engine = select_engine
        self.current_chapter = current_chapter
        self.total_chapters = total_chapters

    @staticmethod
    def schemas() -> List[Dict[str, Any]]:
        return writer_tool_schemas()

    async def execute(self, name: str, arguments: Any) -> str:
        """根据工具名分发执行；任何异常都转为可读的工具结果文本，避免中断 agentic 循环。"""
        args = self._parse_args(arguments)
        try:
            if name == "lookup_card":
                return await self._lookup_card(str(args.get("name") or "").strip())
            if name == "query_canon":
                return await self._query_canon(str(args.get("query") or "").strip(), self._as_int(args.get("top_k"), 8))
            if name == "query_relations":
                return await self._query_relations(
                    str(args.get("entity") or "").strip(), str(args.get("other") or "").strip()
                )
            if name == "read_chapter":
                return await self._read_chapter(str(args.get("chapter_id") or "").strip())
            if name == "search_prose":
                return await self._search_prose(
                    str(args.get("query") or "").strip(), self._as_int(args.get("top_k"), 5)
                )
        except Exception as exc:
            logger.warning("Tool %s failed: %s", name, exc)
            return f"[工具 {name} 执行出错：{exc}]"
        return f"[未知工具：{name}]"

    @staticmethod
    def _parse_args(arguments: Any) -> Dict[str, Any]:
        if isinstance(arguments, dict):
            return arguments
        try:
            data = json.loads(arguments or "{}")
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _as_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    async def _lookup_card(self, name: str) -> str:
        if not name:
            return "[lookup_card 需要 name 参数]"
        card = await self.adapter.get_character_card(self.project_id, name)
        kind = "角色"
        if not card:
            card = await self.adapter.get_world_card(self.project_id, name)
            kind = "世界观"
        if not card:
            return f"未找到名为『{name}』的设定卡（可先用 query_canon 检索近似名称）。"
        return f"【{kind}设定卡：{name}】\n" + _truncate(_format_card(card))

    async def _query_canon(self, query: str, top_k: int) -> str:
        if not query:
            return "[query_canon 需要 query 参数]"
        top_k = max(1, min(top_k, 20))
        items = (
            await self.select_engine.retrieval_select(
                project_id=self.project_id,
                query=query,
                item_types=["fact", "character", "world"],
                storage=self.adapter,
                top_k=top_k,
                current_chapter=self.current_chapter,
                total_chapters=self.total_chapters,
            )
            or []
        )
        if not items:
            return f"未检索到与『{query}』相关的已确立事实/设定。"
        lines = [f"【与『{query}』相关的 canon（按相关度）】"]
        for it in items:
            tag = getattr(getattr(it, "type", None), "value", "") or ""
            lines.append(f"- [{tag}] {str(getattr(it, 'content', '')).strip()}")
        return _truncate("\n".join(lines))

    async def _query_relations(self, entity: str, other: str) -> str:
        if not entity:
            return "[query_relations 需要 entity 参数]"
        get_path = getattr(self.adapter, "get_relations_path", None)
        if get_path is None:
            return "（当前存储不支持关系图查询。）"
        try:
            from app.context_engine.relation_graph import RelationGraph

            path = get_path(self.project_id)
            # 关系图为小文件、纯本地读取；放线程池避免阻塞事件循环。
            graph = await asyncio.to_thread(RelationGraph.load, path)
        except Exception as exc:
            logger.warning("query_relations load failed: %s", exc)
            return f"[关系图加载失败：{exc}]"
        return _truncate(graph.describe(entity, other or None))

    async def _read_chapter(self, chapter_id: str) -> str:
        if not chapter_id:
            return "[read_chapter 需要 chapter_id 参数]"
        draft = getattr(self.adapter, "draft", None)
        content = None
        if draft is not None:
            try:
                content = await draft.get_final_draft(self.project_id, chapter_id)
            except Exception:
                content = None
            if content is None:
                try:
                    latest = await draft.get_latest_draft(self.project_id, chapter_id)
                    content = getattr(latest, "content", None) if latest else None
                except Exception:
                    content = None
        if not content:
            return f"章节『{chapter_id}』暂无正文。"
        text = str(content)
        if len(text) <= 3200:
            body = text
        else:
            body = text[:1600].rstrip() + "\n…(中略)…\n" + text[-1600:].lstrip()
        return f"【章节 {chapter_id} 正文（首尾片段）】\n" + body

    async def _search_prose(self, query: str, top_k: int) -> str:
        if not query:
            return "[search_prose 需要 query 参数]"
        top_k = max(1, min(top_k, 10))
        chunks = await self.adapter.search_text_chunks(self.project_id, query, limit=top_k) or []
        if not chunks:
            return f"未在已写正文中检索到与『{query}』相关的片段。"
        lines = [f"【正文检索：『{query}』】"]
        for ch in chunks:
            if not isinstance(ch, dict):
                continue
            chap = ch.get("chapter") or ""
            txt = str(ch.get("text") or "").strip()
            prefix = f"[{chap}] " if chap else ""
            lines.append(f"- {prefix}{txt}")
        return _truncate("\n".join(lines))
