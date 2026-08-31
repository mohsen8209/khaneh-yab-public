from __future__ import annotations

import http.cookiejar
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from divar_scout.auth import AuthError, AuthService, verify_password
from divar_scout.config import load_config
from divar_scout.dashboard import DashboardApp, DashboardServer
from divar_scout.db import Database
from divar_scout.models import Listing


class AuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "auth.sqlite3")
        self.auth = AuthService(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_password_is_salted_hashed_and_duplicate_username_is_rejected(self) -> None:
        self.auth.register("user_one", "کاربر اول", "SecurePass123")
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT password_hash FROM users WHERE username = 'user_one'"
            ).fetchone()
        encoded = str(row["password_hash"])
        self.assertNotIn("SecurePass123", encoded)
        self.assertTrue(verify_password("SecurePass123", encoded))
        self.assertFalse(verify_password("WrongPass123", encoded))
        with self.assertRaises(AuthError):
            self.auth.register("USER_ONE", "کاربر تکراری", "AnotherPass123")

    def test_admin_approval_and_suspension_revoke_access(self) -> None:
        user = self.auth.register("waiting_user", "کاربر منتظر", "SecurePass123")
        admin = self.auth.create_admin("root_admin", "مدیر اصلی", "AdminSecure123")
        session = self.auth.authenticate("waiting_user", "SecurePass123")
        self.assertEqual(session.user.status, "pending")

        approved = self.auth.set_user_status(admin, user.id, "approved")
        self.assertEqual(approved.status, "approved")
        self.assertEqual(self.auth.session(session.token).user.status, "approved")

        self.auth.set_user_status(admin, user.id, "suspended")
        self.assertIsNone(self.auth.session(session.token))
        with self.assertRaises(AuthError):
            self.auth.authenticate("waiting_user", "SecurePass123")

    def test_feedback_and_models_are_isolated_per_user(self) -> None:
        first = self.auth.register("first_user", "کاربر اول", "SecurePass123")
        second = self.auth.register("second_user", "کاربر دوم", "SecurePass456")
        listing = Listing(
            token="same-listing",
            title="خانه مشترک در فهرست",
            url="https://divar.ir/v/same-listing",
            image_count=4,
        )
        self.database.upsert_listing(listing, detail_fetched=True)
        self.database.set_feedback(
            listing.token, "favorite", ["good_size"], user_id=first.id
        )
        self.database.set_feedback(
            listing.token, "rejected", ["too_small"], user_id=second.id
        )
        self.database.save_personalization_model(
            {"owner": "first"}, "one", user_id=first.id
        )
        self.database.save_personalization_model(
            {"owner": "second"}, "two", user_id=second.id
        )

        self.assertEqual(
            self.database.list_records(user_id=first.id)[0]["feedback"], "favorite"
        )
        self.assertEqual(
            self.database.list_records(user_id=second.id)[0]["feedback"], "rejected"
        )
        self.assertEqual(
            self.database.load_personalization_model(user_id=first.id)[0]["owner"],
            "first",
        )
        self.assertEqual(
            self.database.load_personalization_model(user_id=second.id)[0]["owner"],
            "second",
        )


class AuthHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "http.sqlite3")
        self.auth = AuthService(self.database)
        self.admin = self.auth.create_admin(
            "root_admin", "مدیر اصلی", "AdminSecure123"
        )
        app = DashboardApp(load_config(), database=self.database)
        self.server = DashboardServer(("127.0.0.1", 0), app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    @staticmethod
    def opener() -> urllib.request.OpenerDirector:
        return urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def request(
        self,
        opener: urllib.request.OpenerDirector,
        path: str,
        *,
        payload: dict | None = None,
        csrf: str | None = None,
    ) -> tuple[int, dict, Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if csrf:
            headers["X-CSRF-Token"] = csrf
        request = urllib.request.Request(
            self.base + path,
            data=data,
            headers=headers,
            method="POST" if payload is not None else "GET",
        )
        try:
            response = opener.open(request)
        except urllib.error.HTTPError as error:
            response = error
        body = json.loads(response.read().decode("utf-8"))
        return response.status, body, response.headers

    def test_pending_account_is_gated_until_admin_approval(self) -> None:
        guest = self.opener()
        status, _, _ = self.request(guest, "/api/stats")
        self.assertEqual(status, 401)

        user_browser = self.opener()
        status, registered, headers = self.request(
            user_browser,
            "/api/auth/register",
            payload={
                "username": "new_user",
                "display_name": "کاربر تازه",
                "password": "SecurePass123",
            },
        )
        self.assertEqual(status, 201)
        self.assertIn("HttpOnly", headers.get("Set-Cookie"))
        self.assertIn("SameSite=Strict", headers.get("Set-Cookie"))
        user_id = registered["user"]["id"]
        status, pending, _ = self.request(user_browser, "/api/stats")
        self.assertEqual(status, 403)
        self.assertEqual(pending["code"], "pending")

        admin_browser = self.opener()
        status, _, _ = self.request(
            admin_browser,
            "/api/auth/login",
            payload={"username": "root_admin", "password": "AdminSecure123"},
        )
        self.assertEqual(status, 200)
        _, admin_state, _ = self.request(admin_browser, "/api/auth/me")
        status, _, _ = self.request(
            admin_browser,
            "/api/admin/users/status",
            payload={"user_id": user_id, "status": "approved"},
            csrf=admin_state["csrf_token"],
        )
        self.assertEqual(status, 200)

        status, profile_required, _ = self.request(user_browser, "/api/stats")
        self.assertEqual(status, 409)
        self.assertEqual(profile_required["code"], "profile_required")

        _, user_state, _ = self.request(user_browser, "/api/auth/me")
        status, saved, _ = self.request(
            user_browser,
            "/api/profile",
            payload={
                "search_mode": "all_tehran",
                "criteria": {
                    "ideal_deposit": 300_000_000,
                    "hard_max_deposit": 350_000_000,
                    "stretch_max_deposit": 350_000_000,
                    "ideal_rent": 30_000_000,
                    "hard_max_rent": 45_000_000,
                    "stretch_max_rent": 45_000_000,
                    "min_size": 60,
                    "ideal_size": 80,
                    "max_size": None,
                    "min_rooms": 1,
                    "max_rooms": None,
                    "ideal_max_age": 10,
                    "hard_max_age": 25,
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
            },
            csrf=user_state["csrf_token"],
        )
        self.assertEqual(status, 200)
        self.assertTrue(saved["profile"]["completed"])
        self.assertEqual(saved["redirect"], "/calibration")

        status, calibration_required, _ = self.request(user_browser, "/api/stats")
        self.assertEqual(status, 409)
        self.assertEqual(calibration_required["code"], "calibration_required")
        self.assertEqual(calibration_required["redirect"], "/calibration")

        self.database.upsert_listing(
            Listing(
                token="missing-district",
                title="آپارتمان با محله نامشخص",
                url="https://divar.ir/v/missing-district",
                district=None,
                deposit=250_000_000,
                rent=30_000_000,
                size=75,
                rooms=1,
                age=8,
                has_parking=True,
                has_elevator=True,
                image_count=3,
                images=["https://s100.divarcdn.com/static/test.jpg"] * 3,
                description="واحد مسکونی با پارکینگ خودرو و آسانسور",
            ),
            detail_fetched=True,
        )
        status, calibration, headers = self.request(user_browser, "/api/calibration")
        self.assertEqual(status, 200)
        self.assertFalse(calibration["ready"])
        self.assertEqual(calibration["calibration"]["target"], 12)
        self.assertEqual(calibration["calibration"]["completed"], 0)
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")

        scan_calls = []
        scan_started = threading.Event()

        def fake_scan(
            detail_limit=None, *, user_initiated=False, source_urls=None,
            archive_missing=True, user_id=None,
        ):
            scan_calls.append(
                (
                    detail_limit, user_initiated, tuple(source_urls or []),
                    archive_missing, user_id,
                )
            )
            scan_started.set()
            return {"ok": True, "summaries": 7, "details": 3}

        self.server.app.run_scan = fake_scan
        status, scan_result, _ = self.request(
            user_browser,
            "/api/scan",
            payload={"detail_limit": 999},
            csrf=user_state["csrf_token"],
        )
        self.assertEqual(status, 202)
        self.assertTrue(scan_result["accepted"])
        self.assertTrue(scan_started.wait(timeout=1))
        self.assertEqual(len(scan_calls), 1)
        self.assertEqual(scan_calls[0][:2], (None, True))
        self.assertEqual(len(scan_calls[0][2]), 1)
        self.assertFalse(scan_calls[0][3])
        self.assertEqual(scan_calls[0][4], user_id)

        status, csrf_error, _ = self.request(
            admin_browser,
            "/api/admin/users/status",
            payload={"user_id": user_id, "status": "suspended"},
        )
        self.assertEqual(status, 403)
        self.assertIn("امنیتی", csrf_error["error"])


    def test_unexpected_get_api_errors_return_complete_json_response(self) -> None:
        admin_browser = self.opener()
        status, _, _ = self.request(
            admin_browser,
            "/api/auth/login",
            payload={"username": "root_admin", "password": "AdminSecure123"},
        )
        self.assertEqual(status, 200)

        with patch.object(
            self.server.app.auth,
            "list_users",
            side_effect=Exception("database connection dropped"),
        ):
            status, payload, headers = self.request(admin_browser, "/api/admin/users")

        self.assertEqual(status, 500)
        self.assertEqual(headers.get_content_type(), "application/json")
        self.assertIn("خطای داخلی سرور", payload["error"])

    def test_admin_database_diagnostics_endpoint_is_admin_only(self) -> None:
        user = self.auth.register("diag_user", "Diag User", "SecurePass123")
        self.auth.set_user_status(self.admin, user.id, "approved")
        user_browser = self.opener()
        status, _, _ = self.request(
            user_browser,
            "/api/auth/login",
            payload={"username": "diag_user", "password": "SecurePass123"},
        )
        self.assertEqual(status, 200)
        status, forbidden, _ = self.request(user_browser, "/api/admin/debug/database")
        self.assertEqual(status, 403)
        self.assertIn("error", forbidden)

        admin_browser = self.opener()
        status, _, _ = self.request(
            admin_browser,
            "/api/auth/login",
            payload={"username": "root_admin", "password": "AdminSecure123"},
        )
        self.assertEqual(status, 200)
        status, diagnostics, _ = self.request(admin_browser, "/api/admin/debug/database")

        self.assertEqual(status, 200)
        self.assertTrue(diagnostics["database"]["exists"])
        self.assertEqual(diagnostics["counts"]["users"], 2)
        self.assertEqual(diagnostics["counts"]["profiles"], 2)
        self.assertNotIn("password_hash", json.dumps(diagnostics))
        self.assertNotIn("csrf", json.dumps(diagnostics))

    def test_health_check_exposes_the_running_release(self) -> None:
        status, payload, headers = self.request(self.opener(), "/healthz")

        self.assertEqual(status, 200)
        self.assertEqual(payload, {"ok": True, "release": "0.1.6"})
        self.assertEqual(headers.get("X-Khaneyab-Release"), "0.1.6")


if __name__ == "__main__":
    unittest.main()
