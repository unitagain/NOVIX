"""Content-free external API egress audit ledger."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from app.config import settings
from app.storage.file_lock import get_file_lock


class EgressLedger:
    def __init__(self, data_dir: Optional[str] = None):
        root = Path(data_dir or settings.data_dir)
        if not root.is_absolute():
            root = (Path(__file__).resolve().parents[2] / root).resolve()
        self.path = root / "_system" / "egress" / "ledger.jsonl"

    async def record(self, row: Dict[str, Any]) -> None:
        payload = {
            "timestamp": time.time(),
            "project_id": row.get("project_id"),
            "corpus_id": row.get("corpus_id"),
            "data_classification": row.get("data_classification") or "project_private",
            "authorized": bool(row.get("authorized", True)),
            "redaction": row.get("redaction") or "content_not_logged",
            "provider_profile": row.get("provider_profile"),
            "provider": row.get("provider"),
            "model": row.get("model"),
            "request_id": row.get("request_id"),
            "request_fingerprint": row.get("request_fingerprint"),
            "payload_bytes": int(row.get("payload_bytes") or 0),
            "status": row.get("status") or "attempted",
            "reason": row.get("reason"),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = get_file_lock()
        async with lock.lock(self.path):
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
                handle.flush()
                os.fsync(handle.fileno())


egress_ledger = EgressLedger()
