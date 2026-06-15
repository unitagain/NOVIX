# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  LLM错误分类 - 将错误分类为可重试和不可重试，用于智能重试处理
  LLM Error Classification - Classifies errors as retryable or non-retryable for intelligent retry handling.
"""

from typing import Tuple

# Error message patterns for classification
# 用于分类的错误消息模式

# Non-retryable errors - should fail immediately
# 不可重试错误 - 应立即失败
# 包括认证、权限、无效请求、计费问题等永久性错误
NON_RETRYABLE_PATTERNS = (
    # Authentication errors / 认证错误
    "invalid_api_key",
    "invalid api key",
    "authentication",
    "unauthorized",
    "api key",
    "apikey",
    "invalid_request_error",
    "invalid request",
    # Permission errors / 权限错误
    "permission",
    "forbidden",
    "access denied",
    # Invalid request errors / 无效请求错误
    "invalid_model",
    "model not found",
    "model_not_found",
    "context_length_exceeded",
    "context length",
    "maximum context",
    "token limit",
    "content_policy",
    "content policy",
    "safety",
    "moderation",
    # Billing errors / 计费错误
    "billing",
    "quota exceeded",
    "insufficient_quota",
    "account",
)

# Retryable errors - should retry with backoff
# 可重试错误 - 应使用退避重试
# 包括超时、连接错误、服务器错误、限流等临时性错误
RETRYABLE_PATTERNS = (
    # Timeout errors / 超时错误
    "timeout",
    "timed out",
    "deadline",
    # Connection errors / 连接错误
    "connection",
    "connect",
    "network",
    "socket",
    "reset",
    "refused",
    "unreachable",
    # Server errors / 服务器错误
    "server_error",
    "internal_error",
    "internal server",
    "502",
    "503",
    "504",
    "bad gateway",
    "service unavailable",
    "gateway timeout",
    "overloaded",
    "capacity",
    # Temporary rate limits / 临时限流
    "rate limit",
    "rate_limit_exceeded",
    "too many requests",
    "429",
    "throttl",
    "slow down",
)


def classify_error(error: Exception) -> Tuple[bool, str]:
    """
    将错误分类为可重试或不可重试

    Classify an error as retryable or non-retryable.

    根据异常类型和错误消息模式进行分类。支持的分类模式：
    - 认证错误 / Authentication errors (不可重试)
    - 权限错误 / Permission errors (不可重试)
    - 连接错误 / Connection errors (可重试)
    - 超时错误 / Timeout errors (可重试)
    - 服务器错误 / Server errors (可重试)
    - 限流错误 / Rate limit errors (通常可重试，除非配额超限)

    Classification based on exception type and error message patterns.

    Args:
        error: 要分类的异常 / The exception to classify

    Returns:
        元组 (is_retryable, reason) / Tuple of (is_retryable, reason)
        - is_retryable (bool): True表示可以重试 / True if error should be retried
        - reason (str): 分类原因代码 / Classification reason code

    Example:
        >>> classify_error(TimeoutError("Request timed out"))
        (True, 'connection_error')
        >>> classify_error(ValueError("invalid_api_key"))
        (False, 'auth_error')
    """
    error_str = str(error).lower()
    error_type = type(error).__name__.lower()

    # Check exception type first
    # 首先检查异常类型
    if any(t in error_type for t in ("timeout", "connection", "network", "socket")):
        return True, "connection_error"

    if any(t in error_type for t in ("auth", "permission", "invalid")):
        return False, "auth_error"

    # Programming errors (AttributeError, TypeError, etc.) are not retryable
    # 程序错误（AttributeError、TypeError 等）不应重试
    if any(t in error_type for t in ("attribute", "type", "value", "key", "index", "assertion")):
        return False, "programming_error"

    # Check error message for non-retryable patterns
    # 检查错误消息中的不可重试模式
    for pattern in NON_RETRYABLE_PATTERNS:
        if pattern in error_str:
            return False, f"non_retryable:{pattern}"

    # Check error message for retryable patterns
    # 检查错误消息中的可重试模式
    for pattern in RETRYABLE_PATTERNS:
        if pattern in error_str:
            return True, f"retryable:{pattern}"

    # Default: retry unknown errors (conservative approach)
    # 默认：重试未知错误（保守方法）
    return True, "unknown_error"


def get_retry_delay(attempt: int, base_delays: list = None, max_delay: float = 60.0) -> float:
    """
    计算带指数退避和抖动的重试延迟

    Calculate retry delay with exponential backoff and jitter.

    使用指数退避策略避免重试风暴（thundering herd）。
    添加随机抖动确保分散式系统中的重试间隔错开。

    使用指数退避策略：
    - 前5次尝试使用预定义的延迟：[1, 2, 4, 8, 16]
    - 之后每次延迟翻倍
    - 最大延迟限制为60秒
    - 添加0-10%的随机抖动

    Uses exponential backoff to prevent thundering herd. Adds random jitter
    to stagger retry intervals in distributed systems.

    Args:
        attempt: 当前尝试次数（从0开始） / Current attempt number (0-indexed)
        base_delays: 每次尝试的基础延迟列表（秒） / List of base delays for each attempt
        max_delay: 最大延迟（秒） / Maximum delay in seconds

    Returns:
        延迟时间（秒） / Delay in seconds

    Example:
        >>> get_retry_delay(0)  # First attempt
        1.0 + jitter
        >>> get_retry_delay(2)  # Third attempt
        4.0 + jitter
        >>> get_retry_delay(10)  # Beyond base delays
        1024.0 + jitter (capped at 60.0)
    """
    if base_delays is None:
        base_delays = [1, 2, 4, 8, 16]

    if attempt < len(base_delays):
        delay = base_delays[attempt]
    else:
        # Exponential backoff for attempts beyond base_delays
        delay = base_delays[-1] * (2 ** (attempt - len(base_delays) + 1))

    # Apply max delay cap
    delay = min(delay, max_delay)

    # Add small jitter (0-10% of delay) to prevent thundering herd
    import random

    jitter = delay * random.uniform(0, 0.1)

    return delay + jitter


class LLMError(Exception):
    """
    结构化 LLM 错误，携带提供商/分类信息以便精准报告
    Structured LLM error carrying provider/classification info for precise error reporting.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str = "unknown",
        reason: str = "unknown_error",
        status_code: int | None = None,
        is_retryable: bool = False,
        original: Exception | None = None,
    ):
        super().__init__(message)
        self.provider = provider
        self.reason = reason
        self.status_code = status_code
        self.is_retryable = is_retryable
        self.original = original

    def to_dict(self) -> dict:
        """Return a JSON-serializable dict for API responses."""
        return {
            "detail": str(self),
            "provider": self.provider,
            "reason": self.reason,
            "status_code": self.status_code,
            "is_retryable": self.is_retryable,
        }
