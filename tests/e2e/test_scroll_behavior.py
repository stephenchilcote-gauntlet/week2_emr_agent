"""Scroll behavior and new-messages pill E2E tests.

Tests that verify scroll-related behavior in the chat area:
  - New messages pill appears when the user is scrolled up and a message arrives
  - Clicking the pill scrolls to the bottom and hides itself
  - Scrolling manually to the bottom hides the pill
  - Scrolled-up state does not auto-scroll when a new message arrives
  - Chat area starts at the bottom (most-recent message visible)
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
def sidebar_after_reply(page: Page) -> Frame:
    """Sidebar with at least one reply AND a guaranteed-scrollable chat area.

    We inject a tall spacer into the chat area so that tests relying on
    scroll position are not skipped due to insufficient content height.
    """
    page.set_default_timeout(E2E_TIMEOUT_MS)
    openemr_login(page)
    select_patient(page, PATIENT_PID, PATIENT_NAME)
    sidebar = get_sidebar_frame(page)
    send_chat_message(sidebar, "What is 2 + 2?")
    # Inject a spacer so the chat area is taller than its viewport,
    # making scrolling meaningful regardless of reply length.
    sidebar.evaluate("""() => {
        const spacer = document.createElement('div')
        spacer.id = 'e2e-scroll-spacer'
        spacer.style.height = '2000px'
        spacer.style.flexShrink = '0'
        document.getElementById('chat-area').appendChild(spacer)
    }""")
    # Scroll to bottom after injecting spacer so we start at the bottom
    sidebar.evaluate("() => { const el = document.getElementById('chat-area'); el.scrollTop = el.scrollHeight }")
    sidebar.wait_for_timeout(100)
    return sidebar


def _scroll_chat_to_top(frame: Frame) -> None:
    """Programmatically scroll the chat area to the very top."""
    frame.evaluate("() => { document.getElementById('chat-area').scrollTop = 0 }")


def _is_near_bottom(frame: Frame) -> bool:
    """Return True if the chat area is scrolled near the bottom (≤50px away)."""
    return frame.evaluate("""() => {
        const el = document.getElementById('chat-area')
        return el.scrollHeight - el.scrollTop - el.clientHeight <= 50
    }""")


def _pill_visible(frame: Frame) -> bool:
    """Return True if the new-messages pill does NOT have the 'hidden' class."""
    return frame.evaluate(
        "() => !document.getElementById('new-messages-pill')?.classList.contains('hidden') ?? false"
    )


# ---------------------------------------------------------------------------
# New messages pill
# ---------------------------------------------------------------------------


class TestNewMessagesPill:
    """New messages pill appears when scrolled up and a message arrives."""

    def test_pill_hidden_on_load(self, sidebar_after_reply: Frame) -> None:
        """New messages pill is hidden when chat is at the bottom (normal state)."""
        assert not _pill_visible(sidebar_after_reply), (
            "Pill should be hidden when chat is scrolled to the bottom"
        )

    def test_pill_appears_when_scrolled_up_and_message_arrives(
        self, sidebar_after_reply: Frame,
    ) -> None:
        """Pill becomes visible when a new message arrives while scrolled up.

        We call the internal scrollToBottom(false) to simulate a message
        arriving while the user is scrolled up, avoiding a race condition
        with the typing indicator auto-scroll.
        """
        # Scroll to top first
        _scroll_chat_to_top(sidebar_after_reply)
        sidebar_after_reply.wait_for_timeout(100)

        # Simulate a message arriving while scrolled up (force=false)
        sidebar_after_reply.evaluate("() => window.__sidebarApp?.scrollToBottom(false)")
        sidebar_after_reply.wait_for_timeout(100)

        assert _pill_visible(sidebar_after_reply), (
            "Pill should be visible after message arrives while scrolled up"
        )

    def test_pill_click_scrolls_to_bottom(
        self, sidebar_after_reply: Frame,
    ) -> None:
        """Clicking the pill scrolls the chat to the bottom."""
        _scroll_chat_to_top(sidebar_after_reply)
        sidebar_after_reply.evaluate("() => window.__sidebarApp?.scrollToBottom(false)")
        sidebar_after_reply.wait_for_timeout(100)

        assert _pill_visible(sidebar_after_reply), "Pill should be visible before click"

        sidebar_after_reply.locator("#new-messages-pill").dispatch_event("click")
        sidebar_after_reply.wait_for_timeout(200)

        assert _is_near_bottom(sidebar_after_reply), (
            "Chat should be scrolled to bottom after clicking pill"
        )

    def test_pill_hidden_after_click(
        self, sidebar_after_reply: Frame,
    ) -> None:
        """Pill hides itself after being clicked."""
        _scroll_chat_to_top(sidebar_after_reply)
        sidebar_after_reply.evaluate("() => window.__sidebarApp?.scrollToBottom(false)")
        sidebar_after_reply.wait_for_timeout(100)

        sidebar_after_reply.locator("#new-messages-pill").dispatch_event("click")
        sidebar_after_reply.wait_for_timeout(200)

        assert not _pill_visible(sidebar_after_reply), (
            "Pill should hide itself after being clicked"
        )

    def test_pill_hidden_when_scrolled_to_bottom_manually(
        self, sidebar_after_reply: Frame,
    ) -> None:
        """Scrolling down to the bottom manually hides the pill."""
        _scroll_chat_to_top(sidebar_after_reply)
        sidebar_after_reply.evaluate("() => window.__sidebarApp?.scrollToBottom(false)")
        sidebar_after_reply.wait_for_timeout(100)

        assert _pill_visible(sidebar_after_reply), "Pill should be visible when scrolled up"

        # Manually scroll to bottom
        sidebar_after_reply.evaluate(
            "() => { const el = document.getElementById('chat-area'); el.scrollTop = el.scrollHeight }"
        )
        # Scroll event fires asynchronously
        sidebar_after_reply.wait_for_timeout(300)

        assert not _pill_visible(sidebar_after_reply), (
            "Pill should hide when user scrolls to bottom manually"
        )


# ---------------------------------------------------------------------------
# Auto-scroll behavior
# ---------------------------------------------------------------------------


class TestAutoScroll:
    """Chat area auto-scrolls to bottom when appropriate."""

    def test_chat_starts_at_bottom(
        self, sidebar_after_reply: Frame,
    ) -> None:
        """After first reply, chat area is scrolled to the bottom."""
        assert _is_near_bottom(sidebar_after_reply), (
            "Chat area should be at the bottom after receiving a reply"
        )

    def test_force_scroll_always_reaches_bottom(
        self, sidebar_after_reply: Frame,
    ) -> None:
        """scrollToBottom(true) always scrolls to the bottom regardless of position.

        This tests the 'force' path used during loadConversation() and after
        history session loads — these always need to scroll to the end.
        """
        # Scroll to the top first
        _scroll_chat_to_top(sidebar_after_reply)
        assert not _is_near_bottom(sidebar_after_reply), "Should start at top"

        # Force scroll to bottom
        sidebar_after_reply.evaluate("() => window.__sidebarApp.scrollToBottom(true)")
        sidebar_after_reply.wait_for_timeout(100)

        assert _is_near_bottom(sidebar_after_reply), (
            "Force-scroll should bring chat to the bottom from any position"
        )

    def test_scrolled_up_no_auto_scroll(
        self, sidebar_after_reply: Frame,
    ) -> None:
        """If scrolled up, a new message does NOT auto-scroll (uses pill instead).

        This test bypasses the typing indicator auto-scroll by directly
        calling scrollToBottom(false) to simulate a message arriving.
        """
        _scroll_chat_to_top(sidebar_after_reply)
        sidebar_after_reply.wait_for_timeout(100)

        # Simulate message arriving while scrolled up
        sidebar_after_reply.evaluate("() => window.__sidebarApp?.scrollToBottom(false)")
        sidebar_after_reply.wait_for_timeout(100)

        # Should still be at the top (no auto-scroll)
        at_top = sidebar_after_reply.evaluate(
            "() => document.getElementById('chat-area').scrollTop === 0"
        )
        assert at_top, "Chat should NOT auto-scroll when user is scrolled up"

        # Pill should be shown instead
        assert _pill_visible(sidebar_after_reply), (
            "Pill should be visible when not auto-scrolling"
        )
