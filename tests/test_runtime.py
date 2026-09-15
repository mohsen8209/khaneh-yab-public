from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import closing
import unittest
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from divar_scout.config import database_path, load_config
from divar_scout.dashboard import DashboardApp
from divar_scout.db import Database
from divar_scout.runtime import (
    bootstrap_admin,
    environment_flag,
    inspect_database_startup,
    scan_forever,
    validate_paas_runtime,
)


ROOT = Path(__file__).resolve().parents[1]


class RuntimeTests(unittest.TestCase):
    def test_paas_environment_binds_publicly_and_uses_persistent_data_dir(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = {
                "KHANEYAB_RUNTIME": "paas",
                "KHANEYAB_DATA_DIR": directory,
                "KHANEYAB_PORT": "8766",
                "KHANEYAB_ALLOW_EMPTY_DATABASE": "true",
            }
            config = load_config(environment=environment)

            self.assertEqual(config.dashboard["host"], "0.0.0.0")
            self.assertEqual(config.dashboard["port"], 8766)
            self.assertEqual(
                validate_paas_runtime(config, environment),
                Path(directory) / "divar_scout.sqlite3",
            )
            self.assertEqual(database_path(environment), Path(directory) / "divar_scout.sqlite3")

    def test_paas_refuses_missing_database_without_explicit_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = {
                "KHANEYAB_RUNTIME": "paas",
                "KHANEYAB_DATA_DIR": directory,
                "KHANEYAB_PORT": "8766",
            }
            config = load_config(environment=environment)

            with self.assertRaisesRegex(RuntimeError, "fresh production database"):
                validate_paas_runtime(config, environment)
            self.assertFalse((Path(directory) / "divar_scout.sqlite3").exists())

    def test_paas_refuses_empty_schema_without_explicit_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "divar_scout.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)"
                )
                connection.commit()
            environment = {
                "KHANEYAB_RUNTIME": "paas",
                "KHANEYAB_DATA_DIR": directory,
                "KHANEYAB_PORT": "8766",
            }
            config = load_config(environment=environment)

            self.assertTrue(inspect_database_startup(path).requires_fresh_bootstrap)
            with self.assertRaisesRegex(RuntimeError, "fresh production database"):
                validate_paas_runtime(config, environment)

    def test_paas_allows_deliberate_empty_database_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = {
                "KHANEYAB_RUNTIME": "paas",
                "KHANEYAB_DATA_DIR": directory,
                "KHANEYAB_PORT": "8766",
                "KHANEYAB_ALLOW_EMPTY_DATABASE": "true",
            }
            config = load_config(environment=environment)

            self.assertEqual(
                validate_paas_runtime(config, environment),
                Path(directory) / "divar_scout.sqlite3",
            )

    def test_paas_refuses_ephemeral_database_configuration(self) -> None:
        environment = {"KHANEYAB_RUNTIME": "paas"}
        config = load_config(environment=environment)

        with self.assertRaisesRegex(RuntimeError, "persistent volume"):
            validate_paas_runtime(config, environment)

    def test_bootstrap_admin_is_created_exactly_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "runtime.sqlite3")
            app = DashboardApp(load_config(environment={}), database=database)
            environment = {
                "KHANEYAB_ADMIN_USERNAME": "cloud_admin",
                "KHANEYAB_ADMIN_DISPLAY_NAME": "مدیر ابری",
                "KHANEYAB_ADMIN_PASSWORD": "CloudPassword123",
            }

            self.assertTrue(bootstrap_admin(app, environment))
            self.assertFalse(bootstrap_admin(app, environment))
            self.assertTrue(app.auth.has_admin())
            self.assertEqual(app.auth.admin_stats()["approved"], 1)

    def test_invalid_bootstrap_password_does_not_stop_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "runtime.sqlite3")
            app = DashboardApp(load_config(environment={}), database=database)

            created = bootstrap_admin(
                app,
                {
                    "KHANEYAB_ADMIN_USERNAME": "cloud_admin",
                    "KHANEYAB_ADMIN_DISPLAY_NAME": "Cloud Admin",
                    "KHANEYAB_ADMIN_PASSWORD": "1234567890",
                },
            )

            self.assertFalse(created)
            self.assertFalse(app.auth.has_admin())

    def test_background_scan_uses_dashboard_lock_and_stops(self) -> None:
        stop_event = threading.Event()

        class FakeApp:
            config = type("Config", (), {"scan": {"poll_interval_seconds": 60}})()

            def __init__(self) -> None:
                self.calls = 0

            def run_scan(self) -> dict[str, int]:
                self.calls += 1
                stop_event.set()
                return {
                    "summary_count": 3,
                    "detail_count": 2,
                    "filtered_count": 1,
                    "expired_count": 0,
                    "alert_count": 0,
                }

        app = FakeApp()
        scan_forever(app, stop_event)  # type: ignore[arg-type]
        self.assertEqual(app.calls, 1)

    def test_boolean_environment_values_are_strict(self) -> None:
        self.assertTrue(environment_flag({}, "FLAG", default=True))
        self.assertFalse(environment_flag({"FLAG": "off"}, "FLAG", default=True))
        with self.assertRaisesRegex(ValueError, "must be true or false"):
            environment_flag({"FLAG": "sometimes"}, "FLAG", default=True)

    def test_app_entrypoint_forces_paas_runtime_and_serves_health(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.json"
            config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
            config["instance"]["expected_dashboard_port"] = 0
            config_path.write_text(json.dumps(config), encoding="utf-8")
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            environment = os.environ.copy()
            environment.update(
                {
                    "KHANEYAB_RUNTIME": "incorrect-panel-value",
                    "KHANEYAB_CONFIG_PATH": str(config_path),
                    "KHANEYAB_HOST": "0.0.0.0",
                    "KHANEYAB_PORT": str(port),
                    "KHANEYAB_DATA_DIR": str(root / "data"),
                    "KHANEYAB_WATCH_ENABLED": "false",
                    "KHANEYAB_ALLOW_EMPTY_DATABASE": "true",
                    "KHANEYAB_ADMIN_USERNAME": "smoke_admin",
                    "KHANEYAB_ADMIN_DISPLAY_NAME": "مدیر تست",
                    "KHANEYAB_ADMIN_PASSWORD": "SmokePassword123",
                }
            )
            process = subprocess.Popen(
                [sys.executable, "app.py"],
                cwd=ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
            )
            try:
                deadline = time.monotonic() + 8
                payload = None
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        break
                    try:
                        with urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as response:
                            payload = json.load(response)
                        break
                    except (URLError, TimeoutError):
                        time.sleep(0.1)
                if payload is None:
                    process.terminate()
                    output, _ = process.communicate(timeout=5)
                    self.fail(f"PaaS entrypoint did not become healthy:\n{output}")
                self.assertEqual(payload, {"ok": True, "release": "0.1.13"})
                self.assertTrue((root / "data" / "divar_scout.sqlite3").is_file())
            finally:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                if process.stdout is not None:
                    process.stdout.close()


if __name__ == "__main__":
    unittest.main()
