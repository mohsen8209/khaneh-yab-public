import tempfile
import threading
import time
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

    def test_concurrent_scan_requests_join_one_shared_background_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            app = DashboardApp(load_config(), database=database)
            calls = []
            started = threading.Event()
            release = threading.Event()

            def fake_run_scan(
                detail_limit=None, *, user_initiated=False, source_urls=None
            ):
                calls.append((detail_limit, user_initiated))
                started.set()
                release.wait(timeout=2)
                return {
                    "summary_count": 2,
                    "detail_count": 1,
                    "scored_count": 1,
                    "alert_count": 0,
                    "expired_count": 0,
                    "filtered_count": 0,
                    "detail_errors": [],
                }

            app.run_scan = fake_run_scan
            first = app.request_scan(user_initiated=True)
            self.assertTrue(started.wait(timeout=1))
            second = app.request_scan(user_initiated=True)

            self.assertTrue(first["accepted"])
            self.assertTrue(first["running"])
            self.assertFalse(first["shared"])
            self.assertTrue(second["accepted"])
            self.assertTrue(second["running"])
            self.assertTrue(second["shared"])
            self.assertEqual(len(calls), 1)

            release.set()
            for _ in range(20):
                status = app.scan_status()
                if not status["running"]:
                    break
                time.sleep(0.05)

            self.assertFalse(app.scan_status()["running"])
            self.assertEqual(app.scan_status()["last_result"]["detail_count"], 1)
            self.assertIsNone(app.scan_status()["last_error"])

    def test_new_profile_during_scan_is_queued_for_a_follow_up_scan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = DashboardApp(
                load_config(), database=Database(Path(directory) / "test.sqlite3")
            )
            available_urls = ["https://divar.ir/s/tehran/rent-apartment?rent=-30"]
            calls: list[tuple[str, ...]] = []
            first_started = threading.Event()
            release_first = threading.Event()
            second_finished = threading.Event()

            app._source_urls = lambda: list(available_urls)

            def fake_run_scan(
                detail_limit=None, *, user_initiated=False, source_urls=None
            ):
                calls.append(tuple(source_urls or []))
                if len(calls) == 1:
                    first_started.set()
                    release_first.wait(timeout=2)
                else:
                    second_finished.set()
                return {
                    "summary_count": len(calls),
                    "detail_count": 1,
                    "scored_count": 1,
                    "alert_count": 0,
                    "expired_count": 0,
                    "filtered_count": 0,
                    "detail_errors": [],
                }

            app.run_scan = fake_run_scan
            first = app.request_scan(user_initiated=True)
            self.assertTrue(first_started.wait(timeout=1))

            available_urls.append(
                "https://divar.ir/s/tehran/rent-apartment?rent=-45"
            )
            second = app.request_scan(user_initiated=True)

            self.assertTrue(second["accepted"])
            self.assertTrue(second["shared"])
            self.assertTrue(second["queued"])
            release_first.set()
            self.assertTrue(second_finished.wait(timeout=1))
            for _ in range(20):
                if not app.scan_status()["running"]:
                    break
                time.sleep(0.05)

            self.assertEqual(
                calls,
                [
                    ("https://divar.ir/s/tehran/rent-apartment?rent=-30",),
                    (
                        "https://divar.ir/s/tehran/rent-apartment?rent=-30",
                        "https://divar.ir/s/tehran/rent-apartment?rent=-45",
                    ),
                ],
            )
            self.assertFalse(app.scan_status()["running"])
            self.assertFalse(app.scan_status()["queued"])


if __name__ == "__main__":
    unittest.main()
