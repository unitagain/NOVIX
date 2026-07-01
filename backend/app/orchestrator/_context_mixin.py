# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  编排器上下文Mixin - 记忆包和写作上下文准备
  ContextMixin - Memory-pack & writer-context preparation methods.
  Extracted from orchestrator.py, injected back via Mixin inheritance.
  所有方法通过 self 访问 Orchestrator 的 storage / agent / select_engine 等属性。
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.context_engine.token_counter import count_tokens
from app.context_engine.budget_manager import create_budget_manager
from app.context_engine.trace_collector import trace_collector
from app.orchestrator.orchestrator_helpers import full_canon_eligible
from app.schemas.draft import SceneBrief
from app.utils.text import normalize_newlines
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ContextMixin:
    """
    编排器上下文Mixin - 记忆包和写作上下文准备

    Provides methods for building working-memory payloads, memory packs,
    and writer context bundles. Integrates with storage, select engine,
    and working memory service for comprehensive context assembly.
    """

    # ---------- public entry points ----------

    async def ensure_memory_pack(
        self,
        project_id: str,
        chapter: str,
        chapter_goal: Optional[str] = None,
        scene_brief: Optional[SceneBrief] = None,
        user_feedback: str = "",
        force_refresh: bool = False,
        source: str = "editor",
        chapter_text_override: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        确保为章节准备了最新的记忆包 / Ensure the latest memory pack exists for the chapter and return the full pack.

        Builds or retrieves a complete memory pack including card snapshots,
        chapter digests, and working memory payload. Handles caching and
        on-demand refresh based on force_refresh flag.

        Args:
            project_id: 项目ID / Project identifier.
            chapter: 章节ID / Chapter identifier.
            chapter_goal: 章节目标 / Chapter goal (optional).
            scene_brief: 场景简要 / Scene brief object (optional).
            user_feedback: 用户反馈 / User feedback text.
            force_refresh: 强制刷新 / Force regenerate memory pack.
            source: 来源标识 / Source identifier ('editor', 'writer', etc).
            chapter_text_override: 章节文本覆盖 / Override chapter text for digest.

        Returns:
            Full memory pack dict with all components, or None if failed.
        """
        self.current_project_id = project_id
        self.current_chapter = chapter

        resolved_scene_brief = scene_brief
        if resolved_scene_brief is None:
            try:
                resolved_scene_brief = await self.draft_storage.get_scene_brief(project_id, chapter)
            except Exception as exc:
                logger.warning("Failed to load scene_brief in ensure_memory_pack: %s", exc)
                resolved_scene_brief = None

        goal_text = self._resolve_chapter_goal(chapter_goal or "", resolved_scene_brief, user_feedback)
        if not goal_text:
            goal_text = "未提供"

        if not force_refresh:
            existing_pack = await self._load_memory_pack(project_id, chapter)
            existing_payload = self._extract_memory_pack_payload(existing_pack)
            if existing_pack and existing_payload is not None:
                if chapter_text_override is not None:
                    try:
                        existing_pack["chapter_digest"] = await self._build_chapter_digest(
                            project_id=project_id,
                            chapter=chapter,
                            chapter_text=chapter_text_override,
                            scene_brief=resolved_scene_brief,
                        )
                        await self.memory_pack_storage.write_pack(project_id, chapter, existing_pack)
                    except Exception as exc:
                        logger.warning("Memory pack digest update failed: %s", exc)
                if not existing_pack.get("card_snapshot"):
                    try:
                        existing_pack["card_snapshot"] = await self._build_card_snapshot(project_id, existing_payload)
                        await self.memory_pack_storage.write_pack(project_id, chapter, existing_pack)
                    except Exception as exc:
                        logger.warning("Memory pack snapshot enrichment failed: %s", exc)
                await self._emit_progress("使用已生成记忆包", stage="memory_pack", note=source)
                return existing_pack

        working_memory_payload = await self._build_working_memory_payload(
            project_id=project_id,
            chapter=chapter,
            chapter_goal=goal_text,
            scene_brief=resolved_scene_brief,
            user_answers=None,
        )
        if working_memory_payload is None:
            if not force_refresh:
                if chapter_text_override is not None:
                    working_memory_payload = {}
                else:
                    return None
            existing_pack = await self._load_memory_pack(project_id, chapter)
            existing_payload = self._extract_memory_pack_payload(existing_pack)
            if existing_pack and existing_payload is not None:
                await self._emit_progress("复用已有记忆包", stage="memory_pack", note="fallback")
                return existing_pack
            if chapter_text_override is None:
                return None
            working_memory_payload = {}

        digest_text = chapter_text_override
        if digest_text is None:
            try:
                draft = await self.draft_storage.get_latest_draft(project_id, chapter)
                digest_text = getattr(draft, "content", None) if draft else None
            except Exception as exc:
                logger.warning("Failed to load draft for digest: %s", exc)
                digest_text = None

        saved_pack = await self._save_memory_pack(
            project_id=project_id,
            chapter=chapter,
            chapter_goal=goal_text,
            scene_brief=resolved_scene_brief,
            working_memory_payload=working_memory_payload,
            source=source,
            chapter_text=digest_text,
        )
        if saved_pack:
            await self._emit_progress("记忆包已更新", stage="memory_pack", note=source)
        return saved_pack

    # ---------- internal helpers ----------

    def _resolve_chapter_goal(
        self, chapter_goal: str, scene_brief: Optional[SceneBrief], fallback_text: str = ""
    ) -> str:
        goal_text = str(chapter_goal or "").strip()
        if not goal_text and scene_brief is not None:
            goal_text = str(getattr(scene_brief, "goal", "") or "").strip()
        if not goal_text and fallback_text:
            goal_text = str(fallback_text or "").strip()
        if not goal_text and scene_brief is not None:
            goal_text = str(getattr(scene_brief, "summary", "") or getattr(scene_brief, "title", "") or "").strip()

        feedback = str(fallback_text or "").strip()
        if feedback:
            if not goal_text:
                goal_text = feedback
            else:
                # Editor flows pass user_feedback as fallback_text. Even when a scene brief exists,
                # we still want the latest instruction to influence retrieval/entity extraction.
                if feedback not in goal_text:
                    goal_text = f"{goal_text}\n\n用户最新指令：{feedback}"
        return goal_text

    async def _build_working_memory_payload(
        self,
        project_id: str,
        chapter: str,
        chapter_goal: str,
        scene_brief: Optional[SceneBrief],
        user_answers: Optional[List[Dict[str, Any]]] = None,
        offline: bool = False,
    ) -> Optional[Dict[str, Any]]:
        working_memory_payload = None
        try:
            working_memory_payload = await self._run_research_loop(
                project_id=project_id,
                chapter=chapter,
                chapter_goal=chapter_goal,
                scene_brief=scene_brief,
                user_answers=user_answers,
                offline=offline,
            )
        except Exception as exc:
            logger.warning("Research loop failed: %s", exc)

        if not working_memory_payload:
            try:
                from app.services.working_memory_service import working_memory_service

                working_memory_payload = await working_memory_service.prepare(
                    project_id=project_id,
                    chapter=chapter,
                    scene_brief=scene_brief,
                    chapter_goal=chapter_goal,
                    language=getattr(self, "language", "zh"),
                    user_answers=user_answers,
                    force_minimum_questions=False,
                )
            except Exception as exc:
                logger.warning("Working memory build failed: %s", exc)

        return working_memory_payload

    async def _load_memory_pack(self, project_id: str, chapter: str) -> Optional[Dict[str, Any]]:
        try:
            return await self.memory_pack_storage.read_pack(project_id, chapter)
        except Exception as exc:
            logger.warning("Memory pack read failed: %s", exc)
        return None

    def _extract_memory_pack_payload(self, pack: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not pack:
            return None
        payload = pack.get("payload") or pack.get("working_memory_payload")
        if isinstance(payload, dict):
            return payload
        return None

    async def _save_memory_pack(
        self,
        project_id: str,
        chapter: str,
        chapter_goal: str,
        scene_brief: Optional[SceneBrief],
        working_memory_payload: Optional[Dict[str, Any]],
        source: str,
        chapter_text: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if working_memory_payload is None:
            return None
        card_snapshot = await self._build_card_snapshot(project_id, working_memory_payload)
        chapter_digest = None
        if chapter_text is not None:
            try:
                chapter_digest = await self._build_chapter_digest(
                    project_id=project_id,
                    chapter=chapter,
                    chapter_text=chapter_text,
                    scene_brief=scene_brief,
                )
            except Exception as exc:
                logger.warning("Chapter digest build failed: %s", exc)
        pack = {
            "chapter": chapter,
            "built_at": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "chapter_goal": chapter_goal,
            "scene_brief": (
                {
                    "title": str(getattr(scene_brief, "title", "") or ""),
                    "goal": str(getattr(scene_brief, "goal", "") or ""),
                }
                if scene_brief is not None
                else {}
            ),
            "card_snapshot": card_snapshot,
            "chapter_digest": chapter_digest or {},
            "payload": working_memory_payload,
        }
        try:
            await self.memory_pack_storage.write_pack(project_id, chapter, pack)
            return pack
        except Exception as exc:
            logger.warning("Memory pack save failed: %s", exc)
            return None

    async def _prepare_memory_pack_payload(
        self,
        project_id: str,
        chapter: str,
        chapter_goal: str,
        scene_brief: Optional[SceneBrief],
        user_answers: Optional[List[Dict[str, Any]]] = None,
        force_refresh: bool = False,
        source: str = "writer",
    ) -> Optional[Dict[str, Any]]:
        existing_pack: Optional[Dict[str, Any]] = None
        if not force_refresh:
            existing_pack = await self._load_memory_pack(project_id, chapter)
            existing_payload = self._extract_memory_pack_payload(existing_pack)
            if existing_payload:
                await self._emit_progress("使用已生成记忆包", stage="memory_pack", note=source)
                return existing_payload

        working_memory_payload = await self._build_working_memory_payload(
            project_id=project_id,
            chapter=chapter,
            chapter_goal=chapter_goal,
            scene_brief=scene_brief,
            user_answers=user_answers,
        )

        if working_memory_payload:
            await self._save_memory_pack(
                project_id=project_id,
                chapter=chapter,
                chapter_goal=chapter_goal,
                scene_brief=scene_brief,
                working_memory_payload=working_memory_payload,
                source=source,
            )
            await self._emit_progress("记忆包已更新", stage="memory_pack", note=source)
            return working_memory_payload

        if force_refresh:
            existing_pack = await self._load_memory_pack(project_id, chapter)
            existing_payload = self._extract_memory_pack_payload(existing_pack)
            if existing_payload:
                await self._emit_progress("复用已有记忆包", stage="memory_pack", note="fallback")
                return existing_payload

        return None

    async def _build_card_snapshot(self, project_id: str, working_memory_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Build compact snapshots of relevant cards to reduce editor hallucinations."""
        evidence_items = (working_memory_payload.get("evidence_pack") or {}).get("items") or []
        seed_entities = working_memory_payload.get("seed_entities") or []

        card_names: List[str] = []
        for item in evidence_items:
            if not isinstance(item, dict):
                continue
            source = item.get("source") or {}
            card = str(source.get("card") or "").strip()
            if card and card not in card_names:
                card_names.append(card)
        for name in seed_entities:
            n = str(name or "").strip()
            if n and n not in card_names:
                card_names.append(n)
        card_names = card_names[:12]

        characters = []
        world = []
        for name in card_names:
            try:
                char_card = await self.card_storage.get_character_card(project_id, name)
            except Exception:
                char_card = None
            if char_card:
                characters.append(char_card.model_dump(mode="json"))
                continue
            try:
                world_card = await self.card_storage.get_world_card(project_id, name)
            except Exception:
                world_card = None
            if world_card:
                world.append(world_card.model_dump(mode="json"))

        style = None
        try:
            style_card = await self.card_storage.get_style_card(project_id)
            if style_card:
                style = style_card.model_dump(mode="json")
        except Exception:
            style = None

        return {"characters": characters[:8], "world": world[:8], "style": style}

    async def _build_chapter_digest(
        self,
        project_id: str,
        chapter: str,
        chapter_text: str,
        scene_brief: Optional[SceneBrief] = None,
        head_chars: int = 1200,
        tail_chars: int = 1600,
        max_names: int = 8,
        max_examples: int = 2,
        example_radius: int = 18,
    ) -> Dict[str, Any]:
        """
        Build a deterministic chapter digest from the chapter text.

        目的：为"编辑补丁模式"提供稳定的本章概览与结尾对齐片段，减少因上下文窗口导致的幻觉。
        注意：不调用 LLM，避免摘要二次污染；摘要/示例均来自原文片段。
        """
        text = normalize_newlines(str(chapter_text or ""))
        text = text.strip()
        if not text:
            return {
                "chapter": chapter,
                "summary": "",
                "head_excerpt": "",
                "tail_excerpt": "",
                "top_characters": [],
                "top_world": [],
                "built_at": datetime.now(timezone.utc).isoformat(),
            }

        head_excerpt = text[:head_chars].strip()
        tail_excerpt = text[-tail_chars:].strip() if len(text) > tail_chars else text

        summary = ""
        if scene_brief is not None:
            for key in ["summary", "goal", "title"]:
                value = str(getattr(scene_brief, key, "") or "").strip()
                if value:
                    summary = value
                    break
        if not summary:
            first_para = (text.split("\n\n", 1)[0] or "").strip()
            summary = first_para[:220].strip()

        async def _top_mentions(card_names: List[str]) -> List[str]:
            scored: List[tuple[str, int]] = []
            for name in card_names:
                n = str(name or "").strip()
                if not n or len(n) < 2:
                    continue
                count = text.count(n)
                if count <= 0:
                    continue
                scored.append((n, count))
            scored.sort(key=lambda x: x[1], reverse=True)
            return [name for name, _ in scored[:max_names]]

        character_names: List[str] = []
        world_names: List[str] = []
        try:
            character_names = await self.card_storage.list_character_cards(project_id)
        except Exception:
            character_names = []
        try:
            world_names = await self.card_storage.list_world_cards(project_id)
        except Exception:
            world_names = []

        top_characters = await _top_mentions(character_names)
        top_world = await _top_mentions(world_names)

        def _examples_for(name: str) -> List[str]:
            examples: List[str] = []
            start = 0
            hits = 0
            while hits < max_examples:
                pos = text.find(name, start)
                if pos < 0:
                    break
                left = max(0, pos - example_radius)
                right = min(len(text), pos + len(name) + example_radius)
                snippet = text[left:right].replace("\n", " ").strip()
                if snippet and snippet not in examples:
                    examples.append(snippet)
                start = pos + len(name)
                hits += 1
            return examples

        mentions = []
        for name in top_characters[:max_names]:
            mentions.append({"name": name, "count": text.count(name), "examples": _examples_for(name)})
        world_mentions = []
        for name in top_world[:max_names]:
            world_mentions.append({"name": name, "count": text.count(name), "examples": _examples_for(name)})

        return {
            "chapter": chapter,
            "summary": summary,
            "head_excerpt": head_excerpt,
            "tail_excerpt": tail_excerpt,
            "top_characters": top_characters,
            "top_world": top_world,
            "character_mentions": mentions,
            "world_mentions": world_mentions,
            "built_at": datetime.now(timezone.utc).isoformat(),
        }

    async def _prepare_writer_context(
        self,
        project_id: str,
        chapter: str,
        chapter_goal: str,
        scene_brief: Optional[SceneBrief],
        character_names: Optional[list],
        user_answers: Optional[List[Dict[str, Any]]] = None,
        force_refresh_memory_pack: bool = True,
        memory_pack_source: str = "writer",
    ) -> Dict[str, Any]:
        """Prepare context for writer and return trace info."""
        critical_items = await self.select_engine.deterministic_select(project_id, "writer", self.storage_adapter)

        query = f"{scene_brief.title} {scene_brief.goal}" if scene_brief else chapter_goal
        try:
            from app.services.chapter_binding_service import chapter_binding_service

            seeds = await chapter_binding_service.get_seed_entities(
                project_id,
                chapter,
                window=2,
                ensure_built=True,
            )
            if seeds:
                query = f"{query} {' '.join(seeds)}".strip()
        except Exception as exc:
            logger.warning("Seed entity lookup failed: %s", exc)
            seeds = []
        # 获取总章节数以动态调整候选池上限
        try:
            all_chapters = await self.draft_storage.list_chapters(project_id)
            total_chapters = len(all_chapters) if all_chapters else 0
        except Exception:
            total_chapters = 0
        dynamic_items = (
            await self.select_engine.retrieval_select(
                project_id=project_id,
                query=query,
                item_types=["character", "world", "fact", "text_chunk"],
                storage=self.storage_adapter,
                top_k=10,
                current_chapter=chapter,
                total_chapters=total_chapters,
            )
            or []
        )

        style_card = next((item.content for item in critical_items if item.type.value == "style_card"), None)

        character_cards = []
        world_cards = []
        facts = []
        text_chunks = []

        for item in dynamic_items:
            if item.type.value == "character_card":
                name = item.id.replace("char_", "")
                card = await self.card_storage.get_character_card(project_id, name)
                if card:
                    character_cards.append(card)
            elif item.type.value == "world_card":
                name = item.id.replace("world_", "")
                card = await self.card_storage.get_world_card(project_id, name)
                if card:
                    world_cards.append(card)
            elif item.type.value == "fact":
                facts.append(item.content)
            elif item.type.value == "text_chunk":
                source = item.metadata.get("source") or {}
                text_chunks.append(
                    {
                        "text": item.content,
                        "chapter": source.get("chapter"),
                        "source": source,
                    }
                )

        # 支柱 7 · 自适应上下文预算（"do the simplest thing that works"）：
        # canon 规模够小（早期/短篇）→ 整体注入全部卡片+事实，免 top-k 截断遗漏、免 agentic 导航；
        # 规模大 → 走情境检索 + agentic JIT（下方默认路径）。阈值见 config.retrieval.full_canon_max_*。
        inject_full_canon = False
        try:
            from app.config import config as app_cfg

            _rc = app_cfg.get("retrieval", {}) or {}
            _max_facts = int(_rc.get("full_canon_max_facts", 80))
            _max_cards = int(_rc.get("full_canon_max_cards", 30))
            _max_tokens = int(_rc.get("full_canon_max_tokens", 12000))
            _char_names = await self.card_storage.list_character_cards(project_id)
            _world_names = await self.card_storage.list_world_cards(project_id)
            _all_facts = await self.canon_storage.get_all_facts(project_id)
            # Phase 13：从纯数量升级为「数量 + token」双门槛——少量超长事实也不会撑爆全注入预算。
            _fact_text = " ".join(str(getattr(f, "statement", "") or "") for f in _all_facts)
            _fact_tokens = count_tokens(_fact_text)
            if full_canon_eligible(
                len(_all_facts),
                len(_char_names) + len(_world_names),
                _fact_tokens,
                max_facts=_max_facts,
                max_cards=_max_cards,
                max_tokens=_max_tokens,
            ):
                inject_full_canon = True
                character_cards = []
                for _n in _char_names:
                    _c = await self.card_storage.get_character_card(project_id, _n)
                    if _c:
                        character_cards.append(_c)
                world_cards = []
                for _n in _world_names:
                    _c = await self.card_storage.get_world_card(project_id, _n)
                    if _c:
                        world_cards.append(_c)
                facts = [str(getattr(f, "statement", "") or "") for f in _all_facts if getattr(f, "statement", None)]
        except Exception as exc:
            logger.warning("Full-canon adaptive injection skipped; fallback to retrieval: %s", exc)

        timeline = await self.canon_storage.get_all_timeline_events(project_id)
        character_states = await self.canon_storage.get_all_character_states(project_id)

        context_package = await self.draft_storage.get_context_for_writing(project_id, chapter)

        # 使用动态预算管理器替代硬编码值
        writer_model = self.gateway.get_model_for_agent("writer")
        writer_profile = self.gateway.get_profile_for_agent("writer")
        budget_manager = create_budget_manager(
            profile=writer_profile,
            model_name=writer_model,
            max_output_tokens=writer_profile.get("max_tokens", 8000) if writer_profile else 8000,
        )

        # 计算已使用的 tokens
        critical_tokens = sum(count_tokens(str(c)) for c in critical_items)
        dynamic_tokens = sum(count_tokens(str(i.content)) for i in dynamic_items)
        base_tokens = critical_tokens + dynamic_tokens

        # 从预算管理器获取分配
        allocation = budget_manager.allocate_for_agent("writer")
        # 上下文包的预算 = summaries + current_draft 的预算
        context_budget = max(0, allocation["summaries"] + allocation["current_draft"] - base_tokens)

        trimmed_context, trim_stats = self._trim_context_package(context_package, context_budget)
        if trim_stats["trimmed"]:
            try:
                await trace_collector.record_context_compress(
                    "archivist",
                    before_tokens=trim_stats["before"],
                    after_tokens=trim_stats["after"],
                    method="drop_low_priority_context",
                )
            except Exception as exc:
                logger.warning("Trace compress failed: %s", exc)
        context_package = trimmed_context

        tail_chunks = context_package.get("previous_tail_chunks") or []
        if tail_chunks:
            seen = {(item.get("chapter"), item.get("text")) for item in text_chunks if isinstance(item, dict)}
            for chunk in tail_chunks:
                if not isinstance(chunk, dict):
                    continue
                key = (chunk.get("chapter"), chunk.get("text"))
                if key in seen:
                    continue
                text_chunks.append(chunk)
                seen.add(key)

        if character_names:
            for name in character_names:
                if not any(getattr(c, "name", None) == name for c in character_cards):
                    card = await self.card_storage.get_character_card(project_id, name)
                    if card:
                        character_cards.append(card)

        writer_context = {
            "scene_brief": scene_brief,
            "chapter_goal": chapter_goal,
            "style_card": style_card,
            "character_cards": character_cards,
            "world_cards": world_cards,
            "facts": facts,
            "text_chunks": text_chunks,
            "timeline": timeline,
            "character_states": character_states,
            "context_package": context_package,
        }

        # Phase 8 · 单一上下文供给：agentic 工具循环（JIT）是默认主路径。先跑 agentic 检索；
        # 成功（真的调用了工具）则其『写作须知』即 working_memory，**跳过 research loop 的重复多轮检索**；
        # gaps 用轻量规则（build_gap_items，不检索/不 LLM）保留"不得编造"约束，questions 由调用方 generate_questions 兜底，
        # memory_pack 延后到写作完成后由 _run_writing_flow.ensure_memory_pack 重建（供 editor）。
        # agentic 不可用（provider 不支持工具 / 未触发 / inject_full_canon / 异常）才回退 research loop（旧装配，行为不变）。
        tools_used = False
        gathered = ""
        try:
            from app.agents.tools import WriterToolset

            toolset = WriterToolset(
                project_id,
                self.storage_adapter,
                self.select_engine,
                current_chapter=chapter,
                total_chapters=total_chapters,
            )

            progress_cb = getattr(self, "progress_callback", None)

            async def _on_agentic_event(ev: Dict[str, Any]) -> None:
                """把 agentic 的 thinking/tool 事件转成结构化 WS 事件（真实过程，非伪进度）。"""
                nonlocal tools_used
                etype = ev.get("type")
                if etype == "tool_call":
                    tools_used = True
                if not progress_cb:
                    return
                try:
                    if etype == "thinking":
                        await progress_cb(
                            {
                                "type": "agent_thinking",
                                "project_id": project_id,
                                "chapter": chapter,
                                "content": str(ev.get("content") or "")[:2000],
                            }
                        )
                    elif etype == "tool_call":
                        await progress_cb(
                            {
                                "type": "agent_tool_call",
                                "project_id": project_id,
                                "chapter": chapter,
                                "name": ev.get("name"),
                                "arguments": ev.get("arguments"),
                            }
                        )
                    elif etype == "tool_result":
                        await progress_cb(
                            {
                                "type": "agent_tool_result",
                                "project_id": project_id,
                                "chapter": chapter,
                                "name": ev.get("name"),
                                "result": str(ev.get("result") or "")[:800],
                            }
                        )
                except Exception:
                    pass  # 透明化事件不得影响主流程

            if not inject_full_canon:
                gathered = await self.writer.gather_context_via_tools(
                    project_id=project_id,
                    chapter=chapter,
                    chapter_goal=chapter_goal,
                    toolset=toolset,
                    on_event=_on_agentic_event,
                )
        except Exception as exc:
            logger.warning("Agentic tool retrieval failed; fallback to assembled context: %s", exc)

        if gathered and tools_used:
            # agentic 成功：单一来源，跳过 research loop 的多轮检索。
            light_gaps: List[Dict[str, Any]] = []
            try:
                from app.services.working_memory_service import working_memory_service

                light_gaps = working_memory_service.build_gap_items(
                    scene_brief, chapter_goal, language=getattr(self, "language", "zh")
                )
            except Exception as exc:
                logger.warning("Light gap build failed: %s", exc)
            writer_context["working_memory"] = gathered
            writer_context["unresolved_gaps"] = light_gaps
            # working_memory 显式 None：让 _run_writing_flow 写作后重建 memory_pack（供 editor 复用）。
            working_memory_payload = {
                "source": "agentic_jit",
                "working_memory": None,
                "evidence_pack": {},
                "gaps": [],
                "unresolved_gaps": light_gaps,
                "questions": [],
            }
        else:
            # 回退：research loop（旧装配多轮检索），行为与历史一致。
            working_memory_payload = await self._prepare_memory_pack_payload(
                project_id=project_id,
                chapter=chapter,
                chapter_goal=chapter_goal,
                scene_brief=scene_brief,
                user_answers=user_answers,
                force_refresh=force_refresh_memory_pack,
                source=memory_pack_source,
            )
            if working_memory_payload:
                writer_context["working_memory"] = working_memory_payload.get("working_memory")
                writer_context["evidence_pack"] = working_memory_payload.get("evidence_pack")
                writer_context["gaps"] = working_memory_payload.get("gaps")
                writer_context["unresolved_gaps"] = working_memory_payload.get("unresolved_gaps")

        # Phase 10：JIT 召回跨会话创作记忆（作者偏好/决策），独立通道注入（不污染 working_memory 的 compact 判定）。
        try:
            mem_query = " ".join(
                str(x or "")
                for x in [
                    chapter_goal,
                    getattr(scene_brief, "goal", "") if scene_brief else "",
                    getattr(scene_brief, "title", "") if scene_brief else "",
                ]
            ).strip()
            if mem_query and getattr(self, "creative_memory_storage", None) is not None:
                creative_memories = await self.creative_memory_storage.recall(project_id, mem_query, top_k=5)
                if creative_memories:
                    writer_context["creative_memory"] = creative_memories
        except Exception as exc:
            logger.warning("Creative memory recall failed: %s", exc)

        return {
            "writer_context": writer_context,
            "critical_items": critical_items,
            "dynamic_items": dynamic_items,
            "questions": working_memory_payload.get("questions") if working_memory_payload else [],
            "working_memory_payload": working_memory_payload,
        }
