from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from .auth import AuthError
from .config import AppConfig, PAAS_RUNTIME, database_path, load_config
from .dashboard import DashboardApp, serve
from .db import Database


TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class DatabaseStartupState:
    exists: bool
    has_schema: bool = False
    users: int = 0
    profiles: int = 0
    completed_profiles: int = 0
    listings: int = 0
    scans: int = 0
    error: str | None = None

    @property
    def requires_fresh_bootstrap(self) -> bool:
        return not self.exists or not self.has_schema or self.users == 0

    @property
    def bootstrap_only(self) -> bool:
        return (
            self.has_schema
            and self.users <= 1
            and self.profiles <= 1
            and self.completed_profiles == 0
            and self.listings == 0
        )


def environment_flag(
    environment: Mapping[str, str], name: str, *, default: bool
) -> bool:
    raw = environment.get(name, "").strip().casefold()
    if not raw:
        return default
    if raw in TRUE_VALUES:
        return True
    if raw in FALSE_VALUES:
        return False
    raise ValueError(f"{name} must be true or false")


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _table_count(connection: sqlite3.Connection, table_name: str) -> int:
    if not _table_exists(connection, table_name):
        return 0
    row = connection.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()
    return int(row["count"] or 0)


def inspect_database_startup(path: Path) -> DatabaseStartupState:
    if not path.exists():
        return DatabaseStartupState(exists=False)
    try:
        with closing(sqlite3.connect(path)) as connection:
            connection.row_factory = sqlite3.Row
            has_schema = _table_exists(connection, "users")
            if not has_schema:
                return DatabaseStartupState(exists=True, has_schema=False)
            completed_profiles = 0
            if _table_exists(connection, "user_profiles"):
                rows = connection.execute("SELECT profile_json FROM user_profiles").fetchall()
                for row in rows:
                    try:
                        profile = json.loads(row["profile_json"] or "{}")
                    except Exception:
                        profile = {}
                    if isinstance(profile, dict) and profile.get("completed"):
                        completed_profiles += 1
            return DatabaseStartupState(
                exists=True,
                has_schema=True,
                users=_table_count(connection, "users"),
                profiles=_table_count(connection, "user_profiles"),
                completed_profiles=completed_profiles,
                listings=_table_count(connection, "listings"),
                scans=_table_count(connection, "scans"),
            )
    except sqlite3.Error as error:
        return DatabaseStartupState(exists=True, error=str(error))


def validate_paas_runtime(
    config: AppConfig, environment: Mapping[str, str] | None = None
) -> Path:
    environment = os.environ if environment is None else environment
    if environment.get("KHANEYAB_RUNTIME", "").strip().casefold() != PAAS_RUNTIME:
        return database_path(environment)
    if config.dashboard["host"] != "0.0.0.0":
        raise RuntimeError("PaaS dashboard host must be 0.0.0.0")
    if not (
        environment.get("KHANEYAB_DATA_DIR", "").strip()
        or environment.get("KHANEYAB_DB_PATH", "").strip()
    ):
        raise RuntimeError(
            "PaaS requires KHANEYAB_DATA_DIR or KHANEYAB_DB_PATH on a persistent volume"
        )
    path = database_path(environment)
    if not path.is_absolute():
        raise RuntimeError("PaaS database path must be absolute")
    state = inspect_database_startup(path)
    allow_empty = environment_flag(
        environment, "KHANEYAB_ALLOW_EMPTY_DATABASE", default=False
    )
    if state.error and not allow_empty:
        raise RuntimeError(
            "PaaS refused to use an unreadable production database at "
            f"{path}: {state.error}"
        )
    if state.requires_fresh_bootstrap and not allow_empty:
        raise RuntimeError(
            "PaaS refused to create a fresh production database at "
            f"{path}. Restore the SQLite database on the persistent Chabokan path "
            "or set KHANEYAB_ALLOW_EMPTY_DATABASE=true only for a deliberate first install."
        )
    if state.bootstrap_only:
        print(
            "WARNING: Production database looks bootstrap-only "
            f"(users={state.users}, completed_profiles={state.completed_profiles}, "
            f"listings={state.listings}). Check the persistent path and backups.",
            flush=True,
        )
    return path


def bootstrap_admin(
    app: DashboardApp, environment: Mapping[str, str] | None = None
) -> bool:
    environment = os.environ if environment is None else environment
    password = environment.get("KHANEYAB_ADMIN_PASSWORD", "")
    if not password or app.auth.has_admin():
        return False
    username = environment.get("KHANEYAB_ADMIN_USERNAME", "root_admin")
    display_name = environment.get("KHANEYAB_ADMIN_DISPLAY_NAME", "مدیر اصلی")
    try:
        user = app.auth.create_admin(username, display_name, password)
    except AuthError:
        print(
            "Bootstrap admin not created: invalid KHANEYAB_ADMIN_* configuration",
            flush=True,
        )
        return False
    print(f"Bootstrap admin created: @{user.username}", flush=True)
    return True


def scan_forever(
    app: DashboardApp,
    stop_event: threading.Event | None = None,
) -> None:
    stop_event = stop_event or threading.Event()
    interval = int(app.config.scan["poll_interval_seconds"])
    while not stop_event.is_set():
        try:
            result = app.run_scan()
            print(
                "Background scan complete: "
                f"{result['summary_count']} summaries, "
                f"{result['detail_count']} details, "
                f"{result['filtered_count']} filtered, "
                f"{result['expired_count']} expired, "
                f"{result['alert_count']} notifications",
                flush=True,
            )
        except Exception as error:
            print(f"Background scan failed; retrying after {interval}s: {error}", flush=True)
        if stop_event.wait(interval):
            return


def run_paas(environment: Mapping[str, str] | None = None) -> None:
    environment = os.environ if environment is None else environment
    config_path = environment.get("KHANEYAB_CONFIG_PATH", "").strip() or None
    config = load_config(config_path, environment=environment)
    db_path = validate_paas_runtime(config, environment)
    app = DashboardApp(config, database=Database(db_path))
    bootstrap_admin(app, environment)
    if environment_flag(environment, "KHANEYAB_WATCH_ENABLED", default=True):
        threading.Thread(
            target=scan_forever,
            args=(app,),
            daemon=True,
            name="khane-yab-background-scan",
        ).start()
    serve(config, app=app)
