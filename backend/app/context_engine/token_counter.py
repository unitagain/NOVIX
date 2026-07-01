# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  Token计数器 - 精确的Token计数，支持tiktoken和估算方案
  Token Counter - Accurate token counting with tiktoken fallback and estimation.

支持的模型 / Supported Models:
  - OpenAI: GPT-4o, GPT-4, GPT-3.5-turbo等
  - Anthropic: Claude系列
  - DeepSeek: deepseek-chat, deepseek-coder
  - 其他: Qwen, Kimi, GLM, Gemini, Grok等
"""

import re
from app.utils.logger import get_logger

logger = get_logger(__name__)

# 尝试导入 tiktoken
# Try to import tiktoken for accurate counting
_tiktoken_available = False
_encoding = None

try:
    import tiktoken

    _tiktoken_available = True
    _encoding = tiktoken.get_encoding("cl100k_base")
except ImportError:
    logger.info("tiktoken 不可用，使用估算方案 / tiktoken not available, using estimation fallback")


# 中文字符正则 / CJK character pattern
_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\u20000-\u2a6df\u2a700-\u2b73f]")


def count_tokens(text: str, use_cache: bool = True) -> int:
    """
    计算文本的token数量

    Count tokens in text.

    使用tiktoken进行精确计数（如果可用），否则使用估算方案。
    Uses tiktoken for accurate counting if available, otherwise uses estimation.

    Args:
        text: 输入文本 / Input text
        use_cache: 是否使用缓存（对于重复文本） / Whether to use cache for repeated text

    Returns:
        token数量 / Token count

    Example:
        >>> count_tokens("Hello, world!")
        3
        >>> count_tokens("你好，世界！")
        6
    """
    if not text:
        return 0

    if _tiktoken_available and _encoding:
        try:
            return len(_encoding.encode(text))
        except Exception as e:
            logger.debug("tiktoken encode failed: %s", e)

    return _estimate_tokens_mixed(text)


def _estimate_tokens_mixed(text: str) -> int:
    """
    混合语言的token估算

    Estimate tokens for mixed language text.

    中文：约 1.5-2 字符/token (取 1.5)
    英文：约 4 字符/token

    Chinese: ~1.5-2 chars/token (use 1.5)
    English: ~4 chars/token

    Args:
        text: 输入文本 / Input text

    Returns:
        估算的token数量 / Estimated token count
    """
    if not text:
        return 0

    cjk_chars = len(_CJK_PATTERN.findall(text))
    other_chars = len(text) - cjk_chars

    # 中文按 1.5 字符/token，英文按 4 字符/token
    cjk_tokens = cjk_chars / 1.5
    other_tokens = other_chars / 4

    return int(cjk_tokens + other_tokens) + 1  # +1 避免为 0


def estimate_tokens_fast(text: str) -> int:
    """
    快速估算（不使用tiktoken）

    Fast estimation without tiktoken.

    用于大量文本的快速预估，避免tiktoken的开销。
    Useful for quick estimation of large texts without tiktoken overhead.

    与 ``count_tokens`` 的关系（两路已统一在 ``_estimate_tokens_mixed`` 之上，无重复逻辑）：
      - ``count_tokens``：精确路径，优先用 tiktoken，不可用时退化到 ``_estimate_tokens_mixed``。
      - ``estimate_tokens_fast``：始终走 ``_estimate_tokens_mixed``，刻意不碰 tiktoken，
        用于装填器等需要大批量、低开销估算的热路径（见 ``agents/writer.py`` packer）。
    Both paths share ``_estimate_tokens_mixed``; this one deliberately skips tiktoken
    for low-overhead bulk estimation.

    Args:
        text: 输入文本 / Input text

    Returns:
        估算的token数量 / Estimated token count
    """
    return _estimate_tokens_mixed(text)


# 常见模型的上下文窗口大小 / Common model context windows
MODEL_CONTEXT_WINDOWS = {
    # OpenAI — GPT-5.x 系列
    "gpt-5.4": 1050000,
    "gpt-5.4-mini": 400000,
    "gpt-5.4-nano": 400000,
    "gpt-5.2": 400000,
    "gpt-5": 400000,
    "gpt-5-mini": 400000,
    "o3": 200000,
    "o3-mini": 200000,
    "o3-pro": 200000,
    "o4-mini": 200000,
    # OpenAI — 旧模型（API 仍可用）
    "gpt-4.1": 1000000,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4-turbo": 128000,
    # Anthropic
    "claude-opus-4-6": 1000000,
    "claude-sonnet-4-6": 1000000,
    "claude-sonnet-4-5": 200000,
    "claude-haiku-4-5": 200000,
    # Anthropic — 旧模型
    "claude-3-5-sonnet-20241022": 200000,
    "claude-3-5-sonnet": 200000,
    "claude-3-opus": 200000,
    # DeepSeek
    "deepseek-chat": 64000,
    "deepseek-reasoner": 64000,
    # Qwen
    "qwen3.5-plus": 131072,
    "qwen3-max": 32768,
    "qwen-turbo": 131072,
    "qwen-plus": 131072,
    "qwen-max": 32768,
    # Kimi (Moonshot)
    "kimi-k2.5": 256000,
    "kimi-k2-turbo-preview": 256000,
    "kimi-k2-thinking": 256000,
    # Kimi — 旧模型
    "moonshot-v1-8k": 8000,
    "moonshot-v1-32k": 32000,
    "moonshot-v1-128k": 128000,
    # GLM (智谱)
    "glm-5": 202000,
    "glm-4.7": 200000,
    "glm-4-plus": 128000,
    "glm-4": 128000,
    # Gemini
    "gemini-3.1-pro-preview": 1000000,
    "gemini-3.1-flash-lite-preview": 1000000,
    "gemini-3-flash-preview": 1000000,
    "gemini-2.5-flash": 1000000,
    "gemini-pro": 32000,
    # Grok (xAI)
    "grok-4": 2000000,
    "grok-4.1-fast": 2000000,
    "grok-3": 131072,
    "grok-3-mini": 131072,
    # Grok — 旧模型
    "grok-2": 131072,
    # Wenxin (Baidu Qianfan)
    "ernie-4.5-turbo-32k": 32000,
    "ernie-x1-turbo-32k": 32000,
    "ernie-4.5-8k": 8000,
    "ernie-4.5-8k-preview": 8000,
    "ernie-5.0": 32000,
    "ernie-5.0-thinking-preview": 32000,
}

# 默认上下文窗口（保守估计） / Default context window (conservative estimate)
DEFAULT_CONTEXT_WINDOW = 32000


def get_model_context_window(model_name: str) -> int:
    """
    获取模型的上下文窗口大小

    Get model's context window size.

    支持精确匹配、前缀匹配和推断（如xxx-128k）。
    若模型不存在，返回保守估计值。

    Supports exact match, prefix match, and inference (e.g., xxx-128k).
    Returns conservative default if model not found.

    Args:
        model_name: 模型名称 / Model name

    Returns:
        上下文窗口大小（token） / Context window size in tokens

    Example:
        >>> get_model_context_window("gpt-4o")
        128000
        >>> get_model_context_window("claude-3-5-sonnet-20241022")
        200000
        >>> get_model_context_window("unknown-model")
        32000
    """
    if not model_name:
        return DEFAULT_CONTEXT_WINDOW

    model_lower = model_name.lower()

    # 精确匹配
    if model_lower in MODEL_CONTEXT_WINDOWS:
        return MODEL_CONTEXT_WINDOWS[model_lower]

    # 前缀匹配
    for key, value in MODEL_CONTEXT_WINDOWS.items():
        if model_lower.startswith(key) or key.startswith(model_lower):
            return value

    # 从模型名推断（如 xxx-128k）
    if "128k" in model_lower:
        return 128000
    if "64k" in model_lower:
        return 64000
    if "32k" in model_lower:
        return 32000
    if "16k" in model_lower:
        return 16000
    if "8k" in model_lower:
        return 8000

    return DEFAULT_CONTEXT_WINDOW
