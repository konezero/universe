from __future__ import annotations

import hashlib
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote


PAIRING_SCHEMA = "universe.remote-pairing.v1"
DEVICE_SCHEMA = "universe.remote-device.v1"
REMOTE_ACCESS_SCHEMA = "universe.remote-access.v1"
PAIRING_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
PAIRING_STATES = frozenset(
    {"ISSUED", "AWAITING_APPROVAL", "APPROVED", "DENIED", "CONSUMED", "EXPIRED"}
)
DEFAULT_PAIRING_TTL_SECONDS = 10 * 60
DEFAULT_SESSION_TTL_SECONDS = 30 * 24 * 60 * 60


class RemoteAccessError(Exception):
    def __init__(self, code: str, detail: str, status: int = 400) -> None:
        self.code = code
        self.detail = detail
        self.status = status
        super().__init__(detail)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _future(seconds: int) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=seconds)
    ).isoformat(timespec="seconds").replace("+00:00", "Z")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _required_text(value: Any, field: str, *, limit: int = 240) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RemoteAccessError("REMOTE_ACCESS_REQUEST_INVALID", f"{field} is required")
    normalized = value.strip()
    if len(normalized) > limit:
        raise RemoteAccessError(
            "REMOTE_ACCESS_REQUEST_INVALID", f"{field} exceeds {limit} characters"
        )
    return normalized


def _pairing_code() -> str:
    raw = "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


class RemoteAccessStore:
    def __init__(self, database_path: Path):
        self.database_path = database_path.expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS remote_pairing (
                    pairing_id TEXT PRIMARY KEY,
                    code_digest TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL,
                    device_name TEXT,
                    user_agent_digest TEXT,
                    request_token_digest TEXT,
                    device_id TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    requested_at TEXT,
                    decided_at TEXT,
                    consumed_at TEXT,
                    CHECK(state IN (
                        'ISSUED', 'AWAITING_APPROVAL', 'APPROVED',
                        'DENIED', 'CONSUMED', 'EXPIRED'
                    ))
                );

                CREATE INDEX IF NOT EXISTS remote_pairing_state_time
                ON remote_pairing(state, expires_at, created_at);

                CREATE TABLE IF NOT EXISTS remote_device (
                    device_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    session_digest TEXT NOT NULL UNIQUE,
                    user_agent_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    revoked_at TEXT
                );

                CREATE INDEX IF NOT EXISTS remote_device_active
                ON remote_device(revoked_at, expires_at, last_seen_at);
                """
            )

    @staticmethod
    def _pairing_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema": PAIRING_SCHEMA,
            "pairing_id": str(row["pairing_id"]),
            "state": str(row["state"]),
            "device_name": row["device_name"],
            "device_id": row["device_id"],
            "created_at": str(row["created_at"]),
            "expires_at": str(row["expires_at"]),
            "requested_at": row["requested_at"],
            "decided_at": row["decided_at"],
            "consumed_at": row["consumed_at"],
        }

    @staticmethod
    def _device_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema": DEVICE_SCHEMA,
            "device_id": str(row["device_id"]),
            "display_name": str(row["display_name"]),
            "created_at": str(row["created_at"]),
            "expires_at": str(row["expires_at"]),
            "last_seen_at": str(row["last_seen_at"]),
            "state": "REVOKED" if row["revoked_at"] else "PAIRED",
            "revoked_at": row["revoked_at"],
        }

    @staticmethod
    def _expire_pairing(connection: sqlite3.Connection, row: sqlite3.Row) -> bool:
        if row["state"] in {"CONSUMED", "DENIED", "EXPIRED"}:
            return row["state"] == "EXPIRED"
        if _parse_time(str(row["expires_at"])) > datetime.now(timezone.utc):
            return False
        connection.execute(
            "UPDATE remote_pairing SET state = 'EXPIRED' WHERE pairing_id = ?",
            (row["pairing_id"],),
        )
        return True

    def create_pairing(
        self,
        *,
        public_base_url: str,
        ttl_seconds: int = DEFAULT_PAIRING_TTL_SECONDS,
    ) -> dict[str, Any]:
        base_url = _required_text(public_base_url, "public_base_url", limit=2048).rstrip(
            "/"
        )
        if ttl_seconds < 60 or ttl_seconds > 60 * 60:
            raise RemoteAccessError(
                "REMOTE_PAIRING_TTL_INVALID", "pairing ttl must be 60..3600 seconds"
            )
        pairing_id = "pair_" + secrets.token_hex(12)
        code = _pairing_code()
        created_at = utc_now()
        expires_at = _future(ttl_seconds)
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO remote_pairing(
                    pairing_id, code_digest, state, created_at, expires_at
                ) VALUES (?, ?, 'ISSUED', ?, ?)
                """,
                (pairing_id, _digest(code.upper()), created_at, expires_at),
            )
        return {
            "schema": PAIRING_SCHEMA,
            "status": "REMOTE_PAIRING_ISSUED",
            "pairing_id": pairing_id,
            "state": "ISSUED",
            "code": code,
            "pair_url": f"{base_url}/pair?code={quote(code, safe='-')}",
            "created_at": created_at,
            "expires_at": expires_at,
        }

    def request_pairing(
        self,
        *,
        code: str,
        device_name: str,
        user_agent: str,
    ) -> dict[str, Any]:
        normalized_code = _required_text(code, "code", limit=32).upper()
        normalized_name = _required_text(device_name, "device_name", limit=120)
        normalized_agent = _required_text(user_agent, "user_agent", limit=1024)
        request_token = secrets.token_urlsafe(32)
        requested_at = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM remote_pairing WHERE code_digest = ?",
                (_digest(normalized_code),),
            ).fetchone()
            if row is None:
                raise RemoteAccessError(
                    "REMOTE_PAIRING_NOT_FOUND", "pairing code is invalid", 404
                )
            if self._expire_pairing(connection, row):
                raise RemoteAccessError(
                    "REMOTE_PAIRING_EXPIRED", "pairing code has expired", 410
                )
            if row["state"] != "ISSUED":
                raise RemoteAccessError(
                    "REMOTE_PAIRING_ALREADY_USED",
                    "pairing code has already been claimed",
                    409,
                )
            connection.execute(
                """
                UPDATE remote_pairing
                SET state = 'AWAITING_APPROVAL', device_name = ?,
                    user_agent_digest = ?, request_token_digest = ?, requested_at = ?
                WHERE pairing_id = ?
                """,
                (
                    normalized_name,
                    _digest(normalized_agent),
                    _digest(request_token),
                    requested_at,
                    row["pairing_id"],
                ),
            )
        return {
            "schema": PAIRING_SCHEMA,
            "status": "REMOTE_PAIRING_APPROVAL_REQUIRED",
            "pairing_id": str(row["pairing_id"]),
            "state": "AWAITING_APPROVAL",
            "device_name": normalized_name,
            "request_token": request_token,
            "requested_at": requested_at,
            "expires_at": str(row["expires_at"]),
        }

    def decide_pairing(self, pairing_id: str, *, approve: bool) -> dict[str, Any]:
        normalized_id = _required_text(pairing_id, "pairing_id")
        decided_at = utc_now()
        next_state = "APPROVED" if approve else "DENIED"
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM remote_pairing WHERE pairing_id = ?",
                (normalized_id,),
            ).fetchone()
            if row is None:
                raise RemoteAccessError(
                    "REMOTE_PAIRING_NOT_FOUND", "pairing request was not found", 404
                )
            if self._expire_pairing(connection, row):
                raise RemoteAccessError(
                    "REMOTE_PAIRING_EXPIRED", "pairing request has expired", 410
                )
            if row["state"] != "AWAITING_APPROVAL":
                raise RemoteAccessError(
                    "REMOTE_PAIRING_STATE_INVALID",
                    f"pairing cannot be decided from {row['state']}",
                    409,
                )
            connection.execute(
                "UPDATE remote_pairing SET state = ?, decided_at = ? WHERE pairing_id = ?",
                (next_state, decided_at, normalized_id),
            )
            updated = connection.execute(
                "SELECT * FROM remote_pairing WHERE pairing_id = ?", (normalized_id,)
            ).fetchone()
        if updated is None:
            raise RemoteAccessError(
                "REMOTE_PAIRING_STATE_INCONSISTENT",
                "pairing decision was not persisted",
                500,
            )
        return self._pairing_row(updated)

    def pairing_status(
        self,
        pairing_id: str,
        *,
        request_token: str,
        user_agent: str,
        session_ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
    ) -> dict[str, Any]:
        normalized_id = _required_text(pairing_id, "pairing_id")
        normalized_token = _required_text(request_token, "request_token", limit=256)
        normalized_agent = _required_text(user_agent, "user_agent", limit=1024)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM remote_pairing WHERE pairing_id = ?",
                (normalized_id,),
            ).fetchone()
            if row is None:
                raise RemoteAccessError(
                    "REMOTE_PAIRING_NOT_FOUND", "pairing request was not found", 404
                )
            if not secrets.compare_digest(
                str(row["request_token_digest"] or ""), _digest(normalized_token)
            ):
                raise RemoteAccessError(
                    "REMOTE_PAIRING_REQUEST_UNAUTHORIZED",
                    "pairing request credential is invalid",
                    401,
                )
            if self._expire_pairing(connection, row):
                return {**self._pairing_row(row), "state": "EXPIRED"}
            if row["state"] != "APPROVED":
                return self._pairing_row(row)
            if str(row["user_agent_digest"] or "") != _digest(normalized_agent):
                raise RemoteAccessError(
                    "REMOTE_PAIRING_DEVICE_MISMATCH",
                    "pairing request moved to a different browser",
                    409,
                )
            session_token = secrets.token_urlsafe(48)
            device_id = "device_" + secrets.token_hex(12)
            now = utc_now()
            expires_at = _future(session_ttl_seconds)
            connection.execute(
                """
                INSERT INTO remote_device(
                    device_id, display_name, session_digest, user_agent_digest,
                    created_at, expires_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    device_id,
                    row["device_name"],
                    _digest(session_token),
                    _digest(normalized_agent),
                    now,
                    expires_at,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE remote_pairing
                SET state = 'CONSUMED', device_id = ?, consumed_at = ?
                WHERE pairing_id = ?
                """,
                (device_id, now, normalized_id),
            )
        return {
            "schema": PAIRING_SCHEMA,
            "status": "REMOTE_PAIRING_CONSUMED",
            "pairing_id": normalized_id,
            "state": "CONSUMED",
            "device_id": device_id,
            "session_token": session_token,
            "session_expires_at": expires_at,
        }

    def authorize_device(self, session_token: str, *, user_agent: str) -> dict[str, Any]:
        normalized_token = _required_text(session_token, "session_token", limit=512)
        normalized_agent = _required_text(user_agent, "user_agent", limit=1024)
        now = utc_now()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM remote_device WHERE session_digest = ?",
                (_digest(normalized_token),),
            ).fetchone()
            if row is None or row["revoked_at"] is not None:
                raise RemoteAccessError(
                    "REMOTE_DEVICE_SESSION_INVALID", "paired device session is invalid", 401
                )
            if _parse_time(str(row["expires_at"])) <= datetime.now(timezone.utc):
                raise RemoteAccessError(
                    "REMOTE_DEVICE_SESSION_EXPIRED", "paired device session expired", 401
                )
            if str(row["user_agent_digest"]) != _digest(normalized_agent):
                raise RemoteAccessError(
                    "REMOTE_DEVICE_SESSION_MISMATCH",
                    "paired device session belongs to another browser",
                    401,
                )
            connection.execute(
                "UPDATE remote_device SET last_seen_at = ? WHERE device_id = ?",
                (now, row["device_id"]),
            )
            refreshed = connection.execute(
                "SELECT * FROM remote_device WHERE device_id = ?", (row["device_id"],)
            ).fetchone()
        if refreshed is None:
            raise RemoteAccessError(
                "REMOTE_DEVICE_STATE_INCONSISTENT",
                "paired device update was not persisted",
                500,
            )
        return self._device_row(refreshed)

    def revoke_device(self, device_id: str) -> dict[str, Any]:
        normalized_id = _required_text(device_id, "device_id")
        revoked_at = utc_now()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE remote_device SET revoked_at = ?
                WHERE device_id = ? AND revoked_at IS NULL
                """,
                (revoked_at, normalized_id),
            )
            if cursor.rowcount != 1:
                raise RemoteAccessError(
                    "REMOTE_DEVICE_NOT_FOUND", "active paired device was not found", 404
                )
            row = connection.execute(
                "SELECT * FROM remote_device WHERE device_id = ?", (normalized_id,)
            ).fetchone()
        if row is None:
            raise RemoteAccessError(
                "REMOTE_DEVICE_STATE_INCONSISTENT",
                "paired device revocation was not persisted",
                500,
            )
        return self._device_row(row)

    def snapshot(self) -> dict[str, Any]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM remote_pairing ORDER BY created_at DESC LIMIT 20"
            ).fetchall()
            for row in rows:
                self._expire_pairing(connection, row)
            pairings = connection.execute(
                """
                SELECT * FROM remote_pairing
                WHERE state IN ('ISSUED', 'AWAITING_APPROVAL', 'APPROVED')
                ORDER BY created_at DESC
                """
            ).fetchall()
            devices = connection.execute(
                "SELECT * FROM remote_device ORDER BY last_seen_at DESC"
            ).fetchall()
        return {
            "schema": REMOTE_ACCESS_SCHEMA,
            "status": "REMOTE_ACCESS_STATE_COLLECTED",
            "pairings": [self._pairing_row(row) for row in pairings],
            "devices": [self._device_row(row) for row in devices],
        }
