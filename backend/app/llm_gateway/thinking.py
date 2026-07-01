# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  Thinking 能力表 + 参数构建（配置驱动 · 能力降级）。
  判定某 provider+model 是否支持「请求参数级」thinking 切换，并构建开启时要合并进请求的参数 dict
  （经各 provider 既有的 body.update(thinking) / kwargs["thinking"] 注入，无需改 provider）。

  设计红线：**只覆盖能用请求参数切换 thinking 的厂商**（Anthropic / OpenAI / Gemini / Qwen / GLM / Grok）；
  DeepSeek 等「靠换模型（deepseek-chat ↔ deepseek-reasoner）」的不在此列 —— 用户规则「需要换模型的不做」。
  内置默认表来自各厂商 2026 文档，可被 `config.yaml` 的 `thinking.providers` 覆盖/扩展（参数会变，配置改值即可）。

  Thinking capability map: decide if a provider+model supports a request-parameter thinking toggle, and build
  the dict to merge into the request when enabled. Param-toggle providers only (no model-switching ones).
"""

from typing import Any, Dict, Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)

# 内置默认：provider 名 → {models: 型号前缀列表, param: 开启时合并进请求的 dict}。
# anthropic 的 param 运行时补 budget_tokens；其余为静态 dict（OpenAI 兼容族经 body.update 合并到请求体顶层）。
_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "anthropic": {
        "models": ["claude-opus-4", "claude-sonnet-4", "claude-mythos"],  # Haiku 不支持
        "param": {"type": "enabled"},  # → {"type":"enabled","budget_tokens":N}
    },
    "openai": {
        "models": ["o1", "o3", "o4", "gpt-5"],  # o 系列 + GPT-5 系（含 thinking）
        "param": {"reasoning_effort": "high"},
    },
    "gemini": {
        "models": ["gemini-2.5", "gemini-3"],  # 2.5+ 原生思考；OpenAI 兼容端点用 reasoning_effort
        "param": {"reasoning_effort": "high"},
    },
    "qwen": {
        "models": ["qwen3", "qwen-max", "qwen-plus"],  # 混合推理
        "param": {"enable_thinking": True},
    },
    "glm": {
        "models": ["glm-5", "glm-4.7", "glm-4.6", "glm-4.5", "glm-z1"],
        "param": {"thinking": {"type": "enabled"}},
    },
    "grok": {
        "models": ["grok-3", "grok-4"],  # Grok 3+ 支持推理
        "param": {"reasoning": {"effort": "high"}},
    },
}

# 开启 thinking 时需丢弃 temperature 的 provider（扩展思考 / 推理模型不接受采样温度）。
_DROP_TEMPERATURE = {"anthropic", "openai"}

_DEFAULT_BUDGET_TOKENS = 8000


def _table() -> Dict[str, Dict[str, Any]]:
    """内置默认 ∪ config.yaml `thinking.providers` 覆盖（config 优先；参数会变，用户改配置即可）。"""
    override: Dict[str, Any] = {}
    try:
        from app.config import config as _cfg

        override = (_cfg.get("thinking", {}) or {}).get("providers", {}) or {}
    except Exception:
        override = {}
    merged: Dict[str, Dict[str, Any]] = {k: dict(v) for k, v in _DEFAULTS.items()}
    for prov, spec in override.items():
        if isinstance(spec, dict):
            merged[str(prov).lower()] = spec
    return merged


def _budget_tokens() -> int:
    try:
        from app.config import config as _cfg

        return int((_cfg.get("thinking", {}) or {}).get("budget_tokens", _DEFAULT_BUDGET_TOKENS))
    except Exception:
        return _DEFAULT_BUDGET_TOKENS


def supports_thinking(provider_name: str, model: str) -> bool:
    """provider+model 是否支持「请求参数级」thinking 切换（型号前缀匹配）。"""
    prov = str(provider_name or "").lower()
    mdl = str(model or "").lower()
    spec = _table().get(prov)
    if not spec:
        return False
    return any(mdl.startswith(str(p).lower()) for p in (spec.get("models") or []))


def build_thinking_param(provider_name: str, model: str) -> Optional[Dict[str, Any]]:
    """构建开启 thinking 时传给 ``gateway.chat(thinking=...)`` 的 dict；不支持则 None（能力降级）。

    anthropic 补 budget_tokens（→ ``{"type":"enabled","budget_tokens":N}``）；其余返回静态 param
    （openai ``reasoning_effort`` / qwen ``enable_thinking`` / glm ``thinking`` / grok ``reasoning``）。
    """
    prov = str(provider_name or "").lower()
    if not supports_thinking(prov, model):
        return None
    spec = _table().get(prov) or {}
    param = dict(spec.get("param") or {})
    if prov == "anthropic":
        param["budget_tokens"] = _budget_tokens()
    return param


def drops_temperature(provider_name: str) -> bool:
    """开启 thinking 时该 provider 是否必须丢弃 temperature。"""
    return str(provider_name or "").lower() in _DROP_TEMPERATURE
