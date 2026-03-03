from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import json as _json

from pydantic import ValidationError

from ..agent.models import AgentSession


class SessionStore:
    """SQLite-backed session store with a small in-memory read cache."""

    def __init__(self, db_path: str = "data/sessions.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, AgentSession] = {}
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    openemr_user_id TEXT,
                    patient_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            self._migrate_add_patient_id(conn)
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sessions_user_patient_created
                ON sessions (openemr_user_id, patient_id, created_at DESC)
                """
            )

    def _migrate_add_patient_id(self, conn: sqlite3.Connection) -> None:
        cursor = conn.execute("PRAGMA table_info(sessions)")
        columns = {row[1] for row in cursor.fetchall()}
        if "patient_id" not in columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN patient_id TEXT")

    @staticmethod
    def _extract_patient_id(session: AgentSession) -> str | None:
        if session.page_context and session.page_context.patient_id:
            return session.page_context.patient_id
        return None

    def save(self, session: AgentSession) -> None:
        patient_id = self._extract_patient_id(session)
        payload = json.dumps(
            session.model_dump(mode="json"),
            default=str,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (id, openemr_user_id, patient_id, created_at, updated_at, payload)
                VALUES (?, ?, ?, ?, datetime('now'), ?)
                ON CONFLICT(id) DO UPDATE SET
                    openemr_user_id=excluded.openemr_user_id,
                    patient_id=excluded.patient_id,
                    updated_at=datetime('now'),
                    payload=excluded.payload
                """,
                (
                    session.id,
                    session.openemr_user_id,
                    patient_id,
                    session.created_at.isoformat(),
                    payload,
                ),
            )
        self._cache[session.id] = session

    def load(self, session_id: str, user_id: str) -> AgentSession | None:
        cached = self._cache.get(session_id)
        if cached is not None:
            return cached if cached.openemr_user_id == user_id else None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM sessions WHERE id = ? AND openemr_user_id = ?",
                (session_id, user_id),
            ).fetchone()
        if row is None:
            return None
        try:
            session = self._decode_session_payload(row[0])
        except (ValidationError, _json.JSONDecodeError):
            return None
        self._cache[session.id] = session
        return session

    def list_for_user(
        self, user_id: str, patient_id: str | None = None,
    ) -> list[AgentSession]:
        with self._connect() as conn:
            if patient_id is not None:
                rows = conn.execute(
                    "SELECT payload FROM sessions WHERE openemr_user_id = ? AND patient_id = ? ORDER BY created_at DESC",
                    (user_id, patient_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT payload FROM sessions WHERE openemr_user_id = ? AND patient_id IS NULL ORDER BY created_at DESC",
                    (user_id,),
                ).fetchall()
        sessions: list[AgentSession] = []
        for row in rows:
            try:
                sessions.append(self._decode_session_payload(row[0]))
            except (ValidationError, _json.JSONDecodeError):
                continue
        for session in sessions:
            self._cache[session.id] = session
        return sessions

    def delete(self, session_id: str, user_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM sessions WHERE id = ? AND openemr_user_id = ?",
                (session_id, user_id),
            )
        self._cache.pop(session_id, None)

    @staticmethod
    def _decode_session_payload(payload_json: str) -> AgentSession:
        payload = json.loads(payload_json)
        if payload.get("page_context") == "":
            payload["page_context"] = None
        if payload.get("manifest") == "":
            payload["manifest"] = None
        return AgentSession.model_validate(payload)
