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

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Mapping, Tuple

from app.error_contract import DomainError, ErrorCategory, normalize_error_code

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
    "insufficient balance",
    "out of credits",
    "40408",
    "token 已经用完",
    "余额不足",
    "额度不足",
    "额度已用完",
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


@dataclass(frozen=True)
class ProviderErrorContract:
    provider: str
    status: int | None
    provider_code: str
    category: str
    retryable: bool
    retry_after: float | None
    safe_detail: str = "LLM provider request failed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status,
            "provider_code": self.provider_code,
            "category": self.category,
            "retryable": self.retryable,
            "retry_after": self.retry_after,
            "safe_detail": self.safe_detail,
        }


def _headers(error: Exception) -> Mapping[str, Any]:
    response = getattr(error, "response", None)
    return getattr(response, "headers", None) or getattr(error, "headers", None) or {}


def _retry_after(error: Exception) -> float | None:
    value = next((value for key, value in _headers(error).items() if str(key).lower() == "retry-after"), None)
    if value is None:
        value = getattr(error, "retry_after", None)
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            parsed = parsedate_to_datetime(str(value))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def normalize_provider_error(error: Exception, *, provider: str = "unknown") -> ProviderErrorContract:
    if isinstance(error, LLMError):
        return error.contract
    response = getattr(error, "response", None)
    status = getattr(error, "status_code", None) or getattr(response, "status_code", None)
    body = getattr(error, "body", None)
    provider_code = getattr(error, "code", None)
    if not provider_code and isinstance(body, Mapping):
        nested = body.get("error")
        detail: Mapping[Any, Any] = nested if isinstance(nested, Mapping) else body
        provider_code = detail.get("code") or detail.get("type")
    provider_code = normalize_error_code(provider_code, "")
    text = f"{provider_code} {error}".lower()
    category = "provider"
    retryable = False
    if status in {401, 403} or any(marker in text for marker in ("auth", "api_key", "unauthorized", "forbidden")):
        category = "authentication" if status == 401 else "permission"
    elif status == 429 and any(marker in text for marker in ("quota", "billing", "credit", "balance")):
        category = "billing"
    elif any(marker in text for marker in ("context_length", "context length", "maximum context", "token limit")):
        category = "context_overflow"
    elif any(marker in text for marker in ("content_policy", "moderation", "safety")):
        category = "policy"
    elif status in {400, 404, 409, 422} or any(marker in text for marker in ("invalid_request", "invalid model")):
        category = "invalid_request"
    elif status == 429:
        category, retryable = "rate_limit", True
    elif status is not None and 500 <= int(status) <= 599:
        category, retryable = "server", True
    elif isinstance(error, (TimeoutError, ConnectionError)):
        category, retryable = "timeout" if isinstance(error, TimeoutError) else "connection", True
    elif any(marker in text for marker in ("quota", "billing", "credit", "balance", "40408")):
        category = "billing"
    elif any(marker in text for marker in ("auth", "api key", "api_key", "unauthorized")):
        category = "authentication"
    elif any(marker in text for marker in ("content policy", "content_policy", "moderation", "safety")):
        category = "policy"
    else:
        for pattern in NON_RETRYABLE_PATTERNS:
            if pattern in text:
                category = "provider"
                break
        else:
            for pattern in RETRYABLE_PATTERNS:
                if pattern in text:
                    category, retryable = "transient", True
                    break
    return ProviderErrorContract(
        provider=normalize_error_code(provider, "unknown"),
        status=int(status) if status is not None else None,
        provider_code=provider_code,
        category=category,
        retryable=retryable,
        retry_after=_retry_after(error),
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
    if isinstance(error, LLMError):
        return error.contract.retryable, error.contract.category
    structured = normalize_provider_error(error)
    if structured.status is not None or structured.provider_code or structured.category != "provider":
        return structured.retryable, structured.category
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

    return False, "unknown_error"


def get_retry_delay(attempt: int, base_delays: list[float] | None = None, max_delay: float = 60.0) -> float:
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


def provider_retry_delay(
    contract: ProviderErrorContract,
    attempt: int,
    base_delays: list[float] | None = None,
    max_delay: float = 60.0,
) -> float:
    """优先服从 provider retry-after，否则使用本地退避。"""

    if contract.retry_after is not None:
        return contract.retry_after
    return get_retry_delay(attempt, base_delays, max_delay)


class LLMError(DomainError):
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
        contract: ProviderErrorContract | None = None,
    ):
        if contract is None and original is None:
            normalized = normalize_error_code(reason, "provider")
            if "auth" in normalized or "api_key" in normalized:
                category = "authentication"
            elif "permission" in normalized or "forbidden" in normalized:
                category = "permission"
            elif "quota" in normalized or "billing" in normalized:
                category = "billing"
            else:
                category = normalized
            contract = ProviderErrorContract(
                provider=normalize_error_code(provider, "unknown"),
                status=status_code,
                provider_code="",
                category=category,
                retryable=is_retryable,
                retry_after=None,
            )
        contract = contract or normalize_provider_error(original or Exception(message), provider=provider)
        provider = contract.provider
        status_code = contract.status
        is_retryable = contract.retryable
        normalized_reason = normalize_error_code(reason, "provider_error")
        if contract.category == "authentication" or "auth" in normalized_reason or "api_key" in normalized_reason:
            category = ErrorCategory.AUTHENTICATION
        elif contract.category == "permission" or "permission" in normalized_reason or "forbidden" in normalized_reason:
            category = ErrorCategory.PERMISSION
        elif contract.category in {"rate_limit", "billing"} or "rate" in normalized_reason or "429" in normalized_reason or "quota" in normalized_reason:
            category = ErrorCategory.RATE_LIMIT
        elif contract.category == "timeout" or "timeout" in normalized_reason or "deadline" in normalized_reason:
            category = ErrorCategory.TIMEOUT
        else:
            category = ErrorCategory.PROVIDER
        super().__init__(
            message,
            code=f"llm.{normalized_reason}",
            category=category,
            retryable=is_retryable,
            safe_detail="LLM provider request failed",
            metadata={"provider": normalize_error_code(provider, "unknown")},
        )
        self.provider = provider
        self.reason = reason
        self.status_code = status_code
        self.is_retryable = is_retryable
        self.original = original
        self.contract = contract

    def to_dict(self) -> dict:
        """Return a privacy-safe, JSON-serializable public representation."""
        return {
            "code": self.code,
            "detail": self.safe_detail,
            **self.contract.to_dict(),
            "reason": self.contract.category,
            "status_code": self.contract.status,
            "is_retryable": self.contract.retryable,
        }
