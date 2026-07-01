# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  LLM 配置服务 - 管理 LLM API 配置文件和智能体分配，支持多提供商，支持从 .env 迁移。
  LLM configuration service - Manages LLM API profiles and agent-to-provider assignments with legacy .env migration support.
"""

import json
import uuid
import os
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
import app.config as app_config
from app.utils.logger import get_logger

logger = get_logger(__name__)


class LLMConfigService:
    """
    LLM 配置管理服务 - 持久化存储 API 配置和智能体分配。

    Manages LLM API profiles (OpenAI, Anthropic, DeepSeek, Gemini, Custom) and agent assignments.
    Supports multiple profiles with per-provider settings.
    Handles legacy .env configuration migration for backward compatibility.

    Attributes:
        data_dir: 数据目录路径 / Path to persistent data directory
        profiles_path: 配置文件路径 / Path to llm_profiles.json
        assignments_path: 分配文件路径 / Path to agent_assignments.json
    """

    def __init__(self, data_dir: Optional[str] = None) -> None:
        self.data_dir = self._resolve_data_dir(data_dir)
        self.profiles_path = self.data_dir / "llm_profiles.json"
        self.assignments_path = self.data_dir / "agent_assignments.json"
        self._ensure_data_dir()
        self._migrate_legacy_config()

    def _resolve_data_dir(self, data_dir: Optional[str]) -> Path:
        """Resolve data directory with backward-compatible fallback."""
        if data_dir:
            resolved = Path(str(data_dir)).expanduser()
            return resolved if resolved.is_absolute() else (Path.cwd() / resolved).resolve()

        primary = self._default_data_dir()
        legacy = self._legacy_data_dir()

        primary_profiles = primary / "llm_profiles.json"
        primary_assignments = primary / "agent_assignments.json"
        legacy_profiles = legacy / "llm_profiles.json"
        legacy_assignments = legacy / "agent_assignments.json"

        if primary_profiles.exists() or primary_assignments.exists():
            return primary
        if legacy_profiles.exists() or legacy_assignments.exists():
            return legacy
        return primary

    def _default_data_dir(self) -> Path:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).parent / "data"

        raw = getattr(getattr(app_config, "settings", None), "data_dir", None) or "../data"
        candidate = Path(str(raw)).expanduser()
        if candidate.is_absolute():
            return candidate

        backend_root = Path(__file__).resolve().parents[2]
        return (backend_root / candidate).resolve()

    def _legacy_data_dir(self) -> Path:
        """Legacy location used by early versions (backend/data)."""
        return (Path(__file__).resolve().parents[2] / "data").resolve()

    def _ensure_data_dir(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _load_json(self, path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Error loading %s: %s", path, e)
            return default

    def _save_json(self, path: Path, data: Any):
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            try:
                os.replace(tmp_path, path)
            except (PermissionError, OSError) as exc:
                # Windows can deny replace when destination is temporarily locked (e.g. AV/indexers).
                # Best-effort retry, then fall back to direct write to avoid breaking startup flows.
                winerror = getattr(exc, "winerror", None)
                is_windows_lock = isinstance(exc, PermissionError) or winerror in {5, 32}
                if not is_windows_lock:
                    raise

                last_exc: Exception = exc
                for attempt in range(4):
                    try:
                        import time

                        time.sleep(0.05 * (attempt + 1))
                        os.replace(tmp_path, path)
                        break
                    except (PermissionError, OSError) as retry_exc:
                        last_exc = retry_exc
                else:
                    logger.warning("Atomic replace failed, falling back to direct write: %s", last_exc)
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
        finally:
            try:
                if tmp_path.exists() and tmp_path != path:
                    tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

    def get_profiles(self) -> List[Dict[str, Any]]:
        """
        获取所有 LLM 配置文件。

        Get all saved LLM API profiles.

        Returns:
            配置文件列表 / List of profile dictionaries.
        """
        profiles = self._load_json(self.profiles_path, [])
        if not isinstance(profiles, list):
            profiles = []

        cleaned: List[Dict[str, Any]] = []
        now_ts = int(datetime.now().timestamp())
        for item in profiles:
            if not isinstance(item, dict):
                continue

            profile = dict(item)
            profile_id = str(profile.get("id") or "").strip()
            if not profile_id:
                profile_id = str(uuid.uuid4())
                profile["id"] = profile_id

            provider = str(profile.get("provider") or "").strip().lower()
            if provider:
                profile["provider"] = provider
            else:
                profile["provider"] = "custom"

            name = str(profile.get("name") or "").strip()
            if not name:
                profile["name"] = f"{provider or 'custom'}:{profile_id[:8]}"

            if "created_at" not in profile:
                profile["created_at"] = now_ts

            # 暴露「是否支持参数级 thinking 切换」给前端，用于对话栏「深度思考」按钮显隐（能力降级，异常默认 False）。
            try:
                from app.llm_gateway.thinking import supports_thinking

                profile["supports_thinking"] = supports_thinking(profile["provider"], str(profile.get("model") or ""))
            except Exception:
                profile["supports_thinking"] = False

            cleaned.append(profile)

        return cleaned

    def get_assignments(self) -> Dict[str, str]:
        """
        获取智能体到 LLM 配置的分配关系。

        Get current agent-to-profile assignments.

        Returns:
            分配字典 (agent_name -> profile_id) / Assignment mapping.
        """
        allowed = {"archivist", "writer", "editor"}
        assignments = self._load_json(self.assignments_path, {})
        if not isinstance(assignments, dict):
            assignments = {}

        profile_ids = {p.get("id") for p in self.get_profiles() if isinstance(p, dict) and p.get("id")}
        cleaned: Dict[str, str] = {}
        for agent in allowed:
            raw = str(assignments.get(agent) or "").strip()
            if raw and raw not in profile_ids:
                raw = ""
            cleaned[agent] = raw

        return cleaned

    def save_profile(self, profile: Dict[str, Any]) -> Dict[str, Any]:
        """
        保存或更新 LLM 配置文件。

        Save a new profile or update an existing one.

        Args:
            profile: 配置文件数据 / Profile dictionary. If no 'id', generates new UUID.

        Returns:
            保存后的配置文件（包含 id 和 created_at） / Profile with id and timestamp.
        """
        profiles = self.get_profiles()
        if not isinstance(profiles, list):
            profiles = []

        incoming = dict(profile or {})
        incoming_id = str(incoming.get("id") or "").strip()
        if not incoming_id:
            incoming_id = str(uuid.uuid4())
            incoming["id"] = incoming_id
            incoming["created_at"] = int(datetime.now().timestamp())

        incoming["updated_at"] = int(datetime.now().timestamp())

        if "provider" in incoming:
            incoming["provider"] = str(incoming.get("provider") or "").strip().lower()

        if not str(incoming.get("name") or "").strip():
            provider = str(incoming.get("provider") or "custom").strip().lower()
            incoming["name"] = f"{provider}:{incoming_id[:8]}"

        replaced = False
        for i, existing in enumerate(profiles):
            if isinstance(existing, dict) and str(existing.get("id") or "").strip() == incoming_id:
                profiles[i] = {**existing, **incoming}
                replaced = True
                break

        if not replaced:
            profiles.append(incoming)

        self._save_json(self.profiles_path, profiles)
        return incoming

    def delete_profile(self, profile_id: str):
        profiles = self.get_profiles()
        profiles = [p for p in profiles if p["id"] != profile_id]
        self._save_json(self.profiles_path, profiles)

        # Also clean up assignments
        assignments = self.get_assignments()
        changed = False
        for agent, pid in assignments.items():
            if pid == profile_id:
                assignments[agent] = ""
                changed = True
        if changed:
            self.save_assignments(assignments)

    def save_assignments(self, assignments: Dict[str, str]):
        allowed = {"archivist", "writer", "editor"}
        clean = {k: v for k, v in assignments.items() if k in allowed}
        current = self.get_assignments()
        current.update(clean)
        self._save_json(self.assignments_path, current)

    def get_profile_by_id(self, profile_id: str) -> Optional[Dict[str, Any]]:
        profiles = self.get_profiles()
        for p in profiles:
            if p["id"] == profile_id:
                return p
        return None

    def _migrate_legacy_config(self):
        """Migrate .env settings to profiles if profiles.json is empty."""
        profiles = self.get_profiles()
        if profiles:
            return  # Already has profiles, skip migration

        logger.info("Migrating legacy configuration...")
        new_profiles = []

        # Helper to check if a key is real
        def is_real_key(val):
            return val and not str(val).startswith("sk-your") and not str(val).startswith("your-")

        # OpenAI
        openai_key = app_config.settings.openai_api_key
        if is_real_key(openai_key):
            new_profiles.append(
                {
                    "id": str(uuid.uuid4()),
                    "name": "Legacy OpenAI",
                    "provider": "openai",
                    "api_key": openai_key,
                    "model": app_config.settings.openai_model or "gpt-4o",
                    "temperature": 0.7,
                }
            )

        # Anthropic
        ant_key = app_config.settings.anthropic_api_key
        if is_real_key(ant_key):
            new_profiles.append(
                {
                    "id": str(uuid.uuid4()),
                    "name": "Legacy Anthropic",
                    "provider": "anthropic",
                    "api_key": ant_key,
                    "model": app_config.settings.anthropic_model or "claude-3-5-sonnet-20241022",
                    "temperature": 0.7,
                }
            )

        # Custom
        custom_url = app_config.settings.custom_base_url
        if custom_url:
            new_profiles.append(
                {
                    "id": str(uuid.uuid4()),
                    "name": "Legacy Custom/SiliconFlow",
                    "provider": "custom",
                    "api_key": app_config.settings.custom_api_key,
                    "base_url": custom_url,
                    "model": app_config.settings.custom_model_name or "default-model",
                    "temperature": 0.7,
                }
            )

        # DeepSeek
        deepseek_key = app_config.settings.deepseek_api_key
        if is_real_key(deepseek_key):
            new_profiles.append(
                {
                    "id": str(uuid.uuid4()),
                    "name": "Legacy DeepSeek",
                    "provider": "deepseek",
                    "api_key": deepseek_key,
                    "model": app_config.settings.deepseek_model or "deepseek-chat",
                    "temperature": 0.7,
                }
            )

        # Gemini
        gemini_key = app_config.settings.gemini_api_key
        if is_real_key(gemini_key):
            new_profiles.append(
                {
                    "id": str(uuid.uuid4()),
                    "name": "Legacy Gemini",
                    "provider": "gemini",
                    "api_key": gemini_key,
                    "model": app_config.settings.gemini_model or "gemini-2.5-flash",
                    "temperature": 0.7,
                }
            )

        if new_profiles:
            self._save_json(self.profiles_path, new_profiles)

            # Setup default assignments
            # Assume the first valid profile is the default
            default_id = new_profiles[0]["id"]
            assignments = {"archivist": default_id, "writer": default_id, "editor": default_id}
            self._save_json(self.assignments_path, assignments)
            logger.info("Migrated %s profiles", len(new_profiles))


# Global instance
llm_config_service = LLMConfigService()
