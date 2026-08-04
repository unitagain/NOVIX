"""Regressions for agentic writing robustness and trace serialization.

覆盖用户报告的 trace 序列化问题：
1. `RequestDeadline is not JSON serializable`：gateway 把活对象注入 model_request 记录，
   经 turn_trace / trace 事件序列化时崩溃（订阅者 to_json 报 WARNING）。
"""

from __future__ import annotations

import json

from app.context_engine.trace_collector import _json_safe_trace_data
from app.context_engine.turn_scope import TurnTrace
from app.llm_gateway.reliability import RequestDeadline


def test_turn_trace_strips_internal_deadline_and_serializes():
    trace = TurnTrace(turn_id="t1", trace_id="tr1")
    trace.model_requests.append(
        {
            "request_id": "r1",
            "provider": "deepseek",
            "input_tokens": 10,
            "_deadline": RequestDeadline(total_seconds=30.0),  # 活对象：不可 JSON 序列化
        }
    )
    payload = trace.to_dict()
    request_view = payload["model_requests"][0]
    assert "_deadline" not in request_view
    assert request_view["request_id"] == "r1"
    # 整体可被 json 序列化，不抛异常。
    assert json.loads(json.dumps(payload))["model_requests"][0]["provider"] == "deepseek"


def test_json_safe_trace_data_drops_private_keys_and_live_objects():
    data = {
        "reason": "retry",
        "_deadline": RequestDeadline(total_seconds=5.0),
        "nested": {"_deadline": RequestDeadline(total_seconds=5.0), "ok": 1},
        "items": [{"_x": object()}, {"keep": 2}],
    }
    safe = _json_safe_trace_data(data)
    assert "_deadline" not in safe
    assert "_deadline" not in safe["nested"] and safe["nested"]["ok"] == 1
    assert safe["items"][0] == {} and safe["items"][1]["keep"] == 2
    # 结果必然 JSON 可序列化。
    json.dumps(safe)
