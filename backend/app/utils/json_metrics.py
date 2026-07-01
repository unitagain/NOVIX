# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  Phase 9 · 结构化输出成功率指标。
  轻量进程内全局计数器：记录每个 agent 的 JSON 解析成功/失败次数，
  用于监控『response_format / 工具结构化输出』的真实质量与 parse fallback 命中率。
  JSON structured-output success-rate metrics (in-process counters).
"""

from __future__ import annotations

from typing import Dict

# {agent_name: {"success": int, "fail": int}}
_counters: Dict[str, Dict[str, int]] = {}


def record_json_result(agent: str, success: bool) -> None:
    """记录一次 JSON 结构化调用的解析结果（成功 or 走了 fallback 仍失败）。"""
    name = str(agent or "unknown")
    bucket = _counters.setdefault(name, {"success": 0, "fail": 0})
    bucket["success" if success else "fail"] += 1


def json_metrics_snapshot() -> Dict[str, Dict[str, float]]:
    """各 agent 成功率快照：{agent: {success, fail, total, success_rate}}（无数据时 rate=1.0）。"""
    out: Dict[str, Dict[str, float]] = {}
    for agent, bucket in _counters.items():
        success = int(bucket.get("success", 0))
        fail = int(bucket.get("fail", 0))
        total = success + fail
        out[agent] = {
            "success": success,
            "fail": fail,
            "total": total,
            "success_rate": round(success / total, 4) if total else 1.0,
        }
    return out


def reset_json_metrics() -> None:
    """清空计数器（测试用）。"""
    _counters.clear()
