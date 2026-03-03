from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.observability.audit import AuditEvent, AuditStore


class TestAuditStore:
    @pytest.fixture
    def audit_store(self, tmp_path):
        db_path = str(tmp_path / "test_audit.db")
        return AuditStore(db_path=db_path)

    def test_record_and_retrieve(self, audit_store):
        event = AuditEvent(
            session_id="sess-1",
            user_id="user-1",
            event_type="chat_received",
            summary="User sent a message (42 chars)",
            details={"message_length": 42},
        )
        audit_store.record(event)
        events = audit_store.get_session_events("sess-1")
        assert len(events) == 1
        assert events[0].session_id == "sess-1"
        assert events[0].event_type == "chat_received"
        assert events[0].details["message_length"] == 42

    def test_multiple_events_ordered(self, audit_store):
        for i in range(3):
            audit_store.record(AuditEvent(
                session_id="sess-1",
                user_id="user-1",
                event_type=f"event_{i}",
                summary=f"Event {i}",
            ))
        events = audit_store.get_session_events("sess-1")
        assert len(events) == 3
        assert events[0].event_type == "event_0"
        assert events[2].event_type == "event_2"

    def test_session_isolation(self, audit_store):
        audit_store.record(AuditEvent(
            session_id="sess-1",
            user_id="user-1",
            event_type="chat_received",
            summary="Session 1 event",
        ))
        audit_store.record(AuditEvent(
            session_id="sess-2",
            user_id="user-1",
            event_type="chat_received",
            summary="Session 2 event",
        ))
        assert len(audit_store.get_session_events("sess-1")) == 1
        assert len(audit_store.get_session_events("sess-2")) == 1
        assert len(audit_store.get_session_events("sess-3")) == 0

    def test_event_id_auto_generated(self, audit_store):
        event = AuditEvent(
            session_id="sess-1",
            user_id="user-1",
            event_type="test",
            summary="Test",
        )
        assert event.id  # Should be auto-generated
        assert len(event.id) > 10

    def test_timestamp_auto_generated(self, audit_store):
        event = AuditEvent(
            session_id="sess-1",
            user_id="user-1",
            event_type="test",
            summary="Test",
        )
        assert event.timestamp is not None

    # -----------------------------------------------------------------------
    # New tests
    # -----------------------------------------------------------------------

    def test_details_defaults_to_empty_dict(self):
        event = AuditEvent(
            session_id="sess-1",
            user_id="user-1",
            event_type="test",
            summary="No details supplied",
        )
        assert event.details == {}

    def test_details_with_complex_values(self, audit_store):
        nested = {"tool": "fhir_read", "args": {"resource_type": "Patient"}, "count": 3}
        event = AuditEvent(
            session_id="sess-complex",
            user_id="user-1",
            event_type="tool_called",
            summary="Tool invoked",
            details=nested,
        )
        audit_store.record(event)
        retrieved = audit_store.get_session_events("sess-complex")
        assert len(retrieved) == 1
        assert retrieved[0].details["tool"] == "fhir_read"
        assert retrieved[0].details["args"]["resource_type"] == "Patient"
        assert retrieved[0].details["count"] == 3

    def test_details_serialized_non_serializable(self, audit_store):
        # datetime is not JSON-serializable by default; audit.py uses default=str.
        now = datetime.now(timezone.utc)
        event = AuditEvent(
            session_id="sess-dt",
            user_id="user-1",
            event_type="test",
            summary="Event with datetime in details",
            details={"recorded_at": now},
        )
        # record() should not raise even though datetime is not JSON-serializable.
        audit_store.record(event)
        retrieved = audit_store.get_session_events("sess-dt")
        assert len(retrieved) == 1
        # After round-trip the datetime becomes a string (default=str).
        assert isinstance(retrieved[0].details["recorded_at"], str)

    def test_get_empty_session_returns_empty_list(self, audit_store):
        result = audit_store.get_session_events("nonexistent-session")
        assert result == []

    def test_record_no_details(self, audit_store):
        event = AuditEvent(
            session_id="sess-nodet",
            user_id="user-1",
            event_type="manifest_approved",
            summary="Manifest approved with no extra details",
        )
        audit_store.record(event)
        retrieved = audit_store.get_session_events("sess-nodet")
        assert len(retrieved) == 1
        assert retrieved[0].details == {}

    def test_duplicate_id_raises(self, audit_store):
        fixed_id = "fixed-uuid-0000"
        event = AuditEvent(
            id=fixed_id,
            session_id="sess-dup",
            user_id="user-1",
            event_type="test",
            summary="First event",
        )
        audit_store.record(event)

        duplicate = AuditEvent(
            id=fixed_id,
            session_id="sess-dup",
            user_id="user-2",
            event_type="test",
            summary="Second event with same ID",
        )
        with pytest.raises(sqlite3.IntegrityError):
            audit_store.record(duplicate)

    def test_events_ordered_by_timestamp(self, audit_store):
        # Insert events with explicitly spaced timestamps so ordering is deterministic.
        base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        for offset_seconds in (0, 5, 10):
            ts = base + timedelta(seconds=offset_seconds)
            audit_store.record(AuditEvent(
                session_id="sess-order",
                user_id="user-1",
                event_type=f"event_at_{offset_seconds}",
                summary=f"Event at T+{offset_seconds}s",
                timestamp=ts,
            ))

        events = audit_store.get_session_events("sess-order")
        assert len(events) == 3
        assert events[0].event_type == "event_at_0"
        assert events[1].event_type == "event_at_5"
        assert events[2].event_type == "event_at_10"

    def test_different_users_same_session(self, audit_store):
        for user in ("user-alpha", "user-beta", "user-gamma"):
            audit_store.record(AuditEvent(
                session_id="shared-sess",
                user_id=user,
                event_type="peer_review",
                summary=f"{user} reviewed the manifest",
            ))

        events = audit_store.get_session_events("shared-sess")
        assert len(events) == 3
        recorded_users = {e.user_id for e in events}
        assert recorded_users == {"user-alpha", "user-beta", "user-gamma"}
