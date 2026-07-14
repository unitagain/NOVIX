# -*- coding: utf-8 -*-
"""
中文说明：该模块为 WenShape 后端组成部分，详细行为见下方英文说明。

Centralized logger helpers for WenShape.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config import get_settings

LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_SENSITIVE_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"),
    re.compile(
        r"(?i)((?:[\"']?(?:api[_-]?key|session[_-]?token|x-wenshape-session-token)[\"']?)\s*[:=]\s*[\"']?)"
        r"[^\"'\s,;}]+"
    ),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
)


def redact_sensitive_text(value: object) -> str:
    text = str(value)
    for pattern in _SENSITIVE_PATTERNS:
        text = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]" if match.lastindex else "[REDACTED]", text)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_sensitive_text(super().format(record))


def _resolve_log_dir() -> Path:
    desktop_log_dir = str(os.getenv("WENSHAPE_DESKTOP_LOG_DIR") or "").strip()
    if desktop_log_dir:
        candidate = Path(desktop_log_dir).expanduser()
        return candidate if candidate.is_absolute() else (Path.cwd() / candidate).resolve()

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "logs"
    return Path(__file__).resolve().parents[2] / "logs"


def _build_handlers(debug_enabled: bool) -> list[logging.Handler]:
    log_dir = _resolve_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.DEBUG if debug_enabled else logging.INFO)
    console_handler.setFormatter(RedactingFormatter(LOG_FORMAT, DATE_FORMAT))

    file_handler = RotatingFileHandler(
        log_dir / "wenshape.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(RedactingFormatter(LOG_FORMAT, DATE_FORMAT))
    return [console_handler, file_handler]


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger without doing work at module import time."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    debug_enabled = bool(get_settings().debug)
    logger.setLevel(logging.DEBUG if debug_enabled else logging.INFO)
    logger.propagate = False

    for handler in _build_handlers(debug_enabled):
        logger.addHandler(handler)

    return logger
