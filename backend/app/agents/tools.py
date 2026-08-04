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
import hashlib
import json
from typing import Any, Dict, List

from app.utils.chapter_id import ChapterIDValidator
from app.utils.logger import get_logger
from app.error_contract import safe_error_code, tool_error_text

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
        except (AttributeError, TypeError, ValueError):
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
                    "沿关系图查询某个实体的关系网，或两个实体之间的全部关系/恩怨。"
                    "返回两层内容：作者设定的人物关系与双向称呼（如『A 是 B 的姐姐，B 称 A「阿姐」；A 称 B「小河」』），"
                    "以及正文中已发生的关系事实（含演变与章节出处 @VxCyyy）。"
                    "写对白前查一次即可确认该怎么称呼对方；也用于回答『张三与李四的全部恩怨』"
                    "『某势力都和谁有关联』这类纯向量/词法答不准的『连点成线』问题。"
                    "本地遍历、确定性、不调用模型。"
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
        {
            "type": "function",
            "function": {
                "name": "read_outline",
                "description": (
                    "读取全文规划大纲（作者对整部作品的结构/走向/伏笔/卷章安排）。"
                    "动笔前查阅可确保本章符合整体规划、不偏离主线、按计划铺垫或回收伏笔。"
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "edit_outline",
                "description": (
                    "修改全文规划大纲。仅在作者要求调整规划（新增卷章安排、改走向、补伏笔计划、"
                    "把已确定的设定写进大纲等）时调用；不要因为写完本章就顺手改写作者的规划。"
                    "写入立即生效并绑定 revision，与常规正文编辑同一套语义："
                    "mode=edit 精确替换唯一出现的一处；mode=append 追加到末尾；mode=replace 整体重写。"
                    "改动前建议先 read_outline 取得原文。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "mode": {
                            "type": "string",
                            "enum": ["edit", "append", "replace"],
                            "description": "edit=精确替换一处（默认）；append=末尾追加；replace=整体重写",
                        },
                        "old_text": {
                            "type": "string",
                            "description": "edit 模式必填：要被替换的大纲原文片段（须与大纲逐字一致且唯一出现）",
                        },
                        "new_text": {
                            "type": "string",
                            "description": "edit 模式必填：替换后的文本（删除该片段则传空字符串）",
                        },
                        "content": {
                            "type": "string",
                            "description": "append/replace 模式必填：要追加或整体写入的大纲文本（Markdown）",
                        },
                    },
                    "required": ["mode"],
                },
            },
        },
    ]


class WriterToolset:
    """把 storage_adapter + select_engine 封装为 Writer 可调用的检索工具。"""

    def __init__(
        self,
        project_id,
        storage_adapter,
        select_engine,
        *,
        current_chapter: str = "",
        total_chapters: int = 0,
        outline_enabled: bool = True,
    ):
        self.project_id = project_id
        self.adapter = storage_adapter
        self.select_engine = select_engine
        self.current_chapter = current_chapter
        self.total_chapters = total_chapters
        self.outline_enabled = bool(outline_enabled)

    def schemas(self) -> List[Dict[str, Any]]:
        schemas = writer_tool_schemas()
        if not self.outline_enabled:
            # 禁用大纲时 read_outline/edit_outline 都不进入工具面，AI 无从查阅或改写。
            schemas = [s for s in schemas if s.get("function", {}).get("name") not in {"read_outline", "edit_outline"}]
        return schemas

    @staticmethod
    def is_result_recoverable(name: str) -> bool:
        return name in {"lookup_card", "query_canon", "query_relations", "read_chapter", "search_prose", "read_outline"}

    async def execute(self, name: str, arguments: Any) -> str:
        """根据工具名分发执行；任何异常都转为可读的工具结果文本，避免中断 agentic 循环。"""
        args = self._parse_args(arguments)
        self._ensure_source_snapshot()
        result = f"[未知工具：{name}]"
        try:
            if name == "lookup_card":
                result = await self._lookup_card(str(args.get("name") or "").strip())
            elif name == "query_canon":
                result = await self._query_canon(
                    str(args.get("query") or "").strip(), self._as_int(args.get("top_k"), 8)
                )
            elif name == "query_relations":
                result = await self._query_relations(
                    str(args.get("entity") or "").strip(), str(args.get("other") or "").strip()
                )
            elif name == "read_chapter":
                result = await self._read_chapter(str(args.get("chapter_id") or "").strip())
            elif name == "search_prose":
                result = await self._search_prose(
                    str(args.get("query") or "").strip(), self._as_int(args.get("top_k"), 5)
                )
            elif name == "read_outline":
                result = await self._read_outline()
            elif name == "edit_outline":
                result = await self._edit_outline(args)
        except Exception as exc:
            logger.warning("Tool %s failed: %s", name, exc)
            result = tool_error_text(name, exc)
        self._register_tool_source(name, args, result)
        self._ensure_source_snapshot()
        return result

    @staticmethod
    def _ensure_source_snapshot() -> None:
        from app.context_engine.turn_scope import current_turn_scope

        scope = current_turn_scope()
        if scope is None or scope.source_registry is None:
            return
        verification = scope.source_registry.verify_mutable_sources()
        if verification.get("valid") is True:
            return
        failure = (verification.get("failures") or [{}])[0]
        raise RuntimeError(
            f"context_actual_source_revision_unavailable:{failure.get('path') or failure.get('reason')}"
        )

    @staticmethod
    def _register_tool_source(name: str, arguments: Dict[str, Any], result: str) -> None:
        from app.context_engine.turn_scope import current_turn_scope

        scope = current_turn_scope()
        if scope is None or not scope.source_closure_required:
            return
        asset_types = {
            "lookup_card": "cards",
            "query_canon": "canon",
            "query_relations": "relations",
            "read_chapter": "prose",
            "search_prose": "prose",
            "read_outline": "outline",
        }
        identity = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        identity_sha = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        scope.register_source_content(
            source_id=f"tool.{name}.{identity_sha}",
            asset_type=asset_types.get(name, "tool_result"),
            content=result,
            selection_reason=f"jit_tool:{name}",
            artifact_ref=f"writer_tool:{name}",
        )

    @staticmethod
    def _parse_args(arguments: Any) -> Dict[str, Any]:
        if isinstance(arguments, dict):
            return arguments
        try:
            data = json.loads(arguments or "{}")
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError):
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
        # 章节标题不是设定卡名称；阻止模型把“第一章/章节设定”等工作对象误路由到卡片检索。
        normalized = name.replace(" ", "").replace("　", "")
        if ("章节" in normalized or normalized.startswith("第") and "章" in normalized) and not await self.adapter.get_character_card(self.project_id, name):
            return f"『{name}』看起来是章节或写作任务，不是设定卡名称；请改用 read_chapter 或 query_canon 查询。"
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
            from app.context_engine.relation_graph import Relation, RelationGraph

            path = get_path(self.project_id)
            # 关系图为小文件、纯本地读取；放线程池避免阻塞事件循环。
            graph = await asyncio.to_thread(RelationGraph.load, path)
            relations = list(graph.relations)
            if self.current_chapter:
                relations = [
                    relation
                    for relation in relations
                    if not relation.chapter or not ChapterIDValidator.is_after(relation.chapter, self.current_chapter)
                ]
            # 合并卡片层设定边（U4）：作者手绘的人物关系与称呼没有章节出处，
            # 是「作者设定」而非「已发生事实」，因此不参与上面的未来章节过滤。
            relations.extend(await self._card_relation_edges(Relation))
            graph = RelationGraph(relations)
        except Exception as exc:
            logger.warning("query_relations load failed: %s", exc)
            return f"[relation_graph_error code={safe_error_code(exc)}]"
        return _truncate(graph.describe(entity, other or None))

    async def _card_relation_edges(self, relation_cls) -> List[Any]:
        """读取卡片层设定关系边并转为 Relation；存储不支持时返回空列表。"""
        get_edges = getattr(self.adapter, "get_card_relation_edges", None)
        if get_edges is None:
            return []
        edges = await get_edges(self.project_id) or []
        return [relation_cls.from_card_edge(edge) for edge in edges if isinstance(edge, dict)]

    async def _read_outline(self) -> str:
        if not self.outline_enabled:
            return "大纲功能当前已禁用。"
        outline = getattr(self.adapter, "outline", None)
        if outline is None:
            return "[read_outline 不可用]"
        try:
            data = await outline.get_outline(self.project_id)
        except Exception as exc:
            logger.warning("read_outline load failed: %s", exc)
            return f"[outline_error code={safe_error_code(exc)}]"
        content = str(data.get("content") or "").strip()
        if not content:
            return "大纲暂为空白。可在资源管理器顶部的「大纲」中规划全文结构、走向与伏笔。"
        return _truncate(f"【全文规划大纲】\n{content}", 6000)

    async def _edit_outline(self, args: Dict[str, Any]) -> str:
        """修改大纲（作者规划资产）。写入即落盘，语义与正文编辑工具一致。

        大纲仍然**不进入事实提取**：本工具只改写作者的规划文本，不产生 Canon/Summary 事实。
        并发以 ``expected_revision`` 乐观控制：读到的 revision 与写入时不一致即冲突，
        交回模型重读，绝不覆盖作者在本轮期间的手工改动。
        """
        from app.control_plane.store import RevisionConflict
        from app.utils.permissions import PermissionLevel, decide_permission

        if not self.outline_enabled:
            return "大纲功能当前已禁用，无法修改。"
        outline = getattr(self.adapter, "outline", None)
        if outline is None:
            return "[edit_outline 不可用]"
        mode = str(args.get("mode") or "edit").strip().lower()
        if mode not in {"edit", "append", "replace"}:
            return f"[edit_outline 的 mode 无效：{mode}（可选 edit / append / replace）]"

        data = await outline.get_outline(self.project_id)
        current = str(data.get("content") or "")
        revision = int(data.get("revision") or 0)

        if mode == "edit":
            old_text = str(args.get("old_text") or "")
            new_text = str(args.get("new_text") or "")
            if not old_text:
                return "[edit_outline 的 edit 模式需要 old_text]"
            occurrences = current.count(old_text)
            if occurrences == 0:
                return "未找到要替换的大纲片段：old_text 未在大纲中出现。请先 read_outline 逐字核对。"
            if occurrences > 1:
                return (
                    f"old_text 在大纲中出现 {occurrences} 次、不唯一，无法安全定位。"
                    "请提供更长、包含上下文的唯一片段后重试。"
                )
            updated = current.replace(old_text, new_text, 1)
        elif mode == "append":
            addition = str(args.get("content") or "").strip()
            if not addition:
                return "[edit_outline 的 append 模式需要非空 content]"
            updated = f"{current.rstrip()}\n\n{addition}" if current.strip() else addition
        else:
            replacement = str(args.get("content") or "")
            if not replacement.strip():
                return "[edit_outline 的 replace 模式需要非空 content]"
            updated = replacement

        if updated == current:
            return "大纲内容未发生变化，未写入。"

        # 副作用在执行点消费权限决策：策略若被收紧为 ask/deny，这里直接拒绝而不是静默写入。
        decision = decide_permission(
            "edit_outline",
            resource_scope={"project_id": self.project_id, "asset": "outline"},
            payload={"mode": mode, "chars": len(updated)},
        )
        if decision.level is not PermissionLevel.ALLOW:
            return f"[permission_{decision.level.value}] 大纲写入未获许可，本次未修改。"

        try:
            saved = await outline.save_outline(self.project_id, updated, expected_revision=revision)
        except RevisionConflict:
            return "[outline_revision_conflict] 大纲在本轮期间已被改动，请重新 read_outline 后再修改。"
        return (
            f"已更新大纲（mode={mode}，当前 {int(saved.get('word_count') or 0)} 字，"
            f"revision={int(saved.get('revision') or 0)}）。"
        )

    async def _read_chapter(self, chapter_id: str) -> str:
        if not chapter_id:
            return "[read_chapter 需要 chapter_id 参数]"
        if self.current_chapter and ChapterIDValidator.is_after(chapter_id, self.current_chapter):
            return f"章节『{chapter_id}』位于当前写作章节『{self.current_chapter}』之后，已按时间边界拒绝读取。"
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
        fetch_limit = min(50, max(top_k, top_k * 4))
        chunks = await self.adapter.search_text_chunks(self.project_id, query, limit=fetch_limit) or []
        if self.current_chapter:
            chunks = [
                chunk
                for chunk in chunks
                if not isinstance(chunk, dict)
                or not chunk.get("chapter")
                or not ChapterIDValidator.is_after(str(chunk.get("chapter") or ""), self.current_chapter)
            ]
        chunks = chunks[:top_k]
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
