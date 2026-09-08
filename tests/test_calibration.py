from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from divar_scout.auth import AuthService
from divar_scout.config import load_config
from divar_scout.dashboard import DashboardApp
from divar_scout.db import Database
from divar_scout.models import Listing, ScoreResult


def profile_payload() -> dict:
    return {
        "search_mode": "all_tehran",
        "criteria": {
            "ideal_deposit": 300_000_000,
            "hard_max_deposit": 350_000_000,
            "stretch_max_deposit": 350_000_000,
            "ideal_rent": 30_000_000,
            "hard_max_rent": 45_000_000,
            "stretch_max_rent": 45_000_000,
            "min_size": 60,
            "ideal_size": 85,
            "max_size": None,
            "min_rooms": 1,
            "max_rooms": None,
            "ideal_max_age": 10,
            "hard_max_age": 30,
            "min_floor": None,
            "max_floor": None,
            "max_building_floors": None,
            "max_units_per_floor": None,
            "parking": "required",
            "elevator": "required",
            "storage": "preferred",
            "balcony": "preferred",
            "district_scope": "all",
            "district_ids": [],
            "exceptional_budget_allowed": False,
        },
    }


class CalibrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "calibration.sqlite3")
        self.auth = AuthService(self.database)
        self.admin = self.auth.create_admin("root_admin", "مدیر", "AdminSecure123")
        pending = self.auth.register("taste_user", "کاربر تست", "SecurePass123")
        self.user = self.auth.set_user_status(self.admin, pending.id, "approved")
        self.app = DashboardApp(load_config(), database=self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def store_candidates(self, count: int = 7) -> None:
        districts = (
            "سعادت آباد",
            "شهرک غرب",
            "یوسف آباد",
            "امیرآباد",
            "مرزداران",
            "گیشا",
            "پونک",
        )
        for index in range(count):
            listing = Listing(
                token=f"candidate-{index}",
                title=f"آپارتمان خوش‌نقشه گزینه {index}",
                url=f"https://divar.ir/v/candidate-{index}",
                district=districts[index],
                deposit=220_000_000 + index * 15_000_000,
                rent=25_000_000 + index * 1_500_000,
                size=68 + index * 5,
                rooms=1 if index < 3 else 2,
                age=2 + index * 3,
                floor=str(index % 5 + 1),
                has_parking=True,
                has_elevator=True,
                has_storage=index % 2 == 0,
                has_balcony=index % 3 != 0,
                image_count=3,
                images=[
                    f"https://s100.divarcdn.com/static/photo-{index}-{photo}.jpg"
                    for photo in range(3)
                ],
                description="واحد مسکونی با پارکینگ خودرو و آسانسور",
            )
            self.database.upsert_listing(listing, detail_fetched=True)

    def test_exactly_twelve_choices_activate_model_and_unlock_dashboard(self) -> None:
        self.store_candidates()
        self.app.save_profile(self.user.id, profile_payload())

        first = self.app.calibration(self.user.id)
        refreshed = self.app.calibration(self.user.id)
        self.assertTrue(first["ready"])
        self.assertGreaterEqual(first["available_pairs"], 12)
        self.assertEqual(
            (first["left"]["token"], first["right"]["token"]),
            (refreshed["left"]["token"], refreshed["right"]["token"]),
        )

        seen_pairs: set[frozenset[str]] = set()
        for choice_index in range(12):
            pair = self.app.calibration(self.user.id)
            self.assertTrue(pair["ready"])
            tokens = frozenset((pair["left"]["token"], pair["right"]["token"]))
            self.assertNotIn(tokens, seen_pairs)
            seen_pairs.add(tokens)
            winner = pair["left"]["token"] if choice_index % 2 == 0 else pair["right"]["token"]
            result = self.app.submit_calibration(
                self.user.id,
                pair["left"]["token"],
                pair["right"]["token"],
                winner,
            )
            self.assertEqual(result["calibration"]["completed"], choice_index + 1)

        self.assertEqual(len(seen_pairs), 12)
        self.assertFalse(self.database.calibration_required(self.user.id))
        complete = self.app.calibration(self.user.id)
        self.assertTrue(complete["calibration"]["complete"])
        self.assertEqual(complete["redirect"], "/")
        learning = self.app.learning_state(self.user.id)
        self.assertEqual(learning["comparison_count"], 12)
        self.assertEqual(learning["signals"], 12)
        self.assertEqual(learning["stage"], "calibrating")
        self.assertGreater(learning["maturity"], 0)
        self.assertEqual(learning["remaining_to_activation"], 0)
        self.assertTrue(learning["learning_continues"])

    def test_submission_is_atomic_and_cannot_be_replayed_or_forged(self) -> None:
        self.store_candidates()
        self.app.save_profile(self.user.id, profile_payload())
        pair = self.app.calibration(self.user.id)

        with self.assertRaisesRegex(ValueError, "فعال نیست"):
            self.app.submit_calibration(
                self.user.id,
                pair["right"]["token"],
                pair["left"]["token"],
                pair["left"]["token"],
            )
        self.assertEqual(self.database.calibration_state(self.user.id)["completed"], 0)

        self.app.submit_calibration(
            self.user.id,
            pair["left"]["token"],
            pair["right"]["token"],
            pair["left"]["token"],
        )
        with self.assertRaisesRegex(ValueError, "فعال نیست"):
            self.app.submit_calibration(
                self.user.id,
                pair["left"]["token"],
                pair["right"]["token"],
                pair["left"]["token"],
            )
        self.assertEqual(self.database.calibration_state(self.user.id)["completed"], 1)
        self.assertEqual(len(self.database.list_comparisons(user_id=self.user.id)), 1)

    def test_calibration_uses_fast_scoring_without_per_listing_market_queries(self) -> None:
        self.store_candidates()
        self.app.save_profile(self.user.id, profile_payload())

        def forbidden_context(*args, **kwargs):
            raise AssertionError("calibration must not run per-listing market context queries")

        self.database.comparable_costs = forbidden_context
        self.database.duplicate_image_count = forbidden_context

        pair = self.app.calibration(self.user.id)

        self.assertTrue(pair["ready"])
        self.assertIsNotNone(pair["left"])
        self.assertIsNotNone(pair["right"])

    def test_calibration_pool_is_not_starved_by_global_top_records(self) -> None:
        payload = profile_payload()
        payload["criteria"]["district_scope"] = "selected"
        payload["criteria"]["district_ids"] = [75]
        self.app.save_profile(self.user.id, payload)

        for index in range(160):
            listing = Listing(
                token=f"outside-{index}",
                title=f"Outside high score {index}",
                url=f"https://divar.ir/v/outside-{index}",
                district="تهرانپارس غربی",
                deposit=200_000_000,
                rent=20_000_000,
                size=80,
                rooms=2,
                age=4,
                floor="۲ از ۵",
                has_parking=True,
                has_elevator=True,
                image_count=4,
                images=[f"https://s100.divarcdn.com/outside-{index}-{photo}.jpg" for photo in range(4)],
            )
            self.database.upsert_listing(listing, detail_fetched=True)
            self.database.save_score(
                listing.token,
                ScoreResult(
                    total=19, location=6, value=5, property=4,
                    trust_points=3, freshness=1, trust=100,
                    location_confidence=100, status="candidate"
                ),
            )

        for index in range(7):
            listing = Listing(
                token=f"selected-{index}",
                title=f"Selected lower score {index}",
                url=f"https://divar.ir/v/selected-{index}",
                district="سعادت‌آباد",
                deposit=220_000_000,
                rent=25_000_000,
                size=70 + index,
                rooms=2,
                age=5,
                floor="۲ از ۵",
                has_parking=True,
                has_elevator=True,
                image_count=4,
                images=[f"https://s100.divarcdn.com/selected-{index}-{photo}.jpg" for photo in range(4)],
            )
            self.database.upsert_listing(listing, detail_fetched=True)
            self.database.save_score(
                listing.token,
                ScoreResult(
                    total=12, location=5, value=3, property=2,
                    trust_points=1, freshness=1, trust=100,
                    location_confidence=100, status="candidate"
                ),
            )

        pair = self.app.calibration(self.user.id)

        self.assertTrue(pair["ready"])
        self.assertGreaterEqual(pair["available_pairs"], 12)
        self.assertTrue(pair["left"]["token"].startswith("selected-"))
        self.assertTrue(pair["right"]["token"].startswith("selected-"))

    def test_existing_completed_profiles_are_not_suddenly_gated(self) -> None:
        from divar_scout.search_profile import validate_profile

        self.database.save_user_profile(self.user.id, validate_profile(profile_payload()))
        self.assertFalse(self.database.calibration_required(self.user.id))

        self.app.save_profile(self.user.id, profile_payload())

        self.assertFalse(self.database.calibration_required(self.user.id))
        self.assertTrue(self.database.calibration_state(self.user.id)["complete"])


if __name__ == "__main__":
    unittest.main()
