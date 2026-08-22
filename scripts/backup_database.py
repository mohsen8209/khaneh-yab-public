from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from divar_scout.config import database_path  # noqa: E402


DEFAULT_OUTPUT = ROOT / "output" / "deploy" / "divar_scout.sqlite3"


def backup_database(source: Path, destination: Path) -> Path:
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Database does not exist: {source}")
    if source == destination:
        raise ValueError("Backup destination must differ from source")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    with closing(sqlite3.connect(source)) as source_connection:
        with closing(sqlite3.connect(destination)) as destination_connection:
            source_connection.backup(destination_connection)
            result = destination_connection.execute("PRAGMA integrity_check").fetchone()
    if not result or result[0] != "ok":
        destination.unlink(missing_ok=True)
        raise RuntimeError("SQLite backup integrity check failed")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a consistent SQLite copy for secure PaaS volume upload"
    )
    parser.add_argument("--source", type=Path, default=database_path())
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    output = backup_database(arguments.source, arguments.output)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
