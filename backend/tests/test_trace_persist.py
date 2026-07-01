# -*- coding: utf-8 -*-
"""Phase 15 · trace 落盘回归测试。

验证 trace_collector.save_trace 把事件 + agent 追踪落盘为可复用 JSON。无网络 / 无 key。
"""

import asyncio
import json

from app.context_engine.trace_collector import trace_collector


def test_save_trace_writes_json(tmp_path):
    path = tmp_path / "sub" / "trace.json"  # 顺带验证自动建目录
    ok = asyncio.run(trace_collector.save_trace(str(path)))
    assert ok
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "events" in data and "agent_traces" in data
    assert isinstance(data["events"], list)
