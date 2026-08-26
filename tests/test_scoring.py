import unittest

from divar_scout.config import load_config
from divar_scout.models import Listing
from divar_scout.neighborhoods import default_atlas
from divar_scout.scoring import (
    listing_exclusion_reason,
    profile_scope_exclusion_reason,
    score_listing,
)


def good_listing(**changes):
    values = {
        "token": "good-1",
        "title": "آپارتمان ۸۵ متر دو خواب",
        "url": "https://divar.ir/v/good-1",
        "deposit": 300_000_000,
        "rent": 30_000_000,
        "size": 85,
        "rooms": 2,
        "age": 7,
        "district": "پاسداران",
        "latitude": 35.79,
        "longitude": 51.46,
        "approximate_location": False,
        "has_parking": True,
        "has_elevator": True,
        "has_storage": True,
        "has_balcony": True,
        "image_count": 8,
        "images_for_property": "بله",
    }
    values.update(changes)
    return Listing(**values)


class ScoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config()
        cls.config.raw["profile"].update(
            {
                "ideal_deposit": 300_000_000,
                "ideal_rent": 30_000_000,
                "hard_max_deposit": 300_000_000,
                "hard_max_rent": 45_000_000,
                "stretch_max_deposit": 350_000_000,
                "stretch_max_rent": 50_000_000,
                "min_size": 60,
                "ideal_size": 90,
                "min_rooms": 1,
                "parking_required": True,
                "elevator_required": True,
                "balcony_preferred": True,
            }
        )
        cls.config.raw["scoring"]["minimum_neighborhood_score"] = 4.8
        cls.atlas = default_atlas()

    def test_atlas_covers_all_selected_neighborhoods(self) -> None:
        self.assertEqual(len(self.atlas.canonical_names), 453)
        self.assertEqual(len(self.atlas.catalog), 453)

    def test_every_neighborhood_has_its_own_explicit_score(self) -> None:
        self.assertEqual(set(self.atlas.individual_scores), self.atlas.canonical_names)
        self.assertEqual(
            len(set(self.atlas.individual_scores.values())),
            len(self.atlas.canonical_names),
        )

    def test_official_divar_catalog_is_complete_and_consistent(self) -> None:
        catalog = self.atlas.catalog
        self.assertEqual(len({row["id"] for row in catalog}), 453)
        self.assertEqual(len({row["slug"] for row in catalog}), 453)
        self.assertEqual({row["name"] for row in catalog}, self.atlas.canonical_names)
        for row in catalog:
            with self.subTest(neighborhood=row["name"]):
                rating = self.atlas.rate(row["name"])
                self.assertTrue(rating.known)
                self.assertEqual(rating.district_id, row["id"])
                self.assertEqual(rating.score, row["score"])
                self.assertEqual(rating.tier, row["tier"])

    def test_original_scores_are_preserved_and_new_areas_are_ranked(self) -> None:
        self.assertEqual(self.atlas.rate("الهیه").score, 7.9)
        self.assertEqual(self.atlas.rate("تهران‌ویلا").score, 5.95)
        self.assertEqual(self.atlas.rate("کامرانیه").score, 7.48)
        self.assertEqual(self.atlas.rate("نارمک").score, 5.971)
        self.assertTrue(self.atlas.rate("اراج قدیمی").known)

    def test_non_residential_divar_places_are_known_but_low_fit(self) -> None:
        airport = self.atlas.rate("فرودگاه مهرآباد")
        self.assertTrue(airport.known)
        self.assertEqual(airport.place_type, "non_residential")
        self.assertEqual(airport.tier, "low_fit")
        self.assertLess(airport.score, self.config.scoring["minimum_neighborhood_score"])

    def test_neighborhoods_are_ranked_inside_the_same_tier(self) -> None:
        elahieh = self.atlas.rate("الهیه")
        saadatabad = self.atlas.rate("سعادت‌آباد")
        self.assertEqual(elahieh.tier, saadatabad.tier)
        self.assertGreater(elahieh.score, saadatabad.score)

    def test_good_listing_alerts(self) -> None:
        result = score_listing(good_listing(), self.config, self.atlas)
        self.assertEqual(result.status, "alert")
        self.assertGreaterEqual(result.total, 15.5)

    def test_shared_housing_is_rejected(self) -> None:
        result = score_listing(
            good_listing(title="همخونه در پاسداران"), self.config, self.atlas
        )
        self.assertEqual(result.status, "rejected")
        self.assertTrue(any("هم" in risk for risk in result.risks))

    def test_motorcycle_parking_is_rejected(self) -> None:
        result = score_listing(
            good_listing(description="فقط پارکینگ موتور دارد"), self.config, self.atlas
        )
        self.assertEqual(result.status, "rejected")

    def test_one_photo_is_rejected_as_spam(self) -> None:
        result = score_listing(
            good_listing(image_count=1, images_for_property=None), self.config, self.atlas
        )
        self.assertEqual(result.status, "rejected")
        self.assertLess(result.trust, 75)

    def test_placeholder_rental_price_is_rejected_as_spam(self) -> None:
        result = score_listing(
            good_listing(deposit=1_000_000, rent=1_000_000),
            self.config,
            self.atlas,
        )
        self.assertEqual(result.status, "rejected")
        self.assertTrue(any("صوری" in risk for risk in result.risks))

    def test_shared_housing_spelling_variants_are_filtered(self) -> None:
        variants = (
            "همخونه میخوام",
            "هم خانه در سعادت آباد",
            "هم‌خونگی",
            "هم اتاقی",
            "شریک منزل",
            "اجاره تخت",
            "پانسیون دانشجویی",
            "خوابگاه دخترانه",
            "سوییت خواب گاه پسرانه",
            "هاستل دانشجویی",
            "اجاره تخت در خوابگاه",
            "Room mate wanted",
        )
        for title in variants:
            with self.subTest(title=title):
                self.assertIsNotNone(
                    listing_exclusion_reason(good_listing(title=title))
                )
        self.assertIsNone(
            listing_exclusion_reason(good_listing(title="آپارتمان هم قیمت پارسال"))
        )
        self.assertIsNone(
            listing_exclusion_reason(good_listing(title="آپارتمان نزدیک خوابگاه دانشگاه"))
        )

    def test_motor_parking_spelling_variants_are_filtered(self) -> None:
        variants = (
            "پارکینگ موتور",
            "پارکینگ مخصوص موتورسیکلت",
            "پارک برای موتور",
            "Pموتور",
            "فقط جای موتور",
        )
        for title in variants:
            with self.subTest(title=title):
                self.assertIsNotNone(
                    listing_exclusion_reason(good_listing(title=title))
                )

    def test_value_fallback_rewards_effective_cost_per_square_meter(self) -> None:
        compact = score_listing(
            good_listing(token="compact", size=60, deposit=300_000_000, rent=30_000_000),
            self.config,
            self.atlas,
        )
        spacious = score_listing(
            good_listing(token="spacious", size=90, deposit=300_000_000, rent=30_000_000),
            self.config,
            self.atlas,
        )
        self.assertGreater(spacious.value, compact.value)

    def test_younger_building_gets_meaningfully_more_property_points(self) -> None:
        young = score_listing(good_listing(token="young", age=5), self.config, self.atlas)
        old = score_listing(good_listing(token="old", age=20), self.config, self.atlas)
        self.assertGreaterEqual(young.property - old.property, 0.4)

    def test_profile_scope_enforces_building_density_limits(self) -> None:
        config = load_config()
        config.raw["profile"]["max_building_floors"] = 6
        config.raw["profile"]["max_units_per_floor"] = 3
        self.assertIsNone(
            profile_scope_exclusion_reason(
                good_listing(building_floors=6, units_per_floor=3), config
            )
        )
        self.assertIsNotNone(
            profile_scope_exclusion_reason(
                good_listing(building_floors=7, units_per_floor=3), config
            )
        )
        self.assertIsNotNone(
            profile_scope_exclusion_reason(
                good_listing(building_floors=6, units_per_floor=4), config
            )
        )

    def test_profile_scope_rejects_unverifiable_budget(self) -> None:
        self.assertIsNotNone(
            profile_scope_exclusion_reason(good_listing(deposit=None), self.config)
        )
        self.assertIsNotNone(
            profile_scope_exclusion_reason(good_listing(rent=None), self.config)
        )

    def test_strict_user_contract_rejects_unverifiable_hard_fields(self) -> None:
        config = load_config()
        config.raw["profile"].update(
            {
                "strict_contract": True,
                "hard_max_age": 25,
                "max_building_floors": 8,
                "max_units_per_floor": 4,
            }
        )
        for changes in (
            {"size": None},
            {"rooms": None},
            {"age": None},
            {"building_floors": None},
            {"units_per_floor": None},
        ):
            with self.subTest(changes=changes):
                self.assertIsNotNone(
                    profile_scope_exclusion_reason(good_listing(**changes), config)
                )

    def test_missing_district_never_crashes_profile_scope_or_scoring(self) -> None:
        config = load_config()
        config.raw["profile"]["allowed_district_names"] = ["پاسداران"]
        listing = good_listing(district=None)

        self.assertIsNotNone(profile_scope_exclusion_reason(listing, config))
        result = score_listing(listing, self.config, self.atlas)
        self.assertIsInstance(result.total, float)

    def test_high_total_with_weak_property_is_not_labeled_top(self) -> None:
        result = score_listing(
            good_listing(
                token="unbalanced",
                district="الهیه",
                size=60,
                rooms=1,
                age=30,
                deposit=300_000_000,
                rent=30_000_000,
                has_storage=False,
                has_balcony=False,
            ),
            self.config,
            self.atlas,
        )
        self.assertGreaterEqual(result.total, self.config.scoring["minimum_alert_score"])
        self.assertLess(result.property, self.config.scoring["minimum_alert_property_score"])
        self.assertEqual(result.status, "candidate")

    def test_low_fit_neighborhood_is_rejected(self) -> None:
        result = score_listing(
            good_listing(district="ترمینال غرب"), self.config, self.atlas
        )
        self.assertEqual(result.status, "rejected")


if __name__ == "__main__":
    unittest.main()
