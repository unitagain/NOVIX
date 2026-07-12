"""Application-wide, privacy-safe error and degradation contracts."""

from __future__ import annotations

import re
import uuid
import logging
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping


class ErrorCategory(str, Enum):
    VALIDATION = "validation"
    AUTHENTICATION = "authentication"
    PERMISSION = "permission"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    PROVIDER = "provider"
    STORAGE = "storage"
    DATA_INTEGRITY = "data_integrity"
    INFRASTRUCTURE = "infrastructure"
    INTERNAL = "internal"


_SAFE_DETAILS = {
    ErrorCategory.VALIDATION: "Invalid request",
    ErrorCategory.AUTHENTICATION: "Authentication failed",
    ErrorCategory.PERMISSION: "Permission denied",
    ErrorCategory.NOT_FOUND: "Resource not found",
    ErrorCategory.CONFLICT: "Resource state conflict",
    ErrorCategory.RATE_LIMIT: "Request rate limited",
    ErrorCategory.TIMEOUT: "Operation timed out",
    ErrorCategory.CANCELLED: "Operation cancelled",
    ErrorCategory.PROVIDER: "LLM provider request failed",
    ErrorCategory.STORAGE: "Storage operation failed",
    ErrorCategory.DATA_INTEGRITY: "Data integrity check failed",
    ErrorCategory.INFRASTRUCTURE: "Infrastructure operation failed",
    ErrorCategory.INTERNAL: "Internal server error",
}

_STATUS_CODES = {
    ErrorCategory.VALIDATION: 400,
    ErrorCategory.AUTHENTICATION: 401,
    ErrorCategory.PERMISSION: 403,
    ErrorCategory.NOT_FOUND: 404,
    ErrorCategory.CONFLICT: 409,
    ErrorCategory.RATE_LIMIT: 429,
    ErrorCategory.TIMEOUT: 504,
    ErrorCategory.CANCELLED: 499,
    ErrorCategory.PROVIDER: 502,
    ErrorCategory.STORAGE: 500,
    ErrorCategory.DATA_INTEGRITY: 409,
    ErrorCategory.INFRASTRUCTURE: 503,
    ErrorCategory.INTERNAL: 500,
}

_CODE_RE = re.compile(r"[^a-z0-9_.:-]+")
logger = logging.getLogger(__name__)


def normalize_error_code(value: Any, default: str = "internal_error") -> str:
    code = _CODE_RE.sub("_", str(value or "").strip().lower()).strip("_.-")
    return code[:120] or default


@dataclass(frozen=True)
class ErrorEnvelope:
    code: str
    category: str
    retryable: bool
    degraded: bool
    safe_detail: str
    request_id: str
    trace_id: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["detail"] = self.safe_detail
        return payload


class DomainError(Exception):
    """Typed internal exception whose public representation is explicitly safe."""

    default_code = "internal_error"
    default_category = ErrorCategory.INTERNAL
    default_retryable = False

    def __init__(
        self,
        message: str = "",
        *,
        code: str | None = None,
        category: ErrorCategory | str | None = None,
        retryable: bool | None = None,
        safe_detail: str | None = None,
        degraded: bool = False,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message or code or self.default_code)
        resolved_category = category or self.default_category
        self.code = normalize_error_code(code or self.default_code)
        self.category = ErrorCategory(resolved_category)
        self.retryable = self.default_retryable if retryable is None else bool(retryable)
        self.safe_detail = safe_detail or _SAFE_DETAILS[self.category]
        self.degraded = bool(degraded)
        self.metadata = dict(metadata or {})


def _llm_error(exc: BaseException) -> DomainError | None:
    reason = getattr(exc, "reason", None)
    if reason is None or not hasattr(exc, "is_retryable"):
        return None
    normalized_reason = normalize_error_code(reason, "provider_error")
    if "auth" in normalized_reason or "api_key" in normalized_reason:
        category = ErrorCategory.AUTHENTICATION
    elif "permission" in normalized_reason or "forbidden" in normalized_reason:
        category = ErrorCategory.PERMISSION
    elif "rate" in normalized_reason or "429" in normalized_reason or "quota" in normalized_reason:
        category = ErrorCategory.RATE_LIMIT
    elif "timeout" in normalized_reason or "deadline" in normalized_reason:
        category = ErrorCategory.TIMEOUT
    else:
        category = ErrorCategory.PROVIDER
    return DomainError(
        code=f"llm.{normalized_reason}",
        category=category,
        retryable=bool(getattr(exc, "is_retryable", False)),
        metadata={"provider": normalize_error_code(getattr(exc, "provider", "unknown"), "unknown")},
    )


def classify_exception(exc: BaseException) -> DomainError:
    if isinstance(exc, DomainError):
        return exc
    llm_error = _llm_error(exc)
    if llm_error is not None:
        return llm_error
    if isinstance(exc, PermissionError):
        return DomainError(code="permission_denied", category=ErrorCategory.PERMISSION)
    if isinstance(exc, FileNotFoundError):
        return DomainError(code="resource_not_found", category=ErrorCategory.NOT_FOUND)
    if isinstance(exc, TimeoutError):
        return DomainError(code="operation_timeout", category=ErrorCategory.TIMEOUT, retryable=True)
    if isinstance(exc, ConnectionError):
        return DomainError(code="connection_error", category=ErrorCategory.INFRASTRUCTURE, retryable=True)
    if isinstance(exc, ValueError):
        message = str(exc)
        code = message if re.fullmatch(r"[a-zA-Z0-9_.:>,=-]{1,200}", message) else "invalid_request"
        return DomainError(code=code, category=ErrorCategory.VALIDATION)
    if isinstance(exc, OSError):
        return DomainError(code="storage_io_error", category=ErrorCategory.STORAGE, retryable=True)
    return DomainError(code="internal_error", category=ErrorCategory.INTERNAL)


def error_envelope(
    exc: BaseException,
    *,
    request_id: str = "",
    trace_id: str = "",
    degraded: bool | None = None,
) -> ErrorEnvelope:
    error = classify_exception(exc)
    return ErrorEnvelope(
        code=error.code,
        category=error.category.value,
        retryable=error.retryable,
        degraded=error.degraded if degraded is None else bool(degraded),
        safe_detail=error.safe_detail,
        request_id=request_id or f"req_{uuid.uuid4().hex}",
        trace_id=trace_id or _current_trace_id() or f"trace_{uuid.uuid4().hex}",
    )


def _current_trace_id() -> str:
    try:
        from opentelemetry import trace

        context = trace.get_current_span().get_span_context()
        if context.is_valid:
            return f"{context.trace_id:032x}"
    except (ImportError, AttributeError, RuntimeError):
        return ""
    return ""


def safe_error_code(exc: BaseException) -> str:
    return classify_exception(exc).code


def tool_error_text(tool_name: str, exc: BaseException) -> str:
    envelope = error_envelope(exc, degraded=True)
    name = normalize_error_code(tool_name, "tool")
    return f"[tool_error name={name} code={envelope.code} trace_id={envelope.trace_id}]"


def benchmark_failure(exc: BaseException) -> dict[str, Any]:
    envelope = error_envelope(exc)
    if envelope.category in {ErrorCategory.AUTHENTICATION.value, ErrorCategory.PERMISSION.value}:
        scope = "policy"
    elif envelope.category in {
        ErrorCategory.VALIDATION.value,
        ErrorCategory.NOT_FOUND.value,
        ErrorCategory.CONFLICT.value,
        ErrorCategory.DATA_INTEGRITY.value,
    }:
        scope = "data"
    else:
        scope = "infrastructure"
    return {
        "success": False,
        "reason": envelope.code,
        "failure_scope": scope,
        "counts_toward_quality": False,
        "error": envelope.to_dict(),
    }


def classify_benchmark_failure_record(value: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize persisted benchmark failures without treating infrastructure as model quality."""
    row = dict(value)
    if row.get("failure_scope"):
        row.setdefault("counts_toward_quality", row["failure_scope"] == "quality")
        return row
    reason = normalize_error_code(row.get("reason") or row.get("code") or "unknown_failure")
    policy_markers = ("external_api", "egress", "permission", "auth", "safety_filter", "content_policy")
    infrastructure_markers = (
        "llm.",
        "provider",
        "timeout",
        "deadline",
        "connection",
        "rate_limit",
        "internal_error",
        "infrastructure",
    )
    data_markers = (
        "missing_",
        "not_distinct",
        "degraded",
        "unavailable",
        "no_context",
        "no_probe",
        "judge_output_",
        "writer_output_",
        "invalid_judge_response",
        "judge_result_not_comparable",
    )
    if any(marker in reason for marker in policy_markers):
        scope = "policy"
    elif any(marker in reason for marker in infrastructure_markers):
        scope = "infrastructure"
    elif any(marker in reason for marker in data_markers):
        scope = "data"
    else:
        scope = "quality"
    row["reason"] = reason
    row["failure_scope"] = scope
    row["counts_toward_quality"] = scope == "quality"
    return row


def status_code_for(exc: BaseException) -> int:
    return _STATUS_CODES[classify_exception(exc).category]


def record_degradation(component: str, exc: BaseException) -> str:
    """Record an intentional best-effort fallback and return its stable error code."""
    code = safe_error_code(exc)
    normalized_component = normalize_error_code(component, "unknown")
    logger.warning("Degraded operation: component=%s code=%s", normalized_component, code, exc_info=True)
    try:
        from app.observability.runtime_metrics import runtime_metrics

        runtime_metrics.increment(f"degradation.{normalized_component}.{code}")
    except (ImportError, RuntimeError) as metric_error:
        logger.debug("Degradation metric dropped: %s", safe_error_code(metric_error))
    return code
