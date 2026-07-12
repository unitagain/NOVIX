"""Crash-safe campaign state and append-only job ledger."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

from app.eval.longform_artifacts import read_json, read_jsonl


class CampaignStore:
    def __init__(self, root: Path, campaign_id: str):
        self.directory = Path(root) / "campaigns" / campaign_id
        self.config_path = self.directory / "config.json"
        self.state_path = self.directory / "state.json"
        self.jobs_path = self.directory / "jobs.jsonl"
        self.failures_path = self.directory / "failures.jsonl"
        self.replay_cases_path = self.directory / "replay_cases.jsonl"
        self.release_manifest_path = self.directory / "release_quality_manifest.json"
        self.job_dir = self.directory / "jobs"

    def load_state(self) -> Dict[str, Any]:
        return dict(read_json(self.state_path, {}) or {})

    def save_json(self, path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        os.replace(temporary, path)

    def append_jsonl(self, path: Path, row: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def save_job_result(self, job_id: str, result: Dict[str, Any]) -> Path:
        path = self.job_dir / f"{job_id}.json"
        self.save_json(path, result)
        return path

    def jobs(self) -> List[Dict[str, Any]]:
        return read_jsonl(self.jobs_path)

    def completed_job_ids(self) -> set[str]:
        return {str(row.get("job_id")) for row in self.jobs() if row.get("status") == "completed"}

    def latest_job_statuses(self) -> Dict[str, str]:
        statuses: Dict[str, str] = {}
        for row in self.jobs():
            if row.get("job_id"):
                statuses[str(row["job_id"])] = str(row.get("status") or "")
        return statuses
