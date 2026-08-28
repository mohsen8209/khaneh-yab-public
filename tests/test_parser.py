import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from divar_scout.config import load_config
from divar_scout.db import Database
from divar_scout.divar_client import (
    DivarClient,
    _split_map_bbox,
    build_map_payload,
    parse_detail,
    parse_map_item,
    parse_money,
)
from divar_scout.models import Listing


class ParserTests(unittest.TestCase):
    def test_bbox_split_has_full_coverage_without_area_overlap(self) -> None:
        root = (50.0, 35.0, 52.0, 37.0)
        children = _split_map_bbox(root)

        self.assertEqual(
            children,
            (
                (50.0, 35.0, 51.0, 36.0),
                (51.0, 35.0, 52.0, 36.0),
                (50.0, 36.0, 51.0, 37.0),
                (51.0, 36.0, 52.0, 37.0),
            ),
        )
        root_area = (root[2] - root[0]) * (root[3] - root[1])
        child_area = sum(
            (max_lon - min_lon) * (max_lat - min_lat)
            for min_lon, min_lat, max_lon, max_lat in children
        )
        self.assertEqual(child_area, root_area)

    def test_map_payload_requests_listing_cards_instead_of_city_clusters(self) -> None:
        payload = build_map_payload(
            load_config(), "https://divar.ir/s/tehran/rent-apartment"
        )
        self.assertGreaterEqual(payload["camera_info"]["zoom"], 14)

    def test_default_map_payload_never_requests_below_ground_floors(self) -> None:
        payload = build_map_payload(load_config())
        floor = payload["search_data"]["form_data"]["data"]["floor"]["number_range"]
        self.assertEqual(floor.get("minimum"), "0")
        self.assertNotIn("maximum", floor)

    def test_cluster_only_response_is_not_treated_as_a_valid_empty_scan(self) -> None:
        client = DivarClient(load_config())
        client._request_json = lambda *args, **kwargs: {
            "count": 120,
            "clusters": [{"count": 120}],
        }
        with self.assertRaisesRegex(RuntimeError, "clusters"):
            client.fetch_summaries()

    def test_capped_map_response_is_split_and_deduplicated(self) -> None:
        client = DivarClient(load_config())
        calls = []

        def post(token: str) -> dict:
            return {
                "map_post_card": {
                    "token": token,
                    "title": token,
                    "images": ["one", "two"],
                }
            }

        def fake_request(*args, **kwargs):
            calls.append(kwargs["payload"]["camera_info"]["bbox"])
            if len(calls) == 1:
                return {"count": 537, "posts": [post("a"), post("b")]}
            child_posts = {
                2: [post("a")],
                3: [post("b")],
                4: [post("c")],
                5: [],
            }[len(calls)]
            return {"count": len(child_posts), "posts": child_posts}

        client._request_json = fake_request
        listings = client.fetch_summaries()

        self.assertEqual({item.token for item in listings}, {"a", "b", "c"})
        self.assertEqual(len(calls), 5)
        self.assertTrue(client.last_fetch_complete)

    def test_unresolved_leaf_marks_inventory_as_incomplete(self) -> None:
        client = DivarClient(load_config())
        client._request_json = lambda *args, **kwargs: {
            "count": 300,
            "posts": [
                {
                    "map_post_card": {
                        "token": "partial",
                        "title": "partial",
                        "images": ["one", "two"],
                    }
                }
            ],
        }
        with patch("divar_scout.divar_client.MAP_TILE_MAX_DEPTH", 0):
            client.fetch_summaries()
        self.assertFalse(client.last_fetch_complete)

    def test_parse_money(self) -> None:
        self.assertEqual(parse_money("۳۰۰ میلیون"), 300_000_000)
        self.assertEqual(parse_money("۱.۵ میلیارد"), 1_500_000_000)
        self.assertEqual(parse_money("‏۲۸,۰۰۰,۰۰۰ تومان"), 28_000_000)
        self.assertEqual(parse_money("رایگان"), 0)

    def test_parse_map_item(self) -> None:
        raw = {
            "map_post_card": {
                "token": "abc123",
                "title": "۸۰ متر دو خواب",
                "price_fields": [
                    {"title": "ودیعه:", "value": "۳۰۰ میلیون"},
                    {"title": "اجاره:", "value": "۳۰ میلیون"},
                ],
                "chips": [
                    {"title": "۸۰ متر"},
                    {"title": "۲ اتاق"},
                    {"title": "۵ سال"},
                    {"icon_url_light": "https://example/parking.png"},
                    {"icon_url_light": "https://example/elevator.png"},
                ],
                "images": ["https://example/photo-id.webp"],
                "action_log": {"server_side_info": {"info": {
                    "sort_date": "2026-08-18T09:06:10.737376Z"
                }}},
            },
            "map_pin_feature": {
                "lat": 35.75,
                "lon": 51.4,
                "properties": {
                    "type": "Feature",
                    "properties": {
                        "image_count": 1,
                        "approximate_location": False,
                        "location_type": "E",
                    },
                },
            },
        }
        listing = parse_map_item(raw)
        self.assertEqual(listing.deposit, 300_000_000)
        self.assertEqual(listing.rent, 30_000_000)
        self.assertEqual(listing.size, 80)
        self.assertEqual(listing.rooms, 2)
        self.assertTrue(listing.has_parking)
        self.assertTrue(listing.has_elevator)
        self.assertIsNone(listing.published_at)
        self.assertEqual(listing.updated_at, "2026-08-18T09:06:10.737376Z")

    def test_parse_detail_widgets(self) -> None:
        summary = parse_map_item(
            {
                "map_post_card": {"token": "abc", "title": "ملک", "chips": []},
                "map_pin_feature": {"properties": {}},
            }
        )
        payload = {
            "sections": [
                {
                    "widgets": [
                        {
                            "widget_type": "GROUP_INFO_ROW",
                            "data": {"items": [
                                {"title": "متراژ", "value": "۸۵"},
                                {"title": "ساخت", "value": "۱۳۹۸"},
                                {"title": "اتاق", "value": "۲"},
                                {"title": "طبقه", "value": "۳ از ۸"},
                                {"title": "تعداد واحد در هر طبقه", "value": "۴"},
                            ]},
                        },
                        {
                            "widget_type": "DESCRIPTION_ROW",
                            "data": {"text": (
                                "انتشار آگهی: ۵ مرداد ۱۴۰۵، ۱۲:۳۳\n"
                                "آخرین نردبان: ۲۷ مرداد ۱۴۰۵، ۱۲:۳۶"
                            )},
                        },
                        {
                            "widget_type": "GROUP_FEATURE_ROW",
                            "data": {"items": [
                                {"title": "پارکینگ", "available": True},
                                {"title": "آسانسور", "available": True},
                                {"title": "بالکن", "available": True},
                            ]},
                        },
                        {
                            "widget_type": "MAP_ROW",
                            "data": {"location": {"type": "FUZZY", "fuzzy_data": {
                                "point": {"latitude": 35.8, "longitude": 51.4}, "radius": 500
                            }}},
                        },
                    ]
                }
            ],
            "seo": {"web_info": {"district_persian": "پاسداران"}},
            "webengage": {"credit": 300_000_000, "rent": 30_000_000, "image_count": 4},
        }
        listing = parse_detail(payload, summary)
        self.assertEqual(listing.district, "پاسداران")
        self.assertEqual(listing.size, 85)
        self.assertTrue(listing.has_balcony)
        self.assertEqual(listing.building_floors, 8)
        self.assertEqual(listing.units_per_floor, 4)
        self.assertEqual(listing.location_radius, 500)
        self.assertEqual(listing.published_at, "2026-07-27T09:03:00Z")
        self.assertEqual(listing.updated_at, "2026-08-18T09:06:00Z")

    def test_detail_rows_override_scaled_webengage_prices(self) -> None:
        summary = Listing(
            token="priced",
            title="ملک",
            url="https://divar.ir/v/priced",
            deposit=300_000_000,
            rent=45_000_000,
        )
        payload = {
            "sections": [{"widgets": [
                {
                    "widget_type": "UNEXPANDABLE_ROW",
                    "data": {"title": "ودیعه", "value": "‏۳۰۰,۰۰۰,۰۰۰ تومان"},
                },
                {
                    "widget_type": "UNEXPANDABLE_ROW",
                    "data": {"title": "اجارهٔ ماهانه", "value": "رایگان"},
                },
            ]}],
            "webengage": {"credit": 300_000, "rent": 0},
        }

        listing = parse_detail(payload, summary)

        self.assertEqual(listing.deposit, 300_000_000)
        self.assertEqual(listing.rent, 0)

    def test_incomplete_detail_price_never_erases_valid_summary_price(self) -> None:
        summary = Listing(
            token="summary-price",
            title="ملک",
            url="https://divar.ir/v/summary-price",
            deposit=250_000_000,
            rent=35_000_000,
        )
        payload = {
            "sections": [],
            "webengage": {"credit": 0, "rent": 0},
        }

        listing = parse_detail(payload, summary)

        self.assertEqual(listing.deposit, 250_000_000)
        self.assertEqual(listing.rent, 35_000_000)

    def test_existing_sort_date_is_migrated_to_real_publication_date(self) -> None:
        publication_widget = {
            "widget_type": "DESCRIPTION_ROW",
            "data": {"text": (
                "انتشار آگهی: ۵ مرداد ۱۴۰۵، ۱۲:۳۳\n"
                "آخرین نردبان: ۲۷ مرداد ۱۴۰۵، ۱۲:۳۶"
            )},
        }
        with TemporaryDirectory() as directory:
            path = Path(directory) / "migration.db"
            database = Database(path)
            database.upsert_listing(
                Listing(
                    token="legacy",
                    title="ملک",
                    url="https://divar.ir/v/legacy",
                    published_at="2026-08-18T09:06:10.737376Z",
                    raw_detail={"sections": [{"widgets": [publication_widget]}]},
                ),
                detail_fetched=True,
            )
            database.set_feedback("legacy", "favorite", ["good_value"])

            migrated = Database(path)
            listing = migrated.get_listing("legacy")
            record = migrated.list_records()[0]

            self.assertEqual(listing.published_at, "2026-07-27T09:03:00Z")
            self.assertEqual(listing.updated_at, "2026-08-18T09:06:10.737376Z")
            self.assertEqual(record["feedback"], "favorite")


if __name__ == "__main__":
    unittest.main()
