from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

from .config import database_path
from .models import Listing, ScoreResult


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def image_fingerprint(url: str) -> str:
    name = Path(urlparse(url).path).stem
    return name.lower()


class Database:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else database_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS listings (
                    token TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    district TEXT,
                    url TEXT NOT NULL,
                    listing_json TEXT NOT NULL,
                    score_json TEXT,
                    total_score REAL,
                    status TEXT NOT NULL DEFAULT 'new',
                    detail_fetched_at TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    notified_at TEXT,
                    notification_eligible INTEGER NOT NULL DEFAULT 1,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    expired_at TEXT,
                    filtered_at TEXT,
                    filter_reason TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_listings_score
                    ON listings(total_score DESC);
                CREATE INDEX IF NOT EXISTS idx_listings_status
                    ON listings(status);
                CREATE INDEX IF NOT EXISTS idx_listings_district
                    ON listings(district);

                CREATE TABLE IF NOT EXISTS listing_images (
                    token TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    PRIMARY KEY (token, fingerprint),
                    FOREIGN KEY (token) REFERENCES listings(token) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_listing_images_fingerprint
                    ON listing_images(fingerprint);

                CREATE TABLE IF NOT EXISTS scans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    summary_count INTEGER DEFAULT 0,
                    detail_count INTEGER DEFAULT 0,
                    expired_count INTEGER DEFAULT 0,
                    filtered_count INTEGER DEFAULT 0,
                    error TEXT
                );

                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    display_name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user'
                        CHECK (role IN ('user', 'admin')),
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'approved', 'rejected', 'suspended')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    approved_at TEXT,
                    approved_by INTEGER,
                    last_login_at TEXT,
                    FOREIGN KEY (approved_by) REFERENCES users(id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_users_status
                    ON users(status, created_at DESC);

                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    csrf_token TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    user_agent TEXT,
                    remote_address TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_user
                    ON sessions(user_id, expires_at);

                CREATE TABLE IF NOT EXISTS auth_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    remote_address TEXT NOT NULL,
                    succeeded INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_auth_attempts_window
                    ON auth_attempts(username, remote_address, created_at DESC);

                CREATE TABLE IF NOT EXISTS auth_audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor_user_id INTEGER,
                    action TEXT NOT NULL,
                    target_user_id INTEGER,
                    remote_address TEXT,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (actor_user_id) REFERENCES users(id) ON DELETE SET NULL,
                    FOREIGN KEY (target_user_id) REFERENCES users(id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_auth_audit_created
                    ON auth_audit_log(created_at DESC);

                CREATE TABLE IF NOT EXISTS user_profiles (
                    user_id INTEGER PRIMARY KEY,
                    search_url TEXT,
                    profile_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS user_feedback (
                    user_id INTEGER NOT NULL,
                    token TEXT NOT NULL,
                    choice TEXT NOT NULL,
                    reasons_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT,
                    PRIMARY KEY (user_id, token),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (token) REFERENCES listings(token) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_user_feedback_user
                    ON user_feedback(user_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS user_comparisons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    left_token TEXT NOT NULL,
                    right_token TEXT NOT NULL,
                    winner_token TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'general',
                    reasons_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (left_token) REFERENCES listings(token) ON DELETE CASCADE,
                    FOREIGN KEY (right_token) REFERENCES listings(token) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_user_comparisons_pair
                    ON user_comparisons(user_id, left_token, right_token);

                CREATE TABLE IF NOT EXISTS user_calibrations (
                    user_id INTEGER PRIMARY KEY,
                    target_count INTEGER NOT NULL DEFAULT 12
                        CHECK (target_count > 0),
                    completed_count INTEGER NOT NULL DEFAULT 0
                        CHECK (completed_count >= 0 AND completed_count <= target_count),
                    current_left_token TEXT,
                    current_right_token TEXT,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (current_left_token) REFERENCES listings(token) ON DELETE SET NULL,
                    FOREIGN KEY (current_right_token) REFERENCES listings(token) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS user_personalization_models (
                    user_id INTEGER PRIMARY KEY,
                    model_json TEXT NOT NULL,
                    trained_at TEXT NOT NULL,
                    training_signature TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS feedback (
                    token TEXT PRIMARY KEY,
                    choice TEXT NOT NULL,
                    reasons_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT,
                    FOREIGN KEY (token) REFERENCES listings(token) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS comparisons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    left_token TEXT NOT NULL,
                    right_token TEXT NOT NULL,
                    winner_token TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'general',
                    reasons_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (left_token) REFERENCES listings(token) ON DELETE CASCADE,
                    FOREIGN KEY (right_token) REFERENCES listings(token) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_comparisons_pair
                    ON comparisons(left_token, right_token);

                CREATE TABLE IF NOT EXISTS personalization_model (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    model_json TEXT NOT NULL,
                    trained_at TEXT NOT NULL,
                    training_signature TEXT NOT NULL
                );

                """
            )
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(listings)")
            }
            if "notification_eligible" not in columns:
                connection.execute(
                    "ALTER TABLE listings ADD COLUMN notification_eligible "
                    "INTEGER NOT NULL DEFAULT 1"
                )
                connection.execute(
                    "UPDATE listings SET notification_eligible = 0"
                )
            if "is_active" not in columns:
                connection.execute(
                    "ALTER TABLE listings ADD COLUMN is_active "
                    "INTEGER NOT NULL DEFAULT 1"
                )
            if "expired_at" not in columns:
                connection.execute("ALTER TABLE listings ADD COLUMN expired_at TEXT")
            if "filtered_at" not in columns:
                connection.execute("ALTER TABLE listings ADD COLUMN filtered_at TEXT")
            if "filter_reason" not in columns:
                connection.execute("ALTER TABLE listings ADD COLUMN filter_reason TEXT")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_listings_active "
                "ON listings(is_active, total_score DESC)"
            )
            feedback_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(feedback)")
            }
            if "reasons_json" not in feedback_columns:
                connection.execute(
                    "ALTER TABLE feedback ADD COLUMN reasons_json "
                    "TEXT NOT NULL DEFAULT '[]'"
                )
            if "updated_at" not in feedback_columns:
                connection.execute("ALTER TABLE feedback ADD COLUMN updated_at TEXT")
                connection.execute(
                    "UPDATE feedback SET updated_at = created_at WHERE updated_at IS NULL"
                )
            comparison_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(comparisons)")
            }
            if "kind" not in comparison_columns:
                connection.execute(
                    "ALTER TABLE comparisons ADD COLUMN kind TEXT NOT NULL DEFAULT 'general'"
                )
            scan_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(scans)")
            }
            if "expired_count" not in scan_columns:
                connection.execute(
                    "ALTER TABLE scans ADD COLUMN expired_count INTEGER DEFAULT 0"
                )
            if "filtered_count" not in scan_columns:
                connection.execute(
                    "ALTER TABLE scans ADD COLUMN filtered_count INTEGER DEFAULT 0"
                )
            self._backfill_listing_dates(connection)

    @staticmethod
    def _backfill_listing_dates(connection: sqlite3.Connection) -> None:
        from .divar_client import extract_listing_dates

        rows = connection.execute(
            "SELECT token, listing_json FROM listings "
            "WHERE detail_fetched_at IS NOT NULL"
        ).fetchall()
        for row in rows:
            item = json.loads(row["listing_json"])
            raw_detail = item.get("raw_detail") or {}
            published_at, bumped_at = extract_listing_dates(raw_detail)
            if not published_at:
                continue
            old_published_at = item.get("published_at")
            changed = old_published_at != published_at
            item["published_at"] = published_at
            if not item.get("updated_at"):
                item["updated_at"] = old_published_at or bumped_at
                changed = True
            if changed:
                connection.execute(
                    "UPDATE listings SET listing_json = ? WHERE token = ?",
                    (
                        json.dumps(item, ensure_ascii=False, separators=(",", ":")),
                        row["token"],
                    ),
                )

    def get_user_profile(self, user_id: int) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT search_url, profile_json, created_at, updated_at "
                "FROM user_profiles WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if not row:
            return {}
        try:
            profile = json.loads(row["profile_json"] or "{}")
        except json.JSONDecodeError:
            profile = {}
        profile["source_url"] = row["search_url"]
        profile["created_at"] = row["created_at"]
        profile["updated_at"] = row["updated_at"]
        return profile

    def save_user_profile(
        self,
        user_id: int,
        profile: dict[str, Any],
        *,
        calibration_target: int | None = None,
    ) -> None:
        if calibration_target is not None and calibration_target <= 0:
            raise ValueError("Calibration target must be positive")
        now = utc_now()
        payload = {
            key: value
            for key, value in profile.items()
            if key not in {"source_url", "created_at", "updated_at"}
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO user_profiles(
                    user_id, search_url, profile_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    search_url = excluded.search_url,
                    profile_json = excluded.profile_json,
                    updated_at = excluded.updated_at
                """,
                (user_id, profile.get("source_url"), encoded, now, now),
            )
            if calibration_target is not None:
                connection.execute(
                    """
                    INSERT INTO user_calibrations(
                        user_id, target_count, completed_count, started_at, updated_at
                    ) VALUES (?, ?, 0, ?, ?)
                    ON CONFLICT(user_id) DO NOTHING
                    """,
                    (user_id, calibration_target, now, now),
                )

    def user_profile_complete(self, user_id: int) -> bool:
        return bool(self.get_user_profile(user_id).get("completed"))

    def start_user_calibration(self, user_id: int, target_count: int = 12) -> dict[str, Any]:
        if target_count <= 0:
            raise ValueError("Calibration target must be positive")
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO user_calibrations(
                    user_id, target_count, completed_count, started_at, updated_at
                ) VALUES (?, ?, 0, ?, ?)
                ON CONFLICT(user_id) DO NOTHING
                """,
                (user_id, target_count, now, now),
            )
        return self.calibration_state(user_id)

    def calibration_state(self, user_id: int) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT target_count, completed_count, current_left_token,
                       current_right_token, started_at, completed_at, updated_at
                FROM user_calibrations WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
        if not row:
            return {
                "required": False,
                "complete": True,
                "target": 0,
                "completed": 0,
                "remaining": 0,
                "progress": 100,
                "current_left_token": None,
                "current_right_token": None,
            }
        target = int(row["target_count"])
        completed = min(target, int(row["completed_count"]))
        complete = completed >= target
        return {
            "required": not complete,
            "complete": complete,
            "target": target,
            "completed": completed,
            "remaining": max(0, target - completed),
            "progress": round(completed / target * 100),
            "current_left_token": row["current_left_token"],
            "current_right_token": row["current_right_token"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "updated_at": row["updated_at"],
        }

    def calibration_required(self, user_id: int) -> bool:
        return bool(self.calibration_state(user_id)["required"])

    def set_calibration_pair(
        self, user_id: int, left_token: str, right_token: str
    ) -> tuple[str, str]:
        if left_token == right_token:
            raise ValueError("Calibration requires two different listings")
        now = utc_now()
        with self.connect() as connection:
            state = connection.execute(
                """
                SELECT target_count, completed_count, current_left_token,
                       current_right_token
                FROM user_calibrations WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
            if not state or int(state["completed_count"]) >= int(state["target_count"]):
                raise ValueError("کالیبراسیون سلیقه کامل شده است")
            current_left = state["current_left_token"]
            current_right = state["current_right_token"]
            if current_left and current_right:
                return str(current_left), str(current_right)
            connection.execute(
                """
                UPDATE user_calibrations
                SET current_left_token = ?, current_right_token = ?, updated_at = ?
                WHERE user_id = ?
                  AND completed_count < target_count
                  AND current_left_token IS NULL
                  AND current_right_token IS NULL
                """,
                (left_token, right_token, now, user_id),
            )
            stored = connection.execute(
                """
                SELECT current_left_token, current_right_token
                FROM user_calibrations WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
        if not stored or not stored["current_left_token"] or not stored["current_right_token"]:
            raise ValueError("زوج مقایسه آماده نشد؛ دوباره تلاش کن")
        return str(stored["current_left_token"]), str(stored["current_right_token"])

    def clear_calibration_pair(self, user_id: int) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE user_calibrations
                SET current_left_token = NULL, current_right_token = NULL, updated_at = ?
                WHERE user_id = ? AND completed_count < target_count
                """,
                (utc_now(), user_id),
            )

    def add_calibration_comparison(
        self,
        user_id: int,
        left_token: str,
        right_token: str,
        winner_token: str,
    ) -> dict[str, Any]:
        if left_token == right_token:
            raise ValueError("مقایسه به دو آگهی متفاوت نیاز دارد")
        if winner_token not in {left_token, right_token}:
            raise ValueError("انتخاب ثبت‌شده معتبر نیست")
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = connection.execute(
                """
                SELECT target_count, completed_count, current_left_token,
                       current_right_token
                FROM user_calibrations WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
            if not state or int(state["completed_count"]) >= int(state["target_count"]):
                raise ValueError("کالیبراسیون سلیقه کامل شده است")
            if (
                state["current_left_token"] != left_token
                or state["current_right_token"] != right_token
            ):
                raise ValueError("این مقایسه دیگر فعال نیست؛ صفحه را تازه کن")
            duplicate = connection.execute(
                """
                SELECT 1 FROM user_comparisons
                WHERE user_id = ?
                  AND ((left_token = ? AND right_token = ?)
                    OR (left_token = ? AND right_token = ?))
                LIMIT 1
                """,
                (user_id, left_token, right_token, right_token, left_token),
            ).fetchone()
            if duplicate:
                raise ValueError("این دو خانه قبلاً مقایسه شده‌اند")
            connection.execute(
                """
                INSERT INTO user_comparisons(
                    user_id, left_token, right_token, winner_token, kind,
                    reasons_json, created_at
                ) VALUES (?, ?, ?, ?, 'calibration', '[]', ?)
                """,
                (user_id, left_token, right_token, winner_token, now),
            )
            completed = int(state["completed_count"]) + 1
            target = int(state["target_count"])
            connection.execute(
                """
                UPDATE user_calibrations
                SET completed_count = ?, current_left_token = NULL,
                    current_right_token = NULL, completed_at = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (completed, now if completed >= target else None, now, user_id),
            )
        return self.calibration_state(user_id)

    def completed_user_profiles(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT p.search_url, p.profile_json "
                "FROM user_profiles p "
                "JOIN users u ON u.id = p.user_id "
                "WHERE u.status = 'approved' AND u.role = 'user'"
            ).fetchall()
        profiles: list[dict[str, Any]] = []
        for row in rows:
            try:
                profile = json.loads(row["profile_json"] or "{}")
            except json.JSONDecodeError:
                continue
            if not profile.get("completed"):
                continue
            profile["source_url"] = row["search_url"]
            profiles.append(profile)
        return profiles

    def upsert_listing(
        self,
        listing: Listing,
        *,
        detail_fetched: bool = False,
        notification_eligible: bool = True,
    ) -> None:
        now = utc_now()
        payload = json.dumps(listing.as_dict(), ensure_ascii=False, separators=(",", ":"))
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO listings (
                    token, title, district, url, listing_json, status,
                    detail_fetched_at, first_seen_at, last_seen_at,
                    notification_eligible
                ) VALUES (?, ?, ?, ?, ?, 'new', ?, ?, ?, ?)
                ON CONFLICT(token) DO UPDATE SET
                    title = excluded.title,
                    district = COALESCE(excluded.district, listings.district),
                    url = excluded.url,
                    listing_json = excluded.listing_json,
                    detail_fetched_at = COALESCE(excluded.detail_fetched_at, listings.detail_fetched_at),
                    last_seen_at = excluded.last_seen_at,
                    is_active = 1,
                    expired_at = NULL,
                    filtered_at = NULL,
                    filter_reason = NULL
                """,
                (
                    listing.token,
                    listing.title,
                    listing.district,
                    listing.url,
                    payload,
                    now if detail_fetched else None,
                    now,
                    now,
                    int(notification_eligible),
                ),
            )
            connection.execute("DELETE FROM listing_images WHERE token = ?", (listing.token,))
            fingerprints = {
                image_fingerprint(url) for url in listing.images if image_fingerprint(url)
            }
            connection.executemany(
                "INSERT OR IGNORE INTO listing_images(token, fingerprint) VALUES (?, ?)",
                ((listing.token, fingerprint) for fingerprint in fingerprints),
            )

    def archive_missing(self, active_tokens: set[str]) -> int:
        tokens = {token for token in active_tokens if token}
        if not tokens:
            return 0
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                "CREATE TEMP TABLE current_scan_tokens "
                "(token TEXT PRIMARY KEY) WITHOUT ROWID"
            )
            connection.executemany(
                "INSERT INTO current_scan_tokens(token) VALUES (?)",
                ((token,) for token in tokens),
            )
            cursor = connection.execute(
                """
                UPDATE listings
                SET is_active = 0,
                    expired_at = COALESCE(expired_at, ?)
                WHERE is_active = 1
                  AND NOT EXISTS (
                      SELECT 1 FROM current_scan_tokens
                      WHERE current_scan_tokens.token = listings.token
                  )
                """,
                (now,),
            )
            return max(0, int(cursor.rowcount))

    def archive_filtered(self, reasons_by_token: dict[str, str]) -> int:
        filtered = {
            token: str(reason)[:240]
            for token, reason in reasons_by_token.items()
            if token and reason
        }
        if not filtered:
            return 0
        now = utc_now()
        changed = 0
        with self.connect() as connection:
            for token, reason in filtered.items():
                cursor = connection.execute(
                    """
                    UPDATE listings
                    SET is_active = 0,
                        expired_at = NULL,
                        filtered_at = COALESCE(filtered_at, ?),
                        filter_reason = ?
                    WHERE token = ?
                    """,
                    (now, reason, token),
                )
                changed += max(0, int(cursor.rowcount))
        return changed

    def save_score(self, token: str, score: ScoreResult) -> None:
        payload = json.dumps(score.as_dict(), ensure_ascii=False, separators=(",", ":"))
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE listings
                SET score_json = ?, total_score = ?, status = ?
                WHERE token = ?
                """,
                (payload, score.total, score.status, token),
            )

    def get_listing(self, token: str) -> Listing | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT listing_json FROM listings WHERE token = ?", (token,)
            ).fetchone()
        return Listing(**json.loads(row["listing_json"])) if row else None

    def has_listing(self, token: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM listings WHERE token = ?", (token,)
            ).fetchone()
        return row is not None

    def detail_fetched(self, token: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT detail_fetched_at FROM listings WHERE token = ?", (token,)
            ).fetchone()
        return bool(row and row["detail_fetched_at"])

    def duplicate_image_count(self, token: str) -> int:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(DISTINCT other.token) AS duplicates
                FROM listing_images own
                JOIN listing_images other ON own.fingerprint = other.fingerprint
                WHERE own.token = ? AND other.token <> own.token
                """,
                (token,),
            ).fetchone()
        return int(row["duplicates"] or 0)

    def comparable_costs(
        self,
        district: str,
        rooms: int | None,
        size: int | None,
        rate: float,
        *,
        exclude_token: str | None = None,
    ) -> list[float]:
        if not district or not size:
            return []
        size_min = max(1, round(size * 0.85))
        size_max = round(size * 1.15)
        costs: list[float] = []
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT listing_json FROM listings
                WHERE district = ?
                  AND detail_fetched_at IS NOT NULL
                  AND is_active = 1
                """,
                (district,),
            ).fetchall()
        for row in rows:
            item = json.loads(row["listing_json"])
            if exclude_token and item.get("token") == exclude_token:
                continue
            item_size = item.get("size")
            if not item_size or not size_min <= item_size <= size_max:
                continue
            if rooms is not None and item.get("rooms") not in (None, rooms):
                continue
            deposit = item.get("deposit") or 0
            rent = item.get("rent") or 0
            costs.append((rent + deposit * rate) / item_size)
        return costs

    def list_records(
        self,
        *,
        limit: int = 500,
        include_inactive: bool = False,
        user_id: int | None = None,
    ) -> list[dict[str, Any]]:
        active_filter = "" if include_inactive else "WHERE listings.is_active = 1"
        if user_id is None:
            feedback_join = "LEFT JOIN feedback AS selected_feedback ON selected_feedback.token = listings.token"
            values: tuple[Any, ...] = (limit,)
        else:
            feedback_join = (
                "LEFT JOIN user_feedback AS selected_feedback "
                "ON selected_feedback.token = listings.token AND selected_feedback.user_id = ?"
            )
            values = (user_id, limit)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT listing_json, score_json, first_seen_at, last_seen_at,
                       detail_fetched_at, notified_at, selected_feedback.choice AS feedback,
                       selected_feedback.reasons_json AS feedback_reasons_json,
                       listings.is_active, listings.expired_at,
                       listings.filtered_at, listings.filter_reason
                FROM listings
                {feedback_join}
                {active_filter}
                ORDER BY COALESCE(total_score, -1) DESC, first_seen_at DESC
                LIMIT ?
                """,
                values,
            ).fetchall()
        records: list[dict[str, Any]] = []
        for row in rows:
            item = json.loads(row["listing_json"])
            item["score"] = json.loads(row["score_json"]) if row["score_json"] else None
            item["first_seen_at"] = row["first_seen_at"]
            item["last_seen_at"] = row["last_seen_at"]
            item["detail_fetched_at"] = row["detail_fetched_at"]
            item["notified_at"] = row["notified_at"]
            item["is_active"] = bool(row["is_active"])
            item["expired_at"] = row["expired_at"]
            item["filtered_at"] = row["filtered_at"]
            item["filter_reason"] = row["filter_reason"]
            item["feedback"] = row["feedback"]
            item["feedback_reasons"] = (
                json.loads(row["feedback_reasons_json"])
                if row["feedback_reasons_json"] else []
            )
            records.append(item)
        return records

    def set_feedback(
        self,
        token: str,
        choice: str | None,
        reasons: list[str] | None = None,
        *,
        user_id: int | None = None,
    ) -> None:
        allowed = {"favorite", "visit", "rejected"}
        clean_reasons = [str(reason)[:80] for reason in (reasons or [])[:6]]
        reasons_json = json.dumps(clean_reasons, ensure_ascii=False, separators=(",", ":"))
        with self.connect() as connection:
            table = "feedback" if user_id is None else "user_feedback"
            identity = "token = ?" if user_id is None else "user_id = ? AND token = ?"
            identity_values = (token,) if user_id is None else (user_id, token)
            if choice is None:
                connection.execute(f"DELETE FROM {table} WHERE {identity}", identity_values)
            elif choice in allowed:
                now = utc_now()
                if user_id is None:
                    connection.execute(
                        """
                        INSERT INTO feedback(token, choice, reasons_json, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(token) DO UPDATE SET choice = excluded.choice,
                                                         reasons_json = excluded.reasons_json,
                                                         updated_at = excluded.updated_at
                        """,
                        (token, choice, reasons_json, now, now),
                    )
                else:
                    connection.execute(
                        """
                        INSERT INTO user_feedback(
                            user_id, token, choice, reasons_json, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(user_id, token) DO UPDATE SET choice = excluded.choice,
                                                                  reasons_json = excluded.reasons_json,
                                                                  updated_at = excluded.updated_at
                        """,
                        (user_id, token, choice, reasons_json, now, now),
                    )
            else:
                raise ValueError("Unsupported feedback choice")

    def add_comparison(
        self,
        left_token: str,
        right_token: str,
        winner_token: str,
        reasons: list[str] | None = None,
        kind: str = "general",
        *,
        user_id: int | None = None,
    ) -> None:
        if left_token == right_token:
            raise ValueError("Comparison requires two different listings")
        if winner_token not in {left_token, right_token}:
            raise ValueError("Winner must be one of the compared listings")
        if kind != "general":
            raise ValueError("Unsupported comparison kind")
        clean_reasons = [str(reason)[:80] for reason in (reasons or [])[:6]]
        with self.connect() as connection:
            values = (
                left_token, right_token, winner_token, kind,
                json.dumps(clean_reasons, ensure_ascii=False, separators=(",", ":")), utc_now(),
            )
            if user_id is None:
                connection.execute(
                    """
                    INSERT INTO comparisons(
                        left_token, right_token, winner_token, kind, reasons_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
            else:
                connection.execute(
                    """
                    INSERT INTO user_comparisons(
                        user_id, left_token, right_token, winner_token, kind,
                        reasons_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, *values),
                )

    def list_feedback(self, *, user_id: int | None = None) -> list[dict[str, Any]]:
        table = "feedback" if user_id is None else "user_feedback"
        user_filter = "" if user_id is None else "AND selected_feedback.user_id = ?"
        values: tuple[Any, ...] = () if user_id is None else (user_id,)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT selected_feedback.token, selected_feedback.choice,
                       selected_feedback.reasons_json, selected_feedback.created_at,
                       selected_feedback.updated_at,
                       listings.listing_json, listings.score_json
                FROM {table} AS selected_feedback
                JOIN listings ON listings.token = selected_feedback.token
                WHERE listings.filtered_at IS NULL {user_filter}
                ORDER BY COALESCE(selected_feedback.updated_at, selected_feedback.created_at) DESC
                """,
                values,
            ).fetchall()
        return [
            {
                "token": row["token"],
                "choice": row["choice"],
                "reasons": json.loads(row["reasons_json"] or "[]"),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "listing": json.loads(row["listing_json"]),
                "score": json.loads(row["score_json"]) if row["score_json"] else None,
            }
            for row in rows
        ]

    def list_comparisons(self, *, user_id: int | None = None) -> list[dict[str, Any]]:
        table = "comparisons" if user_id is None else "user_comparisons"
        user_filter = "" if user_id is None else "AND selected_comparisons.user_id = ?"
        values: tuple[Any, ...] = () if user_id is None else (user_id,)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT selected_comparisons.*, left_listing.listing_json AS left_json,
                       left_listing.score_json AS left_score_json,
                       right_listing.listing_json AS right_json,
                       right_listing.score_json AS right_score_json
                FROM {table} AS selected_comparisons
                JOIN listings AS left_listing
                  ON left_listing.token = selected_comparisons.left_token
                JOIN listings AS right_listing
                  ON right_listing.token = selected_comparisons.right_token
                WHERE left_listing.filtered_at IS NULL
                  AND right_listing.filtered_at IS NULL
                  {user_filter}
                ORDER BY selected_comparisons.id DESC
                """,
                values,
            ).fetchall()
        return [
            {
                "id": row["id"],
                "left_token": row["left_token"],
                "right_token": row["right_token"],
                "winner_token": row["winner_token"],
                "kind": row["kind"],
                "reasons": json.loads(row["reasons_json"] or "[]"),
                "created_at": row["created_at"],
                "left": json.loads(row["left_json"]),
                "left_score": json.loads(row["left_score_json"]) if row["left_score_json"] else None,
                "right": json.loads(row["right_json"]),
                "right_score": json.loads(row["right_score_json"]) if row["right_score_json"] else None,
            }
            for row in rows
        ]

    def compared_pairs(self, *, user_id: int | None = None) -> set[frozenset[str]]:
        table = "comparisons" if user_id is None else "user_comparisons"
        user_filter = "" if user_id is None else " WHERE user_id = ?"
        values: tuple[Any, ...] = () if user_id is None else (user_id,)
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT left_token, right_token FROM {table}{user_filter}", values
            ).fetchall()
        return {frozenset((row["left_token"], row["right_token"])) for row in rows}

    def personalization_signature(self, *, user_id: int | None = None) -> str:
        feedback_table = "feedback" if user_id is None else "user_feedback"
        comparisons_table = "comparisons" if user_id is None else "user_comparisons"
        feedback_filter = "" if user_id is None else "AND selected_feedback.user_id = ?"
        comparison_filter = "" if user_id is None else "AND selected_comparisons.user_id = ?"
        values: tuple[Any, ...] = () if user_id is None else (user_id,)
        with self.connect() as connection:
            feedback = connection.execute(
                f"""
                SELECT COUNT(*) AS count,
                       MAX(COALESCE(updated_at, created_at)) AS latest
                FROM {feedback_table} AS selected_feedback
                JOIN listings ON listings.token = selected_feedback.token
                WHERE listings.filtered_at IS NULL {feedback_filter}
                """,
                values,
            ).fetchone()
            comparisons = connection.execute(
                f"""
                SELECT COUNT(*) AS count, MAX(selected_comparisons.created_at) AS latest
                FROM {comparisons_table} AS selected_comparisons
                JOIN listings AS left_listing
                  ON left_listing.token = selected_comparisons.left_token
                JOIN listings AS right_listing
                  ON right_listing.token = selected_comparisons.right_token
                WHERE left_listing.filtered_at IS NULL
                  AND right_listing.filtered_at IS NULL
                  {comparison_filter}
                """,
                values,
            ).fetchone()
        return "|".join(
            (
                str(feedback["count"] or 0),
                str(feedback["latest"] or ""),
                str(comparisons["count"] or 0),
                str(comparisons["latest"] or ""),
            )
        )

    def save_personalization_model(
        self, model: dict[str, Any], signature: str, *, user_id: int | None = None
    ) -> None:
        payload = json.dumps(model, ensure_ascii=False, separators=(",", ":"))
        with self.connect() as connection:
            if user_id is None:
                connection.execute(
                    """
                    INSERT INTO personalization_model(id, model_json, trained_at, training_signature)
                    VALUES (1, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET model_json = excluded.model_json,
                                                  trained_at = excluded.trained_at,
                                                  training_signature = excluded.training_signature
                    """,
                    (payload, utc_now(), signature),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO user_personalization_models(
                        user_id, model_json, trained_at, training_signature
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET model_json = excluded.model_json,
                                                       trained_at = excluded.trained_at,
                                                       training_signature = excluded.training_signature
                    """,
                    (user_id, payload, utc_now(), signature),
                )

    def load_personalization_model(
        self, *, user_id: int | None = None
    ) -> tuple[dict[str, Any], str] | None:
        table = "personalization_model" if user_id is None else "user_personalization_models"
        identity = "id = 1" if user_id is None else "user_id = ?"
        values: tuple[Any, ...] = () if user_id is None else (user_id,)
        with self.connect() as connection:
            row = connection.execute(
                f"SELECT model_json, training_signature FROM {table} WHERE {identity}", values
            ).fetchone()
        if not row:
            return None
        return json.loads(row["model_json"]), row["training_signature"]

    def clear_personalization(self, *, user_id: int | None = None) -> None:
        with self.connect() as connection:
            if user_id is None:
                connection.execute("DELETE FROM feedback")
                connection.execute("DELETE FROM comparisons")
                connection.execute("DELETE FROM personalization_model")
            else:
                connection.execute("DELETE FROM user_feedback WHERE user_id = ?", (user_id,))
                connection.execute("DELETE FROM user_comparisons WHERE user_id = ?", (user_id,))
                connection.execute("DELETE FROM user_personalization_models WHERE user_id = ?", (user_id,))

    def mark_notified(self, token: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE listings SET notified_at = ? WHERE token = ?", (utc_now(), token)
            )

    def was_notified(self, token: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT notified_at FROM listings WHERE token = ?", (token,)
            ).fetchone()
        return bool(row and row["notified_at"])

    def is_notification_eligible(self, token: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT notification_eligible FROM listings WHERE token = ?", (token,)
            ).fetchone()
        return bool(row and row["notification_eligible"])

    def start_scan(self) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO scans(started_at) VALUES (?)", (utc_now(),)
            )
            return int(cursor.lastrowid)

    def finish_scan(
        self,
        scan_id: int,
        *,
        summary_count: int,
        detail_count: int,
        expired_count: int = 0,
        filtered_count: int = 0,
        error: str | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE scans
                SET finished_at = ?, summary_count = ?, detail_count = ?,
                    expired_count = ?, filtered_count = ?, error = ?
                WHERE id = ?
                """,
                (
                    utc_now(), summary_count, detail_count, expired_count,
                    filtered_count, error, scan_id,
                ),
            )

    def stats(self) -> dict[str, Any]:
        with self.connect() as connection:
            counts = connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN status = 'alert' THEN 1 ELSE 0 END) AS alerts,
                       SUM(CASE WHEN status = 'review' THEN 1 ELSE 0 END) AS reviews,
                       SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected,
                       SUM(CASE WHEN status = 'candidate' THEN 1 ELSE 0 END) AS candidates,
                       MAX(total_score) AS best_score
                FROM listings
                WHERE is_active = 1
                """
            ).fetchone()
            last_scan = connection.execute(
                "SELECT * FROM scans ORDER BY id DESC LIMIT 1"
            ).fetchone()
        result = dict(counts) if counts else {}
        with self.connect() as connection:
            favorite_count = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM feedback
                JOIN listings ON listings.token = feedback.token
                WHERE feedback.choice = 'favorite' AND listings.is_active = 1
                """
            ).fetchone()
        result["favorites"] = int(favorite_count["count"] or 0)
        result["last_scan"] = dict(last_scan) if last_scan else None
        return result
