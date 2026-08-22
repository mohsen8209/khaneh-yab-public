from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .db import Database, utc_now


USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,31}$")
SESSION_DAYS = 7
MAX_FAILED_LOGINS = 6
LOGIN_WINDOW_MINUTES = 15
PASSWORD_ALGORITHM = "scrypt"
PASSWORD_N = 2**15
PASSWORD_R = 8
PASSWORD_P = 1
PASSWORD_LENGTH = 32
PASSWORD_MAX_MEMORY = 64 * 1024 * 1024


class AuthError(ValueError):
    pass


class RateLimitError(AuthError):
    pass


@dataclass(frozen=True)
class AuthUser:
    id: int
    username: str
    display_name: str
    role: str
    status: str
    created_at: str
    approved_at: str | None = None
    last_login_at: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> "AuthUser":
        return cls(
            id=int(row["id"]),
            username=str(row["username"]),
            display_name=str(row["display_name"]),
            role=str(row["role"]),
            status=str(row["status"]),
            created_at=str(row["created_at"]),
            approved_at=row["approved_at"],
            last_login_at=row["last_login_at"],
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "display_name": self.display_name,
            "role": self.role,
            "status": self.status,
            "created_at": self.created_at,
            "approved_at": self.approved_at,
            "last_login_at": self.last_login_at,
        }


@dataclass(frozen=True)
class AuthSession:
    token: str
    csrf_token: str
    expires_at: str
    user: AuthUser


def normalize_username(value: str) -> str:
    return value.strip().casefold()


def validate_username(value: str) -> str:
    username = normalize_username(value)
    if not USERNAME_PATTERN.fullmatch(username):
        raise AuthError("نام کاربری باید ۳ تا ۳۲ نویسه و فقط شامل حروف انگلیسی، عدد، نقطه، خط تیره یا زیرخط باشد")
    return username


def validate_display_name(value: str) -> str:
    display_name = re.sub(r"\s+", " ", value).strip()
    if not 2 <= len(display_name) <= 60:
        raise AuthError("نام نمایشی باید بین ۲ تا ۶۰ نویسه باشد")
    return display_name


def validate_password(value: str) -> str:
    if not 10 <= len(value) <= 128:
        raise AuthError("رمز عبور باید بین ۱۰ تا ۱۲۸ نویسه باشد")
    if not any(character.isalpha() for character in value):
        raise AuthError("رمز عبور باید حداقل یک حرف داشته باشد")
    if not any(character.isdigit() for character in value):
        raise AuthError("رمز عبور باید حداقل یک عدد داشته باشد")
    return value


def hash_password(password: str) -> str:
    validate_password(password)
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=PASSWORD_N,
        r=PASSWORD_R,
        p=PASSWORD_P,
        dklen=PASSWORD_LENGTH,
        maxmem=PASSWORD_MAX_MEMORY,
    )
    return "$".join(
        (
            PASSWORD_ALGORITHM,
            str(PASSWORD_N),
            str(PASSWORD_R),
            str(PASSWORD_P),
            salt.hex(),
            derived.hex(),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$", 5)
        if algorithm != PASSWORD_ALGORITHM:
            return False
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(bytes.fromhex(expected)),
            maxmem=PASSWORD_MAX_MEMORY,
        )
        return hmac.compare_digest(derived, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


class AuthService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def register(self, username: str, display_name: str, password: str) -> AuthUser:
        username = validate_username(username)
        display_name = validate_display_name(display_name)
        password_hash = hash_password(password)
        now = utc_now()
        try:
            with self.database.connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO users(
                        username, display_name, password_hash, role, status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, 'user', 'pending', ?, ?)
                    """,
                    (username, display_name, password_hash, now, now),
                )
                user_id = int(cursor.lastrowid)
                connection.execute(
                    """
                    INSERT INTO user_profiles(user_id, created_at, updated_at)
                    VALUES (?, ?, ?)
                    """,
                    (user_id, now, now),
                )
                self._audit(connection, "user.registered", target_user_id=user_id)
                row = connection.execute(
                    "SELECT * FROM users WHERE id = ?", (user_id,)
                ).fetchone()
        except Exception as error:
            if "UNIQUE constraint failed" in str(error):
                raise AuthError("این نام کاربری قبلاً ثبت شده است") from None
            raise
        return AuthUser.from_row(row)

    def create_admin(self, username: str, display_name: str, password: str) -> AuthUser:
        username = validate_username(username)
        display_name = validate_display_name(display_name)
        password_hash = hash_password(password)
        now = utc_now()
        try:
            with self.database.connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO users(
                        username, display_name, password_hash, role, status,
                        created_at, updated_at, approved_at
                    ) VALUES (?, ?, ?, 'admin', 'approved', ?, ?, ?)
                    """,
                    (username, display_name, password_hash, now, now, now),
                )
                user_id = int(cursor.lastrowid)
                connection.execute(
                    "INSERT INTO user_profiles(user_id, created_at, updated_at) VALUES (?, ?, ?)",
                    (user_id, now, now),
                )
                self._audit(connection, "admin.created", target_user_id=user_id)
                row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        except Exception as error:
            if "UNIQUE constraint failed" in str(error):
                raise AuthError("این نام کاربری قبلاً ثبت شده است") from None
            raise
        return AuthUser.from_row(row)

    def has_admin(self) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM users WHERE role = 'admin' LIMIT 1"
            ).fetchone()
        return row is not None

    def authenticate(
        self,
        username: str,
        password: str,
        *,
        remote_address: str = "",
        user_agent: str = "",
    ) -> AuthSession:
        username = normalize_username(username)
        self._enforce_rate_limit(username, remote_address)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
            ).fetchone()
        valid = bool(row and verify_password(password, str(row["password_hash"])))
        self._record_attempt(username, remote_address, valid)
        if not valid:
            raise AuthError("نام کاربری یا رمز عبور نادرست است")
        user = AuthUser.from_row(row)
        if user.status in {"rejected", "suspended"}:
            raise AuthError("دسترسی این حساب فعال نیست")
        session = self.create_session(
            user, remote_address=remote_address, user_agent=user_agent
        )
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?",
                (utc_now(), utc_now(), user.id),
            )
            self._audit(
                connection,
                "auth.login",
                actor_user_id=user.id,
                target_user_id=user.id,
                remote_address=remote_address,
            )
        return session

    def create_session(
        self,
        user: AuthUser,
        *,
        remote_address: str = "",
        user_agent: str = "",
    ) -> AuthSession:
        raw_token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(24)
        now = datetime.now(UTC)
        expires_at = (now + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")
        with self.database.connect() as connection:
            connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now.isoformat(timespec="seconds"),))
            connection.execute(
                """
                INSERT INTO sessions(
                    token_hash, user_id, csrf_token, created_at, expires_at,
                    last_seen_at, user_agent, remote_address
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    token_hash(raw_token), user.id, csrf_token,
                    now.isoformat(timespec="seconds"), expires_at,
                    now.isoformat(timespec="seconds"), user_agent[:300], remote_address[:80],
                ),
            )
        return AuthSession(raw_token, csrf_token, expires_at, user)

    def session(self, raw_token: str | None) -> AuthSession | None:
        if not raw_token:
            return None
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT users.*, sessions.csrf_token, sessions.expires_at
                FROM sessions JOIN users ON users.id = sessions.user_id
                WHERE sessions.token_hash = ? AND sessions.expires_at > ?
                """,
                (token_hash(raw_token), now),
            ).fetchone()
            if not row:
                return None
            connection.execute(
                "UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?",
                (now, token_hash(raw_token)),
            )
        return AuthSession(raw_token, str(row["csrf_token"]), str(row["expires_at"]), AuthUser.from_row(row))

    def logout(self, raw_token: str | None, *, remote_address: str = "") -> None:
        if not raw_token:
            return
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT user_id FROM sessions WHERE token_hash = ?", (token_hash(raw_token),)
            ).fetchone()
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(raw_token),))
            if row:
                self._audit(
                    connection,
                    "auth.logout",
                    actor_user_id=int(row["user_id"]),
                    target_user_id=int(row["user_id"]),
                    remote_address=remote_address,
                )

    def change_password(self, user_id: int, current_password: str, new_password: str) -> None:
        validate_password(new_password)
        with self.database.connect() as connection:
            row = connection.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
            if not row or not verify_password(current_password, str(row["password_hash"])):
                raise AuthError("رمز عبور فعلی نادرست است")
            connection.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (hash_password(new_password), utc_now(), user_id),
            )
            connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            self._audit(connection, "auth.password_changed", actor_user_id=user_id, target_user_id=user_id)

    def list_users(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT users.*,
                       (SELECT COUNT(*) FROM sessions
                        WHERE sessions.user_id = users.id
                          AND sessions.expires_at > ?) AS active_sessions
                FROM users
                ORDER BY CASE users.status WHEN 'pending' THEN 0 ELSE 1 END,
                         users.created_at DESC
                """,
                (utc_now(),),
            ).fetchall()
        return [{**AuthUser.from_row(row).as_dict(), "active_sessions": int(row["active_sessions"])} for row in rows]

    def admin_stats(self) -> dict[str, int]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM users GROUP BY status"
            ).fetchall()
        counts = {str(row["status"]): int(row["count"]) for row in rows}
        return {status: counts.get(status, 0) for status in ("pending", "approved", "rejected", "suspended")}

    def set_user_status(
        self,
        actor: AuthUser,
        target_user_id: int,
        status: str,
        *,
        remote_address: str = "",
    ) -> AuthUser:
        if actor.role != "admin" or actor.status != "approved":
            raise AuthError("دسترسی مدیریت لازم است")
        if status not in {"approved", "rejected", "suspended"}:
            raise AuthError("وضعیت حساب معتبر نیست")
        with self.database.connect() as connection:
            target = connection.execute("SELECT * FROM users WHERE id = ?", (target_user_id,)).fetchone()
            if not target:
                raise AuthError("کاربر پیدا نشد")
            if target["role"] == "admin":
                raise AuthError("وضعیت روت‌ادمین از این پنل قابل تغییر نیست")
            now = utc_now()
            approved_at = now if status == "approved" else target["approved_at"]
            connection.execute(
                """
                UPDATE users
                SET status = ?, updated_at = ?, approved_at = ?, approved_by = ?
                WHERE id = ?
                """,
                (status, now, approved_at, actor.id, target_user_id),
            )
            if status != "approved":
                connection.execute("DELETE FROM sessions WHERE user_id = ?", (target_user_id,))
            self._audit(
                connection,
                f"admin.user_{status}",
                actor_user_id=actor.id,
                target_user_id=target_user_id,
                remote_address=remote_address,
            )
            row = connection.execute("SELECT * FROM users WHERE id = ?", (target_user_id,)).fetchone()
        return AuthUser.from_row(row)

    def _enforce_rate_limit(self, username: str, remote_address: str) -> None:
        since = (datetime.now(UTC) - timedelta(minutes=LOGIN_WINDOW_MINUTES)).isoformat(timespec="seconds")
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count FROM auth_attempts
                WHERE username = ? AND remote_address = ?
                  AND succeeded = 0 AND created_at >= ?
                """,
                (username, remote_address, since),
            ).fetchone()
        if int(row["count"] or 0) >= MAX_FAILED_LOGINS:
            raise RateLimitError("تلاش‌های ورود بیش از حد بود؛ ۱۵ دقیقه دیگر دوباره امتحان کنید")

    def _record_attempt(self, username: str, remote_address: str, succeeded: bool) -> None:
        with self.database.connect() as connection:
            if succeeded:
                connection.execute(
                    "DELETE FROM auth_attempts WHERE username = ? AND remote_address = ?",
                    (username, remote_address),
                )
            connection.execute(
                "INSERT INTO auth_attempts(username, remote_address, succeeded, created_at) VALUES (?, ?, ?, ?)",
                (username, remote_address, int(succeeded), utc_now()),
            )

    @staticmethod
    def _audit(
        connection: Any,
        action: str,
        *,
        actor_user_id: int | None = None,
        target_user_id: int | None = None,
        remote_address: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO auth_audit_log(
                actor_user_id, action, target_user_id, remote_address,
                details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                actor_user_id, action, target_user_id, remote_address[:80],
                json.dumps(details or {}, ensure_ascii=False, separators=(",", ":")), utc_now(),
            ),
        )
