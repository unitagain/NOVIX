from typing import List, Dict, Any, Optional
from app.llm_gateway.providers.base import BaseLLMProvider


class MockProvider(BaseLLMProvider):
    def __init__(self):
        super().__init__(api_key="", model="mock", max_tokens=2000, temperature=0.0)

    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        prompt = "\n".join([f"{m.get('role')}: {m.get('content')}" for m in messages])
        content = (
            "[MOCK] NOVIX is running in demo mode.\n\n"
            "No API key is configured, so this is a placeholder response.\n\n"
            "---\n"
            "Input:\n"
            f"{prompt}\n"
        )

        return {
            "content": content,
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
            "model": self.model,
            "finish_reason": "stop",
        }

    def get_provider_name(self) -> str:
        return "mock"
