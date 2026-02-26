from __future__ import annotations

import json
import sqlite3
from pathlib import Path

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
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sessions_user_created
                ON sessions (openemr_user_id, created_at DESC)
                """
            )

    def save(self, session: AgentSession) -> None:
        payload = json.dumps(
            session.model_dump(mode="json"),
            default=str,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (id, openemr_user_id, created_at, updated_at, payload)
                VALUES (?, ?, ?, datetime('now'), ?)
                ON CONFLICT(id) DO UPDATE SET
                    openemr_user_id=excluded.openemr_user_id,
                    updated_at=datetime('now'),
                    payload=excluded.payload
                """,
                (
                    session.id,
                    session.openemr_user_id,
                    session.created_at.isoformat(),
                    payload,
                ),
            )
        self._cache[session.id] = session

    def load(self, session_id: str) -> AgentSession | None:
        if session_id in self._cache:
            return self._cache[session_id]
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            session = self._decode_session_payload(row[0])
        except ValidationError:
            return None
        self._cache[session.id] = session
        return session

    def list_for_user(self, user_id: str) -> list[AgentSession]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM sessions WHERE openemr_user_id = ? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        sessions: list[AgentSession] = []
        for row in rows:
            try:
                sessions.append(self._decode_session_payload(row[0]))
            except ValidationError:
                continue
        for session in sessions:
            self._cache[session.id] = session
        return sessions

    @staticmethod
    def _decode_session_payload(payload_json: str) -> AgentSession:
        payload = json.loads(payload_json)
        if payload.get("page_context") == "":
            payload["page_context"] = None
        if payload.get("manifest") == "":
            payload["manifest"] = None
        return AgentSession.model_validate(payload)
