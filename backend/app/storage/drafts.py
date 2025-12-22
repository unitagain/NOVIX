"""
Draft Storage / 草稿存储
Manage scene briefs, drafts, reviews, and summaries
管理场景简报、草稿、审稿意见和摘要
"""

from pathlib import Path
from typing import List, Optional, Dict, Any
import re
from datetime import datetime
import shutil
from app.storage.base import BaseStorage
from app.schemas.draft import (
    SceneBrief,
    Draft,
    ReviewResult,
    ChapterSummary
)


class DraftStorage(BaseStorage):
    """Storage operations for drafts and related content / 草稿相关内容的存储操作"""

    def _parse_chapter_number(self, chapter: str) -> Optional[int]:
        """Parse chapter number from id / 从章节ID解析章节号

        Examples:
        - ch01 -> 1
        - ch1 -> 1

        示例：
        - ch01 -> 1
        - ch1 -> 1
        """
        if not chapter:
            return None
        m = re.search(r"(\d+)", chapter)
        if not m:
            return None
        try:
            return int(m.group(1))
        except Exception:
            return None
    
    async def save_scene_brief(
        self,
        project_id: str,
        chapter: str,
        brief: SceneBrief
    ) -> None:
        """
        Save scene brief / 保存场景简报
        
        Args:
            project_id: Project ID / 项目ID
            chapter: Chapter ID / 章节ID
            brief: Scene brief / 场景简报
        """
        file_path = (
            self.get_project_path(project_id) /
            "drafts" / chapter / "scene_brief.yaml"
        )
        await self.write_yaml(file_path, brief.model_dump())
    
    async def get_scene_brief(
        self,
        project_id: str,
        chapter: str
    ) -> Optional[SceneBrief]:
        """
        Get scene brief / 获取场景简报
        
        Args:
            project_id: Project ID / 项目ID
            chapter: Chapter ID / 章节ID
            
        Returns:
            Scene brief or None / 场景简报或None
        """
        file_path = (
            self.get_project_path(project_id) /
            "drafts" / chapter / "scene_brief.yaml"
        )
        
        if not file_path.exists():
            return None
        
        data = await self.read_yaml(file_path)
        return SceneBrief(**data)
    
    async def save_draft(
        self,
        project_id: str,
        chapter: str,
        version: str,
        content: str,
        word_count: int,
        pending_confirmations: List[str] = None
    ) -> Draft:
        """
        Save draft / 保存草稿
        
        Args:
            project_id: Project ID / 项目ID
            chapter: Chapter ID / 章节ID
            version: Draft version / 版本号
            content: Draft content / 草稿内容
            word_count: Word count / 字数
            pending_confirmations: Pending confirmations / 待确认事项
            
        Returns:
            Saved draft / 保存的草稿
        """
        draft = Draft(
            chapter=chapter,
            version=version,
            content=content,
            word_count=word_count,
            pending_confirmations=pending_confirmations or [],
            created_at=datetime.now()
        )
        
        file_path = (
            self.get_project_path(project_id) /
            "drafts" / chapter / f"draft_{version}.md"
        )
        
        await self.write_text(file_path, content)
        
        # Save metadata / 保存元数据
        meta_path = (
            self.get_project_path(project_id) /
            "drafts" / chapter / f"draft_{version}.meta.yaml"
        )
        await self.write_yaml(meta_path, draft.model_dump(mode='json'))
        
        return draft
    
    async def get_draft(
        self,
        project_id: str,
        chapter: str,
        version: str
    ) -> Optional[Draft]:
        """
        Get draft / 获取草稿
        
        Args:
            project_id: Project ID / 项目ID
            chapter: Chapter ID / 章节ID
            version: Draft version / 版本号
            
        Returns:
            Draft or None / 草稿或None
        """
        file_path = (
            self.get_project_path(project_id) /
            "drafts" / chapter / f"draft_{version}.md"
        )
        
        if not file_path.exists():
            return None
        
        content = await self.read_text(file_path)
        
        # Load metadata / 加载元数据
        meta_path = (
            self.get_project_path(project_id) /
            "drafts" / chapter / f"draft_{version}.meta.yaml"
        )
        
        if meta_path.exists():
            meta = await self.read_yaml(meta_path)
            return Draft(**meta)
        
        # Fallback: create basic draft object / 回退：创建基本草稿对象
        return Draft(
            chapter=chapter,
            version=version,
            content=content,
            word_count=len(content),
            pending_confirmations=[],
            created_at=datetime.now()
        )
    
    async def list_draft_versions(
        self,
        project_id: str,
        chapter: str
    ) -> List[str]:
        """
        List all draft versions for a chapter / 列出章节的所有草稿版本
        
        Args:
            project_id: Project ID / 项目ID
            chapter: Chapter ID / 章节ID
            
        Returns:
            List of version strings / 版本号列表
        """
        drafts_dir = (
            self.get_project_path(project_id) /
            "drafts" / chapter
        )
        
        if not drafts_dir.exists():
            return []
        
        versions = []
        for f in drafts_dir.glob("draft_*.md"):
            version = f.stem.replace("draft_", "")
            versions.append(version)
        
        return sorted(versions)
    
    async def save_review(
        self,
        project_id: str,
        chapter: str,
        review: ReviewResult
    ) -> None:
        """
        Save review result / 保存审稿结果
        
        Args:
            project_id: Project ID / 项目ID
            chapter: Chapter ID / 章节ID
            review: Review result / 审稿结果
        """
        file_path = (
            self.get_project_path(project_id) /
            "drafts" / chapter / "review.yaml"
        )
        await self.write_yaml(file_path, review.model_dump())
    
    async def get_review(
        self,
        project_id: str,
        chapter: str
    ) -> Optional[ReviewResult]:
        """
        Get review result / 获取审稿结果
        
        Args:
            project_id: Project ID / 项目ID
            chapter: Chapter ID / 章节ID
            
        Returns:
            Review result or None / 审稿结果或None
        """
        file_path = (
            self.get_project_path(project_id) /
            "drafts" / chapter / "review.yaml"
        )
        
        if not file_path.exists():
            return None
        
        data = await self.read_yaml(file_path)
        return ReviewResult(**data)
    
    async def save_final_draft(
        self,
        project_id: str,
        chapter: str,
        content: str
    ) -> None:
        """
        Save final draft / 保存成稿
        
        Args:
            project_id: Project ID / 项目ID
            chapter: Chapter ID / 章节ID
            content: Final content / 成稿内容
        """
        file_path = (
            self.get_project_path(project_id) /
            "drafts" / chapter / "final.md"
        )
        await self.write_text(file_path, content)
    
    async def get_final_draft(
        self,
        project_id: str,
        chapter: str
    ) -> Optional[str]:
        """
        Get final draft / 获取成稿
        
        Args:
            project_id: Project ID / 项目ID
            chapter: Chapter ID / 章节ID
            
        Returns:
            Final content or None / 成稿内容或None
        """
        file_path = (
            self.get_project_path(project_id) /
            "drafts" / chapter / "final.md"
        )
        
        if not file_path.exists():
            return None
        
        return await self.read_text(file_path)
    
    async def save_chapter_summary(
        self,
        project_id: str,
        summary: ChapterSummary
    ) -> None:
        """
        Save chapter summary / 保存章节摘要
        
        Args:
            project_id: Project ID / 项目ID
            summary: Chapter summary / 章节摘要
        """
        file_path = (
            self.get_project_path(project_id) /
            "summaries" / f"{summary.chapter}_summary.yaml"
        )
        await self.write_yaml(file_path, summary.model_dump())
    
    async def get_chapter_summary(
        self,
        project_id: str,
        chapter: str
    ) -> Optional[ChapterSummary]:
        """
        Get chapter summary / 获取章节摘要
        
        Args:
            project_id: Project ID / 项目ID
            chapter: Chapter ID / 章节ID
            
        Returns:
            Chapter summary or None / 章节摘要或None
        """
        file_path = (
            self.get_project_path(project_id) /
            "summaries" / f"{chapter}_summary.yaml"
        )
        
        if not file_path.exists():
            return None
        
        data = await self.read_yaml(file_path)
        return ChapterSummary(**data)
    
    async def list_chapters(self, project_id: str) -> List[str]:
        """
        List all chapters / 列出所有章节
        
        Args:
            project_id: Project ID / 项目ID
            
        Returns:
            List of chapter IDs / 章节ID列表
        """
        drafts_dir = self.get_project_path(project_id) / "drafts"
        
        if not drafts_dir.exists():
            return []
        
        return [
            d.name for d in drafts_dir.iterdir()
            if d.is_dir()
        ]

    async def delete_chapter(self, project_id: str, chapter: str) -> bool:
        """Delete all stored artifacts for a chapter / 删除某章节的全部存档

        This removes:
        - drafts/{chapter}/ (scene brief, all draft versions, reviews, final, conflicts, etc.)
        - summaries/{chapter}_summary.yaml

        这将删除：
        - drafts/{chapter}/（场景简报、所有草稿版本、审稿、成稿、冲突等）
        - summaries/{chapter}_summary.yaml

        Returns:
        - True if anything was deleted
        - False if nothing existed
        """

        project_path = self.get_project_path(project_id)
        chapter_dir = project_path / "drafts" / chapter
        summary_path = project_path / "summaries" / f"{chapter}_summary.yaml"

        deleted_any = False

        if chapter_dir.exists() and chapter_dir.is_dir():
            shutil.rmtree(chapter_dir)
            deleted_any = True

        if summary_path.exists() and summary_path.is_file():
            summary_path.unlink()
            deleted_any = True

        return deleted_any

    async def select_previous_summaries(
        self,
        project_id: str,
        current_chapter: str,
        near_window: int = 2,
        mid_window: int = 5,
        max_near: int = 2,
        max_mid: int = 3,
        max_far: int = 5,
        max_chars: int = 6000,
    ) -> List[str]:
        """Select previous summaries with distance tiers / 按距离分级选取前文摘要

        Strategy / 策略：
        - near (<= near_window): include brief + key events / 近章：包含概述+关键事件
        - mid  (<= mid_window): include brief only / 中章：仅包含概述
        - far  (> mid_window): include title only / 远章：只保留标题

        Returns:
        - List[str] where each item is a formatted summary block.
        - Each block is designed to be inserted into LLM context.

        返回：
        - 摘要文本块列表，可直接塞进大模型上下文。
        """

        current_num = self._parse_chapter_number(current_chapter)
        if current_num is None:
            return []

        # Collect candidates / 收集候选章节
        chapters = await self.list_chapters(project_id)
        pairs: List[tuple[int, str]] = []
        for ch in chapters:
            n = self._parse_chapter_number(ch)
            if n is None:
                continue
            if n < current_num:
                pairs.append((n, ch))

        pairs.sort(key=lambda x: x[0])

        near: List[str] = []
        mid: List[str] = []
        far: List[str] = []

        # Walk from newest to oldest to apply max limits / 从近到远施加数量上限
        for n, ch in reversed(pairs):
            dist = current_num - n
            if dist <= 0:
                continue

            summary = await self.get_chapter_summary(project_id, ch)
            if not summary:
                continue

            if dist <= near_window and len(near) < max_near:
                key_events = "\n".join([f"- {e}" for e in (summary.key_events or [])][:6])
                open_loops = "\n".join([f"- {e}" for e in (summary.open_loops or [])][:6])
                near.append(
                    f"{ch}: {summary.title}\n"
                    f"{summary.brief_summary}\n"
                    f"Key Events / 关键事件:\n{key_events if key_events else '-'}\n"
                    f"Open Loops / 未解悬念:\n{open_loops if open_loops else '-'}"
                )
                continue

            if dist <= mid_window and len(mid) < max_mid:
                mid.append(f"{ch}: {summary.title}\n{summary.brief_summary}")
                continue

            if len(far) < max_far:
                far.append(f"{ch}: {summary.title}")

        # Output should be chronological for readability / 输出按时间顺序更易读
        selected_far = list(reversed(far))
        selected_mid = list(reversed(mid))
        selected_near = list(reversed(near))

        selected = selected_far + selected_mid + selected_near

        # Enforce budget by trimming far -> mid -> near
        # 通过裁剪远章 -> 中章 -> 近章来满足预算
        if max_chars and max_chars > 0:
            selected_far, selected_mid, selected_near = self._trim_summary_blocks(
                selected_far=selected_far,
                selected_mid=selected_mid,
                selected_near=selected_near,
                max_chars=max_chars,
            )
            selected = selected_far + selected_mid + selected_near

        return selected

    def _trim_summary_blocks(
        self,
        selected_far: List[str],
        selected_mid: List[str],
        selected_near: List[str],
        max_chars: int,
    ) -> tuple[List[str], List[str], List[str]]:
        """Trim summary blocks to fit a character budget / 按字符预算裁剪摘要块

        Strategy / 策略：
        - Drop far blocks first
        - Then drop mid blocks
        - Near blocks are kept as much as possible

        策略：
        - 优先删除远章摘要
        - 再删除中章摘要
        - 近章摘要尽量保留
        """

        def total_len() -> int:
            return sum(len(x) for x in (selected_far + selected_mid + selected_near))

        while total_len() > max_chars and selected_far:
            selected_far.pop(0)

        while total_len() > max_chars and selected_mid:
            selected_mid.pop(0)

        # As last resort, truncate oldest near blocks
        # 兜底：截断最早的近章摘要
        while total_len() > max_chars and selected_near:
            i = 0
            keep = max(200, max_chars - (total_len() - len(selected_near[i])))
            selected_near[i] = selected_near[i][:keep] + "..."
            if len(selected_near[i]) <= 203:
                break

        return selected_far, selected_mid, selected_near

    async def save_conflict_report(
        self,
        project_id: str,
        chapter: str,
        report: Dict[str, Any],
    ) -> None:
        """Save conflict report / 保存冲突报告

        Path:
        - drafts/{chapter}/conflicts.yaml

        路径：
        - drafts/{chapter}/conflicts.yaml
        """

        file_path = (
            self.get_project_path(project_id) /
            "drafts" / chapter / "conflicts.yaml"
        )
        await self.write_yaml(file_path, report)
