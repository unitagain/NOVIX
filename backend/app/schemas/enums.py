"""
Shared enumerations for WenShape.
WenShape 共享枚举定义。
"""

from enum import Enum


class WritingLanguage(str, Enum):
    """Supported writing languages for novel creation. / 支持的小说写作语言。"""

    CHINESE = "zh"
    ENGLISH = "en"
