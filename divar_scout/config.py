from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config.json"
DEFAULT_DB_PATH = ROOT / "data" / "divar_scout.sqlite3"
NEIGHBORHOODS_PATH = ROOT / "data" / "tehran_neighborhoods.json"
PAAS_RUNTIME = "paas"


@dataclass(frozen=True)
class AppConfig:
    raw: dict[str, Any]
    path: Path

    @property
    def search(self) -> dict[str, Any]:
        return self.raw["search"]

    @property
    def profile(self) -> dict[str, Any]:
        return self.raw["profile"]

    @property
    def scoring(self) -> dict[str, Any]:
        return self.raw["scoring"]

    @property
    def scan(self) -> dict[str, Any]:
        return self.raw["scan"]

    @property
    def notifications(self) -> dict[str, Any]:
        return self.raw["notifications"]

    @property
    def personalization(self) -> dict[str, Any]:
        return self.raw["personalization"]

    @property
    def dashboard(self) -> dict[str, Any]:
        return self.raw["dashboard"]


def load_config(
    path: str | Path | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> AppConfig:
    environment = os.environ if environment is None else environment
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    with config_path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    _apply_environment(raw, environment)
    _validate(raw, runtime_mode=environment.get("KHANEYAB_RUNTIME", "local"))
    return AppConfig(raw=raw, path=config_path.resolve())


def database_path(environment: Mapping[str, str] | None = None) -> Path:
    environment = os.environ if environment is None else environment
    explicit_path = environment.get("KHANEYAB_DB_PATH", "").strip()
    if explicit_path:
        return Path(explicit_path).expanduser()
    data_dir = environment.get("KHANEYAB_DATA_DIR", "").strip()
    if data_dir:
        return Path(data_dir).expanduser() / DEFAULT_DB_PATH.name
    return DEFAULT_DB_PATH


def _apply_environment(raw: dict[str, Any], environment: Mapping[str, str]) -> None:
    runtime_mode = environment.get("KHANEYAB_RUNTIME", "local").strip().casefold()
    if runtime_mode != PAAS_RUNTIME:
        return
    dashboard = raw["dashboard"]
    dashboard["host"] = environment.get("KHANEYAB_HOST", "0.0.0.0").strip()
    port = (
        environment.get("KHANEYAB_PORT", "").strip()
        or environment.get("PORT", "").strip()
    )
    if port:
        dashboard["port"] = int(port)


def _validate(raw: dict[str, Any], *, runtime_mode: str = "local") -> None:
    required = {
        "search", "profile", "scoring", "scan", "notifications",
        "personalization", "dashboard",
    }
    missing = required.difference(raw)
    if missing:
        raise ValueError(f"Missing config sections: {', '.join(sorted(missing))}")

    instance = raw.get("instance")
    if instance:
        expected_root = str(instance.get("expected_root_name") or "")
        expected_port = int(instance.get("expected_dashboard_port") or 0)
        if instance.get("id") != "public-house-finder":
            raise ValueError("Public project requires its own instance id")
        if (
            expected_root
            and runtime_mode.strip().casefold() != PAAS_RUNTIME
            and ROOT.name != expected_root
        ):
            raise ValueError(
                f"Public project must run from a {expected_root!r} directory, not {ROOT.name!r}"
            )
        if expected_port and int(raw["dashboard"]["port"]) != expected_port:
            raise ValueError("Public dashboard port does not match its isolated instance port")
        if DEFAULT_DB_PATH.resolve().parent != (ROOT / "data").resolve():
            raise ValueError("Public database path escaped the public project root")

    profile = raw["profile"]
    if profile["hard_max_rent"] > profile["stretch_max_rent"]:
        raise ValueError("hard_max_rent must not exceed stretch_max_rent")
    if profile["hard_max_deposit"] > profile["stretch_max_deposit"]:
        raise ValueError("hard_max_deposit must not exceed stretch_max_deposit")
    if not raw["search"]["url"].startswith("https://divar.ir/"):
        raise ValueError("search.url must be a divar.ir URL")
    personalization = raw["personalization"]
    if personalization["activation_signals"] >= personalization["full_strength_signals"]:
        raise ValueError("activation_signals must be below full_strength_signals")
    if not 0 < personalization["max_adjustment"] <= 3:
        raise ValueError("max_adjustment must be between 0 and 3")
