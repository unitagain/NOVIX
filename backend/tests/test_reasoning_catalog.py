from app.llm_gateway.model_catalog import fallback_models, filter_mainstream_text_models, reasoning_capability
from app.llm_gateway.thinking import build_reasoning_param


def test_current_fallbacks_exclude_known_stale_models():
    assert "gpt-4o" not in fallback_models("openai")
    assert "claude-sonnet-4-6" not in fallback_models("anthropic")
    assert "gpt-5.6-terra" in fallback_models("openai")


def test_runtime_models_filter_non_text_and_deprecated_entries():
    assert filter_mainstream_text_models(
        ["gemini-3.5-flash", "gemini-image", "legacy-chat", "text-embedding-3-large"]
    ) == ["gemini-3.5-flash"]


def test_openai_reasoning_levels_and_translation():
    capability = reasoning_capability("openai", "gpt-5.6-terra")
    assert capability["levels"] == ["auto", "off", "low", "medium", "high", "xhigh", "max"]
    assert build_reasoning_param("openai", "gpt-5.6-terra", "max") == {"reasoning_effort": "max"}
    assert build_reasoning_param("openai", "gpt-5.6-terra", "off") == {"reasoning_effort": "none"}


def test_provider_specific_reasoning_translation():
    assert build_reasoning_param("anthropic", "claude-sonnet-5", "high") == {
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "high"},
    }
    assert build_reasoning_param("deepseek", "deepseek-v4-pro", "max") == {
        "thinking": {"type": "enabled"},
        "reasoning_effort": "max",
    }
    assert build_reasoning_param("qwen", "qwen3.7-plus", "off") == {"enable_thinking": False}
    assert build_reasoning_param("kimi", "kimi-k2.6", "high") == {"thinking": {"type": "enabled"}}


def test_forced_reasoning_models_do_not_offer_off():
    assert reasoning_capability("grok", "grok-4.5")["can_disable"] is False
    assert "off" not in reasoning_capability("anthropic", "claude-mythos-5")["levels"]


def test_custom_provider_defaults_to_openai_chat_dialect():
    capability = reasoning_capability("custom", "vendor-model")
    assert capability["dialect"] == "openai_chat"
    assert build_reasoning_param("custom", "vendor-model", "medium") == {"reasoning_effort": "medium"}
