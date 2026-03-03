"""Input handling tests.

Tests that verify correct behavior for chat input edge cases:
  - Enter key sends message (Shift+Enter does not)
  - Send button is hidden when input is empty
  - Character counter appears near limit
  - Send button is disabled (not just hidden) when over char limit
  - Send button re-enables after reducing message below the limit
  - Keyboard shortcut submit works the same as button click
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


# ---------------------------------------------------------------------------
# Button visibility
# ---------------------------------------------------------------------------


def _has_class(frame: Frame, selector: str, cls: str) -> bool:
    """Return True if the element has the given CSS class."""
    return frame.evaluate(
        f"() => document.querySelector({selector!r})?.classList.contains({cls!r}) ?? false"
    )


class TestSendButtonVisibility:
    """Send button is visible iff input has non-whitespace text."""

    def test_send_button_hidden_when_empty(self, sidebar: Frame) -> None:
        """Send button is not visible when input is empty."""
        assert not _has_class(sidebar, "#send-button", "visible"), (
            "Send button should not have 'visible' class when input is empty"
        )

    def test_send_button_visible_when_has_text(self, sidebar: Frame) -> None:
        """Send button becomes visible after typing text."""
        sidebar.locator("#chat-input").fill("Hello")
        assert _has_class(sidebar, "#send-button", "visible"), (
            "Send button should have 'visible' class when input has text"
        )

    def test_send_button_hidden_after_clearing(self, sidebar: Frame) -> None:
        """Send button hides again when input is cleared."""
        sidebar.locator("#chat-input").fill("Hello")
        sidebar.locator("#chat-input").fill("")
        assert not _has_class(sidebar, "#send-button", "visible"), (
            "Send button should not be visible after clearing input"
        )

    def test_send_button_hidden_for_whitespace_only(self, sidebar: Frame) -> None:
        """Send button stays hidden if input contains only whitespace."""
        sidebar.locator("#chat-input").fill("   ")
        assert not _has_class(sidebar, "#send-button", "visible"), (
            "Send button should not be visible for whitespace-only input"
        )


# ---------------------------------------------------------------------------
# Character counter
# ---------------------------------------------------------------------------


class TestCharacterCounter:
    """Character counter appears near limit and disables send when over."""

    def test_counter_hidden_below_warn_threshold(self, sidebar: Frame) -> None:
        """Counter is not visible for short messages."""
        sidebar.locator("#chat-input").fill("Short message")
        assert _has_class(sidebar, "#char-counter", "hidden"), (
            "Char counter should have 'hidden' class for short messages"
        )

    def test_counter_appears_near_limit(self, sidebar: Frame) -> None:
        """Counter appears when approaching MAX_CHARS."""
        sidebar.locator("#chat-input").fill("x" * WARN_CHARS)
        counter = sidebar.locator("#char-counter")
        expect(counter).not_to_have_class("hidden")
        assert str(WARN_CHARS) in counter.inner_text()

    def test_send_disabled_over_limit(self, sidebar: Frame) -> None:
        """Send button is disabled (not just hidden) when over MAX_CHARS."""
        sidebar.locator("#chat-input").fill("x" * (MAX_CHARS + 100))
        send_btn = sidebar.locator("#send-button")
        expect(send_btn).to_be_disabled()

    def test_send_reenabled_after_reducing_below_limit(self, sidebar: Frame) -> None:
        """After going over limit then reducing, send button must re-enable.

        Regression: updateCharacterCounter used `overLimit || existing_disabled`
        which kept the button disabled after the user reduced the message.
        """
        chat_input = sidebar.locator("#chat-input")

        # Go over limit
        chat_input.fill("x" * (MAX_CHARS + 100))
        expect(sidebar.locator("#send-button")).to_be_disabled()

        # Reduce to just under limit
        chat_input.fill("x" * (MAX_CHARS - 100))

        # Send button should be enabled again (visible + not disabled)
        send_btn = sidebar.locator("#send-button")
        assert _has_class(sidebar, "#send-button", "visible"), (
            "Send button should be visible after reducing below limit"
        )
        expect(send_btn).not_to_be_disabled()


# ---------------------------------------------------------------------------
# Keyboard submit
# ---------------------------------------------------------------------------


class TestKeyboardSubmit:
    """Enter key submits; Shift+Enter inserts newline."""

    def test_enter_key_sends_message(self, page: Page) -> None:
        """Pressing Enter with text in input sends the message."""
        page.set_default_timeout(E2E_TIMEOUT_MS)
        openemr_login(page)
        select_patient(page, PATIENT_PID, PATIENT_NAME)
        sidebar = get_sidebar_frame(page)

        sidebar.locator("#chat-input").fill("What is 2 + 2?")
        sidebar.locator("#chat-input").press("Enter")

        # User message should appear
        user_msgs = sidebar.locator(".message.role-user")
        expect(user_msgs.last).to_be_visible(timeout=5000)
        assert "2 + 2" in user_msgs.last.inner_text()

        # Wait for assistant reply
        sidebar.wait_for_function(
            "() => document.querySelectorAll('.message.role-assistant').length > 0",
            timeout=E2E_TIMEOUT_MS,
        )

    def test_shift_enter_does_not_send(self, sidebar: Frame) -> None:
        """Shift+Enter inserts a newline instead of sending."""
        chat_input = sidebar.locator("#chat-input")
        chat_input.fill("Line one")
        chat_input.press("Shift+Enter")

        # No user messages should appear (message not sent)
        user_msgs = sidebar.locator(".message.role-user")
        assert user_msgs.count() == 0

        # Input should still have content (not cleared)
        value = chat_input.input_value()
        assert len(value) > 0, "Input should still have content after Shift+Enter"

    def test_enter_does_not_send_over_limit(self, sidebar: Frame) -> None:
        """Enter key does nothing when message exceeds MAX_CHARS."""
        chat_input = sidebar.locator("#chat-input")
        chat_input.fill("x" * (MAX_CHARS + 100))
        chat_input.press("Enter")

        # No user messages should appear
        user_msgs = sidebar.locator(".message.role-user")
        assert user_msgs.count() == 0
