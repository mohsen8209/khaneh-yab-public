from __future__ import annotations

import copy
import json
import re
import time
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from .config import AppConfig
from .models import Listing


API_ROOT = "https://api.divar.ir"
USER_AGENT = "KhanehYab/0.1 (personal apartment monitor; contact: local-user)"
MAP_TILE_MAX_DEPTH = 4
MAP_TILE_MAX_REQUESTS = 25
PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
PERSIAN_MONTHS = {
    name: month
    for month, name in enumerate(
        (
            "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
            "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
        ),
        start=1,
    )
}
TEHRAN_TIMEZONE = timezone(timedelta(hours=3, minutes=30))


def normalize_digits(value: str) -> str:
    return value.translate(PERSIAN_DIGITS).replace("٬", ",")


def parse_integer(value: str | int | float | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    normalized = normalize_digits(value)
    match = re.search(r"-?\d[\d,]*", normalized)
    return int(match.group(0).replace(",", "")) if match else None


def parse_money(value: str | int | None) -> int | None:
    if value is None or isinstance(value, int):
        return value
    normalized = normalize_digits(value).replace("تومان", "").strip()
    if "رایگان" in normalized:
        return 0
    number_match = re.search(r"\d+(?:[.,]\d+)?", normalized.replace(",", ""))
    if not number_match:
        return None
    number = float(number_match.group(0))
    if "میلیارد" in normalized:
        number *= 1_000_000_000
    elif "میلیون" in normalized:
        number *= 1_000_000
    elif "هزار" in normalized:
        number *= 1_000
    return int(number)


def jalali_to_gregorian(year: int, month: int, day: int) -> tuple[int, int, int]:
    year += 1595
    days = (
        -355668
        + (365 * year)
        + ((year // 33) * 8)
        + (((year % 33) + 3) // 4)
        + day
    )
    days += (month - 1) * 31 if month < 7 else ((month - 7) * 30) + 186

    gregorian_year = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gregorian_year += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gregorian_year += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gregorian_year += (days - 1) // 365
        days = (days - 1) % 365

    gregorian_day = days + 1
    leap = (
        gregorian_year % 4 == 0 and gregorian_year % 100 != 0
    ) or gregorian_year % 400 == 0
    month_lengths = (0, 31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    gregorian_month = 1
    while gregorian_day > month_lengths[gregorian_month]:
        gregorian_day -= month_lengths[gregorian_month]
        gregorian_month += 1
    return gregorian_year, gregorian_month, gregorian_day


def _parse_divar_datetime(text: str, label: str) -> str | None:
    normalized = normalize_digits(text).replace("\u200f", "")
    match = re.search(
        rf"{re.escape(label)}\s*:\s*(\d{{1,2}})\s+([^\s،,]+)\s+"
        r"(\d{4})\s*[،,]\s*(\d{1,2}):(\d{2})",
        normalized,
    )
    if not match:
        return None
    day, month_name, year, hour, minute = match.groups()
    month = PERSIAN_MONTHS.get(month_name)
    if month is None:
        return None
    try:
        gregorian = jalali_to_gregorian(int(year), month, int(day))
        local_time = datetime(
            *gregorian,
            int(hour),
            int(minute),
            tzinfo=TEHRAN_TIMEZONE,
        )
    except (ValueError, IndexError):
        return None
    return local_time.astimezone(UTC).isoformat().replace("+00:00", "Z")


def extract_listing_dates(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    published_at = None
    bumped_at = None
    for widget in _walk_widgets(payload.get("sections", [])):
        if widget.get("widget_type") != "DESCRIPTION_ROW":
            continue
        text = (widget.get("data") or {}).get("text", "")
        if not published_at:
            published_at = _parse_divar_datetime(text, "انتشار آگهی")
        if not bumped_at:
            bumped_at = _parse_divar_datetime(text, "آخرین نردبان")
        if published_at and bumped_at:
            break
    return published_at, bumped_at


def _range_value(raw: str) -> tuple[str | None, str | None]:
    minimum, separator, maximum = raw.partition("-")
    return (minimum or None, maximum or None) if separator else (raw or None, None)


def _json_value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"boolean": {"value": value}}
    if isinstance(value, list):
        return {"repeated_string": {"value": value}}
    if isinstance(value, tuple):
        minimum, maximum = value
        body = {key: item for key, item in (("minimum", minimum), ("maximum", maximum)) if item}
        return {"number_range": body}
    return {"str": {"value": str(value)}}


def build_map_payload(config: AppConfig, search_url: str | None = None) -> dict[str, Any]:
    parsed = urlparse(search_url or config.search["url"])
    query = parse_qs(parsed.query)
    form_data: dict[str, Any] = {}

    bbox_raw = query.get("bbox", ["50.7,35.1,52.0,36.5"])[0]
    bbox = [float(item) for item in bbox_raw.split(",")]
    form_data["bbox"] = {
        "repeated_float": {"value": [{"value": item} for item in bbox]}
    }

    districts = list(query.get("districts", [""])[0].split(","))
    districts.extend(config.search.get("extra_district_ids", []))
    districts = sorted({item for item in districts if item})
    form_data["districts"] = _json_value(districts)

    for name in (
        "credit", "rent", "size", "building-age", "floor", "floors_count",
        "unit_per_floor", "price_per_square", "published_at",
    ):
        if name in query:
            form_data[name] = _json_value(_range_value(query[name][0]))
    for name in (
        "elevator", "parking", "warehouse", "balcony", "has-photo", "has-video",
        "rebuilt", "exclude_default_locations", "map_free_roaming",
    ):
        if name in query:
            form_data[name] = _json_value(query[name][0].lower() == "true")
    for name in (
        "rooms", "business-type", "building_direction", "heating_system",
        "cooling_system", "deed_type", "floor_type", "warm_water_provider", "status",
        "user_selected_location_polygon_encoded", "multi_geo_polygon_encoded",
        "multi_geo_polygon_encoded_navar_saheli",
    ):
        if name in query:
            form_data[name] = _json_value(
                [item for value in query[name] for item in value.split(",") if item]
            )
    for name in ("exchange", "toilet", "query", "recent_ads", "geo_polygon_encoded"):
        if name in query:
            form_data[name] = _json_value(query[name][-1])

    if config.search.get("include_stretch_budget", True):
        form_data.setdefault("credit", _json_value((None, None)))["number_range"]["maximum"] = str(config.profile["stretch_max_deposit"])
        form_data.setdefault("rent", _json_value((None, None)))["number_range"]["maximum"] = str(config.profile["stretch_max_rent"])

    form_data["category"] = _json_value("apartment-rent")
    min_lon, min_lat, max_lon, max_lat = bbox
    return {
        "city_ids": ["1"],
        "search_data": {"form_data": {"data": form_data}},
        "camera_info": {
            "bbox": {
                "min_latitude": min_lat,
                "min_longitude": min_lon,
                "max_latitude": max_lat,
                "max_longitude": max_lon,
            },
            "place_hash": f"1|{','.join(districts)}|apartment-rent||",
            # At city-level zoom Divar returns map clusters only. Listing-level
            # zoom is required for the response to contain actual post cards.
            "zoom": 14.0,
        },
    }


def _map_bbox(payload: dict[str, Any]) -> tuple[float, float, float, float]:
    bbox = payload["camera_info"]["bbox"]
    return (
        float(bbox["min_longitude"]),
        float(bbox["min_latitude"]),
        float(bbox["max_longitude"]),
        float(bbox["max_latitude"]),
    )


def _set_map_bbox(
    payload: dict[str, Any], bbox: tuple[float, float, float, float]
) -> None:
    min_lon, min_lat, max_lon, max_lat = bbox
    payload["search_data"]["form_data"]["data"]["bbox"] = {
        "repeated_float": {
            "value": [
                {"value": min_lon},
                {"value": min_lat},
                {"value": max_lon},
                {"value": max_lat},
            ]
        }
    }
    payload["camera_info"]["bbox"] = {
        "min_latitude": min_lat,
        "min_longitude": min_lon,
        "max_latitude": max_lat,
        "max_longitude": max_lon,
    }


def _split_map_bbox(
    bbox: tuple[float, float, float, float]
) -> tuple[tuple[float, float, float, float], ...]:
    min_lon, min_lat, max_lon, max_lat = bbox
    mid_lon = (min_lon + max_lon) / 2
    mid_lat = (min_lat + max_lat) / 2
    return (
        (min_lon, min_lat, mid_lon, mid_lat),
        (mid_lon, min_lat, max_lon, mid_lat),
        (min_lon, mid_lat, mid_lon, max_lat),
        (mid_lon, mid_lat, max_lon, max_lat),
    )


class DivarClient:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.timeout = int(config.scan["timeout_seconds"])
        self.last_fetch_complete = True

    def fetch_summaries(self, search_url: str | None = None) -> list[Listing]:
        root_payload = build_map_payload(self.config, search_url)
        posts_by_token: dict[str, dict[str, Any]] = {}
        request_count = 0
        self.last_fetch_complete = True

        def fetch_tile(payload: dict[str, Any], depth: int) -> None:
            nonlocal request_count
            if request_count >= MAP_TILE_MAX_REQUESTS:
                self.last_fetch_complete = False
                return
            request_count += 1
            response = self._request_json(
                f"{API_ROOT}/v8/mapview/viewport",
                method="POST",
                payload=payload,
            )
            posts = response.get("posts", [])
            reported_count = int(response.get("count") or len(posts))
            if not posts and response.get("clusters") and reported_count > 0:
                raise RuntimeError(
                    "Divar returned map clusters instead of listing cards; scan was not saved as empty"
                )
            for item in posts:
                card = item.get("map_post_card") or {}
                feature = item.get("map_pin_feature") or {}
                token = card.get("token") or _pin_properties(feature).get("token")
                if token:
                    posts_by_token[str(token)] = item
            if reported_count <= len(posts):
                return
            if depth >= MAP_TILE_MAX_DEPTH:
                self.last_fetch_complete = False
                return
            for child_bbox in _split_map_bbox(_map_bbox(payload)):
                child_payload = copy.deepcopy(payload)
                _set_map_bbox(child_payload, child_bbox)
                fetch_tile(child_payload, depth + 1)

        fetch_tile(root_payload, 0)
        return [parse_map_item(item) for item in posts_by_token.values()]

    def fetch_detail(self, summary: Listing) -> Listing:
        response = self._request_json(f"{API_ROOT}/v8/posts-v2/web/{summary.token}")
        return parse_detail(response, summary)

    def _request_json(
        self, url: str, *, method: str = "GET", payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": "https://divar.ir",
            "Referer": "https://divar.ir/",
            "User-Agent": USER_AGENT,
            "X-Screen-Size": "1280x720",
        }
        request = Request(url, data=body, headers=headers, method=method)
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as error:
                last_error = error
                if error.code not in {429, 500, 502, 503, 504}:
                    raise
            except URLError as error:
                last_error = error
            time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"Divar request failed after retries: {url}") from last_error


def _pin_properties(feature: dict[str, Any]) -> dict[str, Any]:
    properties = feature.get("properties") or {}
    if properties.get("type") == "Feature":
        return properties.get("properties") or {}
    return properties


def _chip_value(chips: Iterable[dict[str, Any]], suffix: str) -> int | None:
    for chip in chips:
        title = chip.get("title", "")
        if suffix in title:
            return parse_integer(title)
    return None


def _feature_from_icon(chips: Iterable[dict[str, Any]], feature: str) -> bool:
    return any(feature in (chip.get("icon_url_light") or "").lower() for chip in chips)


def parse_map_item(item: dict[str, Any]) -> Listing:
    card = item.get("map_post_card") or {}
    feature = item.get("map_pin_feature") or {}
    properties = _pin_properties(feature)
    chips = card.get("chips") or properties.get("chips") or []
    prices = card.get("price_fields") or properties.get("prices") or []
    price_by_title = {row.get("title", ""): row.get("value") for row in prices}
    age_title = next(
        (
            chip.get("title", "")
            for chip in chips
            if "سال" in chip.get("title", "") or "نوساز" in chip.get("title", "")
        ),
        "",
    )
    info = (((card.get("action_log") or {}).get("server_side_info") or {}).get("info") or {})
    images = card.get("images") or properties.get("images") or []
    subtitle = properties.get("subtitle3", "")
    return Listing(
        token=card.get("token") or properties.get("token", ""),
        title=card.get("title") or properties.get("title", ""),
        url=f"https://divar.ir/v/{card.get('token') or properties.get('token', '')}",
        deposit=parse_money(price_by_title.get("ودیعه:")),
        rent=parse_money(price_by_title.get("اجاره:")),
        size=_chip_value(chips, "متر"),
        rooms=_chip_value(chips, "اتاق"),
        age=0 if "نوساز" in age_title else parse_integer(age_title),
        latitude=feature.get("lat") or properties.get("lat"),
        longitude=feature.get("lon") or properties.get("lon"),
        location_type=feature.get("location_type") or properties.get("location_type"),
        approximate_location=bool(
            feature.get("approximate_location", properties.get("approximate_location", True))
        ),
        has_parking=_feature_from_icon(chips, "parking"),
        has_elevator=_feature_from_icon(chips, "elevator"),
        image_count=int(properties.get("image_count") or len(images)),
        images=list(images),
        is_stock_image=bool(properties.get("is_stock_image", False)),
        advertiser=subtitle.split("،", 1)[1].strip() if "،" in subtitle else None,
        updated_at=info.get("sort_date"),
        raw_summary=item,
    )


def _walk_widgets(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        if "widget_type" in value and isinstance(value.get("data"), dict):
            yield value
        for child in value.values():
            yield from _walk_widgets(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_widgets(child)


def parse_detail(payload: dict[str, Any], summary: Listing) -> Listing:
    listing = Listing(**summary.as_dict())
    rows: dict[str, str] = {}
    features: dict[str, bool] = {}
    images: list[str] = []
    description = ""
    title = summary.title
    location: dict[str, Any] | None = None

    for widget in _walk_widgets(payload.get("sections", [])):
        kind = widget.get("widget_type")
        data = widget.get("data") or {}
        if kind == "LEGEND_TITLE_ROW" and data.get("title"):
            title = data["title"]
        elif kind == "DESCRIPTION_ROW" and data.get("text"):
            text = data["text"]
            if len(text) > len(description):
                description = text
        elif kind == "GROUP_INFO_ROW":
            rows.update({item.get("title", ""): item.get("value", "") for item in data.get("items", [])})
        elif kind == "UNEXPANDABLE_ROW":
            rows[data.get("title", "")] = data.get("value", "")
        elif kind in {"GROUP_FEATURE_ROW", "FEATURE_ROW"}:
            items = data.get("items") or [data]
            for feature in items:
                if feature.get("title"):
                    features[feature["title"]] = feature.get("available", True)
        elif kind == "IMAGE_CAROUSEL":
            images = [
                item["image"].get("url")
                for item in data.get("items", [])
                if item.get("image", {}).get("url")
            ]
        elif kind == "MAP_ROW":
            location = data.get("location")

    seo = payload.get("seo") or {}
    web_info = seo.get("web_info") or {}
    schema = seo.get("post_seo_schema") or {}
    engagement = payload.get("webengage") or {}
    build_year = parse_integer(rows.get("ساخت"))
    current_jalali_year = datetime.now().year - 621

    listing.title = title
    listing.url = payload.get("share", {}).get("web_url") or listing.url
    listing.description = description or schema.get("description", "")
    listing.district = web_info.get("district_persian") or engagement.get("district")
    row_deposit = parse_money(rows.get("ودیعه"))
    row_rent = parse_money(rows.get("اجارهٔ ماهانه"))
    listing.deposit = (
        row_deposit
        if row_deposit is not None
        else listing.deposit
        if listing.deposit is not None
        else parse_integer(engagement.get("credit"))
    )
    listing.rent = (
        row_rent
        if row_rent is not None
        else listing.rent
        if listing.rent is not None
        else parse_integer(engagement.get("rent"))
    )
    listing.size = parse_integer(rows.get("متراژ")) or listing.size
    listing.rooms = parse_integer(rows.get("اتاق")) or listing.rooms
    listing.build_year = build_year
    listing.age = max(0, current_jalali_year - build_year) if build_year else listing.age
    listing.floor = rows.get("طبقه")
    listing.building_floors = (
        parse_integer(rows.get("تعداد کل طبقات"))
        or parse_integer(rows.get("تعداد طبقات"))
    )
    if listing.building_floors is None and listing.floor:
        floor_numbers = [
            int(value)
            for value in re.findall(
                r"\d+", normalize_digits(listing.floor).replace(",", "")
            )
        ]
        if len(floor_numbers) >= 2:
            listing.building_floors = floor_numbers[-1]
    listing.units_per_floor = (
        parse_integer(rows.get("تعداد واحد در هر طبقه"))
        or parse_integer(rows.get("واحد در هر طبقه"))
        or parse_integer(rows.get("واحد در طبقه"))
    )
    listing.images_for_property = rows.get("تصویر‌ها برای همین ملک است؟")
    listing.has_parking = features.get("پارکینگ", listing.has_parking)
    listing.has_elevator = features.get("آسانسور", listing.has_elevator)
    listing.has_storage = features.get("انباری", listing.has_storage)
    listing.has_balcony = features.get("بالکن", listing.has_balcony)
    listing.images = images or listing.images
    listing.image_count = int(engagement.get("image_count") or len(listing.images))
    published_at, bumped_at = extract_listing_dates(payload)
    listing.published_at = published_at or listing.published_at
    listing.updated_at = listing.updated_at or bumped_at

    if location:
        listing.location_type = location.get("type") or listing.location_type
        fuzzy = location.get("fuzzy_data") or {}
        exact = location.get("exact_data") or {}
        point = fuzzy.get("point") or exact.get("point") or {}
        listing.latitude = point.get("latitude") or listing.latitude
        listing.longitude = point.get("longitude") or listing.longitude
        listing.location_radius = fuzzy.get("radius")
        listing.approximate_location = bool(fuzzy)
    elif schema.get("geo"):
        listing.latitude = float(schema["geo"].get("latitude"))
        listing.longitude = float(schema["geo"].get("longitude"))

    listing.raw_detail = payload
    return listing
