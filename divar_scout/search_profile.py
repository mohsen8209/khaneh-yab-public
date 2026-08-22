from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlunparse

from .config import AppConfig, ROOT


DEFAULT_SEARCH_URL = "https://divar.ir/s/tehran/rent-apartment"
DISTRICTS_PATH = ROOT / "data" / "divar_tehran_districts.json"
PROFILE_VERSION = 2
REQUIREMENT_VALUES = {"required", "preferred", "irrelevant"}
ROOM_QUERY_VALUES = {
    0: "بدون اتاق",
    1: "یک",
    2: "دو",
    3: "سه",
    4: "چهار",
    5: "پنج یا بیشتر",
}
PERSIAN_DIGITS = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"
)


class SearchProfileError(ValueError):
    pass


def _digits(value: str) -> str:
    return value.translate(PERSIAN_DIGITS).replace("٬", "").replace(",", "")


def _integer(value: str | int | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(_digits(str(value)).strip())
    except ValueError as error:
        raise SearchProfileError(f"مقدار عددی نامعتبر است: {value}") from error


def districts() -> list[dict[str, Any]]:
    with DISTRICTS_PATH.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return [
        {
            "id": int(item["id"]),
            "name": str(item["name"]).strip(),
            "slug": str(item.get("slug") or ""),
            "latitude": float(item["latitude"]),
            "longitude": float(item["longitude"]),
        }
        for item in payload["districts"]
    ]


def district_catalog() -> dict[int, dict[str, Any]]:
    return {item["id"]: item for item in districts()}


def empty_profile() -> dict[str, Any]:
    return {
        "version": PROFILE_VERSION,
        "completed": False,
        "search_mode": "all_tehran",
        "source_url": DEFAULT_SEARCH_URL,
        "criteria": {},
    }


def normalize_profile(profile: dict[str, Any] | None) -> dict[str, Any]:
    if not profile:
        return empty_profile()
    normalized = copy.deepcopy(profile)
    normalized["version"] = PROFILE_VERSION
    normalized["search_mode"] = "all_tehran"
    normalized["source_url"] = DEFAULT_SEARCH_URL
    normalized.pop("source_filters", None)
    normalized.pop("source_warnings", None)
    criteria = normalized.setdefault("criteria", {})
    criteria.setdefault("max_building_floors", None)
    criteria.setdefault("max_units_per_floor", None)
    criteria.pop("minimum_neighborhood_score", None)
    return normalized


def validate_profile(payload: dict[str, Any]) -> dict[str, Any]:
    mode = str(payload.get("search_mode") or "all_tehran")
    if mode != "all_tehran":
        raise SearchProfileError(
            "جست‌وجوی عمومی فقط از همه اجاره‌آپارتمان‌های تهران انجام می‌شود"
        )

    raw = payload.get("criteria")
    if not isinstance(raw, dict):
        raise SearchProfileError("پاسخ‌های خانه موردنظر کامل نیست")
    criteria: dict[str, Any] = {}
    integer_fields = (
        "ideal_deposit",
        "hard_max_deposit",
        "stretch_max_deposit",
        "ideal_rent",
        "hard_max_rent",
        "stretch_max_rent",
        "min_size",
        "ideal_size",
        "max_size",
        "min_rooms",
        "max_rooms",
        "ideal_max_age",
        "hard_max_age",
        "min_floor",
        "max_floor",
        "max_building_floors",
        "max_units_per_floor",
    )
    optional_fields = {
        "max_size",
        "max_rooms",
        "ideal_max_age",
        "hard_max_age",
        "min_floor",
        "max_floor",
        "max_building_floors",
        "max_units_per_floor",
    }
    for name in integer_fields:
        value = raw.get(name)
        if value in {None, ""} and name in optional_fields:
            criteria[name] = None
            continue
        parsed_value = _integer(value)
        negative_allowed = name in {"min_floor", "max_floor"}
        if parsed_value is None or (parsed_value < 0 and not negative_allowed):
            raise SearchProfileError(f"مقدار {name} کامل یا معتبر نیست")
        criteria[name] = parsed_value

    if criteria["ideal_deposit"] > criteria["hard_max_deposit"]:
        raise SearchProfileError("ودیعه ایده‌آل نمی‌تواند از سقف قطعی بیشتر باشد")
    if criteria["ideal_rent"] > criteria["hard_max_rent"]:
        raise SearchProfileError("اجاره ایده‌آل نمی‌تواند از سقف قطعی بیشتر باشد")
    if criteria["hard_max_deposit"] > criteria["stretch_max_deposit"]:
        raise SearchProfileError(
            "سقف استثنایی ودیعه باید حداقل برابر سقف قطعی باشد"
        )
    if criteria["hard_max_rent"] > criteria["stretch_max_rent"]:
        raise SearchProfileError(
            "سقف استثنایی اجاره باید حداقل برابر سقف قطعی باشد"
        )
    if criteria["min_size"] <= 0 or criteria["ideal_size"] < criteria["min_size"]:
        raise SearchProfileError(
            "متراژ ایده‌آل باید حداقل برابر حداقل متراژ باشد"
        )
    if (
        criteria["max_size"] is not None
        and criteria["max_size"] < criteria["ideal_size"]
    ):
        raise SearchProfileError("حداکثر متراژ از متراژ ایده‌آل کمتر است")
    if (
        criteria["max_rooms"] is not None
        and criteria["max_rooms"] < criteria["min_rooms"]
    ):
        raise SearchProfileError("حداکثر خواب از حداقل خواب کمتر است")
    if (
        criteria["ideal_max_age"] is not None
        and criteria["hard_max_age"] is not None
        and criteria["ideal_max_age"] > criteria["hard_max_age"]
    ):
        raise SearchProfileError(
            "سن بنای ایده‌آل از حداکثر سن قابل قبول بیشتر است"
        )
    if (
        criteria["min_floor"] is not None
        and criteria["max_floor"] is not None
        and criteria["min_floor"] > criteria["max_floor"]
    ):
        raise SearchProfileError("حداقل طبقه از حداکثر طبقه بیشتر است")
    if criteria["max_building_floors"] == 0:
        raise SearchProfileError("تعداد طبقات ساختمان باید حداقل یک باشد")
    if criteria["max_units_per_floor"] == 0:
        raise SearchProfileError("تعداد واحد در طبقه باید حداقل یک باشد")

    for name in ("parking", "elevator", "storage", "balcony"):
        value = str(raw.get(name) or "")
        if value not in REQUIREMENT_VALUES:
            raise SearchProfileError(f"اهمیت {name} مشخص نشده است")
        criteria[name] = value

    district_scope = str(raw.get("district_scope") or "all")
    if district_scope not in {"all", "selected"}:
        raise SearchProfileError("محدوده محله‌ها معتبر نیست")
    catalog = district_catalog()
    raw_districts = raw.get("district_ids", [])
    if not isinstance(raw_districts, list):
        raise SearchProfileError("فهرست محله‌ها معتبر نیست")
    district_ids: list[int] = []
    for value in raw_districts:
        district_id = _integer(value)
        if district_id is None or district_id not in catalog:
            raise SearchProfileError(f"شناسه محله {value} معتبر نیست")
        if district_id not in district_ids:
            district_ids.append(district_id)
    if district_scope == "selected" and not district_ids:
        raise SearchProfileError("حداقل یک محله را انتخاب کنید")
    criteria["district_scope"] = district_scope
    criteria["district_ids"] = district_ids if district_scope == "selected" else []
    criteria["district_names"] = [
        catalog[item]["name"] for item in criteria["district_ids"]
    ]

    exceptional = raw.get("exceptional_budget_allowed", False)
    if not isinstance(exceptional, bool):
        raise SearchProfileError("وضعیت بودجه استثنایی معتبر نیست")
    criteria["exceptional_budget_allowed"] = exceptional
    if not exceptional:
        criteria["stretch_max_deposit"] = criteria["hard_max_deposit"]
        criteria["stretch_max_rent"] = criteria["hard_max_rent"]

    return {
        "version": PROFILE_VERSION,
        "completed": True,
        "search_mode": "all_tehran",
        "source_url": DEFAULT_SEARCH_URL,
        "criteria": criteria,
    }


def config_for_profile(base: AppConfig, profile: dict[str, Any]) -> AppConfig:
    profile = normalize_profile(profile)
    if not profile.get("completed"):
        return base
    criteria = profile["criteria"]
    raw = copy.deepcopy(base.raw)
    raw["search"]["url"] = crawl_url_for_profile(profile)
    raw["search"]["include_stretch_budget"] = False
    raw["profile"].update(
        {
            "ideal_deposit": criteria["ideal_deposit"],
            "ideal_rent": criteria["ideal_rent"],
            "hard_max_deposit": criteria["hard_max_deposit"],
            "hard_max_rent": criteria["hard_max_rent"],
            "stretch_max_deposit": criteria["stretch_max_deposit"],
            "stretch_max_rent": criteria["stretch_max_rent"],
            "min_size": criteria["min_size"],
            "ideal_size": criteria["ideal_size"],
            "max_size": criteria["max_size"],
            "min_rooms": criteria["min_rooms"],
            "max_rooms": criteria["max_rooms"],
            "ideal_max_age": criteria["ideal_max_age"],
            "hard_max_age": criteria["hard_max_age"],
            "min_floor": criteria["min_floor"],
            "max_floor": criteria["max_floor"],
            "max_building_floors": criteria["max_building_floors"],
            "max_units_per_floor": criteria["max_units_per_floor"],
            "parking_required": criteria["parking"] == "required",
            "elevator_required": criteria["elevator"] == "required",
            "storage_required": criteria["storage"] == "required",
            "balcony_required": criteria["balcony"] == "required",
            "parking_preferred": criteria["parking"] == "preferred",
            "elevator_preferred": criteria["elevator"] == "preferred",
            "storage_preferred": criteria["storage"] == "preferred",
            "balcony_preferred": criteria["balcony"] == "preferred",
            "allowed_district_names": criteria["district_names"],
        }
    )
    raw["scoring"]["minimum_neighborhood_score"] = 0
    return AppConfig(raw=raw, path=base.path)


def crawl_url_for_profile(profile: dict[str, Any]) -> str:
    profile = normalize_profile(profile)
    if not profile.get("completed"):
        return DEFAULT_SEARCH_URL
    criteria = profile["criteria"]
    maximum_deposit = (
        criteria["stretch_max_deposit"]
        if criteria["exceptional_budget_allowed"]
        else criteria["hard_max_deposit"]
    )
    maximum_rent = (
        criteria["stretch_max_rent"]
        if criteria["exceptional_budget_allowed"]
        else criteria["hard_max_rent"]
    )
    maximum_size = criteria.get("max_size")
    query: dict[str, str] = {
        "credit": f"-{maximum_deposit}",
        "rent": f"-{maximum_rent}",
        "size": f"{criteria['min_size']}-{maximum_size if maximum_size is not None else ''}",
        "has-photo": "true",
    }

    minimum_rooms = min(5, int(criteria["min_rooms"]))
    maximum_rooms = criteria.get("max_rooms")
    maximum_rooms = 5 if maximum_rooms is None else min(5, int(maximum_rooms))
    query["rooms"] = ",".join(
        ROOM_QUERY_VALUES[number]
        for number in range(minimum_rooms, maximum_rooms + 1)
    )
    if criteria.get("hard_max_age") is not None:
        query["building-age"] = f"-{criteria['hard_max_age']}"
    if criteria.get("min_floor") is not None or criteria.get("max_floor") is not None:
        minimum_floor = criteria.get("min_floor")
        maximum_floor = criteria.get("max_floor")
        query["floor"] = (
            f"{minimum_floor if minimum_floor is not None else ''}-"
            f"{maximum_floor if maximum_floor is not None else ''}"
        )
    if criteria.get("max_building_floors") is not None:
        query["floors_count"] = f"-{criteria['max_building_floors']}"
    if criteria.get("max_units_per_floor") is not None:
        query["unit_per_floor"] = f"-{criteria['max_units_per_floor']}"
    for criterion, parameter in (
        ("parking", "parking"),
        ("elevator", "elevator"),
        ("storage", "warehouse"),
        ("balcony", "balcony"),
    ):
        if criteria[criterion] == "required":
            query[parameter] = "true"
    if criteria["district_scope"] == "selected":
        query["districts"] = ",".join(
            str(item) for item in criteria["district_ids"]
        )

    encoded = urlencode(query)
    return urlunparse(
        ("https", "divar.ir", "/s/tehran/rent-apartment", "", encoded, "")
    )
