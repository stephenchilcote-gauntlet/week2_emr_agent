from __future__ import annotations

import tempfile
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
