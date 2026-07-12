"""Privacy-gated export of production-derived P12 evaluation cases."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from app.eval.longform_artifacts import read_json, write_jsonl


def export_p12_cases_from_traces(
    trace_paths: Iterable[str | Path],
    *,
    output_path: Path,
    allow_content: bool,
    redact_fields: Iterable[str],
) -> Dict[str, Any]:
    """Export only explicit p12_eval_case events; generic prompts are never inferred into memory cases."""
    redactions = {str(item) for item in redact_fields}
    cases: List[Dict[str, Any]] = []
    rejected = 0
    for path in trace_paths:
        payload = read_json(Path(path), {}) or {}
        for event in payload.get("events") or []:
            if str(event.get("type") or "") != "p12_eval_case":
                continue
            case = dict(event.get("data") or {})
            if not _valid_case(case):
                rejected += 1
                continue
            if not allow_content:
                case = _redact(case, redactions)
                if _contains_redaction(case):
                    rejected += 1
                    continue
            case["source_trace_sha256"] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
            case["privacy_reviewed"] = True
            cases.append(case)
    deduped = {str(case.get("pair_id") or case.get("id")): case for case in cases}
    rows = [deduped[key] for key in sorted(deduped)]
    write_jsonl(output_path, rows)
    return {"success": True, "exported": len(rows), "rejected": rejected, "path": str(output_path)}


def _valid_case(case: Dict[str, Any]) -> bool:
    variants = case.get("variants")
    return bool(case.get("pair_id") and isinstance(variants, dict) and len(variants) == 2)


def _redact(value: Any, fields: set[str]) -> Any:
    if isinstance(value, dict):
        return {key: ("[REDACTED]" if key in fields else _redact(item, fields)) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, fields) for item in value]
    return value


def _contains_redaction(value: Any) -> bool:
    return "[REDACTED]" in json.dumps(value, ensure_ascii=False, default=str)
