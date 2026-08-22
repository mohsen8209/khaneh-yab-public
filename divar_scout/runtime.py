from __future__ import annotations

import os
import threading
from collections.abc import Mapping
from pathlib import Path

from .config import AppConfig, PAAS_RUNTIME, database_path, load_config
from .dashboard import DashboardApp, serve
from .db import Database


TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


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
    user = app.auth.create_admin(username, display_name, password)
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
