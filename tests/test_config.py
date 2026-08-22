import unittest

from divar_scout.config import load_config
from divar_scout.divar_client import build_map_payload


class ConfigTests(unittest.TestCase):
    def test_public_bootstrap_does_not_inherit_personal_search_filters(self) -> None:
        config = load_config()
        payload = build_map_payload(config)
        data = payload["search_data"]["form_data"]["data"]

        self.assertEqual(data["category"]["str"]["value"], "apartment-rent")
        self.assertEqual(data["districts"]["repeated_string"]["value"], [])
        for personal_filter in ("credit", "rent", "size", "rooms", "parking", "elevator"):
            with self.subTest(filter=personal_filter):
                self.assertNotIn(personal_filter, data)


if __name__ == "__main__":
    unittest.main()
