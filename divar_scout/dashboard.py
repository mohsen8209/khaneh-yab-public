from __future__ import annotations

import hmac
import json
import mimetypes
import threading
import traceback
from copy import deepcopy
from datetime import UTC, datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import __version__
from .auth import AuthError, AuthService, AuthSession, AuthUser, RateLimitError
from .config import AppConfig
from .db import Database
from .models import Listing, ScoreResult
from .neighborhoods import default_atlas
from .notifier import NtfyNotifier
from .personalization import PersonalizationEngine
from .scoring import (
    listing_exclusion_reason,
    profile_scope_exclusion_reason,
    score_listing,
)
from .search_profile import (
    UNUSABLE_DIVAR_DISTRICT_IDS,
    config_for_profile,
    crawl_urls_for_profile,
    districts,
    empty_profile,
    normalize_profile,
    validate_profile,
)
from .service import ScoutService


STATIC_ROOT = Path(__file__).resolve().parent / "static"
SESSION_COOKIE = "khane_yab_public_session"
MAX_BODY_BYTES = 64 * 1024
CALIBRATION_TARGET = 12
CALIBRATION_RECORD_LIMIT = 140
CALIBRATION_POOL_SCAN_LIMIT = 2000
MAX_CALIBRATION_SCAN_BATCHES = 3
CLIENT_DISCONNECT_ERRORS = (
    BrokenPipeError,
    ConnectionAbortedError,
    ConnectionResetError,
)


class DashboardApp:
    def __init__(self, config: AppConfig, database: Database | None = None) -> None:
        self.config = config
        self.database = database or Database()
        self.auth = AuthService(self.database)
        self.service = ScoutService(config, database=self.database)
        self.atlas = default_atlas()
        self.scan_lock = threading.Lock()
        self.scan_state_lock = threading.Lock()
        self._active_scan_urls: set[str] = set()
        self._queued_scan_jobs: dict[tuple[str, ...], int | None] = {}
        self._scan_state: dict[str, Any] = {
            "running": False,
            "queued": False,
            "started_at": None,
            "finished_at": None,
            "last_result": None,
            "last_error": None,
        }
        self.engine_lock = threading.Lock()
        self._engines: dict[int | None, PersonalizationEngine] = {}
        self.personalization = self.engine(None)

    def engine(self, user_id: int | None) -> PersonalizationEngine:
        with self.engine_lock:
            if user_id not in self._engines:
                self._engines[user_id] = PersonalizationEngine(
                    self.user_config(user_id), self.database, self.atlas, user_id=user_id
                )
            return self._engines[user_id]

    def profile(self, user_id: int) -> dict[str, Any]:
        stored = self.database.get_user_profile(user_id)
        return normalize_profile(stored) if stored else empty_profile()

    def user_config(self, user_id: int | None) -> AppConfig:
        if user_id is None:
            return self.config
        return config_for_profile(self.config, self.profile(user_id))

    def profile_payload(self, user_id: int) -> dict[str, Any]:
        catalog = []
        for item in districts():
            if item["id"] in UNUSABLE_DIVAR_DISTRICT_IDS:
                continue
            rating = self.atlas.rate(item["name"])
            catalog.append({**item, "score": rating.score, "quality": rating.label})
        catalog.sort(key=lambda item: (-float(item["score"]), item["name"]))
        return {"profile": self.profile(user_id), "districts": catalog}

    def save_profile(self, user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        was_complete = self.database.user_profile_complete(user_id)
        profile = validate_profile(payload)
        self.database.save_user_profile(
            user_id,
            profile,
            calibration_target=CALIBRATION_TARGET if not was_complete else None,
        )
        with self.engine_lock:
            self._engines.pop(user_id, None)
        self.engine(user_id).retrain()
        return profile

    def listings(
        self, query: dict[str, list[str]], user_id: int | None = None
    ) -> list[dict[str, Any]]:
        records = self._personalized_records(user_id)
        status = query.get("status", [""])[0]
        district = query.get("district", [""])[0]
        search = query.get("q", [""])[0].strip().lower()
        min_score = float(query.get("min_score", ["0"])[0] or 0)
        include_pending = query.get("include_pending", ["0"])[0] == "1"
        result: list[dict[str, Any]] = []
        for item in records:
            score = item.get("score") or {}
            if not score and not include_pending:
                continue
            if status and score.get("status") != status:
                continue
            if district and item.get("district") != district:
                continue
            if float(score.get("total") or 0) < min_score:
                continue
            haystack = f"{item.get('title', '')} {item.get('district', '')}".lower()
            if search and search not in haystack:
                continue
            result.append(_public_listing(item))
        result.sort(
            key=lambda item: (
                item.get("feedback") != "rejected",
                float((item.get("score") or {}).get("total") or -1),
                item.get("first_seen_at") or "",
            ),
            reverse=True,
        )
        return result

    def stats(self, user_id: int | None = None) -> dict[str, Any]:
        stats = self.database.stats()
        scored = [item for item in self._personalized_records(user_id) if item.get("score")]
        stats["total"] = len(scored)
        stats["favorites"] = sum(item.get("feedback") == "favorite" for item in scored)
        for status, key in (
            ("alert", "alerts"),
            ("review", "reviews"),
            ("candidate", "candidates"),
            ("rejected", "rejected"),
        ):
            stats[key] = sum(item["score"].get("status") == status for item in scored)
        stats["best_score"] = max(
            (
                float(item["score"].get("total") or 0)
                for item in scored
                if item["score"].get("status") != "rejected"
            ),
            default=0,
        )
        stats["learning"] = self.engine(user_id).state()
        return stats

    def learning_state(self, user_id: int | None = None) -> dict[str, Any]:
        return self.engine(user_id).state()

    def database_diagnostics(self) -> dict[str, Any]:
        return self.database.database_diagnostics()

    def set_feedback(
        self,
        token: str,
        choice: str | None,
        reasons: list[str] | None = None,
        *,
        user_id: int | None = None,
    ) -> dict[str, Any]:
        if not self.database.has_listing(token):
            raise ValueError("آگهی پیدا نشد")
        self.database.set_feedback(token, choice, reasons, user_id=user_id)
        return self.engine(user_id).retrain()

    def comparison(
        self,
        query: dict[str, list[str]] | None = None,
        *,
        user_id: int | None = None,
    ) -> dict[str, Any]:
        records = self._personalized_records(user_id)
        raw_excluded = (query or {}).get("exclude", [""])[0]
        excluded = {
            frozenset(pair.split(",", 1))
            for pair in raw_excluded.split("|")
            if "," in pair
        }
        pair = self.engine(user_id).select_comparison(records, excluded)
        if pair is None:
            return {"left": None, "right": None, "state": self.learning_state(user_id)}
        left, right, kind = pair
        return {
            "left": _public_listing(left),
            "right": _public_listing(right),
            "kind": kind,
            "state": self.learning_state(user_id),
        }

    def submit_comparison(
        self,
        left_token: str,
        right_token: str,
        winner_token: str,
        reasons: list[str] | None = None,
        kind: str = "general",
        *,
        user_id: int | None = None,
    ) -> dict[str, Any]:
        self.database.add_comparison(
            left_token,
            right_token,
            winner_token,
            reasons,
            kind,
            user_id=user_id,
        )
        return self.engine(user_id).retrain()

    def calibration(self, user_id: int) -> dict[str, Any]:
        state = self.database.calibration_state(user_id)
        public_state = _public_calibration_state(state)
        if state["complete"]:
            return {
                "ready": False,
                "left": None,
                "right": None,
                "calibration": public_state,
                "learning": self.learning_state(user_id),
                "redirect": "/",
            }

        records = self._personalized_records(
            user_id,
            limit=CALIBRATION_POOL_SCAN_LIMIT,
            include_market_context=False,
        )
        eligible_records = [
            item
            for item in records
            if item.get("score")
            and item["score"].get("status") not in {"rejected", "new"}
            and item.get("feedback") != "rejected"
            and item.get("detail_fetched_at")
        ][:50]
        eligible = {item["token"]: item for item in eligible_records}
        compared = self.database.compared_pairs(user_id=user_id)
        available_pairs = sum(
            frozenset((left["token"], right["token"])) not in compared
            for index, left in enumerate(eligible_records)
            for right in eligible_records[index + 1:]
        )
        left_token = state.get("current_left_token")
        right_token = state.get("current_right_token")
        if not (left_token in eligible and right_token in eligible):
            if left_token or right_token:
                self.database.clear_calibration_pair(user_id)
            if available_pairs < int(state["remaining"]):
                return {
                    "ready": False,
                    "left": None,
                    "right": None,
                    "available": len(eligible),
                    "available_pairs": available_pairs,
                    "calibration": public_state,
                    "learning": self.learning_state(user_id),
                }
            pair = self.engine(user_id).select_comparison(eligible_records)
            if pair is None:
                return {
                    "ready": False,
                    "left": None,
                    "right": None,
                    "available": len(eligible),
                    "calibration": public_state,
                    "learning": self.learning_state(user_id),
                }
            left, right, _kind = pair
            if int(state["completed"]) % 2:
                left, right = right, left
            left_token, right_token = self.database.set_calibration_pair(
                user_id, left["token"], right["token"]
            )
            left = eligible.get(left_token)
            right = eligible.get(right_token)
            if left is None or right is None:
                self.database.clear_calibration_pair(user_id)
                return self.calibration(user_id)
            state = self.database.calibration_state(user_id)
            public_state = _public_calibration_state(state)
        else:
            left = eligible[left_token]
            right = eligible[right_token]

        return {
            "ready": True,
            "left": _public_listing(left),
            "right": _public_listing(right),
            "available": len(eligible),
            "available_pairs": available_pairs,
            "calibration": public_state,
            "learning": self.learning_state(user_id),
        }

    def submit_calibration(
        self,
        user_id: int,
        left_token: str,
        right_token: str,
        winner_token: str,
    ) -> dict[str, Any]:
        state = self.database.add_calibration_comparison(
            user_id, left_token, right_token, winner_token
        )
        learning = self.engine(user_id).retrain()
        return {
            "ok": True,
            "calibration": _public_calibration_state(state),
            "learning": learning,
            "redirect": "/" if state["complete"] else None,
        }

    def reset_personalization(self, user_id: int | None = None) -> dict[str, Any]:
        self.database.clear_personalization(user_id=user_id)
        return self.engine(user_id).retrain()

    def _personalized_records(
        self,
        user_id: int | None = None,
        *,
        limit: int = 500,
        include_market_context: bool = True,
    ) -> list[dict[str, Any]]:
        if user_id is not None and not self.database.user_profile_complete(user_id):
            return []
        engine = self.engine(user_id)
        user_config = self.user_config(user_id)
        records = [
            item
            for item in self.database.list_records(user_id=user_id, limit=limit)
            if listing_exclusion_reason(item) is None
            and profile_scope_exclusion_reason(item, user_config) is None
        ]
        listing_fields = Listing.__dataclass_fields__
        detailed_listings = {
            item["token"]: Listing(
                **{key: value for key, value in item.items() if key in listing_fields}
            )
            for item in records
            if user_id is not None and item.get("detail_fetched_at")
        }
        rate = float(user_config.profile["deposit_to_monthly_rate"])
        comparable_costs = (
            self.database.comparable_costs_for(list(detailed_listings.values()), rate)
            if include_market_context else {}
        )
        duplicate_counts = (
            self.database.duplicate_image_counts(list(detailed_listings))
            if include_market_context else {}
        )
        for item in records:
            listing = detailed_listings.get(item["token"])
            if listing is not None:
                base_score = score_listing(
                    listing,
                    user_config,
                    self.atlas,
                    duplicate_image_count=(
                        duplicate_counts.get(listing.token, 0)
                        if include_market_context else 0
                    ),
                    comparable_costs=comparable_costs.get(listing.token, []),
                )
                item["score"] = base_score.as_dict()
            if item.get("score"):
                item["score"] = engine.personalize_payload(item, item["score"])
                if item.get("feedback") == "rejected":
                    item["score"]["status"] = "rejected"
                    item["score"]["user_rejected"] = True
        return records

    def safe_config(
        self, *, user_id: int | None = None, is_admin: bool = False
    ) -> dict[str, Any]:
        notifications = self.config.notifications
        current = self.user_config(user_id)
        return {
            "profile": current.profile,
            "scoring": current.scoring,
            "notifications": {
                "enabled": bool(notifications.get("enabled", False) and is_admin),
                "server": notifications["server"] if is_admin else "",
                "topic": notifications["topic"] if is_admin else "",
                "subscription_url": (
                    notifications["server"].rstrip("/") + "/" + notifications["topic"]
                    if is_admin else ""
                ),
            },
            "personalization": current.personalization,
        }

    def run_scan(
        self,
        detail_limit: int | None = None,
        *,
        user_initiated: bool = False,
        source_urls: list[str] | None = None,
        archive_missing: bool = True,
        user_id: int | None = None,
    ) -> dict[str, Any]:
        with self.scan_lock:
            service = (
                self.service
                if user_id is None
                else ScoutService(
                    self.user_config(user_id),
                    database=self.database,
                    atlas=self.atlas,
                )
            )
            return service.run_once(
                detail_limit=detail_limit,
                source_urls=self._source_urls() if source_urls is None else source_urls,
                archive_missing=archive_missing,
            ).as_dict()

    def _source_urls(self) -> list[str]:
        source_urls = sorted(
            {
                source_url
                for profile in self.database.completed_user_profiles()
                for source_url in crawl_urls_for_profile(profile)
            }
        )
        return source_urls or [self.config.search["url"]]

    def _source_urls_for_user(self, user_id: int | None) -> list[str]:
        if user_id is None:
            return self._source_urls()
        return crawl_urls_for_profile(self.profile(user_id))

    def request_scan(
        self,
        detail_limit: int | None = None,
        *,
        user_initiated: bool = False,
        user_id: int | None = None,
    ) -> dict[str, Any]:
        requested_urls = self._source_urls_for_user(user_id)
        if not requested_urls:
            raise ValueError("محله‌های انتخابی دیگر در جست‌وجوی فعلی دیوار فعال نیستند؛ معیارها را ویرایش کنید")
        with self.scan_state_lock:
            if self._scan_state["running"]:
                queued = not set(requested_urls).issubset(self._active_scan_urls)
                if queued:
                    job_key = tuple(sorted(set(requested_urls)))
                    self._queued_scan_jobs.setdefault(job_key, user_id)
                    self._scan_state["queued"] = bool(self._queued_scan_jobs)
                status = deepcopy(self._scan_state)
                status.update({"accepted": True, "shared": True, "poll_after_seconds": 6})
                return status
            started_at = _utc_timestamp()
            self._active_scan_urls = set(requested_urls)
            self._queued_scan_jobs.clear()
            self._scan_state = {
                "running": True,
                "queued": False,
                "started_at": started_at,
                "finished_at": None,
                "last_result": None,
                "last_error": None,
            }
        threading.Thread(
            target=self._scan_job,
            args=(detail_limit, requested_urls),
            kwargs={
                "user_initiated": user_initiated,
                "archive_missing": user_id is None,
                "user_id": user_id,
            },
            daemon=True,
            name="khane-yab-user-scan",
        ).start()
        return {
            "accepted": True,
            "shared": False,
            "running": True,
            "queued": False,
            "started_at": started_at,
            "finished_at": None,
            "last_result": None,
            "last_error": None,
            "poll_after_seconds": 6,
        }

    def scan_status(self) -> dict[str, Any]:
        with self.scan_state_lock:
            status = deepcopy(self._scan_state)
        status["poll_after_seconds"] = 6
        status["last_scan"] = self.database.stats().get("last_scan")
        return status

    def _scan_job(
        self,
        detail_limit: int | None,
        source_urls: list[str],
        *,
        user_initiated: bool = False,
        archive_missing: bool = True,
        user_id: int | None = None,
    ) -> None:
        try:
            current_urls = source_urls
            calibration_batch = 1
            while True:
                result = self.run_scan(
                    detail_limit=detail_limit,
                    user_initiated=user_initiated,
                    source_urls=current_urls,
                    archive_missing=archive_missing,
                    user_id=user_id,
                )
                if self._needs_calibration_top_up(
                    user_id, result, calibration_batch
                ):
                    calibration_batch += 1
                    archive_missing = False
                    continue
                with self.scan_state_lock:
                    if self._queued_scan_jobs:
                        next_job = sorted(self._queued_scan_jobs)[0]
                        user_id = self._queued_scan_jobs.pop(next_job)
                        current_urls = list(next_job)
                        self._active_scan_urls = set(current_urls)
                        archive_missing = False
                        calibration_batch = 1
                        self._scan_state.update(
                            {
                                "queued": bool(self._queued_scan_jobs),
                                "last_result": result,
                                "last_error": None,
                            }
                        )
                        continue
                    self._active_scan_urls.clear()
                    self._scan_state.update(
                        {
                            "running": False,
                            "queued": False,
                            "finished_at": _utc_timestamp(),
                            "last_result": result,
                            "last_error": None,
                        }
                    )
                    return
        except Exception as error:
            with self.scan_state_lock:
                self._active_scan_urls.clear()
                self._queued_scan_jobs.clear()
                self._scan_state.update(
                    {
                        "running": False,
                        "queued": False,
                        "finished_at": _utc_timestamp(),
                        "last_result": None,
                        "last_error": str(error),
                    }
                )

    def _needs_calibration_top_up(
        self,
        user_id: int | None,
        result: dict[str, Any],
        batch: int,
    ) -> bool:
        if (
            user_id is None
            or batch >= MAX_CALIBRATION_SCAN_BATCHES
            or int(result.get("detail_count") or 0) <= 0
            or not self.database.user_profile_complete(user_id)
            or not self.database.calibration_required(user_id)
        ):
            return False
        state = self.calibration(user_id)
        return not bool(
            state.get("ready") or state.get("calibration", {}).get("complete")
        )


class DashboardHandler(BaseHTTPRequestHandler):
    server: "DashboardServer"

    def do_GET(self) -> None:  # noqa: N802
        try:
            self._handle_get()
        except CLIENT_DISCONNECT_ERRORS:
            return
        except (AuthError, KeyError, ValueError, RuntimeError) as error:
            self._safe_error_response(self._known_error, error)
        except Exception as error:
            self._safe_error_response(self._internal_error, error)

    def _handle_get(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/assets/"):
            self._serve_asset(path)
            return
        if path == "/healthz":
            self._json({"ok": True, "release": __version__})
            return
        if path == "/api/auth/me":
            self._auth_state()
            return

        session = self._session()
        if path in {"/login", "/register"}:
            if session and session.user.status == "approved":
                self._redirect(self._approved_home(session.user))
            elif session and session.user.status == "pending":
                self._redirect("/pending")
            else:
                self._file(STATIC_ROOT / "auth.html")
            return
        if path == "/pending":
            if not session:
                self._redirect("/login")
            elif session.user.status == "approved":
                self._redirect(self._approved_home(session.user))
            else:
                self._file(STATIC_ROOT / "pending.html")
            return
        if path == "/admin":
            if not self._is_admin(session):
                self._page_access_denied(session)
            else:
                self._file(STATIC_ROOT / "admin.html")
            return
        if path == "/setup":
            if not session:
                self._redirect("/login")
            elif session.user.status != "approved":
                self._redirect("/pending")
            elif session.user.role == "admin":
                self._redirect("/admin")
            else:
                self._file(STATIC_ROOT / "setup.html")
            return
        if path == "/calibration":
            if not session:
                self._redirect("/login")
            elif session.user.status != "approved":
                self._redirect("/pending")
            elif session.user.role == "admin":
                self._redirect("/admin")
            elif not self.server.app.database.user_profile_complete(session.user.id):
                self._redirect("/setup")
            elif not self.server.app.database.calibration_required(session.user.id):
                self._redirect("/")
            else:
                self._file(STATIC_ROOT / "calibration.html")
            return
        if path == "/":
            if not session:
                self._redirect("/login")
            elif session.user.status != "approved":
                self._redirect("/pending")
            elif session.user.role != "admin" and not self.server.app.database.user_profile_complete(session.user.id):
                self._redirect("/setup")
            elif session.user.role != "admin" and self.server.app.database.calibration_required(session.user.id):
                self._redirect("/calibration")
            else:
                self._file(STATIC_ROOT / "index.html")
            return

        if path.startswith("/api/admin/"):
            if not self._require_admin(session):
                return
            if path == "/api/admin/users":
                self._json(
                    {
                        "users": self.server.app.auth.list_users(),
                        "stats": self.server.app.auth.admin_stats(),
                    }
                )
            elif path == "/api/admin/debug/database":
                self._json(self.server.app.database_diagnostics())
            else:
                self._json({"error": "مسیر مدیریت پیدا نشد"}, status=HTTPStatus.NOT_FOUND)
            return

        if path.startswith("/api/"):
            user = self._require_approved(session)
            if not user:
                return
            query = parse_qs(parsed.query)
            if path == "/api/profile":
                self._json(self.server.app.profile_payload(user.id))
                return
            if not self._require_profile(user):
                return
            if path == "/api/calibration":
                self._json(self.server.app.calibration(user.id))
                return
            if path == "/api/scan/status":
                self._json(self.server.app.scan_status())
                return
            if not self._require_calibration(user):
                return
            if path == "/api/listings":
                self._json(self.server.app.listings(query, user.id))
            elif path == "/api/stats":
                self._json(self.server.app.stats(user.id))
            elif path == "/api/config":
                self._json(
                    self.server.app.safe_config(
                        user_id=user.id, is_admin=user.role == "admin"
                    )
                )
            elif path == "/api/learning":
                self._json(self.server.app.learning_state(user.id))
            elif path == "/api/comparison":
                self._json(self.server.app.comparison(query, user_id=user.id))
            else:
                self._json({"error": "مسیر پیدا نشد"}, status=HTTPStatus.NOT_FOUND)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            self._verify_origin()
            body = self._read_json()
            if path == "/api/auth/register":
                user = self.server.app.auth.register(
                    body.get("username", ""),
                    body.get("display_name", ""),
                    body.get("password", ""),
                )
                session = self.server.app.auth.create_session(
                    user,
                    remote_address=self.client_address[0],
                    user_agent=self.headers.get("User-Agent", ""),
                )
                self._set_session_cookie(session)
                self._json(
                    {"ok": True, "user": user.as_dict(), "redirect": "/pending"},
                    status=HTTPStatus.CREATED,
                )
                return
            if path == "/api/auth/login":
                session = self.server.app.auth.authenticate(
                    body.get("username", ""),
                    body.get("password", ""),
                    remote_address=self.client_address[0],
                    user_agent=self.headers.get("User-Agent", ""),
                )
                self._set_session_cookie(session)
                redirect = (
                    "/pending"
                    if session.user.status == "pending"
                    else self._approved_home(session.user)
                )
                self._json(
                    {"ok": True, "user": session.user.as_dict(), "redirect": redirect}
                )
                return

            session = self._session()
            if not session:
                self._json(
                    {"error": "ابتدا وارد حساب شوید", "code": "auth_required"},
                    status=HTTPStatus.UNAUTHORIZED,
                )
                return
            if not self._verify_csrf(session):
                return
            if path == "/api/auth/logout":
                self.server.app.auth.logout(
                    session.token, remote_address=self.client_address[0]
                )
                self._clear_session_cookie()
                self._json({"ok": True})
                return
            if path == "/api/auth/password":
                self.server.app.auth.change_password(
                    session.user.id,
                    body.get("current_password", ""),
                    body.get("new_password", ""),
                )
                self._clear_session_cookie()
                self._json({"ok": True, "reauthenticate": True})
                return
            if path == "/api/admin/users/status":
                if not self._require_admin(session):
                    return
                user = self.server.app.auth.set_user_status(
                    session.user,
                    int(body["user_id"]),
                    str(body["status"]),
                    remote_address=self.client_address[0],
                )
                self._json({"ok": True, "user": user.as_dict()})
                return

            user = self._require_approved(session)
            if not user:
                return
            if path == "/api/profile":
                profile = self.server.app.save_profile(user.id, body)
                self._json(
                    {
                        "ok": True,
                        "profile": profile,
                        "redirect": self._approved_home(user),
                    }
                )
                return
            if not self._require_profile(user):
                return
            if path == "/api/calibration":
                self._json(
                    self.server.app.submit_calibration(
                        user.id,
                        body["left_token"],
                        body["right_token"],
                        body["winner_token"],
                    )
                )
                return
            if path == "/api/scan":
                detail_limit = body.get("detail_limit") if user.role == "admin" else None
                self._json(
                    self.server.app.request_scan(
                        detail_limit,
                        user_initiated=user.role != "admin",
                        user_id=user.id if user.role != "admin" else None,
                    ),
                    status=HTTPStatus.ACCEPTED,
                )
                return
            if not self._require_calibration(user):
                return
            if path == "/api/feedback":
                state = self.server.app.set_feedback(
                    body["token"], body.get("choice"), body.get("reasons"), user_id=user.id
                )
                self._json({"ok": True, "learning": state})
            elif path == "/api/comparison":
                state = self.server.app.submit_comparison(
                    body["left_token"],
                    body["right_token"],
                    body["winner_token"],
                    body.get("reasons"),
                    body.get("kind", "general"),
                    user_id=user.id,
                )
                self._json({"ok": True, "learning": state})
            elif path == "/api/personalization/reset":
                self._json(
                    {
                        "ok": True,
                        "learning": self.server.app.reset_personalization(user.id),
                    }
                )
            elif path == "/api/notify-test":
                if user.role != "admin":
                    self._json(
                        {"error": "اعلان سراسری فقط در اختیار ادمین است"},
                        status=HTTPStatus.FORBIDDEN,
                    )
                    return
                self._send_notification_test()
                self._json({"ok": True})
            else:
                self._json({"error": "مسیر پیدا نشد"}, status=HTTPStatus.NOT_FOUND)
        except CLIENT_DISCONNECT_ERRORS:
            return
        except RateLimitError as error:
            self._safe_error_response(self._rate_limit_error, error)
        except (AuthError, KeyError, ValueError, RuntimeError) as error:
            self._safe_error_response(self._known_error, error)
        except json.JSONDecodeError:
            self._safe_error_response(self._invalid_json_error, ValueError())
        except Exception as error:
            self._safe_error_response(self._internal_error, error)

    def _safe_error_response(self, responder: Any, error: Exception) -> None:
        try:
            responder(error)
        except CLIENT_DISCONNECT_ERRORS:
            return

    def _rate_limit_error(self, error: Exception) -> None:
        self._json({"error": str(error)}, status=HTTPStatus.TOO_MANY_REQUESTS)

    def _invalid_json_error(self, _error: Exception) -> None:
        self._json({"error": "بدنه درخواست معتبر نیست"}, status=HTTPStatus.BAD_REQUEST)

    def _known_error(self, error: Exception) -> None:
        if urlparse(self.path).path.startswith("/api/"):
            self._json({"error": str(error)}, status=HTTPStatus.BAD_REQUEST)
            return
        self.send_error(HTTPStatus.BAD_REQUEST, str(error))

    def _internal_error(self, error: Exception) -> None:
        print(
            f"Unhandled dashboard error on {self.command} {self.path}: {error}",
            flush=True,
        )
        traceback.print_exception(type(error), error, error.__traceback__)
        if urlparse(self.path).path.startswith("/api/"):
            self._json(
                {"error": "خطای داخلی سرور؛ چند ثانیه دیگر دوباره تلاش کن"},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return
        self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR)

    def _send_notification_test(self) -> None:
        listing = Listing(
            token="notification-test",
            title="اعلان آزمایشی خانه‌یاب",
            url="https://divar.ir",
            district="تهران",
            deposit=300_000_000,
            rent=30_000_000,
        )
        score = ScoreResult(
            total=18.0,
            location=7.0,
            value=4.0,
            property=2.5,
            trust_points=3.0,
            freshness=1.0,
            trust=100,
            location_confidence=100,
            status="alert",
            reasons=["ارتباط اعلان گوشی برقرار است"],
            risks=[],
        )
        NtfyNotifier(self.server.app.config).send(listing, score, force=True)

    def _auth_state(self) -> None:
        session = self._session()
        if not session:
            self._json({"authenticated": False})
            return
        self._json(
            {
                "authenticated": True,
                "approved": session.user.status == "approved",
                "profile_completed": (
                    True
                    if session.user.role == "admin"
                    else self.server.app.database.user_profile_complete(session.user.id)
                ),
                "calibration_required": (
                    False
                    if session.user.role == "admin"
                    else self.server.app.database.calibration_required(session.user.id)
                ),
                "user": session.user.as_dict(),
                "csrf_token": session.csrf_token,
            }
        )

    def _session(self) -> AuthSession | None:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get(SESSION_COOKIE)
        return self.server.app.auth.session(morsel.value if morsel else None)

    def _require_approved(self, session: AuthSession | None) -> AuthUser | None:
        if not session:
            self._json(
                {"error": "ابتدا وارد حساب شوید", "code": "auth_required"},
                status=HTTPStatus.UNAUTHORIZED,
            )
            return None
        if session.user.status != "approved":
            self._json(
                {
                    "error": "حساب هنوز توسط ادمین تأیید نشده است",
                    "code": session.user.status,
                },
                status=HTTPStatus.FORBIDDEN,
            )
            return None
        return session.user

    def _require_admin(self, session: AuthSession | None) -> AuthUser | None:
        user = self._require_approved(session)
        if not user:
            return None
        if user.role != "admin":
            self._json(
                {"error": "دسترسی مدیریت لازم است"},
                status=HTTPStatus.FORBIDDEN,
            )
            return None
        return user

    def _require_profile(self, user: AuthUser) -> bool:
        if user.role == "admin" or self.server.app.database.user_profile_complete(user.id):
            return True
        self._json(
            {
                "error": "ابتدا مشخصات خانه موردنظرت را کامل کن",
                "code": "profile_required",
                "redirect": "/setup",
            },
            status=HTTPStatus.CONFLICT,
        )
        return False

    def _require_calibration(self, user: AuthUser) -> bool:
        if user.role == "admin" or not self.server.app.database.calibration_required(user.id):
            return True
        self._json(
            {
                "error": "ابتدا ۱۲ انتخاب اولیه سلیقه را کامل کن",
                "code": "calibration_required",
                "redirect": "/calibration",
            },
            status=HTTPStatus.CONFLICT,
        )
        return False

    def _approved_home(self, user: AuthUser) -> str:
        if user.role == "admin":
            return "/admin"
        if not self.server.app.database.user_profile_complete(user.id):
            return "/setup"
        if self.server.app.database.calibration_required(user.id):
            return "/calibration"
        return "/"

    @staticmethod
    def _is_admin(session: AuthSession | None) -> bool:
        return bool(
            session
            and session.user.status == "approved"
            and session.user.role == "admin"
        )

    def _page_access_denied(self, session: AuthSession | None) -> None:
        if not session:
            self._redirect("/login")
        elif session.user.status != "approved":
            self._redirect("/pending")
        else:
            self._redirect("/")

    def _verify_csrf(self, session: AuthSession) -> bool:
        supplied = self.headers.get("X-CSRF-Token", "")
        if not supplied or not hmac.compare_digest(supplied, session.csrf_token):
            self._json(
                {"error": "نشست امنیتی معتبر نیست؛ صفحه را تازه کنید"},
                status=HTTPStatus.FORBIDDEN,
            )
            return False
        return True

    def _verify_origin(self) -> None:
        origin = self.headers.get("Origin")
        if not origin:
            return
        parsed = urlparse(origin)
        if parsed.netloc.casefold() != self.headers.get("Host", "").casefold():
            raise AuthError("مبدأ درخواست معتبر نیست")

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_BODY_BYTES:
            raise ValueError("حجم درخواست بیش از حد مجاز است")
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _serve_asset(self, path: str) -> None:
        target = (STATIC_ROOT / path.removeprefix("/assets/")).resolve()
        if STATIC_ROOT.resolve() not in target.parents:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self._file(target)

    def _set_session_cookie(self, session: AuthSession) -> None:
        secure = (
            "; Secure"
            if self.headers.get("X-Forwarded-Proto", "").casefold() == "https"
            else ""
        )
        self._pending_cookie = (
            f"{SESSION_COOKIE}={session.token}; Path=/; HttpOnly; SameSite=Strict; "
            f"Max-Age={7 * 24 * 60 * 60}{secure}"
        )

    def _clear_session_cookie(self) -> None:
        self._pending_cookie = (
            f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
        )

    def _json(self, payload: Any, *, status: int = HTTPStatus.OK) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        if hasattr(self, "_pending_cookie"):
            self.send_header("Set-Cookie", self._pending_cookie)
            del self._pending_cookie
        self.end_headers()
        self.wfile.write(content)

    def _file(self, path: Path) -> None:
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.end_headers()
        self.wfile.write(content)

    def _redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self._security_headers()
        self.end_headers()

    def _security_headers(self) -> None:
        self.send_header("X-Khaneyab-Release", __version__)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' https: data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
        )

    def log_message(self, format: str, *args: Any) -> None:
        return


class DashboardServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, address: tuple[str, int], app: DashboardApp) -> None:
        super().__init__(address, DashboardHandler)
        self.app = app


def _public_listing(item: dict[str, Any]) -> dict[str, Any]:
    raw_keys = {"raw_summary", "raw_detail"}
    return {key: value for key, value in item.items() if key not in raw_keys}


def _public_calibration_state(state: dict[str, Any]) -> dict[str, Any]:
    private_keys = {"current_left_token", "current_right_token"}
    return {key: value for key, value in state.items() if key not in private_keys}


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def serve(config: AppConfig, app: DashboardApp | None = None) -> None:
    host = config.dashboard["host"]
    port = int(config.dashboard["port"])
    server = DashboardServer((host, port), app or DashboardApp(config))
    print(
        f"Khaneh-yab public dashboard {__version__}: http://{host}:{port}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
