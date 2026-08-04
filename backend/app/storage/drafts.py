"""
中文说明：该模块为 WenShape 后端组成部分，详细行为见下方英文说明。

Draft Storage
Manages scene briefs, drafts, reviews, and summaries.
"""

import re
import shutil
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import os

from app.config import config as app_cfg
from app.context.retriever import DynamicContextRetriever
from app.control_plane.store import RevisionConflict, SQLiteControlStore
from app.schemas.draft import ChapterSummary, Draft, ReviewResult, SceneBrief
from app.schemas.volume import VolumeSummary
from app.storage.base import BaseStorage
from app.error_contract import record_degradation
from app.storage.file_lock import get_file_lock
from app.storage.volumes import VolumeStorage
from app.utils.chapter_id import ChapterIDValidator, normalize_chapter_id

# Max number of previous-version backups to keep per chapter.
_storage_cfg = app_cfg.get("storage", {})
MAX_DRAFT_PREV_BACKUPS = int(_storage_cfg.get("max_draft_prev_backups", 3))


class DraftStorage(BaseStorage):
    """File-based draft storage."""

    def __init__(self, data_dir: Optional[str] = None):
        super().__init__(data_dir)
        self.control_store = SQLiteControlStore(Path(self.data_dir) / "_system" / "control.sqlite3")
        self.context_retriever = DynamicContextRetriever(self)
        self.volume_storage = VolumeStorage(data_dir)

    def _canonicalize_chapter_id(self, chapter_id: str) -> str:
        normalized = normalize_chapter_id(chapter_id)
        if normalized and ChapterIDValidator.validate(normalized):
            return normalized
        return str(chapter_id).strip() if chapter_id else ""

    def _resolve_chapter_dir_name(self, project_id: str, chapter: str) -> str:
        drafts_dir = self.get_project_path(project_id) / "drafts"
        canonical = self._canonicalize_chapter_id(chapter)
        if drafts_dir.exists():
            canonical_path = drafts_dir / canonical
            if canonical_path.exists():
                return canonical
            raw_path = drafts_dir / str(chapter)
            if raw_path.exists():
                return str(chapter)
            for path in drafts_dir.iterdir():
                if path.is_dir() and self._canonicalize_chapter_id(path.name) == canonical:
                    return path.name
        return canonical

    def get_chapter_draft_dir(self, project_id: str, chapter: str) -> Path:
        """Resolve the draft directory for a chapter.

        Args:
            project_id: Project id.
            chapter: Chapter id.

        Returns:
            Draft directory path.
        """
        resolved = self._resolve_chapter_dir_name(project_id, chapter)
        return self.get_project_path(project_id) / "drafts" / resolved

    def get_latest_draft_file(self, project_id: str, chapter: str) -> Optional[Path]:
        """Return the most recently modified draft file for a chapter.

        Args:
            project_id: Project id.
            chapter: Chapter id.

        Returns:
            Path to latest draft file if present.
        """
        draft_dir = self.get_chapter_draft_dir(project_id, chapter)
        if not draft_dir.exists():
            return None
        candidates = list(draft_dir.glob("draft_*.md"))
        final_path = draft_dir / "final.md"
        if final_path.exists():
            candidates.append(final_path)
        if not candidates:
            return None
        return max(candidates, key=lambda path: path.stat().st_mtime)

    async def get_working_text(self, project_id: str, chapter: str) -> Tuple[str, Optional[Path]]:
        """Return the newest chapter prose the user currently sees, with its file.

        中文说明：写作/编辑/分析的「当前正文」唯一 owner。在 final.md（用户在编辑器
        中保存/接受的真相）与 draft_*.md（AI 工作流中间产物）之间按修改时间取最新，
        mtime 相等时偏向 final.md——避免旧 draft 版本永久遮蔽用户在编辑器里的修改与
        删除（这是「中断后续写基于已删除旧文」缺陷的根因）。
        """
        try:
            draft_dir = self.get_chapter_draft_dir(project_id, chapter)
            if not draft_dir.exists():
                return "", None
            final_path = draft_dir / "final.md"
            draft_files = [path for path in draft_dir.glob("draft_*.md") if path.is_file()]
            newest_draft = (
                max(draft_files, key=lambda path: path.stat().st_mtime) if draft_files else None
            )
            if final_path.is_file() and newest_draft is not None:
                chosen = (
                    final_path
                    if final_path.stat().st_mtime >= newest_draft.stat().st_mtime
                    else newest_draft
                )
            elif final_path.is_file():
                chosen = final_path
            else:
                chosen = newest_draft
            if chosen is None:
                return "", None
            return await self.read_text(chosen), chosen
        except (OSError, UnicodeError, ValueError):
            return "", None

    def _final_paths(self, project_id: str, chapter: str) -> Tuple[Path, Path]:
        canonical = self._canonicalize_chapter_id(chapter)
        self._migrate_chapter_dir(project_id, chapter, canonical)
        chapter_dir = self.get_project_path(project_id) / "drafts" / canonical
        final_path = chapter_dir / "final.md"
        history_dir = chapter_dir / "history"
        return final_path, history_dir

    @staticmethod
    def _rotate_draft_history(final_path: Path, history_dir: Path) -> None:
        """Move current final.md into history/ with timestamp, prune old backups."""
        history_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        backup_name = f"final_{ts}.md"
        try:
            os.replace(str(final_path), str(history_dir / backup_name))
        except OSError:
            return

        backups = sorted(
            [p for p in history_dir.iterdir() if p.name.startswith("final_") and p.suffix == ".md"],
            key=lambda p: p.stat().st_mtime,
        )
        while len(backups) > MAX_DRAFT_PREV_BACKUPS:
            oldest = backups.pop(0)
            try:
                oldest.unlink()
            except OSError:
                pass

    async def save_current_draft(
        self,
        project_id: str,
        chapter: str,
        content: str,
        word_count: Optional[int] = None,
        pending_confirmations: Optional[List[str]] = None,
        create_prev_backup: bool = True,
        expected_revision: Optional[int] = None,
    ) -> Draft:
        """Save the current draft (single-version) to final.md.

        设计目标：
        - 用户只看到"当前正文"，保存语义为覆盖写（类似 VSCode Auto Save）
        - 为防误写，默认保留最近 N 份历史备份（history/final_<timestamp>.md）

        Args:
            project_id: Project id.
            chapter: Chapter id.
            content: Draft content.
            word_count: Optional word count override.
            pending_confirmations: Optional pending confirmations for meta.
            create_prev_backup: Whether to keep history backups (best-effort).

        Returns:
            Draft meta object (version 固定为 "current").
        """
        canonical = self._canonicalize_chapter_id(chapter)
        final_path, history_dir = self._final_paths(project_id, canonical)
        file_lock = get_file_lock()
        async with self.content_transaction(project_id):
            async with file_lock.lock(final_path.parent / ".draft_transaction"):
                return await self._save_current_draft_unlocked(
                    project_id=project_id,
                    canonical=canonical,
                    final_path=final_path,
                    history_dir=history_dir,
                    content=content,
                    word_count=word_count,
                    pending_confirmations=pending_confirmations,
                    create_prev_backup=create_prev_backup,
                    expected_revision=expected_revision,
                )

    async def _save_current_draft_unlocked(
        self,
        *,
        project_id: str,
        canonical: str,
        final_path: Path,
        history_dir: Path,
        content: str,
        word_count: Optional[int],
        pending_confirmations: Optional[List[str]],
        create_prev_backup: bool,
        expected_revision: Optional[int],
    ) -> Draft:
        """Save draft content and metadata while the chapter transaction is held."""

        payload = content or ""
        wc = int(word_count if word_count is not None else len(payload))
        revision_key = f"{project_id}/{canonical}/final.md"
        fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        revision = self.control_store.get_revision("draft", revision_key)
        if expected_revision is not None and int(revision["revision"]) != int(expected_revision):
            from app.observability.runtime_metrics import runtime_metrics

            runtime_metrics.increment("commit.conflict")
            raise RevisionConflict(f"revision_conflict:{revision['revision']}!={int(expected_revision)}")
        if revision["fingerprint"] == fingerprint and final_path.exists():
            return Draft(
                chapter=canonical,
                version="current",
                content=payload,
                word_count=wc,
                pending_confirmations=pending_confirmations or [],
                created_at=datetime.now(),
            )

        if create_prev_backup and final_path.exists():
            self._rotate_draft_history(final_path, history_dir)

        try:
            await self.write_text(final_path, payload)
        except Exception:
            # If writing failed after we rotated, attempt to restore from latest backup.
            if create_prev_backup and history_dir.exists() and not final_path.exists():
                backups = sorted(
                    [p for p in history_dir.iterdir() if p.name.startswith("final_") and p.suffix == ".md"],
                    key=lambda p: p.stat().st_mtime,
                )
                if backups:
                    try:
                        os.replace(str(backups[-1]), str(final_path))
                    except OSError:
                        pass
            raise

        draft = Draft(
            chapter=canonical,
            version="current",
            content=payload,
            word_count=wc,
            pending_confirmations=pending_confirmations or [],
            created_at=datetime.now(),
        )
        meta_path = final_path.with_suffix(".meta.yaml")
        await self.write_yaml(meta_path, draft.model_dump(mode="json"))
        self.control_store.bump_revision(
            "draft",
            revision_key,
            expected_revision=int(revision["revision"]),
            fingerprint=fingerprint,
        )
        from app.observability.runtime_metrics import runtime_metrics

        runtime_metrics.increment("commit.success")
        return draft

    def _migrate_chapter_dir(self, project_id: str, chapter: str, canonical: str) -> None:
        drafts_dir = self.get_project_path(project_id) / "drafts"
        if not drafts_dir.exists():
            return
        source_name = self._resolve_chapter_dir_name(project_id, chapter)
        if not source_name or source_name == canonical:
            return
        source_path = drafts_dir / source_name
        target_path = drafts_dir / canonical
        if not source_path.exists() or not source_path.is_dir():
            return
        if target_path.exists():
            for item in source_path.iterdir():
                target_item = target_path / item.name
                if not target_item.exists():
                    item.rename(target_item)
            try:
                source_path.rmdir()
            except OSError:
                pass
            return
        source_path.rename(target_path)

    def _resolve_summary_path(self, project_id: str, chapter: str) -> Optional[Path]:
        summaries_dir = self.get_project_path(project_id) / "summaries"
        canonical = self._canonicalize_chapter_id(chapter)
        if summaries_dir.exists():
            canonical_path = summaries_dir / f"{canonical}_summary.yaml"
            if canonical_path.exists():
                return canonical_path
            raw_path = summaries_dir / f"{chapter}_summary.yaml"
            if raw_path.exists():
                return raw_path
            for path in summaries_dir.glob("*_summary.yaml"):
                name = path.stem.replace("_summary", "")
                if self._canonicalize_chapter_id(name) == canonical:
                    return path
        return summaries_dir / f"{canonical}_summary.yaml"

    def _migrate_summary_file(self, project_id: str, chapter: str, canonical: str) -> None:
        summaries_dir = self.get_project_path(project_id) / "summaries"
        if not summaries_dir.exists():
            return
        target_path = summaries_dir / f"{canonical}_summary.yaml"
        if target_path.exists():
            return
        source_path = self._resolve_summary_path(project_id, chapter)
        if not source_path:
            return
        if source_path.exists() and source_path != target_path:
            source_path.rename(target_path)

    async def get_scene_brief(self, project_id: str, chapter: str) -> Optional[SceneBrief]:
        """Get a scene brief."""
        resolved = self._resolve_chapter_dir_name(project_id, chapter)
        file_path = self.get_project_path(project_id) / "drafts" / resolved / "scene_brief.yaml"
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
        pending_confirmations: Optional[List[str]] = None,
        expected_revision: Optional[int] = None,
    ) -> Draft:
        """Save a draft."""
        canonical = self._canonicalize_chapter_id(chapter)
        self._migrate_chapter_dir(project_id, chapter, canonical)
        draft = Draft(
            chapter=canonical,
            version=version,
            content=content,
            word_count=word_count,
            pending_confirmations=pending_confirmations or [],
            created_at=datetime.now(),
        )

        file_path = self.get_project_path(project_id) / "drafts" / canonical / f"draft_{version}.md"
        meta_path = self.get_project_path(project_id) / "drafts" / canonical / f"draft_{version}.meta.yaml"
        file_lock = get_file_lock()
        async with self.content_transaction(project_id):
            async with file_lock.lock(file_path.parent / ".draft_transaction"):
                revision_key = f"{project_id}/{canonical}/draft_{version}.md"
                fingerprint = hashlib.sha256(content.encode("utf-8")).hexdigest()
                revision = self.control_store.get_revision("draft", revision_key)
                if expected_revision is not None and int(revision["revision"]) != int(expected_revision):
                    from app.observability.runtime_metrics import runtime_metrics

                    runtime_metrics.increment("commit.conflict")
                    raise RevisionConflict(f"revision_conflict:{revision['revision']}!={int(expected_revision)}")
                if revision["fingerprint"] == fingerprint and file_path.exists():
                    return draft
                await self.write_text(file_path, content)
                await self.write_yaml(meta_path, draft.model_dump(mode="json"))
                self.control_store.bump_revision(
                    "draft",
                    revision_key,
                    expected_revision=int(revision["revision"]),
                    fingerprint=fingerprint,
                )
                from app.observability.runtime_metrics import runtime_metrics

                runtime_metrics.increment("commit.success")

        return draft

    async def get_draft(self, project_id: str, chapter: str, version: str) -> Optional[Draft]:
        """Get a draft."""
        resolved = self._resolve_chapter_dir_name(project_id, chapter)
        canonical = self._canonicalize_chapter_id(chapter)
        file_path = self.get_project_path(project_id) / "drafts" / resolved / f"draft_{version}.md"
        meta_path = self.get_project_path(project_id) / "drafts" / resolved / f"draft_{version}.meta.yaml"
        file_lock = get_file_lock()
        async with file_lock.lock(file_path.parent / ".draft_transaction"):
            if not file_path.exists():
                return None

            content = await self.read_text(file_path)
            if meta_path.exists():
                meta = await self.read_yaml(meta_path)
                meta["chapter"] = canonical or meta.get("chapter") or chapter
                # The prose file is authoritative. This repairs a crash between
                # content and metadata replacement instead of returning stale text.
                meta["content"] = content
                meta["word_count"] = len(content)
                return Draft(**meta)

        return Draft(
            chapter=canonical or chapter,
            version=version,
            content=content,
            word_count=len(content),
            pending_confirmations=[],
            created_at=datetime.now(),
        )

    async def get_latest_draft(self, project_id: str, chapter: str) -> Optional[Draft]:
        """Get the latest draft."""
        versions = await self.list_draft_versions(project_id, chapter)
        if not versions:
            return None
        latest = versions[-1]
        return await self.get_draft(project_id, chapter, latest)

    async def list_draft_versions(self, project_id: str, chapter: str) -> List[str]:
        """List draft versions for a chapter."""
        resolved = self._resolve_chapter_dir_name(project_id, chapter)
        drafts_dir = self.get_project_path(project_id) / "drafts" / resolved
        if not drafts_dir.exists():
            return []

        versions = []
        for file_path in drafts_dir.glob("draft_*.md"):
            versions.append(file_path.stem.replace("draft_", ""))

        def version_sort_key(version: str) -> Tuple[int, int, str]:
            match = re.fullmatch(r"v(\d+)", version)
            if match:
                return (0, int(match.group(1)), version)
            return (1, 0, version)

        return sorted(versions, key=version_sort_key)

    async def save_review(self, project_id: str, chapter: str, review: ReviewResult) -> None:
        """Save a review result."""
        canonical = self._canonicalize_chapter_id(chapter)
        self._migrate_chapter_dir(project_id, chapter, canonical)
        file_path = self.get_project_path(project_id) / "drafts" / canonical / "review.yaml"
        await self.write_yaml(file_path, review.model_dump())

    async def get_review(self, project_id: str, chapter: str) -> Optional[ReviewResult]:
        """Get a review result."""
        resolved = self._resolve_chapter_dir_name(project_id, chapter)
        file_path = self.get_project_path(project_id) / "drafts" / resolved / "review.yaml"
        if not file_path.exists():
            return None
        data = await self.read_yaml(file_path)
        return ReviewResult(**data)

    async def save_final_draft(
        self,
        project_id: str,
        chapter: str,
        content: str,
        expected_revision: Optional[int] = None,
    ) -> None:
        """Save a final draft."""
        await self.save_current_draft(
            project_id=project_id,
            chapter=chapter,
            content=content,
            create_prev_backup=True,
            expected_revision=expected_revision,
        )

    def get_draft_revision(self, project_id: str, chapter: str, version: str = "current") -> Dict[str, Any]:
        canonical = self._canonicalize_chapter_id(chapter)
        name = "final.md" if version == "current" else f"draft_{version}.md"
        return self.control_store.get_revision("draft", f"{project_id}/{canonical}/{name}")

    async def get_final_draft(self, project_id: str, chapter: str) -> Optional[str]:
        """Get a final draft."""
        resolved = self._resolve_chapter_dir_name(project_id, chapter)
        file_path = self.get_project_path(project_id) / "drafts" / resolved / "final.md"
        if file_path.exists():
            return await self.read_text(file_path)

        # Backward compatibility: migrate from legacy draft_*.md if final.md is missing.
        legacy_path = self.get_latest_draft_file(project_id, resolved)
        if not legacy_path or not legacy_path.exists() or legacy_path.name == "final.md":
            return None
        try:
            text = await self.read_text(legacy_path)
        except Exception:
            return None

        try:
            await self.save_current_draft(
                project_id=project_id,
                chapter=resolved,
                content=text,
                create_prev_backup=False,
            )
        except Exception as exc:
            # Migration is best-effort; still return the legacy content if saving failed.
            record_degradation("draft_legacy_migration", exc)
        return text

    async def save_chapter_summary(self, project_id: str, summary: ChapterSummary) -> None:
        """Save a chapter summary."""
        raw_chapter = summary.chapter
        summary.chapter = self._canonicalize_chapter_id(summary.chapter)
        summary = self._ensure_volume_id(summary)
        self._migrate_summary_file(project_id, raw_chapter, summary.chapter)
        file_path = self.get_project_path(project_id) / "summaries" / f"{summary.chapter}_summary.yaml"
        await self.write_yaml(file_path, summary.model_dump())

    async def get_chapter_summary(self, project_id: str, chapter: str) -> Optional[ChapterSummary]:
        """Get a chapter summary."""
        canonical = self._canonicalize_chapter_id(chapter)
        file_path = self._resolve_summary_path(project_id, chapter)
        if not file_path.exists():
            return None
        data = await self.read_yaml(file_path)
        summary = ChapterSummary(**data)
        summary.chapter = canonical or summary.chapter
        return self._ensure_volume_id(summary)

    async def list_chapter_summaries(
        self,
        project_id: str,
        volume_id: Optional[str] = None,
    ) -> List[ChapterSummary]:
        """List chapter summaries."""
        summaries_dir = self.get_project_path(project_id) / "summaries"
        if not summaries_dir.exists():
            return []

        summaries: Dict[str, ChapterSummary] = {}
        summary_mtime: Dict[str, float] = {}
        for file_path in summaries_dir.glob("*_summary.yaml"):
            try:
                data = await self.read_yaml(file_path)
                summary = ChapterSummary(**data)
                summary.chapter = self._canonicalize_chapter_id(
                    summary.chapter or file_path.stem.replace("_summary", "")
                )
                summary = self._ensure_volume_id(summary)
                if volume_id and summary.volume_id != volume_id:
                    continue
                chapter_id = summary.chapter
                current_mtime = file_path.stat().st_mtime
                if chapter_id not in summaries or current_mtime > summary_mtime.get(chapter_id, 0):
                    summaries[chapter_id] = summary
                    summary_mtime[chapter_id] = current_mtime
            except (OSError, UnicodeError, TypeError, ValueError):
                continue

        def summary_sort_key(summary: ChapterSummary):
            vol_weight = self._volume_sort_weight(summary.volume_id)
            order_weight = summary.order_index if isinstance(summary.order_index, int) else 10**9
            chapter_weight = ChapterIDValidator.calculate_weight(summary.chapter)
            return (vol_weight, order_weight, chapter_weight)

        ordered = sorted(summaries.values(), key=summary_sort_key)
        return ordered

    async def list_chapters(self, project_id: str) -> List[str]:
        """List chapters for a project."""
        drafts_dir = self.get_project_path(project_id) / "drafts"
        if not drafts_dir.exists():
            return []

        chapters = []
        seen = set()
        for path in drafts_dir.iterdir():
            if not path.is_dir():
                continue
            canonical = self._canonicalize_chapter_id(path.name)
            if not canonical or canonical in seen:
                continue
            seen.add(canonical)
            chapters.append(canonical)
        # If user has customized chapter order (via ChapterSummary.order_index),
        # we respect it; otherwise fallback to chapter id weight ordering.
        try:
            summaries = await self.list_chapter_summaries(project_id)
        except Exception:
            summaries = []

        summary_map: Dict[str, ChapterSummary] = {}
        for s in summaries:
            if s and s.chapter:
                summary_map[self._canonicalize_chapter_id(s.chapter)] = s

        def chapter_sort_key(chapter_id: str):
            summary = summary_map.get(chapter_id)
            vol_id = (summary.volume_id if summary else None) or (
                ChapterIDValidator.extract_volume_id(chapter_id) or "V1"
            )
            vol_weight = self._volume_sort_weight(vol_id)
            order_weight = summary.order_index if summary and isinstance(summary.order_index, int) else 10**9
            chapter_weight = ChapterIDValidator.calculate_weight(chapter_id)
            return (vol_weight, order_weight, chapter_weight)

        return sorted(chapters, key=chapter_sort_key)

    async def delete_chapter(self, project_id: str, chapter: str) -> bool:
        """Delete all draft artifacts for a chapter, with cascade cleanup.

        级联清理关联数据：事实表中该章节引入的事实、章节绑定、记忆包。
        Cascades: canon facts introduced in this chapter, chapter bindings, memory pack.
        """
        project_path = self.get_project_path(project_id)
        canonical = self._canonicalize_chapter_id(chapter)
        deleted_any = False
        drafts_dir = project_path / "drafts"
        if drafts_dir.exists():
            for path in drafts_dir.iterdir():
                if path.is_dir() and self._canonicalize_chapter_id(path.name) == canonical:
                    shutil.rmtree(path)
                    deleted_any = True

        summaries_dir = project_path / "summaries"
        if summaries_dir.exists():
            for path in summaries_dir.glob("*_summary.yaml"):
                name = path.stem.replace("_summary", "")
                if self._canonicalize_chapter_id(name) == canonical:
                    path.unlink()
                    deleted_any = True

        # 级联清理关联数据 / Cascade cleanup of related data
        if deleted_any:
            await self._cascade_delete(project_id, canonical)

        return deleted_any

    async def _cascade_delete(self, project_id: str, chapter: str) -> None:
        """Clean up canon facts, bindings, and memory pack for a deleted chapter."""
        from app.storage.canon import CanonStorage
        from app.storage.memory_pack import MemoryPackStorage
        from app.storage.bindings import ChapterBindingStorage
        from app.utils.logger import get_logger

        logger = get_logger(__name__)

        # 1. 删除该章节引入的事实 / Delete facts introduced in this chapter
        try:
            canon = CanonStorage(str(self.data_dir))
            count = await canon.delete_facts_by_chapter(project_id, chapter)
            if count:
                logger.info("Cascade: deleted %d facts for chapter %s", count, chapter)
        except Exception as exc:
            logger.warning("Cascade: failed to delete facts for chapter %s: %s", chapter, exc)

        # 2. 删除章节绑定 / Delete chapter bindings
        try:
            binding_storage = ChapterBindingStorage(str(self.data_dir))
            binding_path = binding_storage.get_bindings_path(project_id, chapter)
            if binding_path.exists():
                binding_path.unlink()
                # 清理空的父目录
                parent = binding_path.parent
                if parent.exists() and not any(parent.iterdir()):
                    parent.rmdir()
                logger.info("Cascade: deleted bindings for chapter %s", chapter)
        except Exception as exc:
            logger.warning("Cascade: failed to delete bindings for chapter %s: %s", chapter, exc)

        # 3. 删除记忆包 / Delete memory pack
        try:
            memory_pack = MemoryPackStorage(str(self.data_dir))
            deleted = await memory_pack.delete_pack(project_id, chapter)
            if deleted:
                logger.info("Cascade: deleted memory pack for chapter %s", chapter)
        except Exception as exc:
            logger.warning("Cascade: failed to delete memory pack for chapter %s: %s", chapter, exc)

    async def get_context_for_writing(self, project_id: str, current_chapter: str) -> Dict[str, Any]:
        """Get structured context for writing."""
        return await self.context_retriever.retrieve_context(project_id, current_chapter)

    async def list_volume_summaries(self, project_id: str) -> List[VolumeSummary]:
        """List volume summaries."""
        summaries: List[VolumeSummary] = []
        volumes = await self.volume_storage.list_volumes(project_id)
        for volume in volumes:
            summary = await self.volume_storage.get_volume_summary(project_id, volume.id)
            if summary:
                summaries.append(summary)
        return summaries

    async def save_conflict_report(self, project_id: str, chapter: str, report: Dict[str, Any]) -> None:
        """Save a conflict report."""
        canonical = self._canonicalize_chapter_id(chapter)
        self._migrate_chapter_dir(project_id, chapter, canonical)
        file_path = self.get_project_path(project_id) / "drafts" / canonical / "conflicts.yaml"
        await self.write_yaml(file_path, report)

    def _ensure_volume_id(self, summary: ChapterSummary) -> ChapterSummary:
        """Ensure volume_id is set on a summary."""
        if not summary.volume_id:
            summary.volume_id = ChapterIDValidator.extract_volume_id(summary.chapter) or "V1"
        return summary

    def _volume_sort_weight(self, volume_id: Optional[str]) -> int:
        """Sort helper for volume ids like V1, V2..."""
        raw = str(volume_id or "").strip().upper()
        if raw.startswith("V"):
            try:
                return int(raw[1:])
            except (TypeError, ValueError):
                return 0
        return 0

    async def reorder_chapters(self, project_id: str, volume_id: str, chapter_order: List[str]) -> List[ChapterSummary]:
        """
        Persist chapter order within a volume by writing ChapterSummary.order_index.

        Args:
            project_id: Project id.
            volume_id: Target volume id (e.g., V1).
            chapter_order: Ordered chapter ids (canonical).

        Returns:
            Updated summaries for chapters in the provided order.
        """
        canonical_volume = str(volume_id or "").strip().upper() or "V1"
        chapter_order = [self._canonicalize_chapter_id(ch) for ch in (chapter_order or []) if str(ch or "").strip()]
        if not chapter_order:
            return []

        existing_chapters = set(await self.list_chapters(project_id))
        for ch in chapter_order:
            if ch not in existing_chapters:
                raise ValueError(f"章节不存在：{ch}")
            inferred_volume = ChapterIDValidator.extract_volume_id(ch) or "V1"
            if str(inferred_volume).strip().upper() != canonical_volume:
                raise ValueError(f"章节 {ch} 不属于分卷 {canonical_volume}")

        updated: List[ChapterSummary] = []
        for idx, chapter_id in enumerate(chapter_order, start=1):
            summary = await self.get_chapter_summary(project_id, chapter_id)
            if not summary:
                summary = ChapterSummary(chapter=chapter_id, volume_id=canonical_volume, title="", word_count=0)
            summary.chapter = self._canonicalize_chapter_id(summary.chapter)
            summary.volume_id = canonical_volume
            summary.order_index = idx
            await self.save_chapter_summary(project_id, summary)
            updated.append(summary)
        return updated
