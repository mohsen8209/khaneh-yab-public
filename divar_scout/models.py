from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Listing:
    token: str
    title: str
    url: str
    deposit: int | None = None
    rent: int | None = None
    size: int | None = None
    rooms: int | None = None
    age: int | None = None
    build_year: int | None = None
    floor: str | None = None
    building_floors: int | None = None
    units_per_floor: int | None = None
    district: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    location_type: str | None = None
    location_radius: int | None = None
    approximate_location: bool = True
    has_parking: bool | None = None
    has_elevator: bool | None = None
    has_storage: bool | None = None
    has_balcony: bool | None = None
    image_count: int = 0
    images: list[str] = field(default_factory=list)
    is_stock_image: bool = False
    images_for_property: str | None = None
    description: str = ""
    advertiser: str | None = None
    published_at: str | None = None
    updated_at: str | None = None
    raw_summary: dict[str, Any] = field(default_factory=dict)
    raw_detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def as_storage_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("raw_summary", None)
        payload.pop("raw_detail", None)
        return payload


@dataclass
class ScoreResult:
    total: float
    location: float
    value: float
    property: float
    trust_points: float
    freshness: float
    trust: int
    location_confidence: int
    status: str
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    base_total: float | None = None
    personal_adjustment: float = 0.0
    personal_general_adjustment: float = 0.0
    personal_confidence: int = 0
    personalized: bool = False
    personal_reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
