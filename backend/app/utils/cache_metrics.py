# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  Phase 13 · prompt caching 命中率指标。
  轻量进程内计数器：累计 Anthropic 缓存读取 / 创建 / 未缓存输入 token，
  算出缓存命中率（cache_read 占总输入 token 的比例），用于监控分层缓存的真实收益。
  仅 Anthropic 返回 cache_*_input_tokens；其余 provider 不计。
"""

from __future__ import annotations

from typing import Dict

_stats = {"cache_read_tokens": 0, "cache_creation_tokens": 0, "uncached_input_tokens": 0, "calls": 0}


def record_cache(read_tokens: int, creation_tokens: int, input_tokens: int = 0) -> None:
    """记录一次调用的缓存读取 / 创建 / 未缓存输入 token。"""
    _stats["cache_read_tokens"] += int(read_tokens or 0)
    _stats["cache_creation_tokens"] += int(creation_tokens or 0)
    _stats["uncached_input_tokens"] += int(input_tokens or 0)
    _stats["calls"] += 1


def cache_metrics_snapshot() -> Dict[str, float]:
    """缓存命中率快照：hit_rate = 缓存读取 token / 总输入 token（越高越省）。"""
    read = _stats["cache_read_tokens"]
    creation = _stats["cache_creation_tokens"]
    uncached = _stats["uncached_input_tokens"]
    total_input = read + creation + uncached
    return {
        "cache_read_tokens": read,
        "cache_creation_tokens": creation,
        "uncached_input_tokens": uncached,
        "calls": _stats["calls"],
        "hit_rate": round(read / total_input, 4) if total_input else 0.0,
    }


def reset_cache_metrics() -> None:
    """清空计数器（测试用）。"""
    for key in _stats:
        _stats[key] = 0
