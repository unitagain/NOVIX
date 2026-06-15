"""
xAI Grok Provider / Grok 适配器
Compatible with OpenAI API / 兼容 OpenAI API
"""

from app.llm_gateway.providers.custom_provider import CustomProvider


class GrokProvider(CustomProvider):
    """xAI Grok provider"""

    def __init__(
        self,
        api_key: str,
        base_url: str = None,
        model: str = "grok-beta",
        max_tokens: int = 8000,
        temperature: float = 0.7,
    ):
        # Default to xAI API
        if not base_url:
            base_url = "https://api.x.ai/v1"

        super().__init__(api_key, base_url, model, max_tokens, temperature)

    def get_provider_name(self) -> str:
        return "grok"
