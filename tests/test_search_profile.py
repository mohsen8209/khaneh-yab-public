from __future__ import annotations

import copy
import unittest
from urllib.parse import parse_qs, urlparse

from divar_scout.config import AppConfig, load_config
from divar_scout.search_profile import (
    DEFAULT_SEARCH_URL,
    PROFILE_VERSION,
    SearchProfileError,
    config_for_profile,
    crawl_url_for_profile,
    crawl_urls_for_profile,
    districts,
    normalize_profile,
    validate_profile,
)


def complete_payload() -> dict:
    return {
        "search_mode": "all_tehran",
        "criteria": {
            "ideal_deposit": 300_000_000,
            "hard_max_deposit": 350_000_000,
            "stretch_max_deposit": 400_000_000,
            "ideal_rent": 30_000_000,
            "hard_max_rent": 45_000_000,
            "stretch_max_rent": 50_000_000,
            "min_size": 60,
            "ideal_size": 85,
            "max_size": 140,
            "min_rooms": 1,
            "max_rooms": 2,
            "ideal_max_age": 10,
            "hard_max_age": 20,
            "min_floor": 0,
            "max_floor": 8,
            "max_building_floors": 12,
            "max_units_per_floor": 4,
            "parking": "required",
            "elevator": "required",
            "storage": "preferred",
            "balcony": "preferred",
            "district_scope": "selected",
            "district_ids": [42, 40],
            "exceptional_budget_allowed": True,
        },
    }


class SearchProfileTests(unittest.TestCase):
    def test_official_district_catalog_has_unique_valid_map_coordinates(self) -> None:
        rows = districts()

        self.assertEqual(len(rows), 453)
        self.assertEqual(len({row["id"] for row in rows}), len(rows))
        for row in rows:
            with self.subTest(district=row["name"]):
                self.assertGreaterEqual(row["latitude"], 35.4)
                self.assertLessEqual(row["latitude"], 35.9)
                self.assertGreaterEqual(row["longitude"], 51.1)
                self.assertLessEqual(row["longitude"], 51.8)

    def test_profile_source_is_always_all_tehran_rentals(self) -> None:
        profile = validate_profile(complete_payload())

        self.assertTrue(profile["completed"])
        self.assertEqual(profile["version"], PROFILE_VERSION)
        self.assertEqual(profile["search_mode"], "all_tehran")
        self.assertEqual(profile["source_url"], DEFAULT_SEARCH_URL)
        self.assertNotIn("source_filters", profile)
        self.assertNotIn("minimum_neighborhood_score", profile["criteria"])

    def test_legacy_link_mode_is_normalized_but_cannot_be_submitted_again(self) -> None:
        legacy = complete_payload()
        legacy.update(
            {
                "version": 1,
                "completed": True,
                "search_mode": "divar_url",
                "source_url": "https://divar.ir/s/tehran/rent-apartment/niavaran",
                "source_filters": {"districts": [42]},
            }
        )
        legacy["criteria"]["minimum_neighborhood_score"] = 4.8

        normalized = normalize_profile(legacy)

        self.assertEqual(normalized["search_mode"], "all_tehran")
        self.assertEqual(normalized["source_url"], DEFAULT_SEARCH_URL)
        self.assertNotIn("source_filters", normalized)
        self.assertNotIn("minimum_neighborhood_score", normalized["criteria"])
        invalid = complete_payload()
        invalid["search_mode"] = "divar_url"
        with self.assertRaises(SearchProfileError):
            validate_profile(invalid)

    def test_all_neighborhoods_and_selected_neighborhoods_are_both_supported(self) -> None:
        selected = validate_profile(complete_payload())
        self.assertEqual(selected["criteria"]["district_names"], ["نیاوران", "آجودانیه"])

        all_payload = complete_payload()
        all_payload["criteria"]["district_scope"] = "all"
        all_payload["criteria"]["district_ids"] = [42]
        all_profile = validate_profile(all_payload)
        self.assertEqual(all_profile["criteria"]["district_ids"], [])
        self.assertEqual(all_profile["criteria"]["district_names"], [])

    def test_disabled_exceptional_budget_collapses_to_hard_limit(self) -> None:
        payload = complete_payload()
        payload["criteria"]["exceptional_budget_allowed"] = False
        profile = validate_profile(payload)

        self.assertEqual(profile["criteria"]["stretch_max_deposit"], 350_000_000)
        self.assertEqual(profile["criteria"]["stretch_max_rent"], 45_000_000)

    def test_invalid_relationships_and_empty_selected_area_are_rejected(self) -> None:
        payload = complete_payload()
        payload["criteria"]["ideal_size"] = 50
        with self.assertRaises(SearchProfileError):
            validate_profile(payload)

        payload = complete_payload()
        payload["criteria"]["district_ids"] = []
        with self.assertRaises(SearchProfileError):
            validate_profile(payload)

    def test_crawl_url_is_generated_only_from_confirmed_answers(self) -> None:
        profile = validate_profile(complete_payload())
        query = parse_qs(urlparse(crawl_url_for_profile(profile)).query)

        self.assertEqual(query["credit"], ["-400000000"])
        self.assertEqual(query["rent"], ["-50000000"])
        self.assertEqual(query["size"], ["60-140"])
        self.assertEqual(query["rooms"], ["یک,دو"])
        self.assertEqual(query["building-age"], ["-20"])
        self.assertEqual(query["floor"], ["0-8"])
        self.assertEqual(query["floors_count"], ["-12"])
        self.assertEqual(query["unit_per_floor"], ["-4"])
        self.assertEqual(query["districts"], ["42,40"])
        self.assertEqual(query["parking"], ["true"])
        self.assertEqual(query["elevator"], ["true"])
        self.assertEqual(query["has-photo"], ["true"])
        self.assertNotIn("warehouse", query)

    def test_below_ground_floors_are_blocked_for_new_and_legacy_profiles(self) -> None:
        payload = complete_payload()
        payload["criteria"]["min_floor"] = -2
        with self.assertRaises(SearchProfileError):
            validate_profile(payload)

        legacy = validate_profile(complete_payload())
        legacy["criteria"]["min_floor"] = -3
        legacy["criteria"]["max_floor"] = -1
        normalized = normalize_profile(legacy)
        self.assertEqual(normalized["criteria"]["min_floor"], 0)
        self.assertEqual(normalized["criteria"]["max_floor"], 0)
        query = parse_qs(urlparse(crawl_url_for_profile(normalized)).query)
        self.assertEqual(query["floor"], ["0-0"])

    def test_floor_zero_filter_is_mandatory_when_user_leaves_floor_open(self) -> None:
        payload = complete_payload()
        payload["criteria"]["min_floor"] = None
        payload["criteria"]["max_floor"] = None
        profile = validate_profile(payload)
        query = parse_qs(urlparse(crawl_url_for_profile(profile)).query)
        self.assertEqual(query["floor"], ["0-"])

    def test_large_district_selection_is_split_at_divar_safe_limit(self) -> None:
        payload = complete_payload()
        active = [row for row in districts() if "قدیمی" not in row["name"]]
        payload["criteria"]["district_ids"] = [row["id"] for row in active[:32]]
        profile = validate_profile(payload)

        urls = crawl_urls_for_profile(profile)
        chunks = [
            parse_qs(urlparse(url).query)["districts"][0].split(",")
            for url in urls
        ]
        bboxes = [
            parse_qs(urlparse(url).query).get("bbox", [""])[0]
            for url in urls
        ]

        self.assertEqual([len(chunk) for chunk in chunks], [4] * 8)
        self.assertEqual(
            sorted(int(item) for chunk in chunks for item in chunk),
            sorted(profile["criteria"]["district_ids"]),
        )
        self.assertTrue(all(bbox for bbox in bboxes))
        self.assertTrue(all(len(bbox.split(",")) == 4 for bbox in bboxes))

    def test_deprecated_district_ids_cannot_disable_divar_filtering(self) -> None:
        profile = validate_profile(complete_payload())
        profile["criteria"]["district_ids"] = [85, 943, 206]

        urls = crawl_urls_for_profile(profile)
        selected = parse_qs(urlparse(urls[0]).query)["districts"][0]

        self.assertEqual(selected, "85")

        payload = complete_payload()
        payload["criteria"]["district_ids"] = [943]
        with self.assertRaises(SearchProfileError):
            validate_profile(payload)

    def test_config_is_private_and_location_always_affects_score_without_cutoff(self) -> None:
        base = load_config()
        original = copy.deepcopy(base.raw)
        profile = validate_profile(complete_payload())

        personalized = config_for_profile(base, profile)

        self.assertEqual(base.raw, original)
        self.assertIsInstance(personalized, AppConfig)
        self.assertEqual(personalized.profile["ideal_size"], 85)
        self.assertEqual(personalized.profile["max_building_floors"], 12)
        self.assertEqual(personalized.profile["max_units_per_floor"], 4)
        self.assertEqual(personalized.profile["allowed_district_names"], ["نیاوران", "آجودانیه"])
        self.assertEqual(personalized.scoring["minimum_neighborhood_score"], 0)


if __name__ == "__main__":
    unittest.main()
