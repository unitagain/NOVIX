"""Application control-plane store singleton."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.config import settings
from app.control_plane.store import SQLiteControlStore


_store: Optional[SQLiteControlStore] = None


def control_database_path() -> Path:
    root = Path(settings.data_dir)
    if not root.is_absolute():
        root = (Path(__file__).resolve().parents[2] / root).resolve()
    return root / "_system" / "control.sqlite3"


def get_control_store() -> SQLiteControlStore:
    global _store
    if _store is None:
        _store = SQLiteControlStore(control_database_path())
    return _store
