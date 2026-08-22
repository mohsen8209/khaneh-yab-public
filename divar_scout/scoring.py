from __future__ import annotations

import re
import statistics
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from .config import AppConfig
from .models import Listing, ScoreResult
from .neighborhoods import NeighborhoodAtlas, normalize_text


SHARED_HOUSING_PATTERNS = (
    r"ه+م+\s*خ+[او]?ن+(?:ه|گی|ای|ی)?",
    r"ه+م+\s*(?:[اآ]?تاق|اطاق)(?:ی)?",
    r"(?:شریک|شریکی)\s*(?:خانه|خونه|منزل|آپارتمان|اتاق|اطاق)",
    r"(?:اجاره|کرایه)\s*(?:ی\s*)?(?:تخت|اتاق|اطاق)",
    r"پانسی+ون",
    r"(?:اجاره|رزرو)\s+(?:تخت|خواب\s*گاه)",
    r"(?:تخت|ظرفیت)\s+(?:خالی\s+)?(?:در\s+)?خواب\s*گاه",
    r"room\s*mate",
)
SHARED_HOUSING_TITLE_PATTERNS = (
    r"^(?:(?:سوئیت|سوییت|سویت)\s*)?(?:خواب\s*گاه|پانسی+ون|هاستل|اقامت\s*گاه)",
    r"(?:خواب\s*گاه|هاستل|اقامت\s*گاه).*(?:دخترانه|پسرانه|دانشجویی|اشتراکی)",
)
MOTOR_PARKING_PATTERNS = (
    r"(?:پارکینگ|پارک|p)\s*(?:فقط\s*)?(?:جهت\s*|برای\s*|مخصوص\s*)?"
    r"(?:موتور|موتورسیکلت)",
    r"فقط\s+(?:جای\s+)?موتور",
)
DECORATIVE_IMAGE_PATTERNS = (
    r"عکس\s*(?:ها\s*)?(?:تزئینی|تزیینی)",
    r"تصویر\s*(?:ها\s*)?(?:تزئینی|تزیینی)",
    r"عکس\s+نمونه",
)
AMBIGUOUS_PARKING_PATTERNS = (
    r"پارکینگ\s+مزاحم",
    r"پارکینگ\s+مشاع",
    r"جای\s+پارک",
    r"پارک\s+در\s+حیاط",
)
MIN_PLAUSIBLE_EFFECTIVE_MONTHLY_COST = 5_000_000


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _floor_number(value: str | None) -> int | None:
    normalized = normalize_text(value or "").translate(
        str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
    )
    if normalized in {"همکف", "هم کف"}:
        return 0
    match = re.search(r"-?\d+", normalized)
    return int(match.group()) if match else None


def listing_exclusion_reason(listing: Listing | Mapping[str, Any]) -> str | None:
    def value(name: str, default: Any = None) -> Any:
        if isinstance(listing, Mapping):
            return listing.get(name, default)
        return getattr(listing, name, default)

    title = normalize_text(str(value("title", "") or ""))
    description = normalize_text(str(value("description", "") or ""))
    text = f"{title} {description}".strip()
    if _matches_any(text, SHARED_HOUSING_PATTERNS) or _matches_any(
        title, SHARED_HOUSING_TITLE_PATTERNS
    ):
        return "آگهی هم‌خانه، خوابگاه، پانسیون یا اجاره اتاق است"
    if _matches_any(text, MOTOR_PARKING_PATTERNS):
        return "پارکینگ آگهی فقط برای موتور است"
    deposit = value("deposit")
    rent = value("rent")
    if deposit is not None and rent is not None:
        effective_monthly_cost = max(0, int(rent)) + (0.03 * max(0, int(deposit)))
        if effective_monthly_cost < MIN_PLAUSIBLE_EFFECTIVE_MONTHLY_COST:
            return "قیمت آگهی صوری یا غیرواقعی است"
    if bool(value("is_stock_image", False)) or _matches_any(
        text, DECORATIVE_IMAGE_PATTERNS
    ):
        return "عکس آگهی تزئینی یا نمونه است"
    image_answer = normalize_text(str(value("images_for_property", "") or ""))
    if image_answer in {"خیر", "نه"}:
        return "عکس‌ها متعلق به همین ملک نیستند"
    image_count = int(value("image_count", 0) or 0)
    if image_count <= 0:
        return "آگهی بدون عکس است"
    if image_count == 1:
        return "آگهی فقط یک عکس دارد"
    return None


def profile_scope_exclusion_reason(
    listing: Listing | Mapping[str, Any], config: AppConfig
) -> str | None:
    def value(name: str, default: Any = None) -> Any:
        if isinstance(listing, Mapping):
            return listing.get(name, default)
        return getattr(listing, name, default)

    profile = config.profile
    deposit = value("deposit")
    rent = value("rent")
    if deposit is None or rent is None:
        return "ودیعه یا اجاره آگهی مشخص نیست و سازگاری آن با بودجه قابل تأیید نیست"
    allowed_districts = {
        normalize_text(name) for name in profile.get("allowed_district_names", [])
    }
    if allowed_districts and normalize_text(value("district", "")) not in allowed_districts:
        return "محله خارج از محدوده انتخابی کاربر است"
    source_bbox = profile.get("source_bbox")
    latitude = value("latitude")
    longitude = value("longitude")
    if source_bbox and latitude is not None and longitude is not None:
        min_lon, min_lat, max_lon, max_lat = source_bbox
        if not (min_lon <= longitude <= max_lon and min_lat <= latitude <= max_lat):
            return "موقعیت آگهی بیرون از محدوده نقشه فیلتر دیوار است"
    size = value("size")
    if size is not None and size < int(profile.get("min_size", 0)):
        return "متراژ کمتر از حداقل موردنظر است"
    maximum_size = profile.get("max_size")
    if size is not None and maximum_size is not None and size > int(maximum_size):
        return "متراژ بیشتر از حداکثر موردنظر است"
    rooms = value("rooms")
    if rooms is not None and rooms < int(profile.get("min_rooms", 0)):
        return "تعداد خواب کمتر از حداقل موردنظر است"
    maximum_rooms = profile.get("max_rooms")
    if rooms is not None and maximum_rooms is not None and rooms > int(maximum_rooms):
        return "تعداد خواب بیشتر از حداکثر موردنظر است"
    age = value("age")
    maximum_age = profile.get("hard_max_age")
    if age is not None and maximum_age is not None and age > int(maximum_age):
        return "سن بنا بیشتر از حداکثر قابل قبول است"
    floor = _floor_number(value("floor"))
    minimum_floor = profile.get("min_floor")
    maximum_floor = profile.get("max_floor")
    if floor is not None and minimum_floor is not None and floor < int(minimum_floor):
        return "طبقه پایین‌تر از محدوده قابل قبول است"
    if floor is not None and maximum_floor is not None and floor > int(maximum_floor):
        return "طبقه بالاتر از محدوده قابل قبول است"
    building_floors = value("building_floors")
    maximum_building_floors = profile.get("max_building_floors")
    if (
        building_floors is not None
        and maximum_building_floors is not None
        and building_floors > int(maximum_building_floors)
    ):
        return "تعداد طبقات ساختمان بیشتر از حداکثر موردنظر است"
    units_per_floor = value("units_per_floor")
    maximum_units_per_floor = profile.get("max_units_per_floor")
    if (
        units_per_floor is not None
        and maximum_units_per_floor is not None
        and units_per_floor > int(maximum_units_per_floor)
    ):
        return "تعداد واحد در هر طبقه بیشتر از حداکثر موردنظر است"
    for required, field, message in (
        ("parking_required", "has_parking", "پارکینگ خودرو ندارد یا تأیید نشده است"),
        ("elevator_required", "has_elevator", "آسانسور ندارد یا تأیید نشده است"),
        ("storage_required", "has_storage", "انباری ندارد یا تأیید نشده است"),
        ("balcony_required", "has_balcony", "بالکن ندارد یا تأیید نشده است"),
    ):
        if profile.get(required) and value(field) is not True:
            return message
    if deposit > int(profile["stretch_max_deposit"]):
        return "ودیعه بیشتر از سقف نهایی است"
    if rent > int(profile["stretch_max_rent"]):
        return "اجاره بیشتر از سقف نهایی است"
    return None


def _freshness_points(published_at: str | None) -> float:
    if not published_at:
        return 0.25
    try:
        published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.25
    age_days = max(0.0, (datetime.now(UTC) - published).total_seconds() / 86400)
    if age_days <= 1:
        return 1.0
    if age_days <= 3:
        return 0.8
    if age_days <= 7:
        return 0.5
    if age_days <= 14:
        return 0.25
    return 0.1


def _property_points(listing: Listing, config: AppConfig, reasons: list[str]) -> float:
    profile = config.profile
    minimum_size = max(1, int(profile.get("min_size", 1)))
    ideal_size = max(minimum_size, int(profile.get("ideal_size", minimum_size + 20)))
    if listing.size is None:
        size_points = 0.25
    else:
        progress = (listing.size - minimum_size) / max(1, ideal_size - minimum_size)
        size_points = min(1.3, max(0.25, 0.55 + progress * 0.75))
        if listing.size >= ideal_size:
            reasons.append(f"متراژ خوب ({listing.size} متر)")

    rooms_points = 0.6 if (listing.rooms or 0) > int(profile.get("min_rooms", 0)) else 0.4
    if listing.age is None:
        age_points = 0.2
    elif profile.get("ideal_max_age") is not None and listing.age <= int(profile["ideal_max_age"]):
        age_points = 1.0
        reasons.append("سن بنا داخل محدوده ایده‌آل")
    elif listing.age <= 3:
        age_points = 1.0
        reasons.append("بنای نوساز یا کم‌سن")
    elif listing.age <= 5:
        age_points = 0.9
        reasons.append("بنای نوساز یا کم‌سن")
    elif listing.age <= 10:
        age_points = 0.75
    elif listing.age <= 15:
        age_points = 0.6
    elif listing.age <= 20:
        age_points = 0.4
    elif listing.age <= 30:
        age_points = 0.2
    else:
        age_points = 0.05

    amenity_points = 0.0
    if listing.has_parking and profile.get("parking_preferred", False):
        amenity_points += 0.1
    if listing.has_elevator and profile.get("elevator_preferred", False):
        amenity_points += 0.1
    if listing.has_storage and profile.get("storage_preferred", False):
        amenity_points += 0.15
    if listing.has_balcony and profile.get("balcony_preferred", True):
        amenity_points += 0.25
        reasons.append("دارای بالکن")
    return round(min(3.0, size_points + rooms_points + age_points + amenity_points), 2)


def _value_points(
    listing: Listing,
    config: AppConfig,
    comparable_costs: list[float],
    reasons: list[str],
    risks: list[str],
) -> tuple[float, bool]:
    profile = config.profile
    deposit = listing.deposit or 0
    rent = listing.rent or 0
    rate = float(profile["deposit_to_monthly_rate"])
    suspiciously_low = False

    if listing.deposit is None or listing.rent is None:
        return 1.5, False

    if listing.size and len(comparable_costs) >= 3:
        own_cost = (rent + deposit * rate) / listing.size
        median = statistics.median(comparable_costs)
        ratio = own_cost / median if median else 1.0
        if ratio < 0.65:
            points = 4.2
            suspiciously_low = True
            risks.append("قیمت بیش از حد پایین‌تر از فایل‌های مشابه است")
        elif ratio <= 0.80:
            points = 5.0
        elif ratio <= 0.90:
            points = 4.6
        elif ratio <= 1.00:
            points = 4.1
        elif ratio <= 1.10:
            points = 3.3
        elif ratio <= 1.20:
            points = 2.4
        else:
            points = 1.4
        delta = round((1 - ratio) * 100)
        if delta > 0:
            reasons.append(f"حدود {delta}٪ ارزان‌تر از مشابه‌های محله")
        else:
            risks.append(f"حدود {abs(delta)}٪ گران‌تر از مشابه‌های محله")
        return points, suspiciously_low

    if listing.size:
        effective_cost = rent + deposit * rate
        ideal_effective_cost = (
            profile["ideal_rent"] + profile["ideal_deposit"] * rate
        )
        ideal_unit_cost = ideal_effective_cost / profile["min_size"]
        unit_cost_ratio = (effective_cost / listing.size) / ideal_unit_cost
        if unit_cost_ratio <= 0.80:
            points = 4.8
        elif unit_cost_ratio <= 0.95:
            points = 4.5
        elif unit_cost_ratio <= 1.05:
            points = 4.2
        elif unit_cost_ratio <= 1.15:
            points = 3.8
        elif unit_cost_ratio <= 1.30:
            points = 3.3
        else:
            points = 2.6
        if points >= 4.2:
            reasons.append("هزینه مؤثر مناسب نسبت به متراژ")
        else:
            risks.append("هزینه مؤثر هر متر بالاتر از هدف است")
    else:
        points = 4.2 if (
            deposit <= profile["ideal_deposit"] and rent <= profile["ideal_rent"]
        ) else 3.5

    if deposit <= profile["ideal_deposit"] and rent <= profile["ideal_rent"]:
        reasons.append("داخل بودجه ایده‌آل")
    elif deposit <= profile["hard_max_deposit"] and rent <= profile["hard_max_rent"]:
        reasons.append("داخل سقف قطعی بودجه")
    else:
        risks.append("وارد محدوده بودجه استثنایی شده است")
    return points, False


def score_listing(
    listing: Listing,
    config: AppConfig,
    atlas: NeighborhoodAtlas,
    *,
    duplicate_image_count: int = 0,
    comparable_costs: list[float] | None = None,
) -> ScoreResult:
    text = f"{listing.title}\n{listing.description}"
    normalized = normalize_text(text)
    reasons: list[str] = []
    risks: list[str] = []
    hard_rejections: list[str] = []
    trust = 100

    exclusion_reason = listing_exclusion_reason(listing)
    if exclusion_reason:
        hard_rejections.append(exclusion_reason)
    profile_exclusion = profile_scope_exclusion_reason(listing, config)
    if profile_exclusion:
        hard_rejections.append(profile_exclusion)
    if _matches_any(normalized, AMBIGUOUS_PARKING_PATTERNS):
        trust -= 12
        risks.append("نوع پارکینگ اختصاصی و عادی نیست یا نیازمند پرسش است")

    if listing.is_stock_image or _matches_any(normalized, DECORATIVE_IMAGE_PATTERNS):
        trust -= 65
    if listing.image_count == 0:
        trust -= 70
    elif listing.image_count == 1:
        trust -= 30
        risks.append("فقط یک عکس دارد")
    elif listing.image_count == 2:
        trust -= 15
        risks.append("تعداد عکس‌ها کم است")
    else:
        reasons.append(f"دارای {listing.image_count} عکس")

    image_answer = normalize_text(listing.images_for_property or "")
    if image_answer in {"خیر", "نه"}:
        trust -= 60
    elif image_answer == "بله":
        reasons.append("تعلق عکس‌ها به همین ملک تأیید شده")
    else:
        trust -= 5

    if duplicate_image_count:
        penalty = 35 if duplicate_image_count >= 2 else 15
        trust -= penalty
        risks.append(f"عکس مشترک با {duplicate_image_count} آگهی دیگر")
    if "فایل های مشابه" in normalized or "فایلهای مشابه" in normalized:
        trust -= 12
        risks.append("متن قالبی و وعده فایل‌های مشابه دارد")
    if listing.deposit is None or listing.rent is None:
        trust -= 25
        risks.append("قیمت کامل و ساختاریافته نیست")

    rating = atlas.rate(listing.district)
    location_confidence = 100
    location_score = rating.score
    if not rating.known:
        location_confidence -= 20
        risks.append("محله هنوز در اطلس رتبه‌بندی نشده")
    else:
        reasons.append(f"محله {rating.label}: {rating.name}")
    if listing.latitude is None or listing.longitude is None:
        location_confidence -= 45
        risks.append("پین مکانی قابل استفاده ندارد")
    if listing.approximate_location:
        location_confidence -= 20
        location_score -= 0.3
        risks.append("پین آگهی تقریبی است")
    if (listing.location_radius or 0) >= 500:
        location_confidence -= 10

    declared_normalized = normalize_text(rating.name)
    title_locations = atlas.names_in_text(listing.title)
    address_match = re.search(r"آدرس\s*[:：]\s*([^\n]+)", listing.description)
    address_locations = atlas.names_in_text(address_match.group(1)) if address_match else set()
    conflicting = {
        name
        for name in title_locations.union(address_locations)
        if normalize_text(name) != declared_normalized
    }
    if conflicting:
        location_confidence -= 25
        location_score -= 0.7
        risks.append("نام محله با متن/آدرس آگهی نیازمند تطبیق است: " + "، ".join(sorted(conflicting)))

    comparable_costs = comparable_costs or []
    value_points, suspiciously_low = _value_points(
        listing, config, comparable_costs, reasons, risks
    )
    if suspiciously_low:
        trust -= 20

    trust = max(0, min(100, trust))
    location_confidence = max(0, min(100, location_confidence))
    location_score = round(max(0.0, min(8.0, location_score)), 2)
    property_points = _property_points(listing, config, reasons)
    trust_points = round(3 * trust / 100, 2)
    freshness = _freshness_points(listing.published_at)
    total = round(
        min(20.0, location_score + value_points + property_points + trust_points + freshness),
        1,
    )

    profile = config.profile
    scoring = config.scoring
    over_hard_budget = (
        (listing.deposit or 0) > profile["hard_max_deposit"]
        or (listing.rent or 0) > profile["hard_max_rent"]
    )
    over_stretch_budget = (
        (listing.deposit or 0) > profile["stretch_max_deposit"]
        or (listing.rent or 0) > profile["stretch_max_rent"]
    )
    neighborhood_rejected = rating.score < scoring["minimum_neighborhood_score"]
    balanced_for_alert = (
        value_points >= scoring.get("minimum_alert_value_score", 3.5)
        and property_points >= scoring.get("minimum_alert_property_score", 2.0)
    )

    if over_stretch_budget:
        hard_rejections.append("بیشتر از سقف بودجه استثنایی است")
    if neighborhood_rejected:
        hard_rejections.append("محله پایین‌تر از حد قابل قبول این جست‌وجو است")

    if hard_rejections:
        risks = hard_rejections + risks
        status = "rejected"
    elif over_hard_budget:
        is_exceptional = (
            total >= scoring["minimum_exceptional_budget_score"]
            and trust >= 85
            and location_score >= 6.5
            and balanced_for_alert
        )
        status = "alert" if is_exceptional else "review"
        if is_exceptional:
            reasons.append("با وجود عبور از بودجه، شرایط استثنایی دارد")
    elif trust < scoring["minimum_trust"] or location_confidence < scoring["minimum_location_confidence"]:
        status = "review"
    elif total >= scoring["minimum_alert_score"] and balanced_for_alert:
        status = "alert"
    else:
        status = "candidate"
        if total >= scoring["minimum_alert_score"] and not balanced_for_alert:
            risks.append("نمره کلی بالاست اما ارزش و کیفیت ملک برای برتر شدن متوازن نیست")

    return ScoreResult(
        total=total,
        location=location_score,
        value=round(value_points, 2),
        property=property_points,
        trust_points=trust_points,
        freshness=round(freshness, 2),
        trust=trust,
        location_confidence=location_confidence,
        status=status,
        reasons=reasons[:6],
        risks=risks[:8],
    )
