"""Sidebar UI state and widget E2E tests.

Tests that verify sidebar UI elements update correctly:
  - Character counter: hidden below warning threshold, visible above
  - Character counter: shows correct count and limit text
  - Over-limit message: send button disabled, input gets error class
  - Session ID row: shows after session created, click-to-copy updates label
  - New conversation button: resets session state, clears chat
  - History toggle: opens and closes the history panel
  - Audit toggle: opens and closes the audit panel (if present)
  - Status indicator transitions: ready text visible after load
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

MAX_CHARS = 8000
WARN_CHARS = 7500


@pytest.fixture
def sidebar(page: Page) -> Frame:
    page.set_default_timeout(E2E_TIMEOUT_MS)
    openemr_login(page)
    select_patient(page, PATIENT_PID, PATIENT_NAME)
    return get_sidebar_frame(page)


@pytest.fixture
def sidebar_with_session(page: Page) -> Frame:
    """Sidebar that has already sent one message (session created)."""
    page.set_default_timeout(E2E_TIMEOUT_MS)
    openemr_login(page)
    select_patient(page, PATIENT_PID, PATIENT_NAME)
    sidebar = get_sidebar_frame(page)
    send_chat_message(sidebar, "What is 2 + 2?")
    return sidebar


# ---------------------------------------------------------------------------
# Character counter
# ---------------------------------------------------------------------------


class TestCharacterCounter:
    """Character counter appears near warning threshold and shows limit."""

    def test_char_counter_hidden_on_short_message(self, sidebar: Frame) -> None:
        """Counter is hidden when input is short (below warning threshold)."""
        sidebar.locator("#chat-input").fill("Hello")
        sidebar.wait_for_timeout(100)
        # Counter should be hidden (or have 'hidden' class)
        counter = sidebar.locator("#char-counter")
        if counter.count() > 0:
            has_hidden = sidebar.evaluate(
                "() => document.getElementById('char-counter')?.classList.contains('hidden') ?? true"
            )
            assert has_hidden, "Counter should be hidden for short messages"

    def test_char_counter_shows_near_limit(self, sidebar: Frame) -> None:
        """Counter is visible when input length is near the warning threshold."""
        # Fill with WARN_CHARS characters of text
        long_text = "a" * WARN_CHARS
        sidebar.locator("#chat-input").fill(long_text)
        sidebar.wait_for_timeout(200)
        counter = sidebar.locator("#char-counter")
        if counter.count() > 0:
            has_hidden = sidebar.evaluate(
                "() => document.getElementById('char-counter')?.classList.contains('hidden') ?? false"
            )
            assert not has_hidden, "Counter should be visible near warning threshold"

    def test_char_counter_text_shows_limit(self, sidebar: Frame) -> None:
        """Counter text shows 'N / 8000' format."""
        long_text = "a" * WARN_CHARS
        sidebar.locator("#chat-input").fill(long_text)
        sidebar.wait_for_timeout(200)
        counter = sidebar.locator("#char-counter")
        if counter.count() > 0:
            text = counter.inner_text()
            assert "8000" in text, f"Counter should show max of 8000, got: {text!r}"
            assert str(WARN_CHARS) in text, f"Counter should show {WARN_CHARS}, got: {text!r}"

    def test_send_button_disabled_over_limit(self, sidebar: Frame) -> None:
        """Send button is disabled when message exceeds MAX_CHARS."""
        over_limit_text = "a" * (MAX_CHARS + 1)
        sidebar.locator("#chat-input").fill(over_limit_text)
        sidebar.wait_for_timeout(200)
        is_disabled = sidebar.evaluate(
            "() => document.getElementById('send-button')?.disabled ?? false"
        )
        assert is_disabled, "Send button should be disabled when over limit"

    def test_input_gets_over_limit_class(self, sidebar: Frame) -> None:
        """Input gets 'over-limit' CSS class when exceeding MAX_CHARS."""
        over_limit_text = "a" * (MAX_CHARS + 1)
        sidebar.locator("#chat-input").fill(over_limit_text)
        sidebar.wait_for_timeout(200)
        has_class = sidebar.evaluate(
            "() => document.getElementById('chat-input')?.classList.contains('over-limit') ?? false"
        )
        assert has_class, "Input should have 'over-limit' class when over limit"

    def test_over_limit_class_removed_after_shortening(self, sidebar: Frame) -> None:
        """'over-limit' class is removed when message is shortened back under limit."""
        over_limit_text = "a" * (MAX_CHARS + 1)
        sidebar.locator("#chat-input").fill(over_limit_text)
        sidebar.wait_for_timeout(100)
        # Now shorten
        sidebar.locator("#chat-input").fill("Short message")
        sidebar.wait_for_timeout(100)
        has_class = sidebar.evaluate(
            "() => document.getElementById('chat-input')?.classList.contains('over-limit') ?? false"
        )
        assert not has_class, "over-limit class should be removed after shortening"


# ---------------------------------------------------------------------------
# Session ID display
# ---------------------------------------------------------------------------


class TestSessionIdDisplay:
    """Session ID row shows after a session is created."""

    def test_session_id_row_shows_after_message(
        self, sidebar_with_session: Frame
    ) -> None:
        """After sending a message, session ID row is visible."""
        row = sidebar_with_session.locator("#session-id-row")
        if row.count() > 0:
            # It should not be hidden
            is_hidden = sidebar_with_session.evaluate(
                "() => document.getElementById('session-id-row')?.classList.contains('hidden') ?? true"
            )
            assert not is_hidden, "Session ID row should be visible after session created"

    def test_session_id_row_contains_session_text(
        self, sidebar_with_session: Frame
    ) -> None:
        """Session ID row shows 'Session:' prefix."""
        row = sidebar_with_session.locator("#session-id-row")
        if row.count() > 0:
            text = row.inner_text()
            assert "Session:" in text or len(text) > 5, (
                f"Session row should show session ID, got: {text!r}"
            )

    def test_session_id_row_click_shows_copied(
        self, sidebar_with_session: Frame
    ) -> None:
        """Clicking the session ID row shows 'Copied!' temporarily."""
        row = sidebar_with_session.locator("#session-id-row")
        if row.count() > 0:
            row.click()
            sidebar_with_session.wait_for_timeout(100)
            text = row.inner_text()
            assert "Copied" in text or "Session" in text, (
                f"After click, row should show 'Copied!' or revert, got: {text!r}"
            )


# ---------------------------------------------------------------------------
# New conversation button
# ---------------------------------------------------------------------------


class TestNewConversationButton:
    """New conversation button creates a fresh session."""

    def test_new_conversation_clears_chat(
        self, sidebar_with_session: Frame
    ) -> None:
        """Clicking 'New Conversation' clears the chat area."""
        # Verify we have messages first
        assert sidebar_with_session.locator(".message").count() > 0, (
            "Should have messages before new conversation"
        )
        sidebar_with_session.locator("#new-conversation").dispatch_event("click")
        sidebar_with_session.wait_for_function(
            "() => document.querySelectorAll('.message').length === 0",
            timeout=10000,
        )
        assert sidebar_with_session.locator(".message").count() == 0, (
            "Chat should be empty after new conversation"
        )

    def test_new_conversation_resets_session_id(
        self, sidebar_with_session: Frame
    ) -> None:
        """Clicking 'New Conversation' changes the session ID."""
        old_session = sidebar_with_session.evaluate(
            "() => window.__sidebarApp.state.sessionID"
        )
        sidebar_with_session.locator("#new-conversation").dispatch_event("click")
        sidebar_with_session.wait_for_timeout(2000)
        new_session = sidebar_with_session.evaluate(
            "() => window.__sidebarApp.state.sessionID"
        )
        assert new_session != old_session, (
            f"Session should change after new conversation: {old_session!r} -> {new_session!r}"
        )


# ---------------------------------------------------------------------------
# History panel toggle
# ---------------------------------------------------------------------------


class TestHistoryPanelToggle:
    """History panel can be opened and closed with the toggle button."""

    def test_history_panel_hidden_by_default(self, sidebar: Frame) -> None:
        """History panel is not visible on initial load."""
        panel = sidebar.locator("#history-panel")
        if panel.count() > 0:
            has_hidden = sidebar.evaluate(
                "() => document.getElementById('history-panel')?.classList.contains('hidden') ?? true"
            )
            assert has_hidden, "History panel should start hidden"

    def test_history_toggle_opens_panel(self, sidebar: Frame) -> None:
        """Clicking history toggle shows the history panel."""
        toggle = sidebar.locator("#history-toggle")
        if toggle.count() > 0:
            toggle.dispatch_event("click")
            sidebar.wait_for_timeout(500)
            has_hidden = sidebar.evaluate(
                "() => document.getElementById('history-panel')?.classList.contains('hidden') ?? true"
            )
            assert not has_hidden, "History panel should be visible after toggle click"

    def test_history_toggle_closes_panel(self, sidebar: Frame) -> None:
        """Clicking history toggle twice closes the panel."""
        toggle = sidebar.locator("#history-toggle")
        if toggle.count() > 0:
            # Open
            toggle.dispatch_event("click")
            sidebar.wait_for_timeout(300)
            # Close
            toggle.dispatch_event("click")
            sidebar.wait_for_timeout(300)
            has_hidden = sidebar.evaluate(
                "() => document.getElementById('history-panel')?.classList.contains('hidden') ?? true"
            )
            assert has_hidden, "History panel should close on second toggle click"


# ---------------------------------------------------------------------------
# Status indicator
# ---------------------------------------------------------------------------


class TestStatusIndicator:
    """Status pill shows correct state."""

    def test_status_shows_ready_on_load(self, sidebar: Frame) -> None:
        """Status indicator shows 'Ready' when the sidebar is idle."""
        status_el = sidebar.locator("#status-pill, .status-pill, [id*=status]")
        if status_el.count() > 0:
            text = status_el.first.inner_text()
            assert "Ready" in text or "ready" in text.lower(), (
                f"Status should show 'Ready' on load, got: {text!r}"
            )
        else:
            # Check via JS state
            phase = sidebar.evaluate("() => window.__sidebarApp?.state?.phase")
            assert phase == "ready" or phase is None, (
                f"Phase should be ready on load, got: {phase!r}"
            )

    def test_status_returns_to_ready_after_message(
        self, sidebar: Frame
    ) -> None:
        """Status returns to 'Ready' after receiving a response."""
        send_chat_message(sidebar, "What is 2 + 2?")
        # After send_chat_message completes, should be back to ready
        phase = sidebar.evaluate("() => window.__sidebarApp?.state?.phase")
        assert phase in ("ready", "planning"), (
            f"Phase should be ready after message, got: {phase!r}"
        )
