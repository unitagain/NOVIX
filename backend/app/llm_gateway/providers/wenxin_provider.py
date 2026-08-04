"""
Wenxin (Baidu Qianfan) Provider / 文心（百度千帆）适配器
Compatible with OpenAI API / 兼容 OpenAI API
"""

from app.llm_gateway.providers.custom_provider import CustomProvider


class WenxinProvider(CustomProvider):
    """Baidu Qianfan Wenxin provider / 百度千帆文心提供商"""

    def __init__(
        self,
        api_key: str,
        base_url: str = None,
        model: str = "ernie-5.0",
        max_tokens: int = 8000,
        temperature: float = 0.7,
    ):
        # Official OpenAI-compatible endpoint for Qianfan
        if not base_url:
            base_url = "https://qianfan.baidubce.com/v2"

        super().__init__(api_key, base_url, model, max_tokens, temperature)

    def get_provider_name(self) -> str:
        return "wenxin"
