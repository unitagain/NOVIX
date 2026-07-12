"""
中文说明：该模块为 WenShape 后端组成部分，详细行为见下方英文说明。

Proxy Router
Handles direct requests to LLM providers for configuration purposes (e.g., fetching models)
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from app.utils.logger import get_logger
from app.utils.openai_client import create_async_openai_client
from app.utils.anthropic_client import create_async_anthropic_client
from app.services.llm_config_service import llm_config_service

logger = get_logger(__name__)

router = APIRouter(prefix="/proxy", tags=["proxy"])


class FetchModelsRequest(BaseModel):
    provider: str
    api_key: Optional[str] = None
    profile_id: Optional[str] = None
    base_url: Optional[str] = None


class TestModelRequest(BaseModel):
    provider: str
    api_key: Optional[str] = None
    profile_id: Optional[str] = None
    model: str
    base_url: Optional[str] = None


def _default_base_url_for_provider(provider: str) -> Optional[str]:
    provider = str(provider or "").strip().lower()
    if provider == "openai":
        return "https://api.openai.com/v1"
    if provider == "deepseek":
        return "https://api.deepseek.com/v1"
    if provider == "qwen":
        return "https://dashscope.aliyuncs.com/compatible-mode/v1"
    if provider == "kimi":
        return "https://api.moonshot.cn/v1"
    if provider == "glm":
        return "https://open.bigmodel.cn/api/paas/v4"
    if provider == "gemini":
        return "https://generativelanguage.googleapis.com/v1beta/openai/"
    if provider == "grok":
        return "https://api.x.ai/v1"
    if provider == "wenxin":
        return "https://qianfan.baidubce.com/v2"
    if provider == "aistudio":
        return "https://aistudio.baidu.com/llm/lmapi/v3"
    if provider == "anthropic":
        return "https://api.anthropic.com"
    return None


ANTHROPIC_FALLBACK_MODELS: List[str] = [
    # Fallback when model list fetch fails; keep small and stable.
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-sonnet-4-5",
    "claude-haiku-4-5",
]

GENERIC_FALLBACK_MODELS = {
    "openai": ["gpt-5.4-mini", "gpt-4o", "gpt-4.1"],
    "deepseek": ["deepseek-chat", "deepseek-reasoner"],
    "gemini": ["gemini-2.5-flash", "gemini-3.1-pro-preview"],
    "qwen": ["qwen3.5-plus", "qwen3-max", "qwen-turbo"],
    "kimi": ["kimi-k2.5", "kimi-k2-thinking"],
    "glm": ["glm-5", "glm-4.7"],
    "grok": ["grok-4", "grok-4.1-fast"],
    "wenxin": ["ernie-4.5-turbo-32k", "ernie-5.0"],
    "aistudio": ["ernie-5.0-thinking-preview", "ernie-5.0"],
    "custom": [],
}


def _resolve_api_key(api_key: Optional[str], profile_id: Optional[str]) -> str:
    supplied = str(api_key or "").strip()
    if supplied:
        return supplied
    if profile_id:
        profile = llm_config_service.get_profile_by_id(profile_id)
        stored = str((profile or {}).get("api_key") or "").strip()
        if stored:
            return stored
    raise HTTPException(status_code=400, detail="API key is required")


def _fallback_models_for_provider(provider: str) -> List[str]:
    normalized = str(provider or "").strip().lower()
    if normalized == "anthropic":
        return ANTHROPIC_FALLBACK_MODELS
    return GENERIC_FALLBACK_MODELS.get(normalized, [])


@router.post("/fetch-models")
async def fetch_models(request: FetchModelsRequest):
    """
    Fetch available models from the provider
    """
    try:
        provider = str(request.provider or "").strip().lower()
        base_url = (request.base_url or "").strip() or None
        api_key = _resolve_api_key(request.api_key, request.profile_id)

        # Anthropic: use official SDK Models API (not OpenAI-compatible /v1/models).
        # Always return from this branch to avoid fallthrough to OpenAI client.
        if provider == "anthropic":
            try:
                logger.debug("Fetch Models Debug: Provider=anthropic, BaseURL=%s", base_url or "(default)")
                client = create_async_anthropic_client(api_key=api_key, base_url=base_url)

                paginator = client.models.list(limit=200)
                model_ids: List[str] = []
                async for model in paginator:
                    model_id = getattr(model, "id", None)
                    if model_id:
                        model_ids.append(str(model_id))
                    if len(model_ids) >= 200:
                        break

                if model_ids:
                    logger.info("Fetch Models Success: Found %s models (anthropic)", len(model_ids))
                    return {"models": sorted(set(model_ids))}

                logger.warning("Fetch Models Warning (anthropic): empty models list")
                return {
                    "models": ANTHROPIC_FALLBACK_MODELS,
                    "warning": "Anthropic model list empty, returning built-in fallback list.",
                }
            except Exception as e:
                logger.warning("Fetch Models Error (anthropic): %s", str(e))
                return {
                    "models": ANTHROPIC_FALLBACK_MODELS,
                    "warning": "Anthropic model list fetch failed; using the built-in fallback list.",
                }

        # Determine base URL based on provider if not provided
        if not base_url:
            base_url = _default_base_url_for_provider(provider)

        # Initialize temp client
        # Note: Some providers might not implement /v1/models correctly.
        logger.debug("Fetch Models Debug: Provider=%s, BaseURL=%s", provider, base_url)

        client = create_async_openai_client(
            api_key=api_key,
            base_url=base_url,
        )

        models_response = await client.models.list()

        # Extract model IDs
        model_ids = [m.id for m in models_response.data]
        logger.info("Fetch Models Success: Found %s models", len(model_ids))
        return {"models": sorted(model_ids)}

    except Exception as e:
        logger.warning("Fetch Models Error: %s", str(e))
        fallback_models = _fallback_models_for_provider(request.provider)
        if fallback_models:
            return {
                "models": fallback_models,
                "warning": "Model list fetch failed; using the built-in fallback list.",
            }
        status_code = getattr(e, "status_code", None) or 400
        raise HTTPException(status_code=status_code, detail="Provider model list request failed")


@router.post("/test-model")
async def test_model(request: TestModelRequest):
    """
    Test whether provider config and selected model are usable.
    """
    try:
        provider = str(request.provider or "").strip().lower()
        model = str(request.model or "").strip()
        if not model:
            raise HTTPException(status_code=400, detail="Model is required.")

        base_url = (request.base_url or "").strip() or _default_base_url_for_provider(provider)
        api_key = _resolve_api_key(request.api_key, request.profile_id)

        if provider == "anthropic":
            client = create_async_anthropic_client(api_key=api_key, base_url=base_url)
            response = await client.messages.create(
                model=model,
                max_tokens=16,
                temperature=0.0,
                messages=[{"role": "user", "content": "Reply with OK only."}],
            )
            content = ""
            if hasattr(response, "content") and response.content:
                first = response.content[0]
                content = getattr(first, "text", "") or ""
            return {"success": True, "provider": provider, "model": model, "message": content or "OK"}

        client = create_async_openai_client(api_key=api_key, base_url=base_url)
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Reply with OK only."}],
                temperature=0.0,
                max_tokens=16,
            )
        except Exception as first_error:
            # Some OpenAI-compatible providers may require max_completion_tokens.
            err = str(first_error).lower()
            if "max_tokens" in err:
                response = await client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": "Reply with OK only."}],
                    temperature=0.0,
                    max_completion_tokens=16,
                )
            else:
                raise

        content = ""
        if hasattr(response, "choices") and response.choices:
            content = response.choices[0].message.content or ""
        return {"success": True, "provider": provider, "model": model, "message": content or "OK"}
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Test Model Error: %s", str(e))
        status_code = getattr(e, "status_code", None) or 400
        raise HTTPException(status_code=status_code, detail="Provider model test failed")
