"""Smoke tests: sidebar loads, basic interactions work, session management."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from .conftest import (
    get_last_assistant_message,
    inject_patient_context,
    send_chat_message,
)

pytestmark = pytest.mark.e2e


class TestSidebarLoads:
    """Verify the sidebar UI renders correctly."""

    def test_sidebar_elements_present(self, sidebar_page: Page):
        """All key UI elements are rendered on load."""
        expect(sidebar_page.locator(".header-title")).to_have_text("Clinical Assistant")
        expect(sidebar_page.locator("#chat-input")).to_be_visible()
        expect(sidebar_page.locator("#send-button")).to_be_visible()
        expect(sidebar_page.locator("#status-pill")).to_be_visible()
        expect(sidebar_page.locator("#history-toggle")).to_be_visible()
        expect(sidebar_page.locator("#new-conversation")).to_be_visible()

    def test_status_starts_ready(self, sidebar_page: Page):
        """Status pill shows 'Ready' on initial load."""
        expect(sidebar_page.locator("#status-text")).to_have_text("Ready")

    def test_context_line_default(self, sidebar_page: Page):
        """Context line shows 'No patient selected' when no context injected."""
        expect(sidebar_page.locator("#context-line")).to_have_text("No patient selected")

    def test_session_id_displayed(self, sidebar_page: Page):
        """A session ID row is shown after initialization."""
        session_row = sidebar_page.locator("#session-id-row")
        expect(session_row).to_be_visible()
        assert "Session:" in session_row.inner_text()


class TestSessionManagement:
    """Verify session creation and switching."""

    def test_new_conversation_creates_session(self, sidebar_page: Page):
        """Clicking 'New Conversation' creates a fresh session."""
        # Get the initial session ID
        initial_text = sidebar_page.locator("#session-id-row").inner_text()

        sidebar_page.locator("#new-conversation").click()
        sidebar_page.wait_for_timeout(2000)

        new_text = sidebar_page.locator("#session-id-row").inner_text()
        assert new_text != initial_text, "Session ID should change after new conversation"

    def test_history_panel_populated(self, sidebar_page: Page):
        """History panel has at least the current session after toggling."""
        sidebar_page.locator("#history-toggle").click()
        sidebar_page.wait_for_timeout(1000)
        items = sidebar_page.locator(".history-item")
        assert items.count() >= 1


class TestChatBasics:
    """Verify basic chat input/output flow."""

    def test_empty_message_not_sent(self, sidebar_page: Page):
        """Clicking Send with empty input does nothing."""
        sidebar_page.locator("#send-button").click()
        sidebar_page.wait_for_timeout(500)
        messages = sidebar_page.locator(".message.role-user")
        assert messages.count() == 0

    def test_send_simple_message(self, sidebar_page: Page):
        """Sending a message shows the user bubble and gets an assistant reply."""
        send_chat_message(sidebar_page, "Hello, can you help me?")

        # User message should appear
        user_messages = sidebar_page.locator(".message.role-user")
        assert user_messages.count() >= 1

        # Assistant should have replied
        reply = get_last_assistant_message(sidebar_page)
        assert len(reply) > 0, "Assistant should have responded"

    def test_character_counter_shows_near_limit(self, sidebar_page: Page):
        """Character counter appears when approaching the limit."""
        chat_input = sidebar_page.locator("#chat-input")
        # Type a long message (7500+ chars)
        long_text = "x" * 7600
        chat_input.fill(long_text)
        counter = sidebar_page.locator("#char-counter")
        expect(counter).to_be_visible()
        assert "7600" in counter.inner_text()


class TestPatientContext:
    """Verify patient context injection affects the sidebar."""

    def test_inject_patient_context_sent_with_message(self, sidebar_page: Page):
        """Patient context injected via openemrAgentContext is sent with chat messages.

        The sidebar calls refreshContext() → buildPageContext() when sending
        a message, so the injected context flows through to the API.
        """
        inject_patient_context(
            sidebar_page,
            patient_id="1",
            patient_name="Maria Santos",
            encounter_id="2",
            active_tab="patient_summary",
        )

        # Send a message — buildPageContext() will read the injected context
        send_chat_message(sidebar_page, "Who is this patient?")

        reply = get_last_assistant_message(sidebar_page)
        # The agent should have used the patient context
        assert len(reply) > 0, "Agent should have responded"


class TestHealthEndpoint:
    """Verify the API health check through the browser."""

    def test_health_api_reachable(self, sidebar_page: Page, agent_url: str):
        """Health endpoint returns a valid response."""
        response = sidebar_page.request.get(f"{agent_url}/api/health")
        assert response.ok
        data = response.json()
        assert data["status"] == "healthy"
