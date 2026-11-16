from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3


def main() -> int:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise SystemExit("LOCALAPPDATA is unavailable")
    default_source = Path(local_app_data) / "Multi-LLM" / "research-v2.db"
    parser = argparse.ArgumentParser(description="Create a consistent read-only backup of research-v2.db")
    parser.add_argument("--source", type=Path, default=default_source)
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    if not source.is_file():
        raise SystemExit("Research database does not exist")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = (args.destination or (
        Path(local_app_data) / "Multi-LLM" / "Backups" / f"research-v2-{timestamp}.db"
    )).resolve()
    if destination.exists():
        raise SystemExit("Backup destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_uri = source.as_uri() + "?mode=ro"
    try:
        with sqlite3.connect(source_uri, uri=True) as source_connection:
            if source_connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise SystemExit("Source database failed integrity_check")
            with sqlite3.connect(destination) as destination_connection:
                source_connection.backup(destination_connection)
                if destination_connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise SystemExit("Backup database failed integrity_check")
    except Exception:
        if destination.exists():
            destination.unlink()
        raise
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
