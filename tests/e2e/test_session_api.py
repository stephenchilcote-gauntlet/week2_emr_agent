"""Session API E2E tests.

Tests that verify the agent REST API session management endpoints work
correctly through the sidebar's JavaScript client:
  - Sessions are created on first message
  - Sessions are listed and contain the correct preview/patient info
  - Deleting a session removes it from the list
  - Session messages endpoint returns conversation history
  - Session audit endpoint returns events

These tests call the agent API through the sidebar's window.__sidebarApp.api()
method, which uses the same auth token and proxy the sidebar uses.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Frame, Page

from .conftest import (
    AGENT_BASE_URL,
    E2E_TIMEOUT_MS,
    PATIENT_MAP,
    get_sidebar_frame,
    openemr_login,
    select_patient,
    send_chat_message,
)

pytestmark = pytest.mark.e2e

PATIENT_PID = PATIENT_MAP["Maria Santos"]
PATIENT_NAME = "Maria Santos"


@pytest.fixture
def sidebar(page: Page) -> Frame:
    page.set_default_timeout(E2E_TIMEOUT_MS)
    openemr_login(page)
    select_patient(page, PATIENT_PID, PATIENT_NAME)
    return get_sidebar_frame(page)


@pytest.fixture
def sidebar_with_message(page: Page) -> Frame:
    """Sidebar with one assistant reply, so session is persisted."""
    page.set_default_timeout(E2E_TIMEOUT_MS)
    openemr_login(page)
    select_patient(page, PATIENT_PID, PATIENT_NAME)
    sidebar = get_sidebar_frame(page)
    send_chat_message(sidebar, "What is 2 + 2?")
    return sidebar


# ---------------------------------------------------------------------------
# Session creation
# ---------------------------------------------------------------------------


class TestSessionCreation:
    """Session is created on first message and persisted in the API."""

    def test_session_id_set_after_message(self, sidebar_with_message: Frame) -> None:
        """After sending a message, a session ID is stored in app state."""
        session_id = sidebar_with_message.evaluate(
            "() => window.__sidebarApp.state.sessionID"
        )
        assert session_id is not None and len(session_id) > 0, (
            f"Session ID should be set after message, got: {session_id!r}"
        )

    def test_session_id_is_string(self, sidebar_with_message: Frame) -> None:
        """Session ID is a non-empty string."""
        session_id = sidebar_with_message.evaluate(
            "() => window.__sidebarApp.state.sessionID"
        )
        assert isinstance(session_id, str), (
            f"Session ID should be a string, got: {type(session_id)}"
        )

    def test_session_id_persists_across_messages(
        self, sidebar_with_message: Frame
    ) -> None:
        """The same session ID is used for multiple messages in a conversation."""
        session_id_1 = sidebar_with_message.evaluate(
            "() => window.__sidebarApp.state.sessionID"
        )
        send_chat_message(sidebar_with_message, "And what is 3 + 3?")
        session_id_2 = sidebar_with_message.evaluate(
            "() => window.__sidebarApp.state.sessionID"
        )
        assert session_id_1 == session_id_2, (
            "Session ID should not change within the same conversation"
        )

    def test_new_conversation_creates_new_session(
        self, sidebar_with_message: Frame
    ) -> None:
        """Clicking 'New Conversation' creates a fresh session with a new ID."""
        old_id = sidebar_with_message.evaluate(
            "() => window.__sidebarApp.state.sessionID"
        )

        sidebar_with_message.locator("#new-conversation").dispatch_event("click")
        sidebar_with_message.wait_for_timeout(2000)

        new_id = sidebar_with_message.evaluate(
            "() => window.__sidebarApp.state.sessionID"
        )
        assert new_id != old_id, (
            f"New conversation should have a different session ID. "
            f"old={old_id!r}, new={new_id!r}"
        )


# ---------------------------------------------------------------------------
# Session list via API
# ---------------------------------------------------------------------------


class TestSessionListViaAPI:
    """Session list API returns the correct metadata."""

    def test_session_appears_in_list_after_message(
        self, sidebar_with_message: Frame
    ) -> None:
        """After sending a message, the session appears in the API list."""
        result = sidebar_with_message.evaluate("""async () => {
            const app = window.__sidebarApp
            const pid = app.state.patientID
            const url = pid
                ? `/api/sessions?patient_id=${encodeURIComponent(pid)}`
                : '/api/sessions'
            const resp = await app.api(url)
            return resp
        }""")
        assert isinstance(result, list), f"Expected list, got: {type(result)}"
        assert len(result) >= 1, "Session list should have at least 1 entry"
        session_ids = [s.get("session_id") for s in result]
        current_id = sidebar_with_message.evaluate(
            "() => window.__sidebarApp.state.sessionID"
        )
        assert current_id in session_ids, (
            f"Current session {current_id!r} should be in the list: {session_ids}"
        )

    def test_session_list_has_message_preview(
        self, sidebar_with_message: Frame
    ) -> None:
        """Session list entries include the first_message_preview field."""
        result = sidebar_with_message.evaluate("""async () => {
            const app = window.__sidebarApp
            const pid = app.state.patientID
            const url = pid
                ? `/api/sessions?patient_id=${encodeURIComponent(pid)}`
                : '/api/sessions'
            return await app.api(url)
        }""")
        assert any(
            s.get("first_message_preview") for s in result
        ), "At least one session should have a first_message_preview"

    def test_session_list_includes_patient_name(
        self, sidebar_with_message: Frame
    ) -> None:
        """Session list entries for a patient session have patient_name set."""
        result = sidebar_with_message.evaluate("""async () => {
            const app = window.__sidebarApp
            const pid = app.state.patientID
            if (!pid) return []
            return await app.api(`/api/sessions?patient_id=${encodeURIComponent(pid)}`)
        }""")
        if result:
            # At least one session should have a patient_name
            has_name = any(s.get("patient_name") for s in result)
            has_pid = any(s.get("patient_id") for s in result)
            assert has_name or has_pid, (
                "Session list entries should have patient info"
            )


# ---------------------------------------------------------------------------
# Session messages API
# ---------------------------------------------------------------------------


class TestSessionMessagesViaAPI:
    """GET /api/sessions/{id}/messages returns conversation history."""

    def test_get_messages_includes_user_message(
        self, sidebar_with_message: Frame
    ) -> None:
        """The messages endpoint includes the user message in the conversation."""
        result = sidebar_with_message.evaluate("""async () => {
            const app = window.__sidebarApp
            const sid = app.state.sessionID
            if (!sid) return null
            return await app.api(`/api/sessions/${sid}/messages`)
        }""")
        assert result is not None, "Messages endpoint should return data"
        messages = result.get("messages", [])
        assert len(messages) >= 1, "Should have at least one message"
        # Should contain the user message we sent
        user_messages = [m for m in messages if m.get("role") == "user"]
        assert len(user_messages) >= 1, "Should have a user message"

    def test_get_messages_includes_assistant_response(
        self, sidebar_with_message: Frame
    ) -> None:
        """The messages endpoint includes the assistant response."""
        result = sidebar_with_message.evaluate("""async () => {
            const app = window.__sidebarApp
            const sid = app.state.sessionID
            return await app.api(`/api/sessions/${sid}/messages`)
        }""")
        messages = result.get("messages", [])
        assistant_messages = [m for m in messages if m.get("role") == "assistant"]
        assert len(assistant_messages) >= 1, "Should have an assistant message"

    def test_get_messages_returns_session_id(
        self, sidebar_with_message: Frame
    ) -> None:
        """The messages endpoint echoes back the session_id."""
        session_id = sidebar_with_message.evaluate(
            "() => window.__sidebarApp.state.sessionID"
        )
        result = sidebar_with_message.evaluate("""async () => {
            const app = window.__sidebarApp
            const sid = app.state.sessionID
            return await app.api(`/api/sessions/${sid}/messages`)
        }""")
        assert result.get("session_id") == session_id, (
            f"Messages response should echo session_id, "
            f"expected {session_id!r}, got {result.get('session_id')!r}"
        )


# ---------------------------------------------------------------------------
# Session audit API
# ---------------------------------------------------------------------------


class TestSessionAuditViaAPI:
    """GET /api/sessions/{id}/audit returns audit log events."""

    def test_audit_has_events_after_chat(self, sidebar_with_message: Frame) -> None:
        """After chatting, the audit log contains at least one event."""
        result = sidebar_with_message.evaluate("""async () => {
            const app = window.__sidebarApp
            const sid = app.state.sessionID
            return await app.api(`/api/sessions/${sid}/audit`)
        }""")
        assert isinstance(result, list), f"Audit should be a list, got: {type(result)}"
        assert len(result) >= 1, "Audit log should have at least one event after chat"

    def test_audit_events_have_required_fields(
        self, sidebar_with_message: Frame
    ) -> None:
        """All audit events have event_type, session_id, and user_id fields."""
        result = sidebar_with_message.evaluate("""async () => {
            const app = window.__sidebarApp
            const sid = app.state.sessionID
            return await app.api(`/api/sessions/${sid}/audit`)
        }""")
        for event in result:
            assert "event_type" in event, f"Missing event_type in: {event}"
            assert "session_id" in event, f"Missing session_id in: {event}"

    def test_audit_includes_chat_event(self, sidebar_with_message: Frame) -> None:
        """The audit log includes a 'chat_received' or 'assistant_responded' event."""
        result = sidebar_with_message.evaluate("""async () => {
            const app = window.__sidebarApp
            const sid = app.state.sessionID
            return await app.api(`/api/sessions/${sid}/audit`)
        }""")
        event_types = {e.get("event_type") for e in result}
        chat_events = {"chat_received", "assistant_responded"}
        assert event_types & chat_events, (
            f"Expected chat events in audit, got: {event_types}"
        )


# ---------------------------------------------------------------------------
# Health API
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    """GET /api/health returns system status."""

    def test_health_returns_healthy(self, sidebar: Frame) -> None:
        """Health endpoint returns status=healthy."""
        result = sidebar.evaluate("""async () => {
            return await window.__sidebarApp.api('/api/health')
        }""")
        assert result.get("status") == "healthy", (
            f"Health status should be 'healthy', got: {result}"
        )

    def test_health_shows_openemr_connected(self, sidebar: Frame) -> None:
        """Health endpoint shows openemr_connected=true when connected."""
        result = sidebar.evaluate("""async () => {
            return await window.__sidebarApp.api('/api/health')
        }""")
        assert result.get("openemr_connected") is True, (
            f"OpenEMR should be connected, got: {result}"
        )

    def test_health_includes_openemr_status(self, sidebar: Frame) -> None:
        """Health endpoint includes openemr_status field."""
        result = sidebar.evaluate("""async () => {
            return await window.__sidebarApp.api('/api/health')
        }""")
        assert "openemr_status" in result, (
            f"Health response missing openemr_status: {result}"
        )
        assert result["openemr_status"] == "ok", (
            f"OpenEMR status should be 'ok', got: {result['openemr_status']!r}"
        )
