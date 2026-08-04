"""Outline 设置读取：config.yaml 全局默认 + project.yaml 项目级覆盖。

大纲设置很小（enabled / require_consult / max_push_tokens），单独一处 owner，供
router、writer 上下文推送与 read_outline 工具共用，避免各处分别硬编码默认值。
"""

from __future__ import annotations

from typing import Any, Dict

from app.config import config


def _global_defaults() -> Dict[str, Any]:
    cfg = config.get("outline", {}) or {}
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "require_consult": bool(cfg.get("require_consult", False)),
        "max_push_tokens": int(cfg.get("max_push_tokens", 2000) or 2000),
    }


def resolve_outline_settings(project_meta: Dict[str, Any] | None) -> Dict[str, Any]:
    """合并全局默认与 project.yaml 的 outline 覆盖，返回归一化设置。"""
    settings = _global_defaults()
    override = (project_meta or {}).get("outline") if isinstance(project_meta, dict) else None
    if isinstance(override, dict):
        if "enabled" in override:
            settings["enabled"] = bool(override.get("enabled"))
        if "require_consult" in override:
            settings["require_consult"] = bool(override.get("require_consult"))
        if "max_push_tokens" in override:
            try:
                settings["max_push_tokens"] = max(0, int(override.get("max_push_tokens")))
            except (TypeError, ValueError):
                pass
    return settings
