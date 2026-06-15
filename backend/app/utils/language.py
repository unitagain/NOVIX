"""
Language normalization utilities.
语言归一化工具。
"""

from typing import Optional


def normalize_language(value: Optional[str], default: str = "zh") -> str:
    """
    Normalize various locale-like values to backend writing language codes: "zh" | "en".

    Accepts:
    - "en", "en-US", "en_US", "EN-us" -> "en"
    - "zh", "zh-CN", "zh_Hans", "zh-hans-cn" -> "zh"
    """
    raw = str(value or "").strip().lower()
    if raw.startswith("en"):
        return "en"
    if raw.startswith("zh"):
        return "zh"
    return str(default or "zh").strip().lower() or "zh"
