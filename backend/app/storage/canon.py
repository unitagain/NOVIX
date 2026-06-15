"""
中文说明：该模块为 WenShape 后端组成部分，详细行为见下方英文说明。

Canon Storage
Manage facts, timeline events, and character states.
"""

from typing import List, Optional, Dict, Any
import re
from app.storage.base import BaseStorage
from app.storage.indexed_cache import get_index_cache
from app.utils.chapter_id import parse_chapter_number, ChapterIDValidator
from app.schemas.canon import Fact, TimelineEvent, CharacterState


class CanonStorage(BaseStorage):

    def _normalize_chapter_id(self, chapter_id: str) -> str:
        if not chapter_id:
            return ""
        normalized = chapter_id.strip().upper()
        if not normalized:
            return ""
        if normalized.startswith("CH"):
            normalized = "C" + normalized[2:]
        if ChapterIDValidator.validate(normalized):
            if normalized.startswith("C"):
                return f"V1{normalized}"
            return normalized
        return normalized

    def _derive_fact_title(self, text: str, max_len: int = 24) -> str:
        """Derive a short title from statement text."""
        if not text:
            return ""
        cleaned = text.strip().replace("\n", " ")
        if len(cleaned) <= max_len:
            return cleaned
        return cleaned[:max_len].rstrip() + "..."

    def _extract_chapter_id(self, value: str) -> str:
        if not value:
            return ""
        raw = str(value)
        match = re.search(r"(V\d+C\d+)", raw, re.IGNORECASE)
        if match:
            return self._normalize_chapter_id(match.group(1))
        match = re.search(r"\b(?:ch|c)\d+\b", raw, re.IGNORECASE)
        if match:
            return self._normalize_chapter_id(match.group(0))
        return self._normalize_chapter_id(raw)

    def _normalize_fact_item(self, item: Dict[str, Any], index: int) -> Dict[str, Any]:
        """Normalize raw fact item for compatibility with legacy data."""
        if not isinstance(item, dict):
            statement = str(item) if item is not None else ""
            return {
                "id": f"F{index + 1:04d}",
                "statement": statement,
                "source": "C0",
                "introduced_in": "C0",
                "confidence": 1.0,
                "title": self._derive_fact_title(statement),
                "content": statement,
            }

        statement = item.get("statement") or item.get("content") or item.get("text") or ""
        source = item.get("source") or item.get("chapter") or item.get("introduced_in") or ""
        introduced_in = item.get("introduced_in") or item.get("source") or item.get("chapter") or source
        fact_id = item.get("id") or item.get("fact_id") or f"F{index + 1:04d}"
        confidence = item.get("confidence", 1.0)
        content = item.get("content") or statement
        title = item.get("title") or item.get("name") or self._derive_fact_title(statement)
        return {
            "id": fact_id,
            "statement": statement,
            "source": source or introduced_in or "C0",
            "introduced_in": introduced_in or source or "C0",
            "confidence": confidence,
            "title": title,
            "content": content,
            "summary_ref": item.get("summary_ref"),
        }

    async def get_all_facts(self, project_id: str) -> List[Fact]:
        """
        Get all facts.

        Args:
            project_id: Project ID.

        Returns:
            List of facts.
        """
        file_path = self.get_project_path(project_id) / "canon" / "facts.jsonl"
        items = await self.read_jsonl(file_path)
        normalized = [self._normalize_fact_item(item, idx) for idx, item in enumerate(items)]
        return [Fact(**item) for item in normalized]

    async def get_all_facts_raw(self, project_id: str) -> List[Dict[str, Any]]:
        """Get all facts as raw dicts with compatibility normalization."""
        file_path = self.get_project_path(project_id) / "canon" / "facts.jsonl"
        items = await self.read_jsonl(file_path)
        return [self._normalize_fact_item(item, idx) for idx, item in enumerate(items)]

    async def get_fact(self, project_id: str, fact_id: str) -> Optional[Fact]:
        """Get a fact by ID (O(1) with index cache)."""
        # 尝试从索引缓存获取
        cache = get_index_cache()
        cached = cache.get_fact_by_id(project_id, fact_id)
        if cached:
            return Fact(**self._normalize_fact_item(cached, 0))

        # 回退到全表扫描（同时构建索引）
        facts = await self.get_all_facts(project_id)
        # 构建索引供下次使用
        await cache.get_or_build_index(project_id, self)
        for fact in facts:
            if fact.id == fact_id:
                return fact
        return None

    async def add_fact(self, project_id: str, fact: Fact) -> None:
        """
        Add a new fact.

        Appends to facts.jsonl and incrementally updates the in-memory index
        (O(1)) instead of invalidating the entire index.
        If the index update fails, the index is marked dirty and will be
        rebuilt on next access (data file is the source of truth).

        Args:
            project_id: Project ID.
            fact: Fact to add.
        """
        file_path = self.get_project_path(project_id) / "canon" / "facts.jsonl"
        fact_data = fact.model_dump()
        await self.append_jsonl(file_path, fact_data)
        # Incremental index update; invalidate on failure so next access rebuilds
        try:
            await get_index_cache().append_fact(project_id, fact_data)
        except Exception:
            await get_index_cache().invalidate(project_id)

    async def update_fact(self, project_id: str, fact_data: Dict[str, Any]) -> bool:
        """Update an existing fact by ID."""
        file_path = self.get_project_path(project_id) / "canon" / "facts.jsonl"
        items = await self.read_jsonl(file_path)
        updated = False
        for idx, item in enumerate(items):
            if item.get("id") == fact_data.get("id"):
                items[idx] = {**item, **fact_data}
                updated = True
                break
        if not updated:
            return False
        await self.write_jsonl(file_path, items)
        # 使索引失效
        await get_index_cache().invalidate(project_id)
        return True

    async def delete_fact(self, project_id: str, fact_id: str) -> bool:
        """Delete a fact by ID."""
        file_path = self.get_project_path(project_id) / "canon" / "facts.jsonl"
        items = await self.read_jsonl(file_path)
        kept = [item for item in items if item.get("id") != fact_id]
        if len(kept) == len(items):
            return False
        await self.write_jsonl(file_path, kept)
        # 使索引失效
        await get_index_cache().invalidate(project_id)
        return True

    async def delete_facts_by_chapter(self, project_id: str, chapter: str) -> int:
        """Delete all facts introduced in a chapter. Returns deleted count."""
        file_path = self.get_project_path(project_id) / "canon" / "facts.jsonl"
        items = await self.read_jsonl(file_path)
        kept = []
        deleted = 0
        target = self._extract_chapter_id(chapter)
        for item in items:
            source = item.get("source") or item.get("introduced_in") or item.get("chapter")
            introduced = item.get("introduced_in") or item.get("source") or item.get("chapter")
            chapter_ref = item.get("chapter_ref") or item.get("chapterRef") or item.get("chapter_id")
            candidates = [source, introduced, chapter_ref]
            normalized = [self._extract_chapter_id(val) for val in candidates if val]
            if target and any(val == target for val in normalized):
                deleted += 1
                continue
            kept.append(item)
        if deleted > 0:
            await self.write_jsonl(file_path, kept)
            await get_index_cache().invalidate(project_id)
        return deleted

    async def delete_and_normalize_by_chapter(self, project_id: str, chapter: str) -> int:
        """
        Normalize all facts and delete those belonging to a chapter in a single pass.

        Combines normalize_fact_records() + delete_facts_by_chapter() into one
        read-process-write cycle, halving file I/O for overwrite scenarios.

        Args:
            project_id: Project ID.
            chapter: Chapter ID whose facts should be deleted.

        Returns:
            Number of facts deleted.
        """
        file_path = self.get_project_path(project_id) / "canon" / "facts.jsonl"
        items = await self.read_jsonl(file_path)

        target = self._extract_chapter_id(chapter)
        kept = []
        deleted = 0

        for item in items:
            if not isinstance(item, dict):
                kept.append(item)
                continue

            # --- normalize fields ---
            source = item.get("source")
            introduced = item.get("introduced_in")
            chapter_ref = item.get("chapter_ref") or item.get("chapterRef") or item.get("chapter_id")
            norm_source = self._extract_chapter_id(source) if source else ""
            norm_intro = self._extract_chapter_id(introduced) if introduced else ""
            norm_ref = self._extract_chapter_id(chapter_ref) if chapter_ref else ""
            canonical = norm_intro or norm_source or norm_ref

            if canonical:
                item = dict(item)
                item["source"] = canonical
                item["introduced_in"] = canonical
                item["chapter_ref"] = canonical

            # --- delete matching chapter ---
            candidates = [norm_source, norm_intro, norm_ref]
            if target and any(val == target for val in candidates if val):
                deleted += 1
                continue

            kept.append(item)

        if deleted > 0 or len(kept) != len(items):
            await self.write_jsonl(file_path, kept)
            await get_index_cache().invalidate(project_id)

        return deleted

    async def normalize_fact_records(self, project_id: str) -> int:
        """Normalize chapter fields for all facts. Returns updated count."""
        file_path = self.get_project_path(project_id) / "canon" / "facts.jsonl"
        items = await self.read_jsonl(file_path)
        updated = 0
        normalized_items = []
        for item in items:
            if not isinstance(item, dict):
                normalized_items.append(item)
                continue
            source = item.get("source")
            introduced = item.get("introduced_in")
            chapter_ref = item.get("chapter_ref") or item.get("chapterRef") or item.get("chapter_id")
            normalized_source = self._extract_chapter_id(source) if source else ""
            normalized_intro = self._extract_chapter_id(introduced) if introduced else ""
            normalized_ref = self._extract_chapter_id(chapter_ref) if chapter_ref else ""
            canonical = normalized_intro or normalized_source or normalized_ref
            if canonical:
                next_item = dict(item)
                next_item["source"] = canonical
                next_item["introduced_in"] = canonical
                next_item["chapter_ref"] = canonical
                if next_item != item:
                    updated += 1
                normalized_items.append(next_item)
            else:
                normalized_items.append(item)
        if updated > 0:
            await self.write_jsonl(file_path, normalized_items)
        return updated

    async def get_facts_by_chapter(self, project_id: str, chapter: str) -> List[Fact]:
        """
        Get facts introduced in a specific chapter / 获取特定章节引入的事实

        Args:
            project_id: Project ID / 项目ID
            chapter: Chapter ID / 章节ID

        Returns:
            List of facts / 事实列表
        """
        all_facts = await self.get_all_facts(project_id)
        return [f for f in all_facts if f.introduced_in == chapter]

    async def get_all_timeline_events(self, project_id: str) -> List[TimelineEvent]:
        """
        Get all timeline events / 获取所有时间线事件

        Args:
            project_id: Project ID / 项目ID

        Returns:
            List of timeline events / 时间线事件列表
        """
        file_path = self.get_project_path(project_id) / "canon" / "timeline.jsonl"
        items = await self.read_jsonl(file_path)
        return [TimelineEvent(**item) for item in items]

    async def add_timeline_event(self, project_id: str, event: TimelineEvent) -> None:
        """
        Add a timeline event / 添加时间线事件

        Args:
            project_id: Project ID / 项目ID
            event: Timeline event to add / 要添加的事件
        """
        file_path = self.get_project_path(project_id) / "canon" / "timeline.jsonl"
        await self.append_jsonl(file_path, event.model_dump())

    async def get_timeline_events_by_chapter(self, project_id: str, chapter: str) -> List[TimelineEvent]:
        """
        Get timeline events from a specific chapter / 获取特定章节的时间线事件

        Args:
            project_id: Project ID / 项目ID
            chapter: Chapter ID / 章节ID

        Returns:
            List of timeline events / 时间线事件列表
        """
        all_events = await self.get_all_timeline_events(project_id)
        return [e for e in all_events if e.source == chapter]

    async def get_timeline_events_near_chapter(
        self,
        project_id: str,
        chapter: str,
        window: int = 3,
        max_events: int = 10,
    ) -> List[TimelineEvent]:
        """Get timeline events near a chapter / 获取邻近章节的时间线事件

        Strategy / 策略：
        - Prefer events whose `source` is within [chapter-window, chapter-1]
        - Fallback to last `max_events` events when chapter id is not numeric

        策略：
        - 优先取来源章节在 [当前章-window, 当前章-1] 的事件
        - 若章节号无法解析，则回退取最近 max_events 条
        """

        current_num = parse_chapter_number(chapter)
        all_events = await self.get_all_timeline_events(project_id)
        if current_num is None:
            return all_events[-max_events:] if all_events else []

        min_num = max(1, current_num - window)
        max_num = current_num - 1

        selected: List[TimelineEvent] = []
        for e in all_events:
            src_num = parse_chapter_number(e.source)
            if src_num is None:
                continue
            if min_num <= src_num <= max_num:
                selected.append(e)

        # Keep chronological order by source chapter number / 按来源章节号保持时间顺序
        selected.sort(key=lambda x: parse_chapter_number(x.source) or 0)
        return selected[-max_events:]

    async def get_all_character_states(self, project_id: str) -> List[CharacterState]:
        """
        Get all character states / 获取所有角色状态

        Args:
            project_id: Project ID / 项目ID

        Returns:
            List of character states / 角色状态列表
        """
        file_path = self.get_project_path(project_id) / "canon" / "character_state.jsonl"
        items = await self.read_jsonl(file_path)
        return [CharacterState(**item) for item in items]

    async def get_character_state(self, project_id: str, character_name: str) -> Optional[CharacterState]:
        """
        Get state of a specific character / 获取特定角色的状态

        Args:
            project_id: Project ID / 项目ID
            character_name: Character name / 角色名称

        Returns:
            Character state or None / 角色状态或None
        """
        all_states = await self.get_all_character_states(project_id)
        for state in reversed(all_states):  # Get most recent state / 获取最新状态
            if state.character == character_name:
                return state
        return None

    async def update_character_state(self, project_id: str, state: CharacterState) -> None:
        """
        Update character state / 更新角色状态

        Args:
            project_id: Project ID / 项目ID
            state: Character state / 角色状态
        """
        file_path = self.get_project_path(project_id) / "canon" / "character_state.jsonl"
        await self.append_jsonl(file_path, state.model_dump())

    def _normalize_text(self, text: str) -> str:
        """Normalize text for comparison / 文本归一化（用于比较）"""
        if not text:
            return ""
        t = text.strip().lower()
        t = re.sub(r"\s+", "", t)
        t = re.sub(r"[\,\.;:!?，。；：！？\"'“”‘’]", "", t)
        return t

    def _has_negation(self, text: str) -> bool:
        """Check if text contains negation cue / 判断文本是否包含否定线索"""
        t = self._normalize_text(text)
        return any(x in t for x in ["不是", "不", "没有", "无"])

    def _maybe_contradict(self, a: str, b: str) -> bool:
        """Heuristic contradiction check / 启发式矛盾判断

        This is intentionally conservative (low false positives).
        这个判断刻意保守（尽量减少误报）。
        """
        na = self._normalize_text(a)
        nb = self._normalize_text(b)
        if not na or not nb:
            return False

        # If texts are identical, not a contradiction / 完全一致则不冲突
        if na == nb:
            return False

        # If one contains negation cue and shares long common substring, flag
        # 若一方有否定且共享较长公共片段，则认为可能冲突
        if self._has_negation(na) != self._has_negation(nb):
            # Common prefix-ish overlap heuristic / 简单重叠判断
            common = 0
            for ch in na:
                if ch in nb:
                    common += 1
            return common >= max(6, min(len(na), len(nb)) // 3)

        return False

    async def detect_conflicts(
        self,
        project_id: str,
        chapter: str,
        new_facts: List[Fact],
        new_timeline_events: List[TimelineEvent],
        new_character_states: List[CharacterState],
    ) -> Dict[str, Any]:
        """Detect conflicts between new updates and existing canon / 检测新增内容与既有设定的冲突

        This is MVP-2 Week6 minimal implementation:
        - Fact contradictions: negation mismatch with similar statements
        - Timeline contradictions: same time + overlapping participants but different location/event
        - Character state contradictions: sudden location change within 1 chapter

        这是 MVP-2 第6周的最小实现：
        - 事实矛盾：相似陈述但否定关系不一致
        - 时间线矛盾：同一时间+参与者重叠但地点/事件描述显著不同
        - 角色状态矛盾：相邻章节出现“瞬移式”位置变化

        Returns / 返回：
        - {"conflicts": ["..."]}
        """

        conflicts: List[str] = []

        # Compare facts / 对比事实
        existing_facts = await self.get_all_facts(project_id)
        for nf in new_facts:
            for ef in existing_facts:
                if self._maybe_contradict(nf.statement, ef.statement):
                    conflicts.append(f"[Fact Conflict] {nf.statement}  <->  {ef.statement} (from {ef.introduced_in})")
                    break

        # Compare timeline / 对比时间线
        existing_events = await self.get_all_timeline_events(project_id)
        for ne in new_timeline_events:
            for ee in existing_events:
                if self._normalize_text(ne.time) and self._normalize_text(ne.time) == self._normalize_text(ee.time):
                    # Participant overlap / 参与者重叠
                    if set(ne.participants or []).intersection(set(ee.participants or [])):
                        if self._normalize_text(ne.location) != self._normalize_text(
                            ee.location
                        ) or self._normalize_text(ne.event) != self._normalize_text(ee.event):
                            conflicts.append(
                                f"[Timeline Conflict] time={ne.time}, participants={ne.participants}: ({ne.event}@{ne.location}) <-> ({ee.event}@{ee.location}) (from {ee.source})"
                            )
                            break

        # Compare character state / 对比角色状态
        current_num = parse_chapter_number(chapter)
        for ns in new_character_states:
            prev = await self.get_character_state(project_id, ns.character)
            if not prev:
                continue
            if not prev.location or not ns.location:
                continue
            if self._normalize_text(prev.location) == self._normalize_text(ns.location):
                continue

            prev_num = parse_chapter_number(prev.last_seen)
            if current_num is not None and prev_num is not None:
                if (current_num - prev_num) <= 1:
                    conflicts.append(
                        f"[State Conflict] {ns.character} location changed too fast: {prev.location}({prev.last_seen}) -> {ns.location}({chapter})"
                    )

        return {"conflicts": conflicts}
