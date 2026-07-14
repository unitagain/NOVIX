# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  摘要和事实表 Mixin - 从档案员智能体中提取的章节/分卷摘要生成、事实表更新和焦点角色绑定方法。
  Summary & canon mixin - Methods for chapter/volume summary generation, canon updates extraction, and focus character binding.
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import yaml

from app.prompts import (
    archivist_canon_updates_prompt,
    archivist_chapter_summary_prompt,
    archivist_focus_characters_binding_prompt,
    archivist_volume_summary_prompt,
)
from app.schemas.canon import Fact, TimelineEvent, CharacterState
from app.schemas.draft import ChapterSummary
from app.schemas.volume import VolumeSummary
from app.context_engine.turn_scope import register_current_provider_payload
from app.utils.logger import get_logger

logger = get_logger(__name__)


class SummaryMixin:
    """
    摘要和事实表 Mixin。

    Methods for chapter/volume summary generation, canon updates, and focus character binding.
    Handles structured extraction of facts, timelines, character states, and narrative summaries.
    """

    async def generate_chapter_summary(
        self,
        project_id: str,
        chapter: str,
        chapter_title: str,
        final_draft: str,
    ) -> ChapterSummary:
        """Generate a structured chapter summary with bounded retries."""
        max_attempts = max(1, int(getattr(self, "CHAPTER_SUMMARY_MAX_ATTEMPTS", 3)))
        retry_hint: Optional[str] = None

        for attempt in range(1, max_attempts + 1):
            try:
                yaml_content = await self._generate_chapter_summary_yaml(
                    chapter=chapter,
                    chapter_title=chapter_title,
                    final_draft=final_draft,
                    retry_hint=retry_hint,
                )
            except Exception as exc:
                logger.warning(
                    "Chapter summary generation failed before parse (chapter=%s, attempt=%s/%s): %s",
                    chapter,
                    attempt,
                    max_attempts,
                    exc,
                )
                if attempt < max_attempts:
                    retry_hint = self._build_summary_retry_hint(str(exc))
                    await asyncio.sleep(min(0.4 * attempt, 1.2))
                    continue
                return self._fallback_chapter_summary(chapter, chapter_title, final_draft)

            summary = self._try_parse_chapter_summary(yaml_content, chapter, chapter_title, final_draft)
            if summary and not self._is_likely_raw_excerpt(summary.brief_summary, final_draft):
                return summary

            reason = "summary resembles leading raw draft excerpt" if summary else "invalid YAML/schema"
            logger.warning(
                "Chapter summary parse/quality check failed (chapter=%s, attempt=%s/%s, reason=%s).",
                chapter,
                attempt,
                max_attempts,
                reason,
            )
            if attempt < max_attempts:
                retry_hint = self._build_summary_retry_hint(reason)
                await asyncio.sleep(min(0.4 * attempt, 1.2))
                continue

        return self._fallback_chapter_summary(chapter, chapter_title, final_draft)

    async def generate_volume_summary(
        self,
        project_id: str,
        volume_id: str,
        chapter_summaries: List[ChapterSummary],
    ) -> VolumeSummary:
        """Generate or refresh a volume summary."""
        chapter_count = len(chapter_summaries)

        if chapter_count == 0:
            return self._fallback_volume_summary(volume_id, chapter_summaries)

        yaml_content = await self._generate_volume_summary_yaml(volume_id, chapter_summaries)
        return self._parse_volume_summary(yaml_content, volume_id, chapter_summaries)

    async def extract_canon_updates(self, project_id: str, chapter: str, final_draft: str) -> Dict[str, Any]:
        """Extract canon updates from the final draft."""
        try:
            yaml_content = await self._generate_canon_updates_yaml(chapter=chapter, final_draft=final_draft)
            return await self._parse_canon_updates_yaml(
                project_id=project_id,
                chapter=chapter,
                yaml_content=yaml_content,
            )
        except Exception:
            return {"facts": [], "timeline_events": [], "character_states": [], "relations": []}

    async def review_consistency(
        self, project_id: str, chapter: str, draft: str, *, max_facts: int = 20
    ) -> Dict[str, Any]:
        """按需一致性评审（Phase 6 · Evaluator-optimizer 的评审端，只报告不改写）。

        对照已确立 canon（事实 + 确定性关系护栏报警）审阅本章草稿，给出结构化问题清单
        （矛盾/性格越界/时间线/伏笔遗漏）。**按需触发**（用户点"查一致性/润色"或确定性护栏报警后），
        不进入默认生成路径，故不徒增成本。失败安全返回（不抛）。
        """
        draft = str(draft or "").strip()
        if not draft:
            return {"issues": [], "alerts": []}

        # ① 便宜确定性报警（无 LLM，常驻）
        try:
            alerts = await self.canon_storage.detect_relation_inconsistencies(project_id)
        except Exception:
            alerts = []

        # ② 贵 LLM 评审（仅按需），喂入有界 canon 事实 + 确定性报警
        try:
            facts = await self.canon_storage.get_eligible_facts(project_id)
        except Exception:
            facts = []
        fact_lines = [
            f"- {str(getattr(f, 'statement', '') or '').strip()}"
            for f in (facts or [])[-max_facts:]
            if str(getattr(f, "statement", "") or "").strip()
        ]
        canon_block = "\n".join(fact_lines) or "（暂无已确立事实）"
        alert_block = "\n".join(f"- {a}" for a in alerts) or "（无确定性报警）"
        system = (
            "你是小说一致性评审员。对照已确立设定(canon)审阅本章草稿，找出：与设定矛盾、"
            "人物性格/能力越界、时间线冲突、伏笔遗漏。只报告问题、不改写正文。"
            '只输出 JSON：{"issues":[{"type":"consistency|character|timeline|foreshadow",'
            '"severity":"high|medium|low","excerpt":"原文片段","detail":"问题说明","suggestion":"修改建议"}]}'
        )
        user = (
            f"【已确立设定】\n{canon_block}\n\n"
            f"【确定性护栏报警】\n{alert_block}\n\n"
            f"【本章草稿】\n{draft[:8000]}\n\n请只输出 JSON。"
        )
        issues: List[Dict[str, Any]] = []
        try:
            from app.agents.intent import _extract_json

            provider = self.gateway.get_provider_for_agent(self.get_agent_name())
            messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
            register_current_provider_payload(messages, owner="archivist.consistency_review")
            resp = await self.gateway.chat(
                messages,
                provider=provider,
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            data = _extract_json(resp.get("content") or "") or {}
            raw_issues = data.get("issues") if isinstance(data, dict) else None
            if isinstance(raw_issues, list):
                issues = [it for it in raw_issues if isinstance(it, dict)]
        except Exception as exc:
            logger.warning("Consistency review failed: %s", exc)
            issues = []

        return {"issues": issues, "alerts": alerts}

    async def bind_focus_characters(
        self,
        project_id: str,
        chapter: str,
        final_draft: str,
        limit: int = 5,
        max_candidates: int = 160,
    ) -> List[str]:
        """
        Bind focus characters for a chapter via LLM during sync.

        输出的是"重点角色（focus）"，用于后续检索 seeds 与 UI 展示。
        强约束：不允许隐式主角，必须在正文中出现姓名或别名才可绑定。
        """
        cleaned_text = str(final_draft or "")
        if not cleaned_text.strip():
            return []

        catalog = await self._build_focus_character_catalog(project_id, cleaned_text)
        if not catalog:
            return []

        prompt_candidates = catalog[:max_candidates]
        prompt = archivist_focus_characters_binding_prompt(
            chapter=chapter,
            candidates=prompt_candidates,
            final_draft=cleaned_text,
            limit=limit,
            language=self.language,
        )
        messages = self.build_messages(
            system_prompt=prompt.system,
            user_prompt=prompt.user,
            context_items=None,
        )
        response = await self.call_llm(messages)

        focus_from_llm = self._parse_focus_characters_yaml(response)
        focus_candidates = {item["name"]: item for item in prompt_candidates}
        focus_names = self._filter_explicit_mentions(
            cleaned_text,
            [name for name in focus_from_llm if name in focus_candidates],
            focus_candidates,
        )

        must_include = self._select_starred_mentions(catalog, cleaned_text, min_stars=3)
        selected: List[str] = []
        for name in must_include:
            if name not in selected:
                selected.append(name)
            if len(selected) >= limit:
                return selected[:limit]

        for name in focus_names:
            if name not in selected:
                selected.append(name)
            if len(selected) >= limit:
                return selected[:limit]

        # Fallback: explicit mentions sorted by (stars desc, mention_count desc)
        mentioned = [item for item in catalog if item.get("mention_count", 0) > 0]
        mentioned.sort(
            key=lambda x: (-int(x.get("stars") or 1), -int(x.get("mention_count") or 0), x.get("name") or "")
        )
        for item in mentioned:
            name = item.get("name") or ""
            if not name or name in selected:
                continue
            selected.append(name)
            if len(selected) >= limit:
                break

        return selected[:limit]

    async def _build_focus_character_catalog(self, project_id: str, text: str) -> List[Dict[str, Any]]:
        names = await self.card_storage.list_character_cards(project_id)
        catalog: List[Dict[str, Any]] = []
        if not names:
            return catalog

        for raw_name in names:
            raw_name = str(raw_name or "").strip()
            if not raw_name:
                continue
            card = await self.card_storage.get_character_card(project_id, raw_name)
            if not card:
                continue
            aliases = [str(a).strip() for a in (card.aliases or []) if str(a).strip()]
            tokens = list(dict.fromkeys([card.name] + aliases))
            mention_count = sum(text.count(token) for token in tokens if token)
            stars = card.stars if card.stars is not None else 1
            catalog.append(
                {
                    "name": card.name,
                    "aliases": aliases,
                    "stars": int(stars) if stars is not None else 1,
                    "mention_count": int(mention_count),
                }
            )

        # Prefer important + mentioned characters, but keep deterministic ordering.
        def sort_key(item: Dict[str, Any]):
            return (-int(item.get("stars") or 1), -int(item.get("mention_count") or 0), str(item.get("name") or ""))

        catalog.sort(key=sort_key)
        return catalog

    def _parse_focus_characters_yaml(self, response: str) -> List[str]:
        if not response:
            return []
        cleaned = str(response).strip()
        if "```" in cleaned:
            # Be tolerant: strip code fences if any.
            start = cleaned.find("```") + 3
            end = cleaned.rfind("```")
            if end > start:
                cleaned = cleaned[start:end].strip()
                if cleaned.lower().startswith("yaml"):
                    cleaned = cleaned[4:].strip()

        try:
            data = yaml.safe_load(cleaned) or {}
        except Exception:
            return []

        raw = data.get("focus_characters") or []
        result: List[str] = []
        for item in raw:
            if isinstance(item, str):
                name = item.strip()
            elif isinstance(item, dict):
                name = str(item.get("name") or item.get("character") or "").strip()
            else:
                name = str(item).strip()
            if name and name not in result:
                result.append(name)
        return result

    def _filter_explicit_mentions(
        self,
        text: str,
        names: List[str],
        candidates: Dict[str, Dict[str, Any]],
    ) -> List[str]:
        cleaned_text = text or ""
        result: List[str] = []
        for name in names or []:
            meta = candidates.get(name) or {}
            tokens = [name]
            tokens.extend(meta.get("aliases") or [])
            if any(token and token in cleaned_text for token in tokens):
                if name not in result:
                    result.append(name)
        return result

    def _select_starred_mentions(
        self,
        catalog: List[Dict[str, Any]],
        text: str,
        min_stars: int = 3,
    ) -> List[str]:
        cleaned_text = text or ""
        hits = []
        for item in catalog or []:
            stars = int(item.get("stars") or 1)
            if stars < min_stars:
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            tokens = [name]
            tokens.extend(item.get("aliases") or [])
            if not any(token and token in cleaned_text for token in tokens):
                continue
            hits.append((int(item.get("mention_count") or 0), name))
        hits.sort(key=lambda x: (-x[0], x[1]))
        return [name for _count, name in hits]

    async def _generate_canon_updates_yaml(self, chapter: str, final_draft: str) -> str:
        """Generate canon updates YAML via LLM."""
        prompt = archivist_canon_updates_prompt(chapter=chapter, final_draft=final_draft, language=self.language)
        messages = self.build_messages(
            system_prompt=prompt.system,
            user_prompt=prompt.user,
            context_items=None,
        )
        response = await self.call_llm(messages)

        if "```yaml" in response:
            yaml_start = response.find("```yaml") + 7
            yaml_end = response.find("```", yaml_start)
            response = response[yaml_start:yaml_end].strip()
        elif "```" in response:
            yaml_start = response.find("```") + 3
            yaml_end = response.find("```", yaml_start)
            response = response[yaml_start:yaml_end].strip()

        return response

    async def _parse_canon_updates_yaml(
        self,
        project_id: str,
        chapter: str,
        yaml_content: str,
    ) -> Dict[str, Any]:
        """Parse canon update YAML."""
        data = yaml.safe_load(yaml_content) or {}

        existing_facts = await self.canon_storage.get_all_facts(project_id)
        next_fact_index = len(existing_facts) + 1

        raw_facts: List[Tuple[str, float]] = []
        context_by_norm: Dict[str, str] = {}
        for item in data.get("facts", []) or []:
            statement = ""
            confidence = 1.0
            context = ""
            if isinstance(item, str):
                statement = item
            elif isinstance(item, dict):
                statement = str(item.get("statement", "") or "")
                context = str(item.get("context", "") or item.get("context_prefix", "") or "").strip()
                conf_raw = item.get("confidence")
                try:
                    confidence = float(conf_raw) if conf_raw is not None else 1.0
                except Exception:
                    confidence = 1.0

            if not statement.strip():
                continue
            raw_facts.append((statement.strip(), max(0.0, min(1.0, confidence))))
            if context:
                context_by_norm[self._normalize_fact_statement(statement.strip())] = context

        selected_facts = self._select_high_value_facts(
            candidates=raw_facts,
            existing_statements=[f.statement for f in (existing_facts or []) if getattr(f, "statement", None)],
            limit=self.MAX_FACTS,
        )

        facts: List[Fact] = []
        for statement, confidence in selected_facts:
            fact_id = f"F{next_fact_index:04d}"
            next_fact_index += 1
            facts.append(
                Fact(
                    id=fact_id,
                    statement=statement.strip(),
                    source=chapter,
                    introduced_in=chapter,
                    confidence=max(0.0, min(1.0, float(confidence))),
                    context_prefix=context_by_norm.get(self._normalize_fact_statement(statement.strip()), ""),
                )
            )

        timeline_events: List[TimelineEvent] = []
        for item in data.get("timeline_events", []) or []:
            if not isinstance(item, dict):
                continue
            timeline_events.append(
                TimelineEvent(
                    time=str(item.get("time", "") or ""),
                    event=str(item.get("event", "") or ""),
                    participants=list(item.get("participants", []) or []),
                    location=str(item.get("location", "") or ""),
                    source=chapter,
                )
            )

        character_states: List[CharacterState] = []
        for item in data.get("character_states", []) or []:
            if not isinstance(item, dict):
                continue
            character = str(item.get("character", "") or "").strip()
            if not character:
                continue
            character_states.append(
                CharacterState(
                    character=character,
                    goals=list(item.get("goals", []) or []),
                    injuries=list(item.get("injuries", []) or []),
                    inventory=list(item.get("inventory", []) or []),
                    relationships=dict(item.get("relationships", {}) or {}),
                    location=item.get("location"),
                    emotional_state=item.get("emotional_state"),
                    last_seen=chapter,
                )
            )

        relations: List[Dict[str, Any]] = []
        for item in data.get("relations", []) or []:
            if not isinstance(item, dict):
                continue
            subject = str(item.get("subject", "") or "").strip()
            relation = str(item.get("relation", "") or "").strip()
            object_ = str(item.get("object", "") or "").strip()
            if not (subject and relation and object_):
                continue
            relations.append(
                {
                    "subject": subject,
                    "relation": relation,
                    "object": object_,
                    "change": str(item.get("change", "") or "").strip(),
                    "chapter": chapter,
                }
            )

        return {
            "facts": facts,
            "timeline_events": timeline_events,
            "character_states": character_states,
            "relations": relations,
        }

    async def _generate_chapter_summary_yaml(
        self,
        chapter: str,
        chapter_title: str,
        final_draft: str,
        retry_hint: Optional[str] = None,
    ) -> str:
        """Generate ChapterSummary YAML via LLM."""
        prompt = archivist_chapter_summary_prompt(
            chapter=chapter,
            chapter_title=chapter_title,
            final_draft=final_draft,
            language=self.language,
        )
        user_prompt = prompt.user
        if retry_hint:
            user_prompt = "\n".join(
                [
                    prompt.user,
                    "",
                    "### Retry constraints (must follow)",
                    str(retry_hint).strip(),
                    "- Output strict YAML only; no markdown/code fences.",
                    "- `brief_summary` must be concise abstraction, not copied draft sentences.",
                ]
            )
        messages = self.build_messages(
            system_prompt=prompt.system,
            user_prompt=user_prompt,
            context_items=None,
        )

        response = await self.call_llm(messages)

        if "```yaml" in response:
            yaml_start = response.find("```yaml") + 7
            yaml_end = response.find("```", yaml_start)
            response = response[yaml_start:yaml_end].strip()
        elif "```" in response:
            yaml_start = response.find("```") + 3
            yaml_end = response.find("```", yaml_start)
            response = response[yaml_start:yaml_end].strip()

        return response

    def _build_summary_retry_hint(self, reason: str) -> str:
        return (
            "The previous output did not pass structured summary checks. "
            f"Failure reason: {str(reason or 'unknown')[:200]}. "
            "Regenerate all fields from the provided draft, especially `brief_summary`."
        )

    async def _generate_volume_summary_yaml(
        self,
        volume_id: str,
        chapter_summaries: List[ChapterSummary],
    ) -> str:
        """Generate VolumeSummary YAML via LLM."""
        items = []
        for summary in chapter_summaries:
            items.append(
                {
                    "chapter": summary.chapter,
                    "title": summary.title,
                    "brief_summary": summary.brief_summary,
                    "key_events": summary.key_events,
                    "open_loops": summary.open_loops,
                }
            )

        prompt = archivist_volume_summary_prompt(volume_id=volume_id, chapter_items=items, language=self.language)
        messages = self.build_messages(
            system_prompt=prompt.system,
            user_prompt=prompt.user,
            context_items=None,
        )

        response = await self.call_llm(messages)

        if "```yaml" in response:
            yaml_start = response.find("```yaml") + 7
            yaml_end = response.find("```", yaml_start)
            response = response[yaml_start:yaml_end].strip()
        elif "```" in response:
            yaml_start = response.find("```") + 3
            yaml_end = response.find("```", yaml_start)
            response = response[yaml_start:yaml_end].strip()

        return response

    def _parse_volume_summary(
        self,
        yaml_content: str,
        volume_id: str,
        chapter_summaries: List[ChapterSummary],
    ) -> VolumeSummary:
        """Parse YAML into a VolumeSummary."""
        try:
            data = yaml.safe_load(yaml_content) or {}
            data["volume_id"] = volume_id
            data.setdefault("brief_summary", "")
            data.setdefault("key_themes", [])
            data.setdefault("major_events", [])
            data["chapter_count"] = len(chapter_summaries)
            data.setdefault("created_at", datetime.now())
            data["updated_at"] = datetime.now()
            return VolumeSummary(**data)
        except Exception:
            return self._fallback_volume_summary(volume_id, chapter_summaries)

    def _fallback_volume_summary(
        self,
        volume_id: str,
        chapter_summaries: List[ChapterSummary],
    ) -> VolumeSummary:
        """Fallback volume summary without LLM."""
        brief_parts = [s.brief_summary for s in chapter_summaries if s.brief_summary]
        brief_summary = " ".join(brief_parts)[:800]
        events = []
        for summary in chapter_summaries:
            events.extend(summary.key_events or [])
        major_events = list(dict.fromkeys([e for e in events if e]))[:20]

        return VolumeSummary(
            volume_id=volume_id,
            brief_summary=brief_summary,
            key_themes=[],
            major_events=major_events,
            chapter_count=len(chapter_summaries),
        )

    def _try_parse_chapter_summary(
        self,
        yaml_content: str,
        chapter: str,
        chapter_title: str,
        final_draft: str,
    ) -> Optional[ChapterSummary]:
        """Best-effort parse YAML into ChapterSummary, returning None on failure."""
        try:
            data = yaml.safe_load(yaml_content) or {}
            data["chapter"] = chapter
            data["title"] = data.get("title") or chapter_title
            data.setdefault("word_count", len(final_draft))
            data.setdefault("key_events", [])
            data.setdefault("new_facts", [])
            data.setdefault("character_state_changes", [])
            data.setdefault("open_loops", [])
            data.setdefault("brief_summary", "")
            return ChapterSummary(**data)
        except Exception as exc:
            logger.warning("Failed to parse chapter summary YAML (chapter=%s): %s", chapter, exc)
            return None

    def _is_likely_raw_excerpt(self, brief_summary: str, final_draft: str) -> bool:
        """
        Detect low-quality summaries that are mostly copied from the draft head.
        """
        summary = self._normalize_summary_text(brief_summary)
        draft = self._normalize_summary_text(final_draft)
        if not summary or not draft:
            return False
        if len(summary) < 80:
            return False

        head = draft[: len(summary)]
        if summary == head:
            return True

        ratio = self._shared_prefix_ratio(summary, head)
        return ratio >= 0.88

    def _normalize_summary_text(self, text: str) -> str:
        return " ".join(str(text or "").replace("\r\n", "\n").split())

    def _shared_prefix_ratio(self, left: str, right: str) -> float:
        max_len = min(len(left), len(right))
        if max_len <= 0:
            return 0.0
        index = 0
        while index < max_len and left[index] == right[index]:
            index += 1
        return index / max_len

    def _fallback_chapter_summary(
        self,
        chapter: str,
        chapter_title: str,
        final_draft: str,
    ) -> ChapterSummary:
        """Fallback summary without LLM."""
        brief = final_draft.strip().replace("\r\n", "\n")
        brief = brief[:400] + ("..." if len(brief) > 400 else "")

        return ChapterSummary(
            chapter=chapter,
            title=chapter_title or chapter,
            word_count=len(final_draft),
            key_events=[],
            new_facts=[],
            character_state_changes=[],
            open_loops=[],
            brief_summary=brief,
        )

    CHAPTER_SUMMARY_MAX_ATTEMPTS = 3
