from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ATLAS_PATH = ROOT / "data" / "tehran_neighborhoods.json"
SNAPSHOT_PATH = ROOT / "data" / "divar_tehran_districts.json"

SOURCE = {
    "provider": "Divar",
    "city_id": 1,
    "endpoint": "https://api.divar.ir/v8/places/cities/1/districts",
    "snapshot_date": "2026-08-21",
    "expected_count": 453,
}

TIER_DEFINITIONS = [
    ("exceptional", "عالی", "کیفیت پایه سکونتی و مطلوبیت شهری بسیار بالا."),
    ("excellent", "خیلی خوب", "کیفیت سکونتی بالا؛ ریزموقعیت و مزاحمت خیابان اصلی همچنان مهم است."),
    ("very_good", "خوب رو به عالی", "تعادل قوی میان کیفیت بافت، خدمات و مطلوبیت سکونتی."),
    ("good", "خوب", "گزینه سکونتی مناسب؛ کیفیت کوچه و ساختمان نقش تعیین‌کننده‌تری دارد."),
    ("acceptable", "قابل قبول", "قابل بررسی با ریزموقعیت مناسب و فایل قوی."),
    ("caution", "نیازمند احتیاط", "بافت، ازدحام یا کاربری پیرامونی نیازمند بررسی دقیق است."),
    ("low_fit", "نامناسب سکونتی", "کاربری غیرمسکونی یا مطلوبیت پایه پایین برای جست‌وجوی خانه."),
]

# These are citywide calibration anchors missing from the original 145-item atlas.
# They are fixed global neighborhood scores, not user preferences.
MANUAL_ANCHORS = {
    # North and northeast
    "آجودانیه": 7.39,
    "اقدسیه": 7.42,
    "کامرانیه": 7.48,
    "دروس": 7.21,
    "ظفر": 7.09,
    "دربند": 7.17,
    "باغ فردوس": 7.12,
    "حکمت": 6.94,
    "صاحبقرانیه": 7.18,
    "کاشانک": 6.91,
    "حصار بوعلی": 6.86,
    "دارآباد": 6.69,
    "سوهانک": 6.53,
    "گلاب دره": 6.47,
    "مینی‌سیتی (شهرک نفت)": 6.31,
    "شهرک نفت": 6.23,
    "شهرک مخابرات": 6.16,
    "شهرک بوعلی": 6.12,
    "کوهسار": 6.08,
    "شهرک محلاتی": 6.01,
    "هزارسنگ": 5.72,
    # Upper west and new western developments
    "گلستان (شهرک راه آهن)": 6.17,
    "آبشار تهران (دریاچه)": 5.84,
    "شهرک چیتگر": 5.75,
    "شهرک خرازی": 5.67,
    "شهرک شهید باقری": 5.46,
    "شهرک الهیه غرب": 5.42,
    "مهستان": 5.63,
    "حصارک": 5.31,
    "فرحزاد": 5.74,
    "مرادآباد": 4.93,
    "شهرک کوهسار": 5.38,
    "دریاچه شهدای خلیج فارس": 5.71,
    "چیتگر شمالی": 5.51,
    "چیتگر جنوبی": 5.26,
    "مروارید شهر": 5.13,
    "شهرک آتی شهر": 5.18,
    "نگین غرب": 5.02,
    "وردآورد": 4.81,
    "پیکان شهر": 4.66,
    "آزادشهر": 4.59,
    "هوانیروز": 5.28,
    "شهرک یاس": 5.22,
    "شهرک انصار": 5.04,
    # North-central and northeast
    "شیراز": 6.73,
    "اراضی عباس آباد": 6.68,
    "نظامی گنجوی (توانیر)": 6.51,
    "قبا": 6.62,
    "بنی‌هاشم": 6.04,
    "حسین‌آباد": 5.89,
    "لویزان": 5.87,
    "قنات‌کوثر": 5.68,
    "شهرک امید": 5.76,
    "مجیدآباد": 5.58,
    "شمیران‌نو": 5.36,
    "استخر": 5.34,
    # East and east-central
    "دبستان": 6.07,
    "مجیدیه": 5.82,
    "شمس‌آباد": 5.77,
    "هفت حوض": 6.06,
    "نارمک": 5.97,
    "نارمک جنوبی": 5.73,
    "دردشت": 5.66,
    "امامت": 5.69,
    "فدک": 5.57,
    "علم و صنعت": 5.54,
    "تهرانپارس غربی": 5.61,
    "تهرانپارس شرقی": 5.42,
    "تهرانپارس جنوبی": 5.36,
    "تهران‌نو": 5.43,
    "حکیمیه": 5.27,
    "اوقاف": 5.16,
    "تسلیحات": 5.18,
    "کرمان": 5.12,
    "وحیدیه": 5.01,
    "زرکش": 4.88,
    "جوادیه تهرانپارس": 4.86,
    "اتحاد": 4.37,
    "خاک سفید": 3.91,
    # Tehran-sar and southwest-middle
    "تهرانسر شمالی": 5.72,
    "تهرانسر مرکزی": 5.57,
    "تهرانسر شرقی": 5.48,
    "تهرانسر غربی": 5.33,
    "شهرک گلها (تهرانسر)": 5.51,
    "شهرک پاسداران": 5.16,
    "شمشیری": 4.77,
    "مهرآباد جنوبی": 4.61,
    "جی": 4.82,
    "هفت چنار": 4.71,
    # Central, historic center and east-central
    "امیریه": 4.71,
    "امیر بهادر": 4.44,
    "سنگلج (شاپور)": 4.02,
    "بازار": 3.43,
    "پامنار": 3.61,
    "سیروس": 3.83,
    "امین حضور": 4.31,
    "ایران": 4.58,
    "قیام": 4.07,
    "آبشار": 4.18,
    "امام حسین": 4.68,
    "شکوفه": 4.62,
    "گرگان": 4.73,
    "نظام‌آباد": 4.76,
    "مینای شمالی (مخبر)": 4.67,
    "مینای جنوبی": 4.53,
    "شهید اسدی": 4.48,
    "دژکام": 4.42,
    # South and southeast
    "نازی‌آباد": 4.92,
    "خانی آباد نو شمالی": 4.74,
    "خانی آباد نو جنوبی": 4.58,
    "یاخجی آباد": 4.45,
    "جوادیه": 4.39,
    "راه آهن": 3.96,
    "مولوی": 3.39,
    "شوش": 3.18,
    "هرندی": 2.79,
    "دولت‌آباد": 4.69,
    "کیانشهر": 4.43,
    "شهرری": 4.22,
    "مشیریه": 4.52,
    "افسریه شمالی": 4.91,
    "افسریه جنوبی": 4.72,
    "مسعودیه": 4.41,
    "قیام‌دشت قدیمی": 3.87,
    "خاوران": 3.78,
    "دولاب": 4.38,
    "اتابک": 4.19,
    "آهنگ": 4.54,
    "ابوذر": 4.37,
    "بیسیم": 3.94,
    "طیب": 3.71,
    "مینابی": 3.66,
    # Southwest and far south
    "خلیج فارس": 4.32,
    "یافت آباد شمالی": 4.16,
    "یافت آباد جنوبی": 3.94,
    "یافت‌آباد": 4.03,
    "شادآباد": 3.52,
    "شهرک ولیعصر شمالی": 4.28,
    "شهرک ولیعصر جنوبی": 4.07,
    "نعمت‌آباد": 3.82,
    "امام زاده حسن": 4.13,
    "فلاح": 3.98,
    "عبدل‌آباد": 3.54,
    "خزانه": 3.68,
    "علی‌آباد": 3.79,
}

NON_RESIDENTIAL_SCORES = {
    "فرودگاه مهرآباد": 2.15,
    "ترمینال جنوب": 2.22,
    "تصفیه خانه": 2.08,
    "کارخانه سیمان": 2.05,
    "میدان میوه و تره‌بار مرکزی": 2.17,
    "بازار آهن": 2.24,
    "بازار گل خاوران": 2.28,
    "پادگان امام حسین": 2.31,
    "بوستان چیتگر": 2.61,
    "بوستان سرخه حصار": 2.64,
    "بوستان شیان": 2.72,
    "بوستان جنگلی لویزان": 2.58,
    "بوستان ولایت": 2.69,
    "باشگاه نفت": 2.74,
}


def normalize(value: str) -> str:
    value = value.translate(str.maketrans({"ي": "ی", "ك": "ک", "ة": "ه", "ۀ": "ه"}))
    value = value.replace("\u200c", " ")
    value = re.sub(r"[^\w\s()]", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip().lower()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def compact_snapshot(source_path: Path) -> dict[str, Any]:
    payload = load_json(source_path)
    source_districts = payload.get("districts", payload)
    if not isinstance(source_districts, list):
        raise ValueError("Divar district source must contain a districts list")
    districts = []
    for item in source_districts:
        centroid = item.get("centroid") or item.get("default_location") or {}
        latitude = centroid.get("latitude", item.get("latitude"))
        longitude = centroid.get("longitude", item.get("longitude"))
        if latitude is None or longitude is None:
            raise ValueError(f"Divar district {item.get('name', item.get('id'))} has no centroid")
        districts.append(
            {
                "id": int(item["id"]),
                "name": str(item["name"]),
                "slug": str(item["slug"]),
                "latitude": round(float(latitude), 7),
                "longitude": round(float(longitude), 7),
            }
        )
    districts.sort(key=lambda item: item["id"])
    if len(districts) != SOURCE["expected_count"]:
        raise ValueError(f"Expected 453 Divar districts, received {len(districts)}")
    for key in ("id", "name", "slug"):
        if len({item[key] for item in districts}) != len(districts):
            raise ValueError(f"Divar snapshot has duplicate {key} values")
    return {"source": SOURCE, "districts": districts}


def distance_km(a: dict[str, Any], b: dict[str, Any]) -> float:
    lat_scale = 111.0
    lng_scale = 91.0
    return math.hypot(
        (a["latitude"] - b["latitude"]) * lat_scale,
        (a["longitude"] - b["longitude"]) * lng_scale,
    )


def geographic_zone(latitude: float, longitude: float) -> str:
    vertical = "north" if latitude >= 35.775 else "central" if latitude >= 35.705 else "south"
    horizontal = "west" if longitude < 51.35 else "east" if longitude >= 51.455 else "center"
    labels = {
        ("north", "west"): "شمال‌غرب",
        ("north", "center"): "شمال",
        ("north", "east"): "شمال‌شرق",
        ("central", "west"): "غرب",
        ("central", "center"): "مرکز",
        ("central", "east"): "شرق",
        ("south", "west"): "جنوب‌غرب",
        ("south", "center"): "جنوب مرکزی",
        ("south", "east"): "جنوب‌شرق",
    }
    return labels[(vertical, horizontal)]


def tier_for(score: float) -> str:
    if score >= 7.45:
        return "exceptional"
    if score >= 6.75:
        return "excellent"
    if score >= 6.15:
        return "very_good"
    if score >= 5.50:
        return "good"
    if score >= 4.70:
        return "acceptable"
    if score >= 3.50:
        return "caution"
    return "low_fit"


def legacy_base_name(name: str) -> str:
    return normalize(re.sub(r"\s+قدیمی\s*$", "", name))


def infer_score(item: dict[str, Any], anchors: list[dict[str, Any]]) -> tuple[float, str]:
    neighbors = sorted((distance_km(item, anchor), anchor) for anchor in anchors)[:7]
    weighted = 0.0
    weight_sum = 0.0
    for distance, anchor in neighbors:
        weight = 1.0 / max(distance, 0.35) ** 1.7
        weighted += anchor["score"] * weight
        weight_sum += weight
    nearest = neighbors[0][0]
    confidence = "medium" if nearest <= 1.5 else "provisional"
    return max(2.50, min(7.65, weighted / weight_sum)), confidence


def unique_score(value: float, used: set[float], immutable: bool = False) -> float:
    candidate = round(value, 3)
    if immutable:
        if candidate in used:
            raise ValueError(f"Curated score collision at {candidate}")
        used.add(candidate)
        return candidate
    for offset in [0.0, 0.001, -0.001, 0.002, -0.002, 0.003, -0.003, 0.004, -0.004,
                   0.005, -0.005, 0.006, -0.006, 0.007, -0.007, 0.008, -0.008,
                   0.009, -0.009, 0.010, -0.010, 0.011, -0.011, 0.012, -0.012]:
        adjusted = round(candidate + offset, 3)
        if adjusted not in used:
            used.add(adjusted)
            return adjusted
    raise ValueError(f"Could not allocate a unique score near {candidate}")


def build_atlas(snapshot: dict[str, Any], old_atlas: dict[str, Any]) -> dict[str, Any]:
    districts = snapshot["districts"]
    official_names = {item["name"] for item in districts}
    unknown_manual = (set(MANUAL_ANCHORS) | set(NON_RESIDENTIAL_SCORES)) - official_names
    if unknown_manual:
        raise ValueError("Manual anchors not present in Divar snapshot: " + ", ".join(sorted(unknown_manual)))

    old_scores = {name: float(score) for name, score in old_atlas["individual_scores"].items()}
    missing_old = set(old_scores) - official_names
    if missing_old:
        raise ValueError("Existing curated names missing from Divar snapshot: " + ", ".join(sorted(missing_old)))

    by_name = {item["name"]: item for item in districts}
    used_scores: set[float] = set()
    records: dict[str, dict[str, Any]] = {}
    anchor_rows: list[dict[str, Any]] = []

    # Preserve all 145 original hand-ranked values exactly.
    for name, score in old_scores.items():
        if old_atlas.get("version", 0) >= 3:
            old_record = next((row for row in old_atlas.get("catalog", []) if row["name"] == name), None)
            if old_record and old_record.get("basis") != "curated_original":
                continue
        item = by_name[name]
        fixed = unique_score(score, used_scores, immutable=True)
        records[name] = {"score": fixed, "confidence": "high", "basis": "curated_original"}
        anchor_rows.append({**item, "score": fixed})

    # Add deliberately calibrated citywide anchors before spatial interpolation.
    for name, score in {**MANUAL_ANCHORS, **NON_RESIDENTIAL_SCORES}.items():
        if name in records:
            continue
        item = by_name[name]
        fixed = unique_score(score, used_scores)
        basis = "non_residential_override" if name in NON_RESIDENTIAL_SCORES else "manual_citywide_anchor"
        records[name] = {"score": fixed, "confidence": "high", "basis": basis}
        if name not in NON_RESIDENTIAL_SCORES:
            anchor_rows.append({**item, "score": fixed})

    # Score modern names first, then let legacy labels inherit their nearby/canonical context.
    modern = [item for item in districts if item["name"] not in records and "قدیمی" not in item["name"]]
    for item in sorted(modern, key=lambda row: row["id"]):
        score, confidence = infer_score(item, anchor_rows)
        records[item["name"]] = {
            "score": unique_score(score, used_scores),
            "confidence": confidence,
            "basis": "spatial_interpolation",
        }

    normalized_records = {normalize(name): name for name in records}
    legacy = [item for item in districts if item["name"] not in records]
    for item in sorted(legacy, key=lambda row: row["id"]):
        canonical = normalized_records.get(legacy_base_name(item["name"]))
        if canonical:
            score = records[canonical]["score"] - 0.001
            confidence = records[canonical]["confidence"]
            basis = "legacy_equivalent"
        else:
            score, confidence = infer_score(item, anchor_rows)
            basis = "legacy_spatial_interpolation"
        records[item["name"]] = {
            "score": unique_score(score, used_scores),
            "confidence": confidence,
            "basis": basis,
            **({"canonical_name": canonical} if canonical else {}),
        }

    catalog = []
    for item in districts:
        record = records[item["name"]]
        place_type = "non_residential" if item["name"] in NON_RESIDENTIAL_SCORES else (
            "legacy_name" if "قدیمی" in item["name"] else "residential"
        )
        catalog.append(
            {
                **item,
                "geographic_zone": geographic_zone(item["latitude"], item["longitude"]),
                "place_type": place_type,
                **record,
                "tier": tier_for(record["score"]),
            }
        )
    catalog.sort(key=lambda row: (-row["score"], row["name"]))

    tiers = []
    for tier_id, label, note in TIER_DEFINITIONS:
        tiers.append(
            {
                "id": tier_id,
                "label": label,
                "note": note,
                "neighborhoods": [row["name"] for row in catalog if row["tier"] == tier_id],
            }
        )
    individual_scores = {row["name"]: row["score"] for row in catalog}
    aliases = old_atlas.get("aliases", {})
    invalid_aliases = {alias: target for alias, target in aliases.items() if target not in official_names}
    if invalid_aliases:
        raise ValueError(f"Aliases point outside official catalog: {invalid_aliases}")

    return {
        "version": 3,
        "source": snapshot["source"],
        "profile": "کیفیت پایه و عمومی سکونت در تهران؛ ثابت و مستقل از سلیقه هر کاربر",
        "methodology": (
            "هر ورودی رسمی دیوار نمره مستقل دارد. ۱۴۵ نمره اولیه حفظ شده، محله‌های مهم جاافتاده "
            "دستی کالیبره شده‌اند و سایر ریزنام‌ها از نزدیک‌ترین نقاط کالیبره‌شده برآورد و با سطح "
            "اطمینان مشخص شده‌اند. نمره ادعای امنیت یا کیفیت یکسان تمام خیابان‌های محله نیست."
        ),
        "score_scale": {"minimum": 0, "maximum": 8, "higher_is_better": True},
        "individual_scores": individual_scores,
        "tiers": tiers,
        "catalog": catalog,
        "aliases": aliases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the complete Tehran neighborhood atlas")
    parser.add_argument(
        "--source",
        type=Path,
        default=SNAPSHOT_PATH,
        help="Raw Divar response or the committed compact snapshot",
    )
    args = parser.parse_args()

    old_atlas = load_json(ATLAS_PATH)
    snapshot = compact_snapshot(args.source)
    atlas = build_atlas(snapshot, old_atlas)
    SNAPSHOT_PATH.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    ATLAS_PATH.write_text(
        json.dumps(atlas, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    basis_counts: dict[str, int] = {}
    for row in atlas["catalog"]:
        basis_counts[row["basis"]] = basis_counts.get(row["basis"], 0) + 1
    print(f"Built {len(atlas['catalog'])} Tehran entries")
    print(json.dumps(basis_counts, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
