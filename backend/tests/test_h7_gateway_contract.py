from __future__ import annotations

import pytest

from app.llm_gateway.contracts import ProviderUsage
from app.llm_gateway.errors import normalize_provider_error, provider_retry_delay
from app.llm_gateway.reliability import RequestDeadline


class SDKError(RuntimeError):
    def __init__(self, message: str, *, status_code=None, code=None, headers=None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.headers = headers or {}


@pytest.mark.parametrize("provider", ["openai", "anthropic", "gemini", "deepseek", "custom"])
def test_all_adapters_share_structured_provider_error_contract(provider):
    error = SDKError("rate limited", status_code=429, code="rate_limit_exceeded", headers={"retry-after": "2"})
    contract = normalize_provider_error(error, provider=provider)
    assert contract.provider == provider
    assert contract.status == 429
    assert contract.provider_code == "rate_limit_exceeded"
    assert contract.category == "rate_limit"
    assert contract.retryable is True
    assert contract.retry_after == 2.0


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (SDKError("invalid key", status_code=401), "authentication"),
        (SDKError("insufficient_quota", status_code=429), "billing"),
        (SDKError("context_length_exceeded", status_code=400), "context_overflow"),
        (SDKError("content_policy", status_code=400), "policy"),
        (SDKError("invalid request", status_code=422), "invalid_request"),
    ],
)
def test_permanent_provider_failures_never_retry(error, category):
    contract = normalize_provider_error(error, provider="openai")
    assert contract.category == category
    assert contract.retryable is False


def test_unknown_provider_error_is_fail_closed():
    contract = normalize_provider_error(RuntimeError("opaque failure"), provider="custom")
    assert contract.retryable is False


def test_usage_distinguishes_reported_missing_and_requested_budget():
    reported = ProviderUsage.from_mapping({"prompt_tokens": 0, "completion_tokens": 0}, requested_max_tokens=128)
    missing = ProviderUsage.from_mapping(None, requested_max_tokens=256)
    assert reported.status == "reported"
    assert reported.requested_max_tokens == 128
    assert missing.status == "missing"
    assert missing.requested_max_tokens == 256


def test_usage_merge_marks_mixed_or_missing_sources_as_estimated():
    reported = ProviderUsage.from_mapping({"prompt_tokens": 2}, requests=1)
    missing = ProviderUsage.from_mapping(None, requests=1)
    assert reported.merge(missing).status == "estimated"


def test_retry_after_takes_priority_over_local_backoff():
    contract = normalize_provider_error(
        SDKError("rate limited", status_code=429, headers={"Retry-After": "5"}), provider="openai"
    )
    assert provider_retry_delay(contract, 0, [1], 60) == 5.0


@pytest.mark.asyncio
async def test_retry_after_sleep_cannot_exceed_total_deadline():
    with pytest.raises(TimeoutError, match="request_deadline_exceeded"):
        await RequestDeadline(0.01).sleep(5)
