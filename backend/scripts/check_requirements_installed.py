#!/usr/bin/env python3
"""Offline requirement checker for local startup scripts.

Avoids hitting package indexes on every launch by checking whether the
required distributions are already present locally. For startup we
intentionally accept newer installed versions, because local dev
environments may already be ahead of the pinned lock file.
"""

from __future__ import annotations

import argparse
import importlib.metadata as metadata
import re
import sys
from pathlib import Path

MAX_SUPPORTED_MINOR = 12


def normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_requirements(path: Path, seen: set[Path] | None = None) -> dict[str, str]:
    seen = seen or set()
    path = path.resolve()
    if path in seen:
        return {}
    seen.add(path)

    resolved: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-r "):
            nested = (path.parent / line[3:].strip()).resolve()
            resolved.update(parse_requirements(nested, seen))
            continue

        requirement = line.split(";", 1)[0].strip()
        if "==" not in requirement:
            continue

        package, version = requirement.split("==", 1)
        package = package.split("[", 1)[0].strip()
        resolved[normalize_name(package)] = version.strip()
    return resolved


def collect_installed() -> dict[str, str]:
    installed: dict[str, str] = {}
    for dist in metadata.distributions():
        name = dist.metadata.get("Name")
        if not name:
            continue
        installed[normalize_name(name)] = dist.version
    return installed


def main() -> int:
    if sys.version_info.major != 3 or sys.version_info.minor > MAX_SUPPORTED_MINOR:
        print(
            "[ERROR] Unsupported Python version. "
            "WenShape backend currently supports Python 3.10-3.12. "
            f"Detected: {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        )
        return 3

    parser = argparse.ArgumentParser(description="Check whether pinned requirements are already installed.")
    parser.add_argument(
        "requirements",
        nargs="?",
        default="requirements.txt",
        help="Path to the requirements file to validate.",
    )
    args = parser.parse_args()

    req_path = Path(args.requirements)
    if not req_path.is_file():
        print(f"[ERROR] Requirements file not found: {req_path}")
        return 2

    expected = parse_requirements(req_path)
    installed = collect_installed()

    missing: list[str] = []
    for name, version in expected.items():
        actual = installed.get(name)
        if actual is None:
            missing.append(f"{name}=={version}")

    if not missing:
        print("[OK] Required distributions are already installed.")
        return 0

    if missing:
        print("[MISSING]")
        for item in missing:
            print(f"  - {item}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
