"""Single owner for engineering checks shared by CI and the release gate."""

from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

EngineeringCheck = Tuple[str, List[str], Path]


def engineering_checks(repo_root: Path) -> list[EngineeringCheck]:
    repo = Path(repo_root).resolve()
    backend = repo / "backend"
    return [
        ("ruff", ["python", "-m", "ruff", "check", "app", "tests", "scripts"], backend),
        ("type_contract", ["python", "scripts/type_contract_check.py"], backend),
        ("pytest", ["python", "-m", "pytest", "-W", "error"], backend),
        ("pip_check", ["python", "-m", "pip", "check"], backend),
        ("error_contract", ["python", "scripts/error_contract_audit.py"], backend),
        ("architecture", ["python", "scripts/architecture_profile.py", "--check"], backend),
        ("requirements_sync", ["python", "scripts/check_requirements_sync.py"], repo),
    ]
