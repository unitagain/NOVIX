"""Project backup, restore, migration and corruption scan CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parents[1]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.ops.project_maintenance import ProjectMaintenanceService


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    backup = sub.add_parser("backup")
    backup.add_argument("--project-id", required=True)
    backup.add_argument("--output", required=True)
    restore = sub.add_parser("restore")
    restore.add_argument("--backup", required=True)
    restore.add_argument("--project-id")
    restore.add_argument("--overwrite", action="store_true")
    scan = sub.add_parser("scan")
    scan.add_argument("--project-id", required=True)
    migrate = sub.add_parser("migrate")
    migrate.add_argument("--project-id", required=True)
    args = parser.parse_args()
    service = ProjectMaintenanceService(args.data_dir)
    if args.command == "backup":
        result = service.backup(args.project_id, args.output)
    elif args.command == "restore":
        result = service.restore(args.backup, project_id=args.project_id, overwrite=args.overwrite)
    elif args.command == "scan":
        result = service.scan(args.project_id)
    else:
        result = service.migrate(args.project_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
