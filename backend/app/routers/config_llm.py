from pathlib import Path
from typing import Dict, Any, Optional, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from dotenv import load_dotenv
import os

import app.config as app_config
from app.llm_gateway import reset_gateway


router = APIRouter(prefix="/config", tags=["config"])


class LLMConfigStatus(BaseModel):
    providers: List[Dict[str, Any]]
    selected_provider: str
    default_provider: str
    agent_providers: Dict[str, str]
    agent_overrides: Dict[str, str]
    configured: Dict[str, bool]


class LLMConfigUpdate(BaseModel):
    provider: Optional[str] = Field(None, description="Provider: openai|anthropic|deepseek|mock")
    default_provider: Optional[str] = Field(None, description="Provider: openai|anthropic|deepseek|mock")
    agent_providers: Optional[Dict[str, str]] = None
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    deepseek_api_key: Optional[str] = None


AGENTS = ["archivist", "writer", "reviewer", "editor"]


def _is_real_key(value: str) -> bool:
    if not value:
        return False
    v = value.strip()
    # Treat template placeholders as not configured
    # 将示例占位 Key 视为未配置
    placeholders = [
        "sk-your-",
        "sk-ant-your-",
        "your-deepseek-key-here",
    ]
    return not any(v.startswith(p) for p in placeholders)


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_env_lines(env_path: Path) -> List[str]:
    if not env_path.exists():
        return []
    return env_path.read_text(encoding="utf-8").splitlines(keepends=False)


def _write_env_lines(env_path: Path, lines: List[str]) -> None:
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _upsert_env_vars(env_path: Path, updates: Dict[str, str]) -> None:
    lines = _read_env_lines(env_path)
    seen = set()
    new_lines: List[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            new_lines.append(line)
            continue

        key, _ = line.split("=", 1)
        key = key.strip()

        if key in updates:
            new_lines.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            new_lines.append(line)

    for k, v in updates.items():
        if k not in seen:
            new_lines.append(f"{k}={v}")

    _write_env_lines(env_path, new_lines)


@router.get("/llm", response_model=LLMConfigStatus)
async def get_llm_config_status():
    providers = [
        {"id": "openai", "label": "OpenAI", "requires_key": True},
        {"id": "anthropic", "label": "Anthropic (Claude)", "requires_key": True},
        {"id": "deepseek", "label": "DeepSeek", "requires_key": True},
        {"id": "mock", "label": "Mock (Demo)", "requires_key": False},
    ]

    default_provider = os.getenv("NOVIX_LLM_PROVIDER") or app_config.config.get("llm", {}).get("default_provider", "openai")

    agent_overrides: Dict[str, str] = {}
    agent_providers: Dict[str, str] = {}

    for agent in AGENTS:
        env_key = f"NOVIX_AGENT_{agent.upper()}_PROVIDER"
        override = os.getenv(env_key, "")
        agent_overrides[agent] = override

        if override:
            agent_providers[agent] = override
            continue

        # Keep agent_providers aligned with the actual effective selection logic:
        # when NOVIX_LLM_PROVIDER is set, it should be treated as the default.
        # Otherwise the frontend may think other providers (e.g. openai) are required and keep showing the setup modal.
        agent_providers[agent] = default_provider

    configured = {
        "openai": _is_real_key(app_config.settings.openai_api_key),
        "anthropic": _is_real_key(app_config.settings.anthropic_api_key),
        "deepseek": _is_real_key(app_config.settings.deepseek_api_key),
        "mock": True,
    }

    return {
        "providers": providers,
        "selected_provider": default_provider,
        "default_provider": default_provider,
        "agent_providers": agent_providers,
        "agent_overrides": agent_overrides,
        "configured": configured,
    }


@router.post("/llm")
async def update_llm_config(payload: LLMConfigUpdate):
    allowed = {"openai", "anthropic", "deepseek", "mock"}
    default_provider = payload.default_provider or payload.provider
    if not default_provider:
        raise HTTPException(status_code=400, detail="Provider is required")
    if default_provider not in allowed:
        raise HTTPException(status_code=400, detail="Invalid provider")

    updates: Dict[str, str] = {"NOVIX_LLM_PROVIDER": default_provider}

    agent_providers = payload.agent_providers or {}
    for agent, provider in agent_providers.items():
        if agent not in AGENTS:
            raise HTTPException(status_code=400, detail=f"Invalid agent: {agent}")
        if provider and provider not in allowed:
            raise HTTPException(status_code=400, detail=f"Invalid provider for {agent}")
        updates[f"NOVIX_AGENT_{agent.upper()}_PROVIDER"] = provider or ""

    if payload.openai_api_key is not None:
        updates["OPENAI_API_KEY"] = payload.openai_api_key
    if payload.anthropic_api_key is not None:
        updates["ANTHROPIC_API_KEY"] = payload.anthropic_api_key
    if payload.deepseek_api_key is not None:
        updates["DEEPSEEK_API_KEY"] = payload.deepseek_api_key

    env_path = _backend_root() / ".env"
    _upsert_env_vars(env_path, updates)

    load_dotenv(dotenv_path=env_path, override=True)
    app_config.reload_runtime_config()

    reset_gateway()

    try:
        import app.routers.session as session_router
        session_router._orchestrator = None
    except Exception:
        pass

    required_key_map = {
        "openai": app_config.settings.openai_api_key,
        "anthropic": app_config.settings.anthropic_api_key,
        "deepseek": app_config.settings.deepseek_api_key,
        "mock": "ok",
    }

    providers_to_check = {default_provider}
    for v in agent_providers.values():
        if v:
            providers_to_check.add(v)

    for p in providers_to_check:
        if p == "mock":
            continue
        if not required_key_map.get(p):
            raise HTTPException(status_code=400, detail=f"API key is required for provider: {p}")

    return {"success": True, "default_provider": default_provider, "agent_providers": agent_providers}
