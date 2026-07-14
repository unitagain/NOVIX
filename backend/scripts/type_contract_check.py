"""Run the incremental type gate for backend kernel contracts."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def main() -> int:
    backend = Path(__file__).resolve().parents[1]
    executable = shutil.which("mypy")
    if not executable:
        print("mypy executable not found; install the development type-check dependency")
        return 2
    completed = subprocess.run([executable], cwd=backend, check=False)
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
