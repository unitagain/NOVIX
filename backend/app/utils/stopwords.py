# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  停用词配置 - 从配置文件加载停用词，支持内置默认值
  Stopwords Configuration - Loads stopwords from config file with built-in defaults.
"""

from pathlib import Path
from typing import Set

import yaml

from app.utils.logger import get_logger

logger = get_logger(__name__)

# Built-in default stopwords / 内置默认停用词
# 统一停用词的唯一代码内来源（Phase 4 三处合一）：合并了原 text_tokenizer 的
# 中/英文停用词与档案员关键词过滤词，避免多处重复、各自漂移。
# Single in-code source of truth for stopwords (Phase 4 dedup): merges the former
# text_tokenizer CJK/English sets with the archivist keyword filter list.

# 中文虚词 / Chinese function words
_CHINESE_STOPWORDS = frozenset(
    [
        "的",
        "了",
        "是",
        "在",
        "我",
        "有",
        "和",
        "就",
        "不",
        "人",
        "都",
        "一",
        "一个",
        "上",
        "也",
        "很",
        "到",
        "说",
        "要",
        "去",
        "你",
        "会",
        "着",
        "没有",
        "看",
        "好",
        "自己",
        "这",
        "那",
        "他",
        "她",
        "它",
        "们",
        "这个",
        "那个",
        "什么",
        "怎么",
        "为什么",
        "哪",
        "哪里",
        "哪个",
        "谁",
        "多少",
        "几",
        "如何",
        "为",
        "与",
        "及",
        "或",
        "但",
        "而",
        "并",
        "因为",
        "所以",
        "如果",
        "虽然",
        "但是",
        "然后",
        "之后",
        "之前",
        "可以",
        "能",
        "应该",
        "必须",
        "需要",
        "想",
        "让",
        "把",
        "被",
        "给",
        "从",
        "向",
        "对",
        "于",
        "以",
        "等",
        "等等",
        "还",
        "又",
        "再",
        # 短语 / phrases & 连词 / conjunctions
        "一些",
        "一种",
        "不会",
        "不是",
        "他们",
        "她们",
        "我们",
        "你们",
        "因此",
        "可能",
        "同时",
        "随着",
        "对于",
        "关于",
    ]
)

# 英文停用词 / English stopwords
_ENGLISH_STOPWORDS = frozenset(
    [
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "must",
        "shall",
        "can",
        "need",
        "dare",
        "to",
        "of",
        "in",
        "for",
        "on",
        "with",
        "at",
        "by",
        "from",
        "as",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "between",
        "under",
        "again",
        "further",
        "then",
        "once",
        "here",
        "there",
        "when",
        "where",
        "why",
        "how",
        "all",
        "each",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "no",
        "nor",
        "not",
        "only",
        "own",
        "same",
        "so",
        "than",
        "too",
        "very",
        "just",
        "and",
        "but",
        "if",
        "or",
        "because",
        "until",
        "while",
        "this",
        "that",
        "these",
        "those",
        "i",
        "me",
        "my",
        "myself",
        "we",
        "our",
        "you",
        "your",
        "he",
        "him",
        "his",
        "she",
        "her",
        "it",
        "its",
        "they",
        "them",
        "their",
        "what",
        "which",
        "who",
        "whom",
        # 档案员关键词过滤补充 / archivist keyword-filter extras
        "chapter",
        "goal",
        "title",
    ]
)

_DEFAULT_STOPWORDS = _CHINESE_STOPWORDS | _ENGLISH_STOPWORDS

_STOPWORDS_FILE = Path(__file__).parent.parent.parent / "stopwords.yaml"

_loaded: Set[str] = set()


def get_stopwords() -> Set[str]:
    """
    获取停用词集合，若可用则从文件加载

    Get stopwords set, loading from file if available.

    首次调用时从文件加载停用词（如果存在），后续调用使用缓存。
    文件中的停用词与内置默认值取**并集**（文件只增不减），确保任何一处都不会因
    精简的 yaml 而丢词。如果文件加载失败，使用内置默认停用词。
    On first call, loads from file if it exists and UNIONs it with built-in defaults
    (the file can only add words, never shrink the set). Subsequent calls use cache.
    If file loading fails, uses built-in defaults.

    Returns:
        停用词集合 / Set of stopwords

    Example:
        >>> stopwords = get_stopwords()
        >>> "的" in stopwords
        True
    """
    global _loaded
    if _loaded:
        return _loaded

    if _STOPWORDS_FILE.exists():
        try:
            with open(_STOPWORDS_FILE, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if isinstance(data, dict):
                words = data.get("stopwords", [])
            elif isinstance(data, list):
                words = data
            else:
                words = []
            file_words = set(str(w).strip() for w in words if str(w).strip())
            _loaded = set(_DEFAULT_STOPWORDS) | file_words
            logger.debug("Loaded %d stopwords (%d from %s)", len(_loaded), len(file_words), _STOPWORDS_FILE)
            return _loaded
        except Exception as exc:
            logger.warning("Failed to load stopwords file: %s, using defaults", exc)

    _loaded = set(_DEFAULT_STOPWORDS)
    return _loaded
