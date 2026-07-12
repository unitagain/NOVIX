"""Artifact paths and JSON persistence for the longform benchmark."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line:
            continue
        item = json.loads(line)
        if isinstance(item, dict):
            rows.append(item)
    return rows


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(json.dumps(row, ensure_ascii=False, default=str) for row in rows)
    path.write_text(payload + ("\n" if payload else ""), encoding="utf-8")


@dataclass(frozen=True)
class BenchmarkPaths:
    root: Path
    benchmark_id: str

    @property
    def benchmark_dir(self) -> Path:
        return self.root / self.benchmark_id

    @property
    def manifest(self) -> Path:
        return self.benchmark_dir / "manifest.json"

    @property
    def corpus_dir(self) -> Path:
        return self.benchmark_dir / "corpus"

    @property
    def generated_dir(self) -> Path:
        return self.benchmark_dir / "generated"

    @property
    def gold_dir(self) -> Path:
        return self.benchmark_dir / "gold"

    @property
    def runs_dir(self) -> Path:
        return self.benchmark_dir / "runs"

    @property
    def chapters(self) -> Path:
        return self.corpus_dir / "chapters.jsonl"

    def run_dir(self, run_id: str) -> Path:
        return self.runs_dir / run_id
