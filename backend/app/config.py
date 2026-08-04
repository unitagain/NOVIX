# -*- coding: utf-8 -*-
"""
中文说明：该模块为 WenShape 后端组成部分，详细行为见下方英文说明。

WenShape runtime configuration utilities.

This module intentionally avoids import-time side effects:
- settings are loaded lazily via `get_settings()`
- YAML config is loaded lazily via `get_config()`
- `reload_runtime_config()` only clears caches
"""

from __future__ import annotations

import logging
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterator, Mapping

import yaml
from dotenv import load_dotenv
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

_TRUE_VALUES = {"1", "true", "yes", "on", "debug", "dev", "development"}
_FALSE_VALUES = {"0", "false", "no", "off", "release", "prod", "production", "test"}


def _load_environment() -> None:
    """Load `.env` from the correct runtime location once per process.

    Search order (first existing file wins):
    1. ``WENSHAPE_DESKTOP_ENV_DIR/.env`` (set by the Electron shell,
       points at the user-writable config directory)
    2. ``.env`` next to the frozen executable
    3. ``.env`` inside ``_MEIPASS`` (bundled default)
    4. Standard ``python-dotenv`` discovery (development)
    """
    if getattr(sys, "frozen", False):
        candidates = []

        desktop_env_dir = os.getenv("WENSHAPE_DESKTOP_ENV_DIR", "").strip()
        if desktop_env_dir:
            candidates.append(Path(desktop_env_dir) / ".env")

        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / ".env")

        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / ".env")

        for env_path in candidates:
            if env_path.exists():
                load_dotenv(dotenv_path=env_path)
                return
        # No .env found — proceed without one (env vars may still be set)
        return

    load_dotenv()


def _resolve_data_dir() -> str:
    """Resolve the shared data directory for both source and packaged modes.

    Priority:
    1. ``DATA_DIR`` env var (set by the Electron desktop shell)
    2. ``<exe-parent>/data`` when running as a frozen PyInstaller bundle
    3. ``<project-root>/data`` during development
    """
    env_data_dir = os.getenv("DATA_DIR", "").strip()
    if env_data_dir:
        return str(Path(env_data_dir).expanduser().resolve())

    if getattr(sys, "frozen", False):
        return str((Path(sys.executable).resolve().parent / "data").resolve())

    project_root = Path(__file__).resolve().parents[2]
    return str((project_root / "data").resolve())


def _config_root() -> Path:
    """Return the directory that contains `config.yaml`.

    In frozen (PyInstaller) mode, bundled data files live inside the
    temporary ``_MEIPASS`` directory, not next to the executable.
    """
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    openai_api_key: str = ""
    anthropic_api_key: str = ""
    deepseek_api_key: str = ""
    gemini_api_key: str = ""

    custom_api_key: str = ""
    custom_base_url: str = ""
    custom_model_name: str = ""

    openai_model: str = "gpt-5.6-terra"
    anthropic_model: str = "claude-sonnet-5"
    deepseek_model: str = "deepseek-v4-flash"
    gemini_model: str = "gemini-2.5-flash"

    wenshape_llm_provider: str = ""
    wenshape_agent_writer_provider: str = ""

    data_dir: str = _resolve_data_dir()

    @field_validator("debug", mode="before")
    @classmethod
    def _parse_debug_flag(cls, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False

        normalized = str(value).strip().lower()
        if normalized in _TRUE_VALUES:
            return True
        if normalized in _FALSE_VALUES:
            return False
        return False

    @field_validator("data_dir", mode="before")
    @classmethod
    def _normalize_data_dir(cls, value: Any) -> str:
        if not value:
            return _resolve_data_dir()
        return str(Path(str(value)).expanduser())


def _replace_env_vars(obj: Any) -> Any:
    """Recursively replace `${VAR_NAME}` placeholders with environment values."""
    if isinstance(obj, dict):
        return {key: _replace_env_vars(val) for key, val in obj.items()}
    if isinstance(obj, list):
        return [_replace_env_vars(item) for item in obj]
    if isinstance(obj, str) and obj.startswith("${") and obj.endswith("}"):
        return os.getenv(obj[2:-1], "")
    return obj


def load_config(config_path: str = "config.yaml") -> Dict[str, Any]:
    """Load YAML runtime config from disk."""
    config_file = _config_root() / config_path
    if not config_file.exists():
        raise FileNotFoundError(f"Config file not found: {config_file}")

    with config_file.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}

    return _replace_env_vars(loaded)


def _fallback_config() -> Dict[str, Any]:
    """Return a minimal configuration when `config.yaml` is missing."""
    runtime_settings = get_settings()
    return {
        "llm": {
            "default_provider": runtime_settings.wenshape_llm_provider or "openai",
            "providers": {},
        },
        "session": {
            "timeout_seconds": 600,
        },
        "context_budget": {
            "total_tokens": 128000,
        },
        "governance": {
            "memory_auto_active": {
                "enabled": True,
                "min_confidence": 0.85,
                "allowed_types": ["preference", "progress"],
                "allowed_scopes": ["project", "chapter"],
            }
        },
    }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings loaded from environment variables."""
    _load_environment()
    return Settings()


@lru_cache(maxsize=1)
def get_config() -> Dict[str, Any]:
    """Return cached YAML config with a safe fallback."""
    try:
        return load_config()
    except FileNotFoundError as exc:
        logger.warning("Config file missing, using fallback config: %s", exc)
        return _fallback_config()


class _SettingsProxy:
    """Backwards-compatible lazy proxy for modules importing `settings`."""

    def __getattr__(self, item: str) -> Any:
        return getattr(get_settings(), item)

    def __setattr__(self, key: str, value: Any) -> None:
        setattr(get_settings(), key, value)

    def __repr__(self) -> str:
        return repr(get_settings())


class _ConfigProxy(Mapping[str, Any]):
    """Backwards-compatible lazy proxy for modules importing `config`."""

    def _target(self) -> Dict[str, Any]:
        return get_config()

    def __getitem__(self, key: str) -> Any:
        return self._target()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._target())

    def __len__(self) -> int:
        return len(self._target())

    def __getattr__(self, item: str) -> Any:
        return getattr(self._target(), item)

    def __repr__(self) -> str:
        return repr(self._target())

    def copy(self) -> Dict[str, Any]:
        return dict(self._target())


settings = _SettingsProxy()
config = _ConfigProxy()


def reload_runtime_config() -> None:
    """Clear cached settings/config so the next access reloads them."""
    get_settings.cache_clear()
    get_config.cache_clear()
