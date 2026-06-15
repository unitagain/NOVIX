"""
Zhipu AI (GLM) Provider / 智谱 GLM 适配器
Compatible with OpenAI API / 兼容 OpenAI API
"""

from app.llm_gateway.providers.custom_provider import CustomProvider


class GLMProvider(CustomProvider):
    """Zhipu AI provider / 智谱 GLM 提供商"""

    def __init__(
        self, api_key: str, base_url: str = None, model: str = "glm-4", max_tokens: int = 8000, temperature: float = 0.7
    ):
        # Default to Zhipu AI BigModel Platform
        if not base_url:
            base_url = "https://open.bigmodel.cn/api/paas/v4"

        super().__init__(api_key, base_url, model, max_tokens, temperature)

    def get_provider_name(self) -> str:
        return "glm"
