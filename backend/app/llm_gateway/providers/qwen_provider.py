"""
Qwen (Tongyi Qianwen) Provider / 通义千问适配器
Compatible with OpenAI API / 兼容 OpenAI API
"""

from app.llm_gateway.providers.custom_provider import CustomProvider


class QwenProvider(CustomProvider):
    """Qwen API provider via Alibaba Cloud / 通义千问提供商"""

    def __init__(
        self,
        api_key: str,
        base_url: str = None,
        model: str = "qwen-turbo",
        max_tokens: int = 8000,
        temperature: float = 0.7,
    ):
        # Default to Alibaba Cloud's OpenAI-compatible endpoint
        # 默认为阿里云的 OpenAI 兼容端点
        if not base_url:
            base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"

        super().__init__(api_key, base_url, model, max_tokens, temperature)

    def get_provider_name(self) -> str:
        return "qwen"
