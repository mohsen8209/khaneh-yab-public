from __future__ import annotations

import unittest

from scripts.smoke_production import SmokeFailure, validate_listing_contract


def criteria() -> dict:
    return {
        "stretch_max_deposit": 350_000_000,
        "stretch_max_rent": 45_000_000,
        "min_size": 60,
        "max_size": 120,
        "min_rooms": 1,
        "max_rooms": 2,
        "hard_max_age": 25,
        "min_floor": None,
        "max_floor": None,
        "max_building_floors": 10,
        "max_units_per_floor": 4,
        "parking": "required",
        "elevator": "required",
        "storage": "preferred",
        "balcony": "preferred",
        "district_scope": "selected",
        "district_names": ["پاسداران"],
    }


def valid_listing() -> dict:
    return {
        "token": "valid",
        "url": "https://divar.ir/v/valid",
        "title": "آپارتمان دو خواب",
        "description": "پارکینگ خودرو و آسانسور",
        "district": "پاسداران",
        "deposit": 300_000_000,
        "rent": 30_000_000,
        "size": 85,
        "rooms": 2,
        "age": 8,
        "floor": "۱ از ۸",
        "building_floors": 8,
        "units_per_floor": 3,
        "has_parking": True,
        "has_elevator": True,
        "has_storage": False,
        "has_balcony": True,
        "image_count": 5,
    }


class ProductionSmokeContractTests(unittest.TestCase):
    def test_valid_listing_satisfies_contract(self) -> None:
        validate_listing_contract(valid_listing(), criteria())

    def test_every_hard_contract_violation_fails_the_smoke_gate(self) -> None:
        violations = (
            {"deposit": 350_000_001},
            {"rent": 45_000_001},
            {"size": None},
            {"size": 59},
            {"rooms": 3},
            {"age": None},
            {"age": 26},
            {"floor": "منفی ۱ از ۵"},
            {"building_floors": 11},
            {"units_per_floor": 5},
            {"has_parking": False},
            {"has_elevator": None},
            {"district": "نارمک"},
            {"image_count": 1},
            {"title": "هم خونه می‌خوام"},
            {"description": "پارکینگ فقط برای موتور"},
            {"url": "https://example.com/v/valid"},
        )
        for change in violations:
            listing = valid_listing()
            listing.update(change)
            with self.subTest(change=change), self.assertRaises(SmokeFailure):
                validate_listing_contract(listing, criteria())


if __name__ == "__main__":
    unittest.main()
