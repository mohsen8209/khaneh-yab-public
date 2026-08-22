from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from scripts.backup_database import backup_database


class BackupDatabaseTests(unittest.TestCase):
    def test_live_sqlite_database_is_copied_with_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.sqlite3"
            destination = Path(directory) / "backup.sqlite3"
            with closing(sqlite3.connect(source)) as connection:
                connection.execute("CREATE TABLE sample(value TEXT NOT NULL)")
                connection.execute("INSERT INTO sample(value) VALUES ('kept')")
                connection.commit()

            backup_database(source, destination)

            with closing(sqlite3.connect(destination)) as connection:
                value = connection.execute("SELECT value FROM sample").fetchone()[0]
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(value, "kept")
            self.assertEqual(integrity, "ok")

    def test_source_cannot_be_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.sqlite3"
            source.touch()
            with self.assertRaisesRegex(ValueError, "differ"):
                backup_database(source, source)


if __name__ == "__main__":
    unittest.main()
