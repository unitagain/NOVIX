"""
Moonshot AI (Kimi) Provider / Kimi (月之暗面) 适配器
Compatible with OpenAI API / 兼容 OpenAI API
"""

from app.llm_gateway.providers.custom_provider import CustomProvider


class KimiProvider(CustomProvider):
    """Moonshot AI provider / Kimi 提供商"""

    def __init__(
        self,
        api_key: str,
        base_url: str = None,
        model: str = "moonshot-v1-8k",
        max_tokens: int = 8000,
        temperature: float = 0.7,
    ):
        # Default to Moonshot AI API
        if not base_url:
            base_url = "https://api.moonshot.cn/v1"

        super().__init__(api_key, base_url, model, max_tokens, temperature)

    def get_provider_name(self) -> str:
        return "kimi"
