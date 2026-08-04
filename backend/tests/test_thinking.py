# -*- coding: utf-8 -*-
"""2026 model-aware reasoning capability regression tests."""

from app.llm_gateway.thinking import build_thinking_param, drops_temperature, reasoning_param_enabled, supports_thinking


def test_current_anthropic_models_support_reasoning():
    assert supports_thinking("anthropic", "claude-opus-4-8") is True
    assert supports_thinking("anthropic", "claude-sonnet-5") is True
    assert supports_thinking("anthropic", "claude-haiku-4-5") is True


def test_current_reasoning_providers_supported():
    assert supports_thinking("openai", "gpt-5.6-terra") is True
    assert supports_thinking("deepseek", "deepseek-v4-pro") is True
    assert supports_thinking("qwen", "qwen3.7-plus") is True
    assert supports_thinking("glm", "glm-5.2") is True
    assert supports_thinking("grok", "grok-4.5") is True
    assert supports_thinking("gemini", "gemini-3.5-flash") is True
    assert supports_thinking("kimi", "kimi-k2.6") is True


def test_unknown_or_non_reasoning_models_unsupported():
    assert supports_thinking("openai", "gpt-4o") is False
    assert supports_thinking("grok", "grok-4.20-non-reasoning-latest") is False
    assert supports_thinking("kimi", "moonshot-v1-128k") is False
    assert supports_thinking("", "") is False


def test_build_param_anthropic_adaptive_effort():
    assert build_thinking_param("anthropic", "claude-opus-4-8") == {
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "high"},
    }


def test_build_param_openai_reasoning_effort():
    assert build_thinking_param("openai", "gpt-5.6-sol") == {"reasoning_effort": "high"}


def test_build_param_provider_specific_formats():
    assert build_thinking_param("deepseek", "deepseek-v4-pro") == {
        "thinking": {"type": "enabled"},
        "reasoning_effort": "high",
    }
    assert build_thinking_param("qwen", "qwen3.7-plus") == {"enable_thinking": True}
    assert build_thinking_param("glm", "glm-5.2") == {"thinking": {"type": "enabled"}}
    assert build_thinking_param("grok", "grok-4.5") == {"reasoning_effort": "high"}
    assert build_thinking_param("kimi", "kimi-k2.6") == {"thinking": {"type": "enabled"}}


def test_build_param_unsupported_returns_none():
    assert build_thinking_param("openai", "gpt-4o") is None
    assert build_thinking_param("grok", "grok-4.20-non-reasoning-latest") is None


def test_temperature_drop_matches_reasoning_api_constraints():
    assert drops_temperature("anthropic") is True
    assert drops_temperature("openai") is True
    assert drops_temperature("deepseek") is True
    assert drops_temperature("kimi") is True
    assert drops_temperature("qwen") is False
    assert drops_temperature("glm") is False
    assert drops_temperature("grok") is False


def test_reasoning_param_enabled_recognizes_explicit_disable_formats():
    assert reasoning_param_enabled(None) is False
    assert reasoning_param_enabled({"enable_thinking": False}) is False
    assert reasoning_param_enabled({"reasoning_effort": "none"}) is False
    assert reasoning_param_enabled({"type": "disabled"}) is False
    assert reasoning_param_enabled({"thinking": {"type": "disabled"}}) is False
    assert reasoning_param_enabled({"reasoning": {"effort": "off"}}) is False


def test_reasoning_param_enabled_recognizes_enabled_formats():
    assert reasoning_param_enabled({"enable_thinking": True}) is True
    assert reasoning_param_enabled({"reasoning_effort": "high"}) is True
    assert reasoning_param_enabled({"thinking": {"type": "enabled"}}) is True
