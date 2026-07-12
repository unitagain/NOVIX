#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the P6 golden replay CI gate."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


def _backend_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_sys_path() -> None:
    backend_dir = _backend_dir()
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run WenShape P6 golden replay gate")
    parser.add_argument("--output", default="", help="Optional JSON output path")
    parser.add_argument("--allow-fail", action="store_true", help="Always exit 0, useful for local inspection")
    return parser.parse_args()


async def main() -> int:
    _ensure_sys_path()
    args = _parse_args()

    from app.eval.golden_replay import run_golden_replay_suite

    result = await run_golden_replay_suite()
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
        print(f"已写入: {args.output}")
    else:
        print(payload)
    return 0 if result.get("success") or args.allow_fail else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
