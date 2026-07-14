#!/usr/bin/env python3
"""Render privacy-safe local usage diagnostics without reading project content."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.observability.usage_diagnostics import build_usage_diagnostics_from_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minimum-turns", type=int, default=10)
    parser.add_argument("--health-url", default="http://127.0.0.1:8000/health")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    request = urllib.request.Request(args.health_url)
    token = os.getenv("WENSHAPE_DESKTOP_SESSION_TOKEN", "").strip()
    if token:
        request.add_header("x-wenshape-session-token", token)
    with urllib.request.urlopen(request, timeout=10) as response:
        health = json.loads(response.read().decode("utf-8"))
    report = build_usage_diagnostics_from_snapshot(
        dict(health.get("runtime_metrics") or {}),
        minimum_turns=max(1, args.minimum_turns),
        budget=((health.get("usage_diagnostics") or {}).get("budget") or {}),
    )
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
