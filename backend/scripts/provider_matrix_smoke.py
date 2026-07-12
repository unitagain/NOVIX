#!/usr/bin/env python3
"""Run content-free real API smoke checks for configured provider profiles."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.llm_gateway import get_gateway
from app.llm_gateway.reliability import PersistentIdempotencyRegistry
from app.services.llm_config_service import llm_config_service


async def _check_profile(gateway: Any, profile: dict[str, Any]) -> dict[str, Any]:
    profile_id = str(profile.get("id") or "")
    started = time.perf_counter()
    base = {
        "profile_id": profile_id,
        "provider": str(profile.get("provider") or ""),
        "configured_model": str(profile.get("model") or ""),
    }
    try:
        response = await gateway.chat(
            [{"role": "user", "content": "Reply with OK only."}],
            provider=profile_id,
            temperature=0.0,
            max_tokens=8,
            retry=False,
            timeout_seconds=30,
            data_classification="synthetic",
            idempotency_key=f"provider-matrix-smoke:{profile_id}:{int(time.time())}",
        )
    except Exception as exc:
        return {
            **base,
            "success": False,
            "error_type": type(exc).__name__,
            "reason": str(getattr(exc, "reason", "") or "provider_request_failed")[:80],
            "status_code": getattr(exc, "status_code", None),
            "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
        }
    usage = dict(response.get("usage") or {})
    return {
        **base,
        "success": True,
        "actual_provider": str(response.get("provider") or ""),
        "actual_model": str(response.get("model") or ""),
        "total_tokens": int(usage.get("total_tokens") or 0),
        "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", action="append", default=[], help="Profile id to check; defaults to all profiles")
    args = parser.parse_args()
    selected = set(args.profile)
    profiles = [
        profile
        for profile in llm_config_service.get_profiles()
        if not selected or str(profile.get("id") or "") in selected
    ]
    with tempfile.TemporaryDirectory(prefix="wenshape-provider-smoke-") as temporary:
        gateway = get_gateway()
        gateway.idempotency_registry = PersistentIdempotencyRegistry(Path(temporary) / "idempotency")
        rows = []
        for profile in profiles:
            rows.append(await _check_profile(gateway, profile))
    result = {
        "schema_version": 1,
        "classification": "synthetic",
        "profiles_checked": len(rows),
        "profiles_available": sum(bool(row["success"]) for row in rows),
        "results": rows,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if rows and all(row["success"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
