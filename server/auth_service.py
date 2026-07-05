from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aiohttp import web


SESSION_COOKIE = "k12_session"
LOCAL_WHITELIST_USERNAME = "127.0.0.1"
NAMED_WHITELIST_USERNAME = "im-run"
NAMED_WHITELIST_USERNAMES = (NAMED_WHITELIST_USERNAME, "linux.do")
LOCAL_WHITELIST_USABLE_COUNT = 999999
IGNORED_REMOTE_JUDGMENT_HEADERS = {"x_forwarded_for", "x_real_ip"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_text() -> str:
    return utc_now().isoformat()


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sanitize_headers(headers: Any) -> dict[str, str]:
    hidden = {"authorization", "cookie", "set-cookie"}
    return {str(key): str(value) for key, value in headers.items() if str(key).lower() not in hidden}


def first_header_ip(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    first = text.split(",", 1)[0].strip()
    if first.startswith("[") and first.endswith("]"):
        first = first[1:-1].strip()
    return first


def effective_remote_from_headers(headers: Any, socket_remote: str) -> tuple[str, str]:
    cf_connecting_ip = first_header_ip(headers.get("CF-Connecting-IP", ""))
    if cf_connecting_ip:
        return cf_connecting_ip, "cf_connecting_ip"
    return socket_remote or "unknown", "socket_remote"


def request_headers_snapshot(request: web.Request) -> dict[str, Any]:
    socket_remote = request.remote or ""
    safe_headers = sanitize_headers(request.headers)
    effective_remote, remote_source = effective_remote_from_headers(request.headers, socket_remote)
    return {
        "remote": effective_remote,
        "remote_source": remote_source,
        "socket_remote": socket_remote,
        "cf_connecting_ip": request.headers.get("CF-Connecting-IP", ""),
        "forwarded": request.headers.get("Forwarded", ""),
        "x_forwarded_for": request.headers.get("X-Forwarded-For", ""),
        "x_real_ip": request.headers.get("X-Real-IP", ""),
        "ignored_remote_judgment_headers": sorted(IGNORED_REMOTE_JUDGMENT_HEADERS),
        "headers": safe_headers,
    }


@dataclass(frozen=True)
class AuthUser:
    username: str
    usable_count: int
    is_active: bool


@dataclass(frozen=True)
class RequestRisk:
    remote: str
    request_count: int
    environment_key: str
    window_seconds: int = 60


class AuthService:
    def __init__(
        self,
        db_path: Path,
        *,
        default_username: str = "admin",
        default_password: str = "admin123456",
        default_usable_count: int = 100,
        session_ttl_hours: int = 12,
    ) -> None:
        self.db_path = db_path
        self.default_username = default_username
        self.default_password = default_password
        self.default_usable_count = default_usable_count
        self.session_ttl = timedelta(hours=session_ttl_hours)

    def initialize(self) -> bool:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        created_default = False
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS auth_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    usable_count INTEGER NOT NULL DEFAULT 0,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_login_at TEXT
                );

                CREATE TABLE IF NOT EXISTS auth_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_hash TEXT NOT NULL UNIQUE,
                    username TEXT NOT NULL,
                    entry_token_hash TEXT NOT NULL DEFAULT '',
                    entry_consumed_at TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    remote TEXT NOT NULL DEFAULT '',
                    user_agent TEXT NOT NULL DEFAULT '',
                    headers_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(username) REFERENCES auth_users(username)
                );

                CREATE INDEX IF NOT EXISTS idx_auth_sessions_token_hash
                    ON auth_sessions(token_hash);
                CREATE INDEX IF NOT EXISTS idx_auth_sessions_expires_at
                    ON auth_sessions(expires_at);
                CREATE INDEX IF NOT EXISTS idx_auth_sessions_username
                    ON auth_sessions(username, expires_at);

                CREATE TABLE IF NOT EXISTS auth_risk_environments (
                    environment_key TEXT PRIMARY KEY,
                    label TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS auth_risk_fingerprints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    environment_key TEXT NOT NULL,
                    dimension TEXT NOT NULL,
                    value TEXT NOT NULL,
                    value_hash TEXT NOT NULL,
                    request_count INTEGER NOT NULL DEFAULT 0,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    UNIQUE(dimension, value_hash),
                    FOREIGN KEY(environment_key) REFERENCES auth_risk_environments(environment_key)
                );

                CREATE INDEX IF NOT EXISTS idx_auth_risk_fingerprints_environment
                    ON auth_risk_fingerprints(environment_key);
                CREATE INDEX IF NOT EXISTS idx_auth_risk_fingerprints_dimension
                    ON auth_risk_fingerprints(dimension, value_hash);

                CREATE TABLE IF NOT EXISTS auth_risk_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    environment_key TEXT NOT NULL,
                    dimension TEXT NOT NULL,
                    value_hash TEXT NOT NULL,
                    seen_at TEXT NOT NULL,
                    FOREIGN KEY(environment_key) REFERENCES auth_risk_environments(environment_key)
                );

                CREATE INDEX IF NOT EXISTS idx_auth_risk_events_window
                    ON auth_risk_events(dimension, value_hash, seen_at);
                CREATE INDEX IF NOT EXISTS idx_auth_risk_events_environment
                    ON auth_risk_events(environment_key, seen_at);
                """
            )
            self._ensure_column(conn, "auth_sessions", "entry_token_hash", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "auth_sessions", "entry_consumed_at", "TEXT")
            self._delete_ignored_remote_judgment_dimensions(conn)
            row = conn.execute("SELECT COUNT(*) AS total FROM auth_users").fetchone()
            if int(row["total"]) == 0:
                self.upsert_user(
                    self.default_username,
                    self.default_password,
                    usable_count=self.default_usable_count,
                    conn=conn,
                )
                created_default = True
            self.delete_expired_sessions(conn=conn)
        return created_default

    def upsert_user(
        self,
        username: str,
        password: str,
        *,
        usable_count: int,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        username = username.strip()
        if not username:
            raise ValueError("username 不能为空")
        if not password:
            raise ValueError("password 不能为空")
        salt = secrets.token_hex(16)
        password_hash = self._password_hash(password, salt)
        now = utc_now_text()

        def run(db: sqlite3.Connection) -> None:
            db.execute(
                """
                INSERT INTO auth_users (
                    username, password_salt, password_hash, usable_count,
                    is_active, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(username) DO UPDATE SET
                    password_salt = excluded.password_salt,
                    password_hash = excluded.password_hash,
                    usable_count = excluded.usable_count,
                    is_active = 1,
                    updated_at = excluded.updated_at
                """,
                (username, salt, password_hash, int(usable_count), now, now),
            )

        if conn is not None:
            run(conn)
            return
        with self._connect() as db:
            run(db)

    def query_user(self, username: str, password: str) -> AuthUser | None:
        row = self._verified_user_row(username, password)
        if row is None:
            return None
        return AuthUser(
            username=str(row["username"]),
            usable_count=int(row["usable_count"]),
            is_active=bool(row["is_active"]),
        )

    def record_request(self, headers_snapshot: dict[str, Any]) -> RequestRisk:
        now_dt = utc_now()
        now = now_dt.isoformat()
        rpm_cutoff = (now_dt - timedelta(seconds=60)).isoformat()
        retention_cutoff = (now_dt - timedelta(days=1)).isoformat()
        dimensions = self._risk_dimensions(headers_snapshot)
        remote = dict(dimensions).get("remote") or "unknown"
        remote_hash = hash_text(remote)
        environment_key = f"remote:{remote_hash[:16]}"

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO auth_risk_environments (environment_key, label, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(environment_key) DO UPDATE SET
                    updated_at = excluded.updated_at
                """,
                (environment_key, f"remote={remote}", now, now),
            )
            for dimension, value in dimensions:
                value_hash = hash_text(value)
                conn.execute(
                    """
                    INSERT INTO auth_risk_fingerprints (
                        environment_key, dimension, value, value_hash,
                        request_count, first_seen_at, last_seen_at
                    )
                    VALUES (?, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(dimension, value_hash) DO UPDATE SET
                        request_count = auth_risk_fingerprints.request_count + 1,
                        last_seen_at = excluded.last_seen_at
                    """,
                    (environment_key, dimension, value, value_hash, now, now),
                )
                conn.execute(
                    """
                    INSERT INTO auth_risk_events (environment_key, dimension, value_hash, seen_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (environment_key, dimension, value_hash, now),
                )
            conn.execute("DELETE FROM auth_risk_events WHERE seen_at < ?", (retention_cutoff,))
            row = conn.execute(
                """
                SELECT COUNT(*) AS request_count
                FROM auth_risk_events
                WHERE dimension = 'remote' AND value_hash = ? AND seen_at >= ?
                """,
                (remote_hash, rpm_cutoff),
            ).fetchone()
        return RequestRisk(
            remote=remote,
            request_count=int(row["request_count"]) if row is not None else 0,
            environment_key=environment_key,
            window_seconds=60,
        )

    def login(self, username: str, password: str, headers_snapshot: dict[str, Any]) -> tuple[str, str, AuthUser] | None:
        row = self._verified_user_row(username, password)
        if row is None or not bool(row["is_active"]) or int(row["usable_count"]) <= 0:
            return None
        user = AuthUser(str(row["username"]), int(row["usable_count"]), bool(row["is_active"]))
        return self._create_session(user, headers_snapshot)

    def trusted_local_login(self, headers_snapshot: dict[str, Any]) -> tuple[str, str, AuthUser]:
        return self.trusted_named_login(LOCAL_WHITELIST_USERNAME, headers_snapshot)

    def trusted_named_login(self, username: str, headers_snapshot: dict[str, Any]) -> tuple[str, str, AuthUser]:
        username = username.strip()
        if not username:
            raise ValueError("username 不能为空")
        password = secrets.token_urlsafe(32)
        user = AuthUser(username, LOCAL_WHITELIST_USABLE_COUNT, True)
        with self._connect() as conn:
            self.upsert_user(
                user.username,
                password,
                usable_count=user.usable_count,
                conn=conn,
            )
            return self._create_session(user, headers_snapshot, conn=conn)

    def _create_session(
        self,
        user: AuthUser,
        headers_snapshot: dict[str, Any],
        *,
        conn: sqlite3.Connection | None = None,
    ) -> tuple[str, str, AuthUser]:
        token = secrets.token_urlsafe(32)
        entry_token = secrets.token_urlsafe(24)
        token_hash = hash_text(token)
        entry_token_hash = hash_text(entry_token)
        now = utc_now()
        expires_at = now + self.session_ttl

        def run(db: sqlite3.Connection) -> None:
            db.execute(
                """
                INSERT INTO auth_sessions (
                    token_hash, username, entry_token_hash, created_at, expires_at, last_seen_at,
                    remote, user_agent, headers_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    token_hash,
                    user.username,
                    entry_token_hash,
                    now.isoformat(),
                    expires_at.isoformat(),
                    now.isoformat(),
                    str(headers_snapshot.get("remote") or ""),
                    str((headers_snapshot.get("headers") or {}).get("User-Agent") or ""),
                    json.dumps(headers_snapshot, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            db.execute(
                "UPDATE auth_users SET last_login_at = ?, updated_at = ? WHERE username = ?",
                (now.isoformat(), now.isoformat(), user.username),
            )

        if conn is not None:
            run(conn)
            return token, entry_token, user
        with self._connect() as db:
            run(db)
        return token, entry_token, user

    def consume_entry_token(self, token: str, entry_token: str) -> AuthUser | None:
        token = token.strip()
        entry_token = entry_token.strip()
        if not token or not entry_token:
            self.logout(token)
            return None
        token_hash = hash_text(token)
        entry_token_hash = hash_text(entry_token)
        now = utc_now_text()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    u.username, u.usable_count, u.is_active,
                    s.expires_at, s.entry_token_hash, s.entry_consumed_at
                FROM auth_sessions s
                JOIN auth_users u ON u.username = s.username
                WHERE s.token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            valid = (
                str(row["expires_at"]) > now
                and bool(row["is_active"])
                and not str(row["entry_consumed_at"] or "").strip()
                and hmac.compare_digest(entry_token_hash, str(row["entry_token_hash"] or ""))
            )
            if not valid:
                conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))
                return None
            conn.execute(
                "UPDATE auth_sessions SET entry_consumed_at = ?, last_seen_at = ? WHERE token_hash = ?",
                (now, now, token_hash),
            )
            return AuthUser(
                username=str(row["username"]),
                usable_count=int(row["usable_count"]),
                is_active=bool(row["is_active"]),
            )

    def user_from_token(self, token: str) -> AuthUser | None:
        token = token.strip()
        if not token:
            return None
        token_hash = hash_text(token)
        now = utc_now_text()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT u.username, u.usable_count, u.is_active, s.expires_at
                FROM auth_sessions s
                JOIN auth_users u ON u.username = s.username
                WHERE s.token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            if str(row["expires_at"]) <= now or not bool(row["is_active"]):
                conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))
                return None
            conn.execute("UPDATE auth_sessions SET last_seen_at = ? WHERE token_hash = ?", (now, token_hash))
            return AuthUser(
                username=str(row["username"]),
                usable_count=int(row["usable_count"]),
                is_active=bool(row["is_active"]),
            )

    def logout(self, token: str) -> None:
        if not token:
            return
        with self._connect() as conn:
            conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (hash_text(token),))

    def active_session_count(self, username: str) -> int:
        username = username.strip()
        if not username:
            return 0
        now = utc_now_text()
        with self._connect() as conn:
            self.delete_expired_sessions(conn=conn)
            row = conn.execute(
                """
                SELECT COUNT(*) AS total
                FROM auth_sessions
                WHERE username = ? AND expires_at > ?
                """,
                (username, now),
            ).fetchone()
        return int(row["total"]) if row is not None else 0

    def delete_expired_sessions(self, *, conn: sqlite3.Connection | None = None) -> None:
        now = utc_now_text()

        def run(db: sqlite3.Connection) -> None:
            db.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now,))

        if conn is not None:
            run(conn)
            return
        with self._connect() as db:
            run(db)

    def _verified_user_row(self, username: str, password: str) -> sqlite3.Row | None:
        username = username.strip()
        if not username or not password:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT username, password_salt, password_hash, usable_count, is_active
                FROM auth_users
                WHERE username = ?
                """,
                (username,),
            ).fetchone()
        if row is None:
            return None
        expected = self._password_hash(password, str(row["password_salt"]))
        if not hmac.compare_digest(expected, str(row["password_hash"])):
            return None
        return row

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        if any(str(row["name"]) == column for row in rows):
            return
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @staticmethod
    def _password_hash(password: str, salt: str) -> str:
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), 120_000)
        return digest.hex()

    @staticmethod
    def _delete_ignored_remote_judgment_dimensions(conn: sqlite3.Connection) -> None:
        dimensions = tuple(sorted(IGNORED_REMOTE_JUDGMENT_HEADERS))
        placeholders = ",".join("?" for _ in dimensions)
        conn.execute(f"DELETE FROM auth_risk_events WHERE dimension IN ({placeholders})", dimensions)
        conn.execute(f"DELETE FROM auth_risk_fingerprints WHERE dimension IN ({placeholders})", dimensions)

    @staticmethod
    def _risk_dimensions(headers_snapshot: dict[str, Any]) -> list[tuple[str, str]]:
        headers = headers_snapshot.get("headers") or {}
        candidates = [
            ("remote", headers_snapshot.get("remote")),
            ("cf_connecting_ip", headers_snapshot.get("cf_connecting_ip")),
            ("socket_remote", headers_snapshot.get("socket_remote")),
            ("forwarded", headers_snapshot.get("forwarded")),
            ("user_agent", headers.get("User-Agent")),
        ]
        dimensions: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for dimension, raw_value in candidates:
            value = str(raw_value or "").strip()
            if not value:
                continue
            key = (dimension, value)
            if key not in seen:
                dimensions.append(key)
                seen.add(key)
        if not any(dimension == "remote" for dimension, _ in dimensions):
            dimensions.insert(0, ("remote", "unknown"))
        return dimensions

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
