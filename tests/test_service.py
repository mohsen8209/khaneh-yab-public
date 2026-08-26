from __future__ import annotations

import copy
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from divar_scout.config import AppConfig, load_config
from divar_scout.db import Database
from divar_scout.models import Listing
from divar_scout.service import ScoutService


def listing(token: str, *, detailed: bool = False) -> Listing:
    values = {
        "token": token,
        "title": "آپارتمان ۹۰ متری دو خواب",
        "url": f"https://divar.ir/v/{token}",
        "deposit": 300_000_000,
        "rent": 30_000_000,
        "size": 90,
        "rooms": 2,
        "image_count": 6,
        "images": [f"https://example.test/{token}-{index}.jpg" for index in range(6)],
        "published_at": datetime.now(UTC).isoformat(),
    }
    if detailed:
        values.update(
            {
                "age": 4,
                "district": "پاسداران",
                "latitude": 35.79,
                "longitude": 51.46,
                "approximate_location": False,
                "has_parking": True,
                "has_elevator": True,
                "has_storage": True,
                "has_balcony": True,
                "images_for_property": "بله",
            }
        )
    return Listing(**values)


class FakeClient:
    def __init__(self) -> None:
        self.run = 0
        self.details = {
            "baseline": listing("baseline", detailed=True),
            "fresh": listing("fresh", detailed=True),
        }

    def fetch_summaries(self) -> list[Listing]:
        self.run += 1
        tokens = ["baseline"] if self.run == 1 else ["baseline", "fresh"]
        return [listing(token) for token in tokens]

    def fetch_detail(self, summary: Listing) -> Listing:
        return self.details[summary.token]


class ExpiringClient:
    def __init__(self) -> None:
        self.run = 0
        self.details = {
            "baseline": listing("baseline", detailed=True),
            "fresh": listing("fresh", detailed=True),
        }

    def fetch_summaries(self) -> list[Listing]:
        self.run += 1
        tokens = ["fresh"] if self.run == 2 else ["baseline", "fresh"]
        return [listing(token) for token in tokens]

    def fetch_detail(self, summary: Listing) -> Listing:
        return self.details[summary.token]


class EmptyClient:
    def fetch_summaries(self) -> list[Listing]:
        return []

    def fetch_detail(self, summary: Listing) -> Listing:
        raise AssertionError("No detail should be requested for an empty search")


class IncompleteInventoryClient:
    last_fetch_complete = False

    def fetch_summaries(self, source_url: str | None = None) -> list[Listing]:
        return [listing("fresh")]

    def fetch_detail(self, summary: Listing) -> Listing:
        return listing(summary.token, detailed=True)


class FilteringClient:
    def fetch_summaries(self) -> list[Listing]:
        valid = listing("valid")
        shared = listing("shared")
        shared.title = "هم خونه می‌خوام"
        motor = listing("motor")
        motor.title = "آپارتمان فول با P موتور"
        one_photo = listing("one-photo")
        one_photo.image_count = 1
        one_photo.images = one_photo.images[:1]
        no_photo = listing("no-photo")
        no_photo.image_count = 0
        no_photo.images = []
        return [valid, shared, motor, one_photo, no_photo]

    def fetch_detail(self, summary: Listing) -> Listing:
        if summary.token != "valid":
            raise AssertionError("Filtered summaries must not fetch details")
        return listing("valid", detailed=True)


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, item: Listing, score: object) -> bool:
        self.sent.append(item.token)
        return True


class ServiceNotificationTests(unittest.TestCase):
    def test_spam_is_filtered_before_detail_fetch_and_storage(self) -> None:
        raw = copy.deepcopy(load_config().raw)
        raw["scan"]["request_delay_seconds"] = 0
        config = AppConfig(raw=raw, path=Path("test-config.json"))
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            service = ScoutService(
                config, database=database, client=FilteringClient()
            )

            result = service.run_once()

            self.assertEqual(result.summary_count, 5)
            self.assertEqual(result.detail_count, 1)
            self.assertEqual(result.filtered_count, 4)
            self.assertEqual(
                [item["token"] for item in database.list_records()], ["valid"]
            )

    def test_new_listing_gets_detail_priority_but_shared_housing_does_not(self) -> None:
        raw = copy.deepcopy(load_config().raw)
        config = AppConfig(raw=raw, path=Path("test-config.json"))
        with tempfile.TemporaryDirectory() as directory:
            service = ScoutService(config, database=Database(Path(directory) / "test.sqlite3"))
            item = listing("normal")
            self.assertGreater(
                service._detail_priority(item, newly_discovered=True),
                service._detail_priority(item, newly_discovered=False),
            )
            shared = listing("shared")
            shared.title = "همخانه در پاسداران"
            self.assertEqual(
                service._detail_priority(shared, newly_discovered=True), -1000
            )

    def test_only_new_qualifying_listing_notifies_once(self) -> None:
        raw = copy.deepcopy(load_config().raw)
        raw["notifications"]["enabled"] = True
        raw["notifications"]["notify_existing_on_first_run"] = False
        raw["scan"]["request_delay_seconds"] = 0
        config = AppConfig(raw=raw, path=Path("test-config.json"))

        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            service = ScoutService(config, database=database, client=FakeClient())
            notifier = FakeNotifier()
            service.notifier = notifier

            first = service.run_once()
            self.assertEqual(first.alert_count, 0)
            self.assertFalse(database.is_notification_eligible("baseline"))

            second = service.run_once()
            self.assertEqual(second.alert_count, 1)
            self.assertEqual(notifier.sent, ["fresh"])
            self.assertTrue(database.was_notified("fresh"))

            third = service.run_once()
            self.assertEqual(third.alert_count, 0)
            self.assertEqual(notifier.sent, ["fresh"])

    def test_personally_rejected_listing_never_notifies(self) -> None:
        raw = copy.deepcopy(load_config().raw)
        raw["notifications"]["enabled"] = True
        raw["notifications"]["notify_existing_on_first_run"] = True
        raw["scan"]["request_delay_seconds"] = 0
        config = AppConfig(raw=raw, path=Path("test-config.json"))

        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.upsert_listing(listing("baseline", detailed=True), detail_fetched=True)
            database.set_feedback("baseline", "rejected")
            service = ScoutService(config, database=database, client=FakeClient())
            notifier = FakeNotifier()
            service.notifier = notifier

            result = service.run_once()

            self.assertEqual(result.alert_count, 0)
            self.assertEqual(notifier.sent, [])

    def test_missing_listing_is_archived_without_losing_learning_history(self) -> None:
        raw = copy.deepcopy(load_config().raw)
        raw["scan"]["request_delay_seconds"] = 0
        config = AppConfig(raw=raw, path=Path("test-config.json"))

        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            service = ScoutService(
                config, database=database, client=ExpiringClient()
            )

            first = service.run_once()
            database.set_feedback("baseline", "favorite", ["good_size"])
            second = service.run_once()

            self.assertEqual(first.expired_count, 0)
            self.assertEqual(second.expired_count, 1)
            self.assertEqual(
                [item["token"] for item in database.list_records()], ["fresh"]
            )
            archived = {
                item["token"]: item
                for item in database.list_records(include_inactive=True)
            }
            self.assertFalse(archived["baseline"]["is_active"])
            self.assertIsNotNone(archived["baseline"]["expired_at"])
            self.assertEqual(database.list_feedback()[0]["token"], "baseline")
            self.assertEqual(database.stats()["total"], 1)

            third = service.run_once()
            restored = {
                item["token"]: item for item in database.list_records()
            }

            self.assertEqual(third.expired_count, 0)
            self.assertEqual(set(restored), {"baseline", "fresh"})
            self.assertTrue(restored["baseline"]["is_active"])
            self.assertIsNone(restored["baseline"]["expired_at"])

    def test_empty_search_response_does_not_archive_everything(self) -> None:
        config = AppConfig(
            raw=copy.deepcopy(load_config().raw), path=Path("test-config.json")
        )
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.upsert_listing(
                listing("baseline", detailed=True), detail_fetched=True
            )
            service = ScoutService(config, database=database, client=EmptyClient())

            result = service.run_once()

            self.assertEqual(result.expired_count, 0)
            self.assertEqual(database.stats()["total"], 1)

    def test_incomplete_tiled_search_does_not_archive_unseen_listings(self) -> None:
        raw = copy.deepcopy(load_config().raw)
        raw["scan"]["request_delay_seconds"] = 0
        config = AppConfig(raw=raw, path=Path("test-config.json"))
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.upsert_listing(
                listing("baseline", detailed=True), detail_fetched=True
            )
            service = ScoutService(
                config, database=database, client=IncompleteInventoryClient()
            )

            result = service.run_once()

            self.assertEqual(result.expired_count, 0)
            self.assertEqual(
                {item["token"] for item in database.list_records()},
                {"baseline", "fresh"},
            )

    def test_focused_profile_scan_never_archives_other_profiles_inventory(self) -> None:
        raw = copy.deepcopy(load_config().raw)
        raw["scan"]["request_delay_seconds"] = 0
        config = AppConfig(raw=raw, path=Path("test-config.json"))
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.upsert_listing(
                listing("other-profile", detailed=True), detail_fetched=True
            )
            service = ScoutService(config, database=database, client=IncompleteInventoryClient())

            result = service.run_once(
                source_urls=["https://divar.ir/s/tehran/rent-apartment?districts=85"],
                archive_missing=False,
            )

            self.assertEqual(result.expired_count, 0)
            self.assertEqual(
                {item["token"] for item in database.list_records()},
                {"other-profile", "fresh"},
            )

    def test_scan_progress_is_persisted_before_the_scan_finishes(self) -> None:
        raw = copy.deepcopy(load_config().raw)
        raw["scan"]["request_delay_seconds"] = 0
        config = AppConfig(raw=raw, path=Path("test-config.json"))
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            progress: list[tuple[int, int, int]] = []
            original = database.update_scan_progress

            def capture(scan_id, *, summary_count, detail_count, filtered_count=0):
                progress.append((summary_count, detail_count, filtered_count))
                original(
                    scan_id,
                    summary_count=summary_count,
                    detail_count=detail_count,
                    filtered_count=filtered_count,
                )

            database.update_scan_progress = capture
            service = ScoutService(config, database=database, client=FilteringClient())

            service.run_once()

            self.assertIn((5, 0, 0), progress)
            self.assertIn((5, 0, 4), progress)
            self.assertEqual(progress[-1], (5, 1, 4))


if __name__ == "__main__":
    unittest.main()
