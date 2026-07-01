# -*- coding: utf-8 -*-
"""Thinking 能力表回归测试：supports_thinking / build_thinking_param / drops_temperature。
覆盖参数级切换厂商（anthropic/openai/gemini/qwen/glm/grok）+ DeepSeek 不在表（靠换模型）+ Haiku 排除。
"""

from app.llm_gateway.thinking import supports_thinking, build_thinking_param, drops_temperature


def test_anthropic_thinking_models_supported_haiku_excluded():
    assert supports_thinking("anthropic", "claude-opus-4-8") is True
    assert supports_thinking("anthropic", "claude-sonnet-4-6") is True
    assert supports_thinking("anthropic", "claude-haiku-4-5") is False  # Haiku 不支持扩展思考


def test_deepseek_not_param_toggle():
    # DeepSeek 靠换模型（chat ↔ reasoner），不在参数级表内 → 无按钮
    assert supports_thinking("deepseek", "deepseek-reasoner") is False
    assert supports_thinking("deepseek", "deepseek-chat") is False


def test_param_toggle_providers_supported():
    assert supports_thinking("openai", "gpt-5.5-thinking") is True
    assert supports_thinking("openai", "o3") is True
    assert supports_thinking("qwen", "qwen3.5-plus") is True
    assert supports_thinking("glm", "glm-5.2") is True
    assert supports_thinking("grok", "grok-4.3") is True
    assert supports_thinking("gemini", "gemini-2.5-flash") is True


def test_unknown_provider_or_model_unsupported():
    assert supports_thinking("kimi", "kimi-k2") is False  # 未列入（疑似型号式）
    assert supports_thinking("openai", "gpt-4o") is False  # 非推理型号
    assert supports_thinking("", "") is False


def test_build_param_anthropic_has_budget():
    p = build_thinking_param("anthropic", "claude-opus-4-8")
    assert p == {"type": "enabled", "budget_tokens": 8000}


def test_build_param_openai_reasoning_effort():
    assert build_thinking_param("openai", "o3") == {"reasoning_effort": "high"}


def test_build_param_qwen_enable_thinking():
    assert build_thinking_param("qwen", "qwen3.5-plus") == {"enable_thinking": True}


def test_build_param_glm_thinking_block():
    assert build_thinking_param("glm", "glm-5.2") == {"thinking": {"type": "enabled"}}


def test_build_param_grok_reasoning():
    assert build_thinking_param("grok", "grok-4") == {"reasoning": {"effort": "high"}}


def test_build_param_unsupported_returns_none():
    assert build_thinking_param("deepseek", "deepseek-reasoner") is None
    assert build_thinking_param("anthropic", "claude-haiku-4-5") is None


def test_drops_temperature_only_anthropic_openai():
    assert drops_temperature("anthropic") is True
    assert drops_temperature("openai") is True
    assert drops_temperature("qwen") is False
    assert drops_temperature("glm") is False
    assert drops_temperature("grok") is False
