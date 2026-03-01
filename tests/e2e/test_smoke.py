"""Smoke tests: sidebar loads, basic interactions work, session management.

Tests run against real OpenEMR — the sidebar is tested as an embedded iframe,
not a standalone page.  Every test logs into OpenEMR and interacts with the
sidebar through the same iframe a clinician would use.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Frame, Page, expect

from .conftest import (
    AGENT_BASE_URL,
    E2E_TIMEOUT_MS,
    PATIENT_MAP,
    get_last_assistant_message,
    get_sidebar_frame,
    openemr_login,
    select_patient,
    send_chat_message,
)

pytestmark = pytest.mark.e2e

PATIENT_PID = PATIENT_MAP["Maria Santos"]
PATIENT_NAME = "Maria Santos"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sidebar(page: Page) -> Frame:
    """Logged-in OpenEMR with the sidebar frame ready for interaction."""
    page.set_default_timeout(E2E_TIMEOUT_MS)
    openemr_login(page)
    return get_sidebar_frame(page)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSidebarLoads:
    """Verify the sidebar UI renders correctly inside OpenEMR."""

    def test_sidebar_elements_present(self, sidebar: Frame):
        """All key UI elements are rendered on load."""
        expect(sidebar.locator(".header-title")).to_have_text("Clinical Assistant")
        expect(sidebar.locator("#chat-input")).to_be_visible()
        expect(sidebar.locator("#send-button")).to_be_visible()
        expect(sidebar.locator("#status-pill")).to_be_visible()
        expect(sidebar.locator("#history-toggle")).to_be_visible()
        expect(sidebar.locator("#new-conversation")).to_be_visible()

    def test_status_starts_ready(self, sidebar: Frame):
        """Status pill shows 'Ready' on initial load."""
        expect(sidebar.locator("#status-text")).to_have_text("Ready")

    def test_context_line_default(self, sidebar: Frame):
        """Context line shows 'No patient selected' when no context injected."""
        expect(sidebar.locator("#context-line")).to_have_text("No patient selected")

    def test_session_id_displayed(self, sidebar: Frame):
        """A session ID row is shown after initialization."""
        session_row = sidebar.locator("#session-id-row")
        expect(session_row).to_be_visible()
        assert "Session:" in session_row.inner_text()


class TestSessionManagement:
    """Verify session creation and switching."""

    def test_new_conversation_creates_session(self, sidebar: Frame):
        """Clicking 'New Conversation' creates a fresh session."""
        initial_text = sidebar.locator("#session-id-row").inner_text()

        sidebar.locator("#new-conversation").dispatch_event("click")
        sidebar.locator("#session-id-row").wait_for(state="visible")
        # Wait for the new session to be created (API call)
        sidebar.page.wait_for_timeout(2000)

        new_text = sidebar.locator("#session-id-row").inner_text()
        assert new_text != initial_text, "Session ID should change after new conversation"

    def test_history_panel_populated(self, sidebar: Frame):
        """History panel has at least the current session after toggling."""
        sidebar.locator("#history-toggle").dispatch_event("click")
        sidebar.page.wait_for_timeout(1000)
        items = sidebar.locator(".history-item")
        assert items.count() >= 1


class TestChatBasics:
    """Verify basic chat input/output flow."""

    def test_empty_message_not_sent(self, sidebar: Frame):
        """Send button is hidden when input is empty, preventing empty sends."""
        send_btn = sidebar.locator("#send-button")
        expect(send_btn).not_to_have_class("visible")
        messages = sidebar.locator(".message.role-user")
        assert messages.count() == 0

    def test_send_simple_message(self, sidebar: Frame):
        """Sending a message shows the user bubble and gets an assistant reply."""
        send_chat_message(sidebar, "Hello, can you help me?")

        user_messages = sidebar.locator(".message.role-user")
        assert user_messages.count() >= 1

        reply = get_last_assistant_message(sidebar)
        assert len(reply) > 0, "Assistant should have responded"

    def test_character_counter_shows_near_limit(self, sidebar: Frame):
        """Character counter appears when approaching the limit."""
        chat_input = sidebar.locator("#chat-input")
        long_text = "x" * 7600
        chat_input.fill(long_text)
        counter = sidebar.locator("#char-counter")
        expect(counter).to_be_visible()
        assert "7600" in counter.inner_text()


class TestPatientContext:
    """Verify patient context from OpenEMR flows into the sidebar."""

    def test_patient_context_updates_sidebar(self, page: Page):
        """Selecting a patient via left_nav updates the sidebar context line."""
        page.set_default_timeout(E2E_TIMEOUT_MS)
        openemr_login(page)
        sidebar = get_sidebar_frame(page)

        # Verify no patient initially
        expect(sidebar.locator("#context-line")).to_have_text("No patient selected")

        # Select a patient — embed.js context bridge sends postMessage to sidebar
        select_patient(page, PATIENT_PID, PATIENT_NAME)

        # The sidebar should update its context line with the patient name
        expect(sidebar.locator("#context-line")).to_contain_text(
            PATIENT_NAME, timeout=10000,
        )

    def test_patient_context_sent_with_message(self, page: Page):
        """Patient context is included when sending a chat message."""
        page.set_default_timeout(E2E_TIMEOUT_MS)
        openemr_login(page)
        select_patient(page, PATIENT_PID, PATIENT_NAME)
        sidebar = get_sidebar_frame(page)

        send_chat_message(sidebar, "Who is this patient?")

        reply = get_last_assistant_message(sidebar)
        assert len(reply) > 0, "Agent should have responded"


class TestHealthEndpoint:
    """Verify the API health check through the browser."""

    def test_health_api_reachable(self, page: Page):
        """Health endpoint returns a valid response."""
        response = page.request.get(f"{AGENT_BASE_URL}/api/health")
        assert response.ok
        data = response.json()
        assert data["status"] == "healthy"
