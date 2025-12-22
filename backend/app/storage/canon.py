"""
Canon Storage / 事实表存储
Manage facts, timeline events, and character states
管理事实表、时间线和角色状态
"""

from typing import List, Optional, Dict, Any
import re
from app.storage.base import BaseStorage
from app.schemas.canon import Fact, TimelineEvent, CharacterState


class CanonStorage(BaseStorage):
    """Storage operations for canon (facts, timeline, character states) / 事实表存储操作"""

    def _parse_chapter_number(self, chapter: str) -> Optional[int]:
        """Parse chapter number from id / 从章节ID解析章节号"""
        if not chapter:
            return None
        m = re.search(r"(\d+)", chapter)
        if not m:
            return None
        try:
            return int(m.group(1))
        except Exception:
            return None
    
    async def get_all_facts(self, project_id: str) -> List[Fact]:
        """
        Get all facts / 获取所有事实
        
        Args:
            project_id: Project ID / 项目ID
            
        Returns:
            List of facts / 事实列表
        """
        file_path = self.get_project_path(project_id) / "canon" / "facts.jsonl"
        items = await self.read_jsonl(file_path)
        return [Fact(**item) for item in items]
    
    async def add_fact(self, project_id: str, fact: Fact) -> None:
        """
        Add a new fact / 添加新事实
        
        Args:
            project_id: Project ID / 项目ID
            fact: Fact to add / 要添加的事实
        """
        file_path = self.get_project_path(project_id) / "canon" / "facts.jsonl"
        await self.append_jsonl(file_path, fact.model_dump())
    
    async def get_facts_by_chapter(
        self,
        project_id: str,
        chapter: str
    ) -> List[Fact]:
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
    
    async def add_timeline_event(
        self,
        project_id: str,
        event: TimelineEvent
    ) -> None:
        """
        Add a timeline event / 添加时间线事件
        
        Args:
            project_id: Project ID / 项目ID
            event: Timeline event to add / 要添加的事件
        """
        file_path = self.get_project_path(project_id) / "canon" / "timeline.jsonl"
        await self.append_jsonl(file_path, event.model_dump())
    
    async def get_timeline_events_by_chapter(
        self,
        project_id: str,
        chapter: str
    ) -> List[TimelineEvent]:
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

        current_num = self._parse_chapter_number(chapter)
        all_events = await self.get_all_timeline_events(project_id)
        if current_num is None:
            return all_events[-max_events:] if all_events else []

        min_num = max(1, current_num - window)
        max_num = current_num - 1

        selected: List[TimelineEvent] = []
        for e in all_events:
            src_num = self._parse_chapter_number(e.source)
            if src_num is None:
                continue
            if min_num <= src_num <= max_num:
                selected.append(e)

        # Keep chronological order by source chapter number / 按来源章节号保持时间顺序
        selected.sort(key=lambda x: self._parse_chapter_number(x.source) or 0)
        return selected[-max_events:]
    
    async def get_all_character_states(
        self,
        project_id: str
    ) -> List[CharacterState]:
        """
        Get all character states / 获取所有角色状态
        
        Args:
            project_id: Project ID / 项目ID
            
        Returns:
            List of character states / 角色状态列表
        """
        file_path = (
            self.get_project_path(project_id) /
            "canon" / "character_state.jsonl"
        )
        items = await self.read_jsonl(file_path)
        return [CharacterState(**item) for item in items]
    
    async def get_character_state(
        self,
        project_id: str,
        character_name: str
    ) -> Optional[CharacterState]:
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
    
    async def update_character_state(
        self,
        project_id: str,
        state: CharacterState
    ) -> None:
        """
        Update character state / 更新角色状态
        
        Args:
            project_id: Project ID / 项目ID
            state: Character state / 角色状态
        """
        file_path = (
            self.get_project_path(project_id) /
            "canon" / "character_state.jsonl"
        )
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
                    conflicts.append(
                        f"[Fact Conflict] {nf.statement}  <->  {ef.statement} (from {ef.introduced_in})"
                    )
                    break

        # Compare timeline / 对比时间线
        existing_events = await self.get_all_timeline_events(project_id)
        for ne in new_timeline_events:
            for ee in existing_events:
                if self._normalize_text(ne.time) and self._normalize_text(ne.time) == self._normalize_text(ee.time):
                    # Participant overlap / 参与者重叠
                    if set(ne.participants or []).intersection(set(ee.participants or [])):
                        if self._normalize_text(ne.location) != self._normalize_text(ee.location) or self._normalize_text(ne.event) != self._normalize_text(ee.event):
                            conflicts.append(
                                f"[Timeline Conflict] time={ne.time}, participants={ne.participants}: ({ne.event}@{ne.location}) <-> ({ee.event}@{ee.location}) (from {ee.source})"
                            )
                            break

        # Compare character state / 对比角色状态
        current_num = self._parse_chapter_number(chapter)
        for ns in new_character_states:
            prev = await self.get_character_state(project_id, ns.character)
            if not prev:
                continue
            if not prev.location or not ns.location:
                continue
            if self._normalize_text(prev.location) == self._normalize_text(ns.location):
                continue

            prev_num = self._parse_chapter_number(prev.last_seen)
            if current_num is not None and prev_num is not None:
                if (current_num - prev_num) <= 1:
                    conflicts.append(
                        f"[State Conflict] {ns.character} location changed too fast: {prev.location}({prev.last_seen}) -> {ns.location}({chapter})"
                    )

        return {"conflicts": conflicts}
