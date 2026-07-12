"""
中文说明：动态上下文检索器，按章节距离与预算聚合可用上下文。

Dynamic context retriever.
"""

from typing import Any, Dict, List, Tuple

from app.utils.chapter_id import ChapterIDValidator
from app.utils.dynamic_ranges import calculate_dynamic_ranges


class DynamicContextRetriever:
    """Retrieve context by distance and budget, with cross-volume summaries."""

    MAX_CONTEXT_TOKENS = 100000
    TOKENS_PER_FACT_LIST = 250
    TOKENS_PER_CHAPTER_SUMMARY = 100
    TOKENS_PER_VOLUME_SUMMARY = 150
    TOKENS_PER_TITLE = 10
    TOKENS_PER_TAIL_CHUNK = 80

    LEVEL_FULL_FACTS = "full_facts"
    LEVEL_SUMMARY_WITH_EVENTS = "summary_events"
    LEVEL_SUMMARY_ONLY = "summary_only"
    LEVEL_TITLE_ONLY = "title_only"

    def __init__(self, storage):
        self.storage = storage

    async def retrieve_context(self, project_id: str, current_chapter: str) -> Dict[str, Any]:
        all_chapters = await self._get_all_previous_chapters(project_id, current_chapter)
        total_chapters = len(all_chapters)

        if total_chapters == 0:
            return {
                "full_facts": [],
                "summary_with_events": [],
                "summary_only": [],
                "title_only": [],
                "volume_summaries": [],
                "previous_tail_chunks": [],
                "total_tokens": 0,
                "chapters_retrieved": 0,
            }

        ranges = calculate_dynamic_ranges(total_chapters)
        chapter_levels = self._assign_retrieval_levels(all_chapters, current_chapter, ranges)
        context = await self._retrieve_within_budget(project_id, chapter_levels, self.MAX_CONTEXT_TOKENS)

        volume_summaries = await self._retrieve_volume_summaries(project_id, current_chapter, context["total_tokens"])
        context["volume_summaries"] = volume_summaries["items"]
        context["total_tokens"] += volume_summaries["tokens"]
        tail_chunks, tail_tokens = await self._retrieve_previous_tail_chunks(project_id, current_chapter)
        context["previous_tail_chunks"] = tail_chunks
        context["total_tokens"] += tail_tokens
        return context

    def _assign_retrieval_levels(
        self,
        all_chapters: List[str],
        current_chapter: str,
        ranges: Dict[str, int],
    ) -> List[Tuple[str, str, int]]:
        result = []
        total = len(all_chapters)
        for idx, chapter in enumerate(all_chapters):
            # 距离按“顺序列表中的相对位置”计算，确保与用户自定义章节顺序一致。
            # all_chapters 为“当前章节之前的章节列表（旧 -> 新）”，因此越靠后越近。
            distance = max(1, total - idx)
            if distance <= ranges["full_facts"]:
                level = self.LEVEL_FULL_FACTS
            elif distance <= ranges["summary_events"]:
                level = self.LEVEL_SUMMARY_WITH_EVENTS
            elif distance <= ranges["summary_only"]:
                level = self.LEVEL_SUMMARY_ONLY
            else:
                level = self.LEVEL_TITLE_ONLY
            result.append((chapter, level, distance))
        result.sort(key=lambda item: item[2])
        return result

    async def _retrieve_within_budget(
        self,
        project_id: str,
        chapter_levels: List[Tuple[str, str, int]],
        max_tokens: int,
    ) -> Dict[str, Any]:
        used_tokens = 0
        result = {
            "full_facts": [],
            "summary_with_events": [],
            "summary_only": [],
            "title_only": [],
            "volume_summaries": [],
            "total_tokens": 0,
            "chapters_retrieved": 0,
        }

        for chapter_id, level, _distance in chapter_levels:
            tokens_needed = self._estimate_tokens(level)
            if used_tokens + tokens_needed <= max_tokens:
                content = await self._retrieve_chapter_content(project_id, chapter_id, level)
                result[self._level_to_key(level)].append(content)
                used_tokens += tokens_needed
            else:
                downgraded = self._downgrade_level(level)
                tokens_needed = self._estimate_tokens(downgraded)
                if used_tokens + tokens_needed <= max_tokens:
                    content = await self._retrieve_chapter_content(project_id, chapter_id, downgraded)
                    result[self._level_to_key(downgraded)].append(content)
                    used_tokens += tokens_needed
                else:
                    content = await self._retrieve_chapter_content(project_id, chapter_id, self.LEVEL_TITLE_ONLY)
                    result["title_only"].append(content)
                    used_tokens += self.TOKENS_PER_TITLE
            result["chapters_retrieved"] += 1

        result["total_tokens"] = used_tokens
        return result

    async def _retrieve_volume_summaries(
        self,
        project_id: str,
        current_chapter: str,
        used_tokens: int,
    ) -> Dict[str, Any]:
        if not hasattr(self.storage, "list_volume_summaries"):
            return {"items": [], "tokens": 0}

        current_volume = ChapterIDValidator.extract_volume_id(current_chapter) or "V1"
        summaries = await self.storage.list_volume_summaries(project_id)
        items = []
        tokens = 0

        for summary in summaries:
            if summary.volume_id == current_volume:
                continue
            if used_tokens + tokens + self.TOKENS_PER_VOLUME_SUMMARY > self.MAX_CONTEXT_TOKENS:
                break
            items.append(
                {
                    "volume_id": summary.volume_id,
                    "brief_summary": summary.brief_summary,
                    "key_themes": summary.key_themes,
                    "major_events": summary.major_events,
                }
            )
            tokens += self.TOKENS_PER_VOLUME_SUMMARY

        return {"items": items, "tokens": tokens}

    async def _retrieve_previous_tail_chunks(
        self,
        project_id: str,
        current_chapter: str,
    ) -> Tuple[List[Dict[str, Any]], int]:
        if not hasattr(self.storage, "get_chapter_tail_chunks"):
            return [], 0
        chapters = await self._get_all_previous_chapters(project_id, current_chapter)
        if not chapters:
            return [], 0
        previous = chapters[-1]
        chunks = await self.storage.get_chapter_tail_chunks(project_id, previous, limit=2)
        token_estimate = 0
        for chunk in chunks:
            token_estimate += max(len(str(chunk.get("text") or "")) // 2, self.TOKENS_PER_TAIL_CHUNK)
        return chunks, token_estimate

    def _downgrade_level(self, level: str) -> str:
        downgrade_map = {
            self.LEVEL_FULL_FACTS: self.LEVEL_SUMMARY_WITH_EVENTS,
            self.LEVEL_SUMMARY_WITH_EVENTS: self.LEVEL_SUMMARY_ONLY,
            self.LEVEL_SUMMARY_ONLY: self.LEVEL_TITLE_ONLY,
            self.LEVEL_TITLE_ONLY: self.LEVEL_TITLE_ONLY,
        }
        return downgrade_map.get(level, self.LEVEL_TITLE_ONLY)

    def _estimate_tokens(self, level: str) -> int:
        token_map = {
            self.LEVEL_FULL_FACTS: self.TOKENS_PER_FACT_LIST + self.TOKENS_PER_CHAPTER_SUMMARY,
            self.LEVEL_SUMMARY_WITH_EVENTS: self.TOKENS_PER_CHAPTER_SUMMARY,
            self.LEVEL_SUMMARY_ONLY: self.TOKENS_PER_CHAPTER_SUMMARY,
            self.LEVEL_TITLE_ONLY: self.TOKENS_PER_TITLE,
        }
        return token_map.get(level, self.TOKENS_PER_TITLE)

    def _level_to_key(self, level: str) -> str:
        key_map = {
            self.LEVEL_FULL_FACTS: "full_facts",
            self.LEVEL_SUMMARY_WITH_EVENTS: "summary_with_events",
            self.LEVEL_SUMMARY_ONLY: "summary_only",
            self.LEVEL_TITLE_ONLY: "title_only",
        }
        return key_map.get(level, "title_only")

    async def _retrieve_chapter_content(
        self,
        project_id: str,
        chapter_id: str,
        level: str,
    ) -> Dict[str, Any]:
        summary = await self.storage.get_chapter_summary(project_id, chapter_id)
        if not summary:
            return {"chapter": chapter_id, "title": chapter_id, "content": "", "level": level}

        content: Dict[str, Any] = {
            "chapter": chapter_id,
            "title": summary.title,
            "level": level,
        }
        if level == self.LEVEL_FULL_FACTS:
            content["summary"] = summary.brief_summary
            content["key_events"] = summary.key_events
            content["open_loops"] = summary.open_loops
        elif level == self.LEVEL_SUMMARY_WITH_EVENTS:
            content["summary"] = summary.brief_summary
            content["key_events"] = summary.key_events
        elif level == self.LEVEL_SUMMARY_ONLY:
            content["summary"] = summary.brief_summary
        return content

    async def _get_all_previous_chapters(self, project_id: str, current_chapter: str) -> List[str]:
        chapters = await self.storage.list_chapters(project_id)
        if not chapters:
            return []

        canonical_current = str(current_chapter or "").strip()
        if canonical_current in chapters:
            index = chapters.index(canonical_current)
            return chapters[:index]

        # 当前章节尚未创建：退化为权重比较，但保持 chapters 的既有顺序（包含自定义排序）。
        try:
            current_weight = ChapterIDValidator.calculate_weight(canonical_current)
        except Exception:
            return chapters
        if current_weight <= 0:
            return chapters
        previous = []
        for chapter_id in chapters:
            try:
                if ChapterIDValidator.calculate_weight(chapter_id) < current_weight:
                    previous.append(chapter_id)
            except (TypeError, ValueError):
                continue
        return previous
