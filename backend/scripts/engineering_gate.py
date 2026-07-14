"""Run the shared backend engineering gate."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.ops.engineering_gate import engineering_checks


def main() -> int:
    repo = Path(__file__).resolve().parents[2]
    results = []
    for name, command, cwd in engineering_checks(repo):
        completed = subprocess.run(command, cwd=cwd, check=False)
        results.append({"name": name, "success": completed.returncode == 0, "returncode": completed.returncode})
        if completed.returncode != 0:
            break
    print(json.dumps({"success": all(row["success"] for row in results), "checks": results}, indent=2))
    return 0 if results and all(row["success"] for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
