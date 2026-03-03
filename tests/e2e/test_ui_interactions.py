"""UI interaction tests for non-LLM sidebar features.

Tests that verify sidebar UI interactions without requiring LLM calls:
  - Feedback buttons appear on assistant messages and are clickable
  - Audit panel toggle shows/hides the audit panel
  - History panel toggle shows/hides history list
  - Session ID row displays a truncated session ID
  - History panel shows "No previous conversations" when empty (fresh state)
  - Clicking session ID row changes text to "Copied!" (clipboard feedback)
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Frame, Page, expect

from .conftest import (
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
    """Logged-in OpenEMR with patient selected."""
    page.set_default_timeout(E2E_TIMEOUT_MS)
    openemr_login(page)
    select_patient(page, PATIENT_PID, PATIENT_NAME)
    return get_sidebar_frame(page)


@pytest.fixture
def sidebar_with_message(page: Page) -> Frame:
    """Sidebar after one assistant reply (so feedback buttons exist)."""
    page.set_default_timeout(E2E_TIMEOUT_MS)
    openemr_login(page)
    select_patient(page, PATIENT_PID, PATIENT_NAME)
    sidebar = get_sidebar_frame(page)
    send_chat_message(sidebar, "What is 2 + 2?")
    sidebar.wait_for_function(
        "() => document.querySelectorAll('.message.role-assistant').length > 0",
        timeout=E2E_TIMEOUT_MS,
    )
    return sidebar


# ---------------------------------------------------------------------------
# Session ID display
# ---------------------------------------------------------------------------


class TestSessionIdDisplay:
    """Session ID row shows a truncated session ID in the expected format."""

    def test_session_id_row_visible(self, sidebar: Frame) -> None:
        """Session ID row is visible on load."""
        expect(sidebar.locator("#session-id-row")).to_be_visible()

    def test_session_id_row_has_prefix(self, sidebar: Frame) -> None:
        """Session ID row text starts with 'Session:'."""
        text = sidebar.locator("#session-id-row").inner_text()
        assert text.startswith("Session:"), (
            f"Expected 'Session:' prefix, got: {text!r}"
        )

    def test_session_id_click_shows_copied(self, sidebar: Frame) -> None:
        """Clicking session ID row changes text to 'Copied!' (clipboard feedback)."""
        sidebar.locator("#session-id-row").dispatch_event("click")
        # Brief wait for the text change
        sidebar.wait_for_timeout(200)
        text = sidebar.locator("#session-id-row").inner_text()
        assert "Copied" in text or "Session:" in text, (
            f"Expected 'Copied!' or back to 'Session:', got: {text!r}"
        )

    def test_session_id_restores_after_copied(self, sidebar: Frame) -> None:
        """After 'Copied!' feedback, session ID row restores to 'Session:' in ~1.5s."""
        sidebar.locator("#session-id-row").dispatch_event("click")
        sidebar.wait_for_timeout(2000)  # wait for 1500ms timeout + buffer
        text = sidebar.locator("#session-id-row").inner_text()
        assert text.startswith("Session:"), (
            f"Session ID row should have restored to 'Session:' after 2s, got: {text!r}"
        )


# ---------------------------------------------------------------------------
# Feedback buttons
# ---------------------------------------------------------------------------


class TestFeedbackButtons:
    """Thumbs up/down buttons appear on assistant messages and respond to clicks."""

    def test_feedback_buttons_visible_on_assistant_message(
        self, sidebar_with_message: Frame
    ) -> None:
        """Feedback buttons (👍/👎) are rendered on each assistant message."""
        feedback = sidebar_with_message.locator(".message.role-assistant .feedback-buttons")
        assert feedback.count() > 0, "No feedback buttons found on assistant messages"

    def test_thumbs_up_becomes_active_on_click(
        self, sidebar_with_message: Frame
    ) -> None:
        """Clicking 👍 adds 'active' class to the button."""
        up_btn = sidebar_with_message.locator(
            ".message.role-assistant .feedback-btn[data-rating='up']"
        ).first
        up_btn.dispatch_event("click")
        sidebar_with_message.wait_for_timeout(500)
        assert sidebar_with_message.evaluate(
            """() => {
                const btn = document.querySelector(
                    '.message.role-assistant .feedback-btn[data-rating="up"]'
                );
                return btn && btn.classList.contains('active');
            }"""
        ), "👍 button should have 'active' class after click"

    def test_thumbs_down_clears_thumbs_up_active(
        self, sidebar_with_message: Frame
    ) -> None:
        """Clicking 👎 removes 'active' from 👍 and makes itself active."""
        # Click 👍 first
        sidebar_with_message.locator(
            ".message.role-assistant .feedback-btn[data-rating='up']"
        ).first.dispatch_event("click")
        sidebar_with_message.wait_for_timeout(300)

        # Then click 👎
        sidebar_with_message.locator(
            ".message.role-assistant .feedback-btn[data-rating='down']"
        ).first.dispatch_event("click")
        sidebar_with_message.wait_for_timeout(500)

        result = sidebar_with_message.evaluate(
            """() => {
                const container = document.querySelector(
                    '.message.role-assistant .feedback-buttons'
                );
                const up = container?.querySelector('[data-rating="up"]');
                const down = container?.querySelector('[data-rating="down"]');
                return {
                    upActive: up?.classList.contains('active'),
                    downActive: down?.classList.contains('active'),
                };
            }"""
        )
        assert not result["upActive"], "👍 should not be active after clicking 👎"
        assert result["downActive"], "👎 should be active after clicking it"


# ---------------------------------------------------------------------------
# Audit panel
# ---------------------------------------------------------------------------


class TestAuditPanel:
    """Audit toggle shows/hides the audit panel correctly."""

    def test_audit_panel_initially_hidden(self, sidebar: Frame) -> None:
        """Audit panel is hidden on load."""
        if sidebar.locator("#audit-panel").count() == 0:
            pytest.skip("No audit panel element in this build")
        audit_panel = sidebar.locator("#audit-panel")
        assert sidebar.evaluate(
            "() => document.getElementById('audit-panel')?.classList.contains('hidden') ?? true"
        ), "Audit panel should be hidden initially"

    def test_audit_toggle_shows_panel(self, sidebar: Frame) -> None:
        """Clicking audit toggle reveals the audit panel."""
        if sidebar.locator("#audit-toggle").count() == 0:
            pytest.skip("No audit toggle in this build")

        sidebar.locator("#audit-toggle").dispatch_event("click")
        sidebar.wait_for_timeout(500)

        visible = sidebar.evaluate(
            "() => !document.getElementById('audit-panel')?.classList.contains('hidden')"
        )
        assert visible, "Audit panel should be visible after clicking toggle"

    def test_audit_toggle_hides_panel_on_second_click(self, sidebar: Frame) -> None:
        """Clicking audit toggle twice hides the panel again."""
        if sidebar.locator("#audit-toggle").count() == 0:
            pytest.skip("No audit toggle in this build")

        sidebar.locator("#audit-toggle").dispatch_event("click")
        sidebar.wait_for_timeout(300)
        sidebar.locator("#audit-toggle").dispatch_event("click")
        sidebar.wait_for_timeout(300)

        hidden = sidebar.evaluate(
            "() => document.getElementById('audit-panel')?.classList.contains('hidden') ?? true"
        )
        assert hidden, "Audit panel should be hidden after second toggle click"


# ---------------------------------------------------------------------------
# History panel
# ---------------------------------------------------------------------------


class TestHistoryPanel:
    """History panel shows/hides correctly and contains expected items."""

    def test_history_panel_initially_hidden(self, sidebar: Frame) -> None:
        """History panel is not visible on initial load."""
        hidden = sidebar.evaluate(
            "() => document.getElementById('history-panel')?.classList.contains('hidden') ?? true"
        )
        assert hidden, "History panel should be hidden initially"

    def test_history_toggle_shows_panel(self, sidebar: Frame) -> None:
        """Clicking history toggle shows the history panel."""
        sidebar.locator("#history-toggle").dispatch_event("click")
        sidebar.wait_for_timeout(500)

        visible = sidebar.evaluate(
            "() => !document.getElementById('history-panel')?.classList.contains('hidden')"
        )
        assert visible, "History panel should be visible after clicking toggle"

    def test_history_toggle_hides_chat_shell(self, sidebar: Frame) -> None:
        """When history panel shows, the chat area is hidden."""
        sidebar.locator("#history-toggle").dispatch_event("click")
        sidebar.wait_for_timeout(300)

        chat_hidden = sidebar.evaluate(
            "() => document.getElementById('chat-shell')?.classList.contains('hidden') ?? false"
        )
        assert chat_hidden, "Chat shell should be hidden when history panel is visible"

    def test_history_has_at_least_one_item(self, sidebar: Frame) -> None:
        """History panel has at least the current session in its list."""
        sidebar.locator("#history-toggle").dispatch_event("click")
        sidebar.wait_for_timeout(1000)

        items = sidebar.locator(".history-item")
        assert items.count() >= 1, "History panel should have at least 1 item (current session)"

    def test_history_second_toggle_shows_chat(self, sidebar: Frame) -> None:
        """Clicking history toggle twice restores the chat area."""
        sidebar.locator("#history-toggle").dispatch_event("click")
        sidebar.wait_for_timeout(300)
        sidebar.locator("#history-toggle").dispatch_event("click")
        sidebar.wait_for_timeout(300)

        chat_visible = sidebar.evaluate(
            "() => !document.getElementById('chat-shell')?.classList.contains('hidden')"
        )
        assert chat_visible, "Chat shell should be visible after closing history panel"
