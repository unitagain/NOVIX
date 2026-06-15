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
# 包括常见的中文虚词和英文停用词
_DEFAULT_STOPWORDS = {
    "的",
    "了",
    "在",
    "与",
    "和",
    "但",
    "而",
    "并",
    "及",
    "或",
    "是",
    "有",
    "为",
    "这",
    "那",
    "一个",
    "一些",
    "一种",
    "可以",
    "不会",
    "没有",
    "不是",
    "自己",
    "他们",
    "她们",
    "我们",
    "你们",
    "因为",
    "所以",
    "因此",
    "可能",
    "需要",
    "必须",
    "如果",
    "然后",
    "同时",
    "随着",
    "对于",
    "关于",
    "chapter",
    "goal",
    "title",
}

_STOPWORDS_FILE = Path(__file__).parent.parent.parent / "stopwords.yaml"

_loaded: Set[str] = set()


def get_stopwords() -> Set[str]:
    """
    获取停用词集合，若可用则从文件加载

    Get stopwords set, loading from file if available.

    首次调用时从文件加载停用词（如果存在），后续调用使用缓存。
    如果文件加载失败，使用内置默认停用词。
    On first call, loads from file if it exists. Subsequent calls use cache.
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
            _loaded = set(str(w).strip() for w in words if str(w).strip())
            logger.debug("Loaded %d stopwords from %s", len(_loaded), _STOPWORDS_FILE)
            return _loaded
        except Exception as exc:
            logger.warning("Failed to load stopwords file: %s, using defaults", exc)

    _loaded = set(_DEFAULT_STOPWORDS)
    return _loaded
