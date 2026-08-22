import tempfile
import unittest
from pathlib import Path

from divar_scout.config import load_config
from divar_scout.dashboard import DashboardApp, DashboardServer
from divar_scout.db import Database
from divar_scout.models import Listing, ScoreResult


class DashboardTests(unittest.TestCase):
    def test_dashboard_port_cannot_be_shared_by_stale_processes(self) -> None:
        self.assertFalse(DashboardServer.allow_reuse_address)

    def test_pending_summaries_are_hidden_from_ranked_list(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            pending = Listing(
                token="pending", title="در انتظار",
                url="https://divar.ir/v/pending", image_count=2,
                deposit=300_000_000, rent=30_000_000,
            )
            scored = Listing(
                token="scored", title="تحلیل شده",
                url="https://divar.ir/v/scored", image_count=2,
                deposit=300_000_000, rent=30_000_000,
            )
            database.upsert_listing(pending)
            database.upsert_listing(scored, detail_fetched=True)
            database.save_score(
                "scored",
                ScoreResult(
                    total=16, location=6, value=4, property=2,
                    trust_points=3, freshness=1, trust=100,
                    location_confidence=100, status="alert"
                ),
            )
            app = DashboardApp(load_config(), database=database)

            visible = app.listings({})
            with_pending = app.listings({"include_pending": ["1"]})

            self.assertEqual([item["token"] for item in visible], ["scored"])
            self.assertEqual(len(with_pending), 2)

    def test_comparison_feedback_updates_learning_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            listings = (
                Listing(
                    token="roomy",
                    title="Roomy option",
                    url="https://divar.ir/v/roomy",
                    district="سعادت آباد",
                    size=95,
                    rooms=2,
                    age=4,
                    deposit=300_000_000,
                    rent=30_000_000,
                    image_count=4,
                ),
                Listing(
                    token="compact",
                    title="Compact option",
                    url="https://divar.ir/v/compact",
                    district="استاد معین",
                    size=65,
                    rooms=1,
                    age=16,
                    deposit=200_000_000,
                    rent=35_000_000,
                    image_count=4,
                ),
            )
            for listing in listings:
                database.upsert_listing(listing, detail_fetched=True)
                database.save_score(
                    listing.token,
                    ScoreResult(
                        total=16,
                        location=6,
                        value=4,
                        property=2,
                        trust_points=3,
                        freshness=1,
                        trust=100,
                        location_confidence=100,
                        status="candidate",
                    ),
                )
            app = DashboardApp(load_config(), database=database)

            comparison = app.comparison()
            pair = {comparison["left"]["token"], comparison["right"]["token"]}
            state = app.submit_comparison(
                comparison["left"]["token"],
                comparison["right"]["token"],
                "roomy",
            )

            self.assertEqual(pair, {"roomy", "compact"})
            self.assertEqual(state["comparison_count"], 1)
            self.assertEqual(state["signals"], 1)
            self.assertIsNone(app.comparison()["left"])

    def test_personal_rejection_immediately_demotes_listing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            for token, total in (("rejected-by-user", 18), ("kept", 15)):
                database.upsert_listing(
                    Listing(
                        token=token,
                        title=token,
                        url=f"https://divar.ir/v/{token}",
                        image_count=4,
                        deposit=300_000_000,
                        rent=30_000_000,
                    ),
                    detail_fetched=True,
                )
                database.save_score(
                    token,
                    ScoreResult(
                        total=total,
                        location=6,
                        value=4,
                        property=2,
                        trust_points=3,
                        freshness=1,
                        trust=100,
                        location_confidence=100,
                        status="alert",
                    ),
                )
            database.set_feedback("rejected-by-user", "rejected")
            app = DashboardApp(load_config(), database=database)

            listings = app.listings({})

            self.assertEqual([item["token"] for item in listings], ["kept", "rejected-by-user"])
            self.assertEqual(listings[-1]["score"]["status"], "rejected")
            self.assertTrue(listings[-1]["score"]["user_rejected"])
            self.assertEqual(app.stats()["best_score"], 15)

    def test_spam_is_absent_from_all_results_stats_and_comparisons(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            for token, title, image_count in (
                ("valid", "آپارتمان دو خواب", 5),
                ("shared", "هم خونه می‌خوام", 5),
                ("motor", "واحد فول با P موتور", 5),
                ("one-photo", "آپارتمان تک خواب", 1),
            ):
                database.upsert_listing(
                    Listing(
                        token=token,
                        title=title,
                        url=f"https://divar.ir/v/{token}",
                        image_count=image_count,
                        deposit=300_000_000,
                        rent=30_000_000,
                    ),
                    detail_fetched=True,
                )
                database.save_score(
                    token,
                    ScoreResult(
                        total=16,
                        location=6,
                        value=4,
                        property=2,
                        trust_points=3,
                        freshness=1,
                        trust=100,
                        location_confidence=100,
                        status="alert",
                    ),
                )
            app = DashboardApp(load_config(), database=database)

            self.assertEqual(
                [item["token"] for item in app.listings({})], ["valid"]
            )
            self.assertEqual(app.stats()["total"], 1)
            self.assertIsNone(app.comparison()["left"])


if __name__ == "__main__":
    unittest.main()
