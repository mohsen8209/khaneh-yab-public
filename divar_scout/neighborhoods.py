from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import NEIGHBORHOODS_PATH


ARABIC_NORMALIZATION = str.maketrans({"ي": "ی", "ك": "ک", "ة": "ه", "ۀ": "ه"})


def normalize_text(value: Any) -> str:
    value = str(value or "").translate(ARABIC_NORMALIZATION).replace("\u200c", " ")
    value = re.sub(r"[^\w\s()]", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip().lower()


@dataclass(frozen=True)
class NeighborhoodRating:
    name: str
    score: float
    tier: str
    label: str
    note: str
    known: bool = True
    district_id: int | None = None
    slug: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    geographic_zone: str | None = None
    place_type: str | None = None
    confidence: str | None = None
    basis: str | None = None


class NeighborhoodAtlas:
    def __init__(self, path: str | Path = NEIGHBORHOODS_PATH) -> None:
        with Path(path).open("r", encoding="utf-8") as handle:
            self.raw: dict[str, Any] = json.load(handle)
        self._ratings: dict[str, NeighborhoodRating] = {}
        self._canonical: dict[str, str] = {}
        individual_scores = self.raw.get("individual_scores")
        if not isinstance(individual_scores, dict):
            raise ValueError("Neighborhood atlas must define individual_scores")
        self._individual_scores: dict[str, float] = {
            name: float(score) for name, score in individual_scores.items()
        }
        tier_name_list = [
            name for tier in self.raw["tiers"] for name in tier["neighborhoods"]
        ]
        tier_names = set(tier_name_list)
        if len(tier_name_list) != len(tier_names):
            raise ValueError("A neighborhood appears in more than one atlas tier")
        missing_scores = tier_names.difference(self._individual_scores)
        extra_scores = set(self._individual_scores).difference(tier_names)
        if missing_scores or extra_scores:
            problems: list[str] = []
            if missing_scores:
                problems.append(f"missing scores: {', '.join(sorted(missing_scores))}")
            if extra_scores:
                problems.append(f"unknown scores: {', '.join(sorted(extra_scores))}")
            raise ValueError("Invalid individual_scores; " + "; ".join(problems))

        catalog = self.raw.get("catalog")
        if not isinstance(catalog, list):
            raise ValueError("Neighborhood atlas must define the official Divar catalog")
        expected_count = int(self.raw.get("source", {}).get("expected_count", 0))
        if not expected_count or len(catalog) != expected_count:
            raise ValueError(
                f"Official Divar catalog must contain {expected_count} entries, found {len(catalog)}"
            )
        for field in ("id", "name", "slug"):
            values = [row.get(field) for row in catalog]
            if None in values or len(values) != len(set(values)):
                raise ValueError(f"Official Divar catalog has missing or duplicate {field} values")
        catalog_names = {row["name"] for row in catalog}
        if catalog_names != set(self._individual_scores):
            raise ValueError("Official Divar catalog and individual_scores do not cover the same names")

        tier_by_name = {
            name: tier["id"]
            for tier in self.raw["tiers"]
            for name in tier["neighborhoods"]
        }
        self._catalog: tuple[dict[str, Any], ...] = tuple(catalog)
        self._metadata: dict[str, dict[str, Any]] = {}
        for row in catalog:
            name = row["name"]
            if float(row["score"]) != self._individual_scores[name]:
                raise ValueError(f"Catalog score mismatch for {name}")
            if row["tier"] != tier_by_name[name]:
                raise ValueError(f"Catalog tier mismatch for {name}")
            if not (35.0 <= float(row["latitude"]) <= 36.0):
                raise ValueError(f"Invalid latitude for {name}")
            if not (50.0 <= float(row["longitude"]) <= 52.0):
                raise ValueError(f"Invalid longitude for {name}")
            if row.get("confidence") not in {"high", "medium", "provisional"}:
                raise ValueError(f"Invalid confidence for {name}")
            self._metadata[name] = row

        for tier in self.raw["tiers"]:
            for name in tier["neighborhoods"]:
                normalized = normalize_text(name)
                if normalized in self._canonical:
                    raise ValueError(f"Neighborhood names normalize to the same value: {name}")
                metadata = self._metadata[name]
                self._canonical[normalized] = name
                self._ratings[normalized] = NeighborhoodRating(
                    name=name,
                    score=self._individual_scores[name],
                    tier=tier["id"],
                    label=tier["label"],
                    note=tier["note"],
                    district_id=int(metadata["id"]),
                    slug=str(metadata["slug"]),
                    latitude=float(metadata["latitude"]),
                    longitude=float(metadata["longitude"]),
                    geographic_zone=str(metadata["geographic_zone"]),
                    place_type=str(metadata["place_type"]),
                    confidence=str(metadata["confidence"]),
                    basis=str(metadata["basis"]),
                )
        for alias, canonical in self.raw.get("aliases", {}).items():
            canonical_key = normalize_text(canonical)
            if canonical_key in self._ratings:
                self._ratings[normalize_text(alias)] = self._ratings[canonical_key]

    def rate(self, name: str | None) -> NeighborhoodRating:
        if not name:
            return NeighborhoodRating(
                name="نامشخص", score=4.0, tier="unknown", label="نامشخص",
                note="محله از داده آگهی استخراج نشده است.", known=False
            )
        normalized = normalize_text(name)
        if normalized in self._ratings:
            return self._ratings[normalized]
        without_parenthetical = normalize_text(re.sub(r"\([^)]*\)", "", name))
        if without_parenthetical in self._ratings:
            return self._ratings[without_parenthetical]
        return NeighborhoodRating(
            name=name, score=4.0, tier="unknown", label="نیازمند رتبه‌بندی",
            note="این نام در فهرست رسمی فعلی دیوار برای تهران ثبت نشده است.", known=False
        )

    def names_in_text(self, text: str) -> set[str]:
        normalized = f" {normalize_text(text)} "
        matches: set[str] = set()
        for key, rating in self._ratings.items():
            if len(key) >= 4 and f" {key} " in normalized:
                matches.add(rating.name)
        return matches

    @property
    def canonical_names(self) -> set[str]:
        return set(self._canonical.values())

    @property
    def individual_scores(self) -> dict[str, float]:
        return dict(self._individual_scores)

    @property
    def catalog(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(row) for row in self._catalog)


@lru_cache(maxsize=1)
def default_atlas() -> NeighborhoodAtlas:
    return NeighborhoodAtlas()
