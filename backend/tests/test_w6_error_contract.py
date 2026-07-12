"""W6 error taxonomy, privacy and failure-classification regressions."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from starlette.requests import Request

from app.agents.writing_actions import WritingActionToolset
from app.error_contract import (
    DomainError,
    ErrorCategory,
    benchmark_failure,
    classify_benchmark_failure_record,
    error_envelope,
)
from app.jobs.durable_queue import DurableTaskQueue
from app.llm_gateway.errors import LLMError
from app.main import global_exception_handler
from scripts.error_contract_audit import audit


def test_error_envelope_is_stable_and_does_not_expose_internal_message():
    secret = "sk-secret-value C:/private/project.txt"
    envelope = error_envelope(RuntimeError(secret), request_id="req-test", trace_id="trace-test")

    payload = envelope.to_dict()
    rendered = json.dumps(payload)
    assert payload == {
        "code": "internal_error",
        "category": "internal",
        "retryable": False,
        "degraded": False,
        "safe_detail": "Internal server error",
        "request_id": "req-test",
        "trace_id": "trace-test",
        "detail": "Internal server error",
    }
    assert secret not in rendered
    assert "private/project" not in rendered


def test_domain_and_provider_errors_preserve_machine_semantics_without_raw_detail():
    conflict = DomainError(
        "draft body must remain internal",
        code="draft_revision_conflict",
        category=ErrorCategory.CONFLICT,
        retryable=False,
    )
    conflict_payload = error_envelope(conflict, request_id="req", trace_id="trace").to_dict()
    assert conflict_payload["code"] == "draft_revision_conflict"
    assert conflict_payload["category"] == "conflict"
    assert "draft body" not in json.dumps(conflict_payload)

    provider = LLMError(
        "authorization: Bearer secret-provider-token",
        provider="deepseek",
        reason="non_retryable:invalid_api_key",
        is_retryable=False,
    )
    provider_payload = error_envelope(provider, request_id="req", trace_id="trace").to_dict()
    assert provider_payload["category"] == "authentication"
    assert provider_payload["code"] == "llm.non_retryable:invalid_api_key"
    assert "secret-provider-token" not in json.dumps(provider_payload)


@pytest.mark.asyncio
async def test_global_http_handler_returns_error_envelope_only():
    secret = "filesystem-secret-value"
    request = Request({"type": "http", "method": "GET", "path": "/test", "headers": []})
    response = await global_exception_handler(request, RuntimeError(secret))
    payload = json.loads(response.body)

    assert response.status_code == 500
    assert payload["code"] == "internal_error"
    assert payload["category"] == "internal"
    assert payload["request_id"]
    assert payload["trace_id"]
    assert secret not in response.body.decode("utf-8")


@pytest.mark.asyncio
async def test_durable_job_persists_safe_error_code(tmp_path: Path):
    queue = DurableTaskQueue(tmp_path)
    job = await queue.enqueue("test", {}, idempotency_key="w6-job")
    claimed = await queue.claim("worker", lease_seconds=30)
    assert claimed and claimed["id"] == job["id"]

    secret = "private-provider-response"
    assert await queue.fail(
        job["id"],
        "worker",
        RuntimeError(secret),
        generation=int(claimed["lease_generation"]),
        retry_delay=0,
    )
    persisted = queue.get(job["id"])
    assert persisted is not None
    assert persisted["last_error"] == "internal_error"
    assert secret not in json.dumps(persisted)


@pytest.mark.asyncio
async def test_tool_failure_is_traceable_but_content_free():
    class BrokenRetrieval:
        def schemas(self):
            return []

        async def execute(self, _name, _arguments):
            raise RuntimeError("secret retrieval payload")

    toolset = WritingActionToolset(retrieval_toolset=BrokenRetrieval())
    result = await toolset.execute("search", {})
    assert "tool_error" in result
    assert "trace_id=" in result
    assert "secret retrieval payload" not in result


def test_benchmark_failures_separate_infrastructure_policy_data_and_quality():
    infrastructure = benchmark_failure(TimeoutError("private prompt"))
    assert infrastructure["failure_scope"] == "infrastructure"
    assert infrastructure["counts_toward_quality"] is False

    policy = classify_benchmark_failure_record({"reason": "external_api_not_allowed"})
    data = classify_benchmark_failure_record({"reason": "missing_context_cases"})
    quality = classify_benchmark_failure_record({"reason": "too_short_candidate"})
    assert policy["failure_scope"] == "policy"
    assert data["failure_scope"] == "data"
    assert quality["failure_scope"] == "quality"
    assert quality["counts_toward_quality"] is True


def _contains_unsafe_exception_reference(node: ast.AST, name: str) -> bool:
    safe_functions = {
        "benchmark_failure",
        "classify_exception",
        "error_envelope",
        "safe_error_code",
        "tool_error_text",
    }
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in safe_functions:
        return False
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "_handle_error":
        return False
    if isinstance(node, ast.Name):
        return node.id == name
    return any(
        _contains_unsafe_exception_reference(child, name)
        for child in ast.iter_child_nodes(node)
    )


def test_caught_exceptions_are_not_written_directly_to_external_sinks():
    app_root = Path(__file__).resolve().parents[1] / "app"
    violations: list[str] = []
    sink_methods = {"append", "extend", "send_json", "send_text", "write_text", "write_bytes"}

    for path in app_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for handler in (node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler) and node.name):
            for node in ast.walk(handler):
                is_sink = isinstance(node, ast.Return)
                is_sink = is_sink or (
                    isinstance(node, (ast.Assign, ast.AnnAssign))
                    and any(isinstance(target, (ast.Attribute, ast.Subscript)) for target in getattr(node, "targets", []))
                )
                is_sink = is_sink or (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in sink_methods
                )
                if is_sink and _contains_unsafe_exception_reference(node, handler.name):
                    violations.append(f"{path.relative_to(app_root)}:{getattr(node, 'lineno', 0)}")

    assert violations == []


def test_error_contract_static_audit_has_no_silent_broad_catches():
    app_root = Path(__file__).resolve().parents[1] / "app"
    assert audit(app_root) == {"unsafe_external_sinks": [], "silent_broad_catches": []}
