"""In-app audit trail for clinical actions."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class AuditEvent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    user_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: str  # e.g. "chat_received", "manifest_submitted", "manifest_approved", "manifest_executed", "verification_ran"
    summary: str  # human-readable summary
    details: dict[str, Any] = Field(default_factory=dict)  # sanitized metadata (no PHI)


class AuditStore:
    """SQLite-backed audit event store."""

    def __init__(self, db_path: str = "data/audit.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_events (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    details TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_session
                ON audit_events (session_id, timestamp)
            """)

    def record(self, event: AuditEvent) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO audit_events (id, session_id, user_id, timestamp, event_type, summary, details)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (event.id, event.session_id, event.user_id,
                 event.timestamp.isoformat(), event.event_type,
                 event.summary, json.dumps(event.details, default=str)),
            )

    def get_session_events(self, session_id: str) -> list[AuditEvent]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, session_id, user_id, timestamp, event_type, summary, details FROM audit_events WHERE session_id = ? ORDER BY timestamp",
                (session_id,),
            ).fetchall()
        events = []
        for row in rows:
            events.append(AuditEvent(
                id=row[0], session_id=row[1], user_id=row[2],
                timestamp=datetime.fromisoformat(row[3]),
                event_type=row[4], summary=row[5],
                details=json.loads(row[6]),
            ))
        return events
