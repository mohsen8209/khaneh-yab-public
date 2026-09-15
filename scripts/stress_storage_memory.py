from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
import tracemalloc
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from divar_scout.db import Database
from divar_scout.models import Listing


def seed_legacy_database(path: Path, rows: int, raw_kb: int) -> None:
    database = Database(path)
    raw_value = "x" * (raw_kb * 1024)
    with database.connect() as connection:
        for index in range(rows):
            token = f"legacy-{index:06d}"
            payload = Listing(
                token=token,
                title=f"Legacy listing {index}",
                url=f"https://divar.ir/v/{token}",
                district="Test",
                size=80,
                rooms=2,
                image_count=3,
                raw_summary={"payload": raw_value},
                raw_detail={"payload": raw_value},
            ).as_dict()
            connection.execute(
                """
                INSERT INTO listings(
                    token, title, district, url, listing_json, status,
                    detail_fetched_at, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, 'new', CURRENT_TIMESTAMP,
                          CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (token, payload["title"], "Test", payload["url"], json.dumps(payload)),
            )


def run(rows: int, raw_kb: int, max_peak_mb: float) -> dict[str, float | int]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "legacy.sqlite3"
        seed_legacy_database(path, rows, raw_kb)
        raw_database_mb = path.stat().st_size / (1024 * 1024)

        tracemalloc.start()
        database = Database(path)
        records = database.list_records(limit=rows)
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        with closing(sqlite3.connect(path)) as connection:
            raw_remaining = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM listings
                    WHERE instr(listing_json, '"raw_detail"') > 0
                       OR instr(listing_json, '"raw_summary"') > 0
                    """
                ).fetchone()[0]
            )
            largest_payload = int(
                connection.execute(
                    "SELECT COALESCE(MAX(length(listing_json)), 0) FROM listings"
                ).fetchone()[0]
            )

        peak_mb = peak / (1024 * 1024)
        result: dict[str, float | int] = {
            "rows": len(records),
            "legacy_database_mb": round(raw_database_mb, 1),
            "python_peak_mb": round(peak_mb, 1),
            "raw_payloads_remaining": raw_remaining,
            "largest_stored_payload_bytes": largest_payload,
        }
        if raw_remaining:
            raise RuntimeError(f"{raw_remaining} raw payloads survived compaction")
        if peak_mb > max_peak_mb:
            raise RuntimeError(
                f"Python peak {peak_mb:.1f} MiB exceeded {max_peak_mb:.1f} MiB"
            )
        return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stress legacy listing compaction under a Python memory budget."
    )
    parser.add_argument("--rows", type=int, default=1000)
    parser.add_argument("--raw-kb", type=int, default=64)
    parser.add_argument("--max-peak-mb", type=float, default=96)
    args = parser.parse_args()
    print(json.dumps(run(args.rows, args.raw_kb, args.max_peak_mb), sort_keys=True))


if __name__ == "__main__":
    main()
