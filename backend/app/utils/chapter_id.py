# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  章节ID工具 - 提供章节ID的规范化、验证、解析和排序功能
  Chapter ID Utilities - Normalize, validate, parse and sort chapter IDs

支持的格式 / Supported Formats:
  - C1, C01 (正文章节 / Regular chapters)
  - ch1, ch01 (旧格式 / Legacy format)
  - V1C1, V2C5 (带卷号格式 / With volume number)
  - C3E1, C2I1 (番外/幕间 / Extra/Interlude)
"""

from typing import Dict, List, Optional
import re


def _normalize_chapter_id(chapter_id: str) -> str:
    """
    规范化章节ID为标准大写格式

    Normalize chapter ID to standard uppercase form.

    Args:
        chapter_id: 原始章节ID / Original chapter ID

    Returns:
        规范化后的章节ID / Normalized chapter ID

    Example:
        >>> _normalize_chapter_id("ch1")
        "C1"
        >>> _normalize_chapter_id("volume1c5")
        "V1C5"
    """
    if not chapter_id:
        return ""
    normalized = str(chapter_id).strip()
    if not normalized:
        return ""
    lowered = normalized.lower()
    if lowered.startswith("volume"):
        lowered = "v" + lowered[6:]
    elif lowered.startswith("vol"):
        lowered = "v" + lowered[3:]
    lowered = re.sub(r"[^a-z0-9]", "", lowered)
    if lowered.startswith("ch"):
        lowered = "c" + lowered[2:]
    return lowered.upper()


def parse_chapter_number(chapter: str) -> Optional[int]:
    """
    从章节ID中提取章节号

    Extract chapter number from chapter ID.

    Args:
        chapter: 章节ID / Chapter ID

    Returns:
        章节号（整数）或None / Chapter number (int) or None

    Examples:
        >>> parse_chapter_number("C1")
        1
        >>> parse_chapter_number("C01")
        1
        >>> parse_chapter_number("V2C5")
        5
        >>> parse_chapter_number("C3E1")
        3
        >>> parse_chapter_number("invalid")
        None
    """
    normalized = _normalize_chapter_id(chapter)
    if not normalized:
        return None
    match = re.match(r"^(?:V\d+)?C(\d+)", normalized)
    if match:
        return int(match.group(1))
    fallback = re.search(r"(\d+)", normalized)
    if fallback:
        return int(fallback.group(1))
    return None


class ChapterIDValidator:
    r"""
    章节ID校验器 - 验证、解析和排序章节ID

    Chapter ID Validator - Validates, parses, and sorts chapter identifiers.

    This class provides comprehensive functionality for working with chapter IDs
    including format validation, component extraction, and ordering operations.

    Attributes:
        PATTERN (re.Pattern): 正则表达式，匹配标准的章节ID格式
            Regex pattern matching standard chapter ID format: V(\d+)?C(\d+)(?:([EI])(\d+))?
    """

    PATTERN = re.compile(r"^(?:V(\d+))?C(\d+)(?:([EI])(\d+))?$", re.IGNORECASE)

    @staticmethod
    def validate(chapter_id: str) -> bool:
        """
        校验章节ID格式是否有效

        Validate chapter ID format.

        Args:
            chapter_id: 要校验的章节ID / Chapter ID to validate

        Returns:
            True 如果格式有效 / True if format is valid
        """
        normalized = _normalize_chapter_id(chapter_id)
        return bool(normalized and ChapterIDValidator.PATTERN.match(normalized))

    @staticmethod
    def parse(chapter_id: str) -> Optional[Dict[str, int]]:
        """
        解析章节ID为组成部分

        Parse chapter ID into components.

        Args:
            chapter_id: 章节ID / Chapter ID

        Returns:
            包含以下键值的字典或None / Dictionary with keys or None if invalid:
            - volume (int): 卷号 / Volume number
            - chapter (int): 章号 / Chapter number
            - type (str|None): 类型 (E=番外/Extra, I=幕间/Interlude) / Type code
            - seq (int): 序号 / Sequence number

        Example:
            >>> ChapterIDValidator.parse("V1C5E2")
            {'volume': 1, 'chapter': 5, 'type': 'E', 'seq': 2}
        """
        normalized = _normalize_chapter_id(chapter_id)
        if not normalized:
            return None
        match = ChapterIDValidator.PATTERN.match(normalized)
        if not match:
            return None
        volume = int(match.group(1)) if match.group(1) else 0
        chapter = int(match.group(2))
        chapter_type = match.group(3)
        seq = int(match.group(4)) if match.group(4) else 0
        return {
            "volume": volume,
            "chapter": chapter,
            "type": chapter_type,
            "seq": seq,
        }

    @staticmethod
    def calculate_weight(chapter_id: str) -> float:
        """
        计算章节的排序权重

        Calculate ordering weight for chapter ID.

        Args:
            chapter_id: 章节ID / Chapter ID

        Returns:
            排序权重（浮点数） / Ordering weight

        Weight Formula:
            base = volume * 1000 + chapter
            - 番外/幕间 / Extra/Interlude: add 0.1 * seq

        Example:
            V1C1 = 1001.0, V1C1E1 = 1001.1, V2C5 = 2005.0
        """
        parsed = ChapterIDValidator.parse(chapter_id)
        if not parsed:
            return 0.0
        base = parsed["volume"] * 1000 + parsed["chapter"]
        if parsed["type"] and parsed["seq"] > 0:
            base += 0.1 * parsed["seq"]
        return float(base)

    @staticmethod
    def sort_chapters(chapter_ids: List[str]) -> List[str]:
        """
        按权重排序章节ID

        Sort chapter IDs by weight.

        Args:
            chapter_ids: 章节ID列表 / List of chapter IDs

        Returns:
            已排序的章节ID列表 / Sorted list of chapter IDs

        Example:
            >>> ChapterIDValidator.sort_chapters(["C3", "C1", "C2"])
            ["C1", "C2", "C3"]
        """
        return sorted(chapter_ids, key=ChapterIDValidator.calculate_weight)

    @staticmethod
    def suggest_next_id(
        existing_ids: List[str],
        chapter_type: str = "normal",
        insert_after: Optional[str] = None,
    ) -> str:
        """
        建议下一个章节ID

        Suggest next chapter ID based on existing chapters.

        Args:
            existing_ids: 现有章节ID列表 / Existing chapter IDs
            chapter_type: 章节类型 / Chapter type (only "normal" supported)
            insert_after: 保留参数，不再使用 / Reserved, no longer used

        Returns:
            推荐的下一个章节ID / Suggested next chapter ID

        Example:
            >>> ChapterIDValidator.suggest_next_id(["C1", "C2"], "normal")
            "C3"
        """
        max_chapter = 0
        for cid in existing_ids:
            parsed = ChapterIDValidator.parse(cid)
            if parsed and not parsed["type"]:
                max_chapter = max(max_chapter, parsed["chapter"])
        return f"C{max_chapter + 1}"

    @staticmethod
    def get_type_label(chapter_id: str) -> str:
        """
        获取章节类型的标签文字

        Get chapter type label in Chinese.

        Args:
            chapter_id: 章节ID / Chapter ID

        Returns:
            章节类型标签 / Chapter type label
            - "序章": Prologue
            - "正文": Regular chapter
            - "尾声": Epilogue
            - "未知": Unknown

        Example:
            >>> ChapterIDValidator.get_type_label("C0")
            "序章"
            >>> ChapterIDValidator.get_type_label("C5")
            "正文"
        """
        parsed = ChapterIDValidator.parse(chapter_id)
        if not parsed:
            return "未知"
        if parsed["chapter"] == 0:
            return "序章"
        if parsed["chapter"] == 999:
            return "尾声"
        return "正文"

    @staticmethod
    def calculate_distance(
        current_chapter: str,
        target_chapter: str,
        avg_chapters_per_volume: int = 15,
    ) -> int:
        """
        计算两章之间的距离

        Calculate distance between two chapters.

        Args:
            current_chapter: 当前章节ID / Current chapter ID
            target_chapter: 目标章节ID / Target chapter ID
            avg_chapters_per_volume: 平均每卷章数（用于跨卷计算） / Average chapters per volume

        Returns:
            两章之间的距离 / Distance between chapters (integer)
            - 同卷：绝对差值 / Same volume: absolute difference
            - 不同卷：卷距×平均章数+章偏移 / Different volumes: volume_distance * avg + offset

        Example:
            >>> ChapterIDValidator.calculate_distance("V1C1", "V1C5")
            4
            >>> ChapterIDValidator.calculate_distance("V1C5", "V2C3")
            20  # (1 * 15) + 3
        """
        current = ChapterIDValidator.parse(current_chapter)
        target = ChapterIDValidator.parse(target_chapter)
        if not current or not target:
            return 10**9

        current_vol = current["volume"]
        target_vol = target["volume"]
        current_ch = current["chapter"]
        target_ch = target["chapter"]

        if current_vol == target_vol:
            return abs(current_ch - target_ch)

        volume_distance = abs(current_vol - target_vol)
        chapter_offset = min(current_ch, target_ch)
        return volume_distance * avg_chapters_per_volume + chapter_offset

    @staticmethod
    def compare(left_chapter: str, right_chapter: str) -> Optional[int]:
        """Compare narrative order, treating an omitted volume as the peer's volume."""

        left = ChapterIDValidator.parse(left_chapter)
        right = ChapterIDValidator.parse(right_chapter)
        if not left or not right:
            return None

        left_volume = left["volume"] or right["volume"] or 1
        right_volume = right["volume"] or left["volume"] or 1

        def order_key(parsed: Dict[str, int], volume: int) -> tuple[int, int, int, int]:
            type_order = 0 if not parsed["type"] else 1
            return volume, parsed["chapter"], type_order, parsed["seq"]

        left_key = order_key(left, left_volume)
        right_key = order_key(right, right_volume)
        if left_key < right_key:
            return -1
        if left_key > right_key:
            return 1
        return 0

    @staticmethod
    def is_after(candidate_chapter: str, reference_chapter: str) -> bool:
        """Return whether *candidate_chapter* is narratively after *reference_chapter*."""

        return ChapterIDValidator.compare(candidate_chapter, reference_chapter) == 1

    @staticmethod
    def extract_volume_id(chapter_id: str) -> Optional[str]:
        """
        从章节ID中提取卷ID

        Extract volume ID from chapter ID.

        Args:
            chapter_id: 章节ID / Chapter ID

        Returns:
            卷ID（如 "V1"）或None / Volume ID or None if not found

        Example:
            >>> ChapterIDValidator.extract_volume_id("V2C5")
            "V2"
            >>> ChapterIDValidator.extract_volume_id("C5")
            None
        """
        parsed = ChapterIDValidator.parse(chapter_id)
        if parsed and parsed["volume"] > 0:
            return f"V{parsed['volume']}"
        return None


def normalize_chapter_id(chapter_id: str, default_volume: str = "V1") -> str:
    """
    规范化章节ID为包含卷号的标准格式

    Normalize chapter ID to canonical form with volume prefix.

    Args:
        chapter_id: 原始章节ID / Original chapter ID
        default_volume: 缺失卷号时使用的默认卷号 / Default volume if missing

    Returns:
        规范化后的章节ID / Normalized chapter ID

    Example:
        >>> normalize_chapter_id("C5")
        "V1C5"
        >>> normalize_chapter_id("V2C3")
        "V2C3"
    """
    normalized = _normalize_chapter_id(chapter_id)
    if not normalized:
        return ""
    if ChapterIDValidator.validate(normalized):
        if normalized.startswith("C"):
            return f"{default_volume}{normalized}"
        return normalized
    return normalized
