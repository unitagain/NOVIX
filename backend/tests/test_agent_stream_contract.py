# -*- coding: utf-8 -*-
"""U5 · agent WS 事件协议契约测试。

冻结 `WritingService._emit_agent_event` 的出站 payload：agentic 循环已把
`ToolExecutionResult.to_dict()` 放进事件的 `tool_result` 键，WS 出口必须把执行状态
透传给前端（此前只留 name/result 预览，前端无从分层）。

契约边界（本文件断言，改动前先读 plan.md §9.3）：
  - 四个新字段全部来自 `event["tool_result"]`，不新增计算逻辑；
  - `error_code` 只取 `error["code"]`，明文 message 不出站；
  - payload 不含正文、prompt 或工具输出全文（`result` 仍为既有 preview 截断）；
  - 缺 `tool_result` 键时降级为成功态，不抛异常。
无网络、无 LLM。
"""

import asyncio

import pytest

from app.context_engine.tool_artifact import ToolExecutionResult, ToolExecutionStatus
from app.orchestrator.writing_service import WritingService


def _service(sink):
    """只装配 _emit_agent_event 所需依赖；其余端口本用例不触达。"""

    async def progress_callback(payload):
        sink.append(payload)

    return WritingService(
        gateway=object(),
        writer=object(),
        draft_storage=object(),
        storage_adapter=object(),
        select_engine=object(),
        context_assembly=object(),
        progress_callback=progress_callback,
    )


def _emit(event):
    sink = []
    asyncio.run(_service(sink)._emit_agent_event("p", "V1C001", event))
    return sink


def _execution(**overrides):
    base = {
        "tool_call_id": "call-1",
        "tool_name": "query_canon",
        "status": ToolExecutionStatus.SUCCEEDED.value,
        "output_preview": "preview",
        "artifact_ref": "artifact://a1",
        "output_hash": "h1",
        "error": None,
        "elapsed_ms": 412,
        "recoverable": True,
    }
    base.update(overrides)
    return ToolExecutionResult(**base).to_dict()


def test_tool_result_payload_carries_execution_state():
    payload = _emit(
        {
            "type": "tool_result",
            "tool_call_id": "call-1",
            "name": "query_canon",
            "result": "preview",
            "tool_result": _execution(),
        }
    )[0]

    assert payload["type"] == "agent_tool_result"
    assert payload["status"] == "succeeded"
    assert payload["error_code"] is None
    assert payload["elapsed_ms"] == 412
    assert payload["recoverable"] is True


@pytest.mark.parametrize(
    "status",
    [
        ToolExecutionStatus.FAILED.value,
        ToolExecutionStatus.TIMED_OUT.value,
        ToolExecutionStatus.CANCELLED.value,
    ],
)
def test_tool_result_payload_carries_each_terminal_status(status):
    payload = _emit(
        {
            "type": "tool_result",
            "tool_call_id": "call-1",
            "name": "read_chapter",
            "result": "",
            "tool_result": _execution(status=status, recoverable=False),
        }
    )[0]

    assert payload["status"] == status
    assert payload["recoverable"] is False


def test_error_payload_exposes_code_only():
    """error 明文 message 可能含正文片段，只允许结构化 code 出站。"""
    payload = _emit(
        {
            "type": "tool_result",
            "tool_call_id": "call-1",
            "name": "read_chapter",
            "result": "",
            "tool_result": _execution(
                status=ToolExecutionStatus.FAILED.value,
                error={"code": "chapter_not_found", "message": "章节 V1C999 不存在：林清越走出了房间"},
                recoverable=False,
            ),
        }
    )[0]

    assert payload["error_code"] == "chapter_not_found"
    assert "error" not in payload
    assert "林清越" not in str(payload)


def test_payload_keys_are_frozen():
    """出站键集合固定：新增键必须先更新协议契约（plan.md §9.3）。"""
    payload = _emit(
        {
            "type": "tool_result",
            "tool_call_id": "call-1",
            "name": "query_canon",
            "result": "preview",
            "tool_result": _execution(),
        }
    )[0]

    assert set(payload) == {
        "type",
        "project_id",
        "chapter",
        "turn_id",
        "tool_call_id",
        "name",
        "result",
        "status",
        "error_code",
        "elapsed_ms",
        "recoverable",
    }


def test_result_preview_is_truncated():
    payload = _emit(
        {
            "type": "tool_result",
            "tool_call_id": "call-1",
            "name": "search_prose",
            "result": "正" * 5000,
            "tool_result": _execution(),
        }
    )[0]

    assert len(payload["result"]) == 1000


def test_missing_tool_result_key_degrades_without_raising():
    """老事件或异常路径无 tool_result 键时降级为成功态，不得抛异常。"""
    payload = _emit(
        {
            "type": "tool_result",
            "tool_call_id": "call-1",
            "name": "query_canon",
            "result": "preview",
        }
    )[0]

    assert payload["status"] == "succeeded"
    assert payload["error_code"] is None
    assert payload["elapsed_ms"] == 0
    assert payload["recoverable"] is False


def test_non_dict_tool_result_degrades_without_raising():
    payload = _emit(
        {
            "type": "tool_result",
            "tool_call_id": "call-1",
            "name": "query_canon",
            "result": "preview",
            "tool_result": "unexpected",
        }
    )[0]

    assert payload["status"] == "succeeded"
    assert payload["elapsed_ms"] == 0


def test_tool_call_payload_is_unchanged():
    """PR-1 只改 tool_result 分支；tool_call 的脱敏 owner 不动。"""
    payload = _emit(
        {
            "type": "tool_call",
            "tool_call_id": "call-1",
            "name": "write_content",
            "arguments": {"mode": "replace", "content": "正文" * 100},
        }
    )[0]

    assert payload["type"] == "agent_tool_call"
    assert payload["arguments"] == {"mode": "replace", "content_chars": 200}
    assert "正文" not in str(payload["arguments"])
