from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from divar_scout.config import AppConfig, load_config
from divar_scout.db import Database
from divar_scout.models import Listing, ScoreResult
from divar_scout.personalization import PersonalizationEngine


def test_config(*, activation: int = 2, full_strength: int = 4) -> AppConfig:
    raw = copy.deepcopy(load_config().raw)
    raw["personalization"]["activation_signals"] = activation
    raw["personalization"]["full_strength_signals"] = full_strength
    return AppConfig(raw=raw, path=Path("test-config.json"))


def listing(token: str, *, size: int, age: int) -> Listing:
    return Listing(
        token=token,
        title=f"خانه {token}",
        url=f"https://divar.ir/v/{token}",
        deposit=300_000_000,
        rent=30_000_000,
        size=size,
        rooms=2 if size >= 80 else 1,
        age=age,
        district="پاسداران",
        latitude=35.79,
        longitude=51.46,
        approximate_location=False,
        has_parking=True,
        has_elevator=True,
        has_storage=size >= 80,
        has_balcony=size >= 80,
        image_count=8,
        images=[
            f"https://s100.divarcdn.com/photo/{token}-{index}.webp"
            for index in range(8)
        ],
    )


def base_score(status: str = "candidate") -> ScoreResult:
    return ScoreResult(
        total=15.0,
        location=6.0,
        value=4.0,
        property=2.2,
        trust_points=3.0,
        freshness=0.5,
        trust=100,
        location_confidence=100,
        status=status,
    )


class PersonalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "test.sqlite3")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def store(self, item: Listing, score: ScoreResult | None = None) -> None:
        self.database.upsert_listing(item, detail_fetched=True)
        self.database.save_score(item.token, score or base_score())

    def test_pairwise_model_learns_preference_and_preserves_base_score(self) -> None:
        preferred = [listing("large-1", size=100, age=4), listing("large-2", size=92, age=6)]
        rejected = [listing("small-1", size=60, age=22), listing("small-2", size=62, age=25)]
        for item in preferred + rejected:
            self.store(item)
        self.database.set_feedback("large-1", "favorite", ["good_size"])
        self.database.set_feedback("large-2", "visit", ["good_age"])
        self.database.set_feedback("small-1", "rejected", ["too_small"])
        self.database.set_feedback("small-2", "rejected", ["too_old"])

        engine = PersonalizationEngine(test_config(), self.database)
        state = engine.retrain()
        large_score = engine.apply(preferred[0], base_score())
        small_score = engine.apply(rejected[0], base_score())

        self.assertTrue(state["trained"])
        self.assertEqual(state["stage"], "active")
        self.assertGreater(large_score.personal_adjustment, small_score.personal_adjustment)
        self.assertEqual(large_score.base_total, 15.0)
        self.assertGreater(large_score.total, small_score.total)

    def test_model_does_not_adjust_scores_before_activation(self) -> None:
        first = listing("first", size=95, age=5)
        second = listing("second", size=60, age=20)
        self.store(first)
        self.store(second)
        self.database.add_comparison("first", "second", "first")

        engine = PersonalizationEngine(
            test_config(activation=3, full_strength=5), self.database
        )
        state = engine.retrain()
        result = engine.apply(first, base_score())

        self.assertTrue(state["trained"])
        self.assertEqual(state["stage"], "collecting")
        self.assertEqual(result.total, 15.0)
        self.assertFalse(result.personalized)

    def test_personalization_never_rescues_hard_rejection(self) -> None:
        preferred = listing("preferred", size=100, age=3)
        rejected = listing("rejected", size=60, age=25)
        self.store(preferred)
        self.store(rejected)
        for _ in range(4):
            self.database.add_comparison("preferred", "rejected", "preferred")

        engine = PersonalizationEngine(test_config(), self.database)
        engine.retrain()
        result = engine.apply(preferred, base_score(status="rejected"))

        self.assertEqual(result.status, "rejected")

    def test_feedback_reasons_are_persisted(self) -> None:
        item = listing("reasoned", size=80, age=8)
        self.store(item)
        self.database.set_feedback("reasoned", "visit", ["good_size", "good_layout"])

        feedback = self.database.list_feedback()

        self.assertEqual(feedback[0]["choice"], "visit")
        self.assertEqual(feedback[0]["reasons"], ["good_size", "good_layout"])

    def test_reason_signal_gently_teaches_a_feature_direction(self) -> None:
        old = listing("old-reason", size=80, age=25)
        young = listing("young-reason", size=80, age=3)
        self.store(old)
        self.store(young)
        self.database.set_feedback("old-reason", "rejected", ["too_old"])

        engine = PersonalizationEngine(
            test_config(activation=1, full_strength=2), self.database
        )
        state = engine.retrain()
        old_result = engine.apply(old, base_score())
        young_result = engine.apply(young, base_score())

        self.assertTrue(state["trained"])
        self.assertGreater(young_result.personal_adjustment, old_result.personal_adjustment)

if __name__ == "__main__":
    unittest.main()
