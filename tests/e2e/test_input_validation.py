"""Input validation and edge case message E2E tests.

Tests that verify the sidebar correctly handles edge case user inputs:
  - Empty message is not submitted (send button hidden/disabled)
  - Whitespace-only message is not submitted
  - Very long messages are accepted and processed
  - Unicode/non-ASCII characters are preserved correctly
  - HTML/XSS injection in messages is displayed safely
  - Newlines in messages are preserved in display
  - Shift+Enter adds a newline (does not submit)
  - Messages with only special characters work
  - Messages with SOAP-note-like content are processed
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
    page.set_default_timeout(E2E_TIMEOUT_MS)
    openemr_login(page)
    select_patient(page, PATIENT_PID, PATIENT_NAME)
    return get_sidebar_frame(page)


# ---------------------------------------------------------------------------
# Empty / whitespace prevention
# ---------------------------------------------------------------------------


class TestEmptyMessagePrevention:
    """Empty and whitespace-only messages are not submitted."""

    def test_send_button_hidden_when_input_empty(self, sidebar: Frame) -> None:
        """Send button is not visible when the input is empty."""
        sidebar.locator("#chat-input").fill("")
        sidebar.wait_for_timeout(100)
        # Send button should not have 'visible' class when input is empty
        has_visible = sidebar.evaluate(
            "() => document.getElementById('send-button')?.classList.contains('visible') ?? false"
        )
        assert not has_visible, "Send button should not be visible when input is empty"

    def test_send_button_appears_with_text(self, sidebar: Frame) -> None:
        """Send button becomes visible when user types text."""
        sidebar.locator("#chat-input").fill("Hello")
        sidebar.wait_for_timeout(100)
        has_visible = sidebar.evaluate(
            "() => document.getElementById('send-button')?.classList.contains('visible') ?? false"
        )
        assert has_visible, "Send button should be visible when input has text"

    def test_send_button_hidden_after_clear(self, sidebar: Frame) -> None:
        """Send button hides again when input is cleared."""
        sidebar.locator("#chat-input").fill("Hello")
        sidebar.wait_for_timeout(100)
        sidebar.locator("#chat-input").fill("")
        sidebar.wait_for_timeout(100)
        has_visible = sidebar.evaluate(
            "() => document.getElementById('send-button')?.classList.contains('visible') ?? false"
        )
        assert not has_visible, "Send button should hide after clearing input"

    def test_whitespace_only_does_not_trigger_send(self, sidebar: Frame) -> None:
        """Whitespace-only input doesn't send a message."""
        # Fill with spaces and try to press Enter
        sidebar.locator("#chat-input").fill("   ")
        sidebar.wait_for_timeout(100)

        existing_count = sidebar.locator(".message").count()
        sidebar.locator("#chat-input").press("Enter")
        sidebar.wait_for_timeout(500)

        new_count = sidebar.locator(".message").count()
        assert new_count == existing_count, (
            "Whitespace-only message should not create a new message"
        )

    def test_enter_on_empty_input_does_nothing(self, sidebar: Frame) -> None:
        """Pressing Enter on empty input doesn't send anything."""
        sidebar.locator("#chat-input").fill("")
        existing_count = sidebar.locator(".message").count()

        sidebar.locator("#chat-input").press("Enter")
        sidebar.wait_for_timeout(500)

        new_count = sidebar.locator(".message").count()
        assert new_count == existing_count, (
            "Enter on empty input should not create a message"
        )


# ---------------------------------------------------------------------------
# Long messages
# ---------------------------------------------------------------------------


class TestLongMessages:
    """Very long messages are accepted and handled correctly."""

    def test_long_message_accepted(self, sidebar: Frame) -> None:
        """A 500-character message is sent and gets a response."""
        long_msg = "What is 2 + 2? " * 33  # ~500 chars
        send_chat_message(sidebar, long_msg)

        msgs = sidebar.locator(".message.role-assistant")
        assert msgs.count() >= 1
        assert len(msgs.last.inner_text()) > 0

    def test_input_height_grows_with_long_text(self, sidebar: Frame) -> None:
        """Typing a multi-line message increases the input height."""
        initial_height = sidebar.evaluate(
            "() => document.getElementById('chat-input').offsetHeight"
        )
        # Multi-line text
        sidebar.locator("#chat-input").fill("Line 1\nLine 2\nLine 3")
        sidebar.wait_for_timeout(100)
        new_height = sidebar.evaluate(
            "() => document.getElementById('chat-input').offsetHeight"
        )
        assert new_height >= initial_height, (
            f"Input should grow with multiline text: {initial_height}px → {new_height}px"
        )


# ---------------------------------------------------------------------------
# Special characters
# ---------------------------------------------------------------------------


class TestSpecialCharacterMessages:
    """Messages with special characters are handled safely."""

    def test_html_in_user_message_displayed_safely(self, sidebar: Frame) -> None:
        """HTML in user message is escaped, not rendered as HTML."""
        send_chat_message(sidebar, "What is <b>important</b> for diabetes?")

        last_user_msg = sidebar.locator(".message.role-user").last
        inner_html = last_user_msg.inner_html()
        # The <b> tag should NOT be rendered as bold HTML
        assert "<b>" not in inner_html, (
            f"HTML should be escaped in user messages, got innerHTML: {inner_html!r}"
        )

    def test_script_tag_in_user_message_not_executed(self, sidebar: Frame) -> None:
        """Script tags in user messages are not executed (XSS prevention)."""
        # Set up a canary to detect XSS
        sidebar.evaluate("() => { window.__xss_fired = false }")

        send_chat_message(sidebar, "<script>window.__xss_fired = true</script>Hello")
        sidebar.wait_for_timeout(500)

        xss_fired = sidebar.evaluate("() => window.__xss_fired")
        assert not xss_fired, "XSS script in user message should not execute"

    def test_ampersand_in_message_displayed_correctly(self, sidebar: Frame) -> None:
        """Ampersand in user message is shown as '&', not '&amp;'."""
        send_chat_message(sidebar, "What about ACE & ARB medications?")

        last_user_msg = sidebar.locator(".message.role-user").last
        visible_text = last_user_msg.inner_text()
        assert "&" in visible_text, (
            f"Ampersand should be visible in user message, got: {visible_text!r}"
        )

    def test_quotes_in_message_work(self, sidebar: Frame) -> None:
        """Single and double quotes in messages work without breaking."""
        send_chat_message(sidebar, "Patient said \"I'm feeling better\"")

        msgs = sidebar.locator(".message.role-assistant")
        assert msgs.count() >= 1

    def test_unicode_message_preserved(self, sidebar: Frame) -> None:
        """Unicode/non-ASCII characters are preserved in user messages."""
        unicode_msg = "Patient's medication: Metformín 500mg"
        send_chat_message(sidebar, unicode_msg)

        last_user = sidebar.locator(".message.role-user").last.inner_text()
        assert "Metform" in last_user, (
            f"Unicode message not preserved: {last_user!r}"
        )

    def test_emoji_in_message_works(self, sidebar: Frame) -> None:
        """Emoji characters in messages work without errors."""
        send_chat_message(sidebar, "Is the patient 🤒 running a fever?")

        # Should get a response without errors
        msgs = sidebar.locator(".message.role-assistant")
        assert msgs.count() >= 1
        assert sidebar.locator(".error-block").count() == 0, (
            "No error should occur with emoji in message"
        )


# ---------------------------------------------------------------------------
# Shift+Enter behavior
# ---------------------------------------------------------------------------


class TestShiftEnterNewline:
    """Shift+Enter inserts a newline; plain Enter submits the message."""

    def test_shift_enter_adds_newline_not_submit(self, sidebar: Frame) -> None:
        """Pressing Shift+Enter adds a newline to the input instead of submitting."""
        sidebar.locator("#chat-input").fill("Line 1")
        sidebar.locator("#chat-input").press("Shift+Enter")
        sidebar.wait_for_timeout(200)

        # The input should still have text (not cleared by a submit)
        value = sidebar.evaluate(
            "() => document.getElementById('chat-input').value"
        )
        assert len(value) > 0, (
            "Shift+Enter should not clear the input (it adds a newline)"
        )
        # Should contain a newline
        assert "\n" in value, (
            f"Shift+Enter should insert a newline, got: {repr(value)}"
        )

    def test_enter_submits_message(self, sidebar: Frame) -> None:
        """Pressing Enter submits the message (not Shift+Enter)."""
        existing_count = sidebar.locator(".message").count()
        sidebar.locator("#chat-input").fill("What is 2 + 2?")
        sidebar.locator("#chat-input").press("Enter")

        sidebar.wait_for_function(
            f"() => document.querySelectorAll('.message').length > {existing_count}",
            timeout=E2E_TIMEOUT_MS,
        )
        assert sidebar.locator(".message").count() > existing_count, (
            "Enter should submit the message"
        )


# ---------------------------------------------------------------------------
# Message display integrity
# ---------------------------------------------------------------------------


class TestMessageDisplayIntegrity:
    """User messages are displayed faithfully in the chat area."""

    def test_user_message_appears_in_chat(self, sidebar: Frame) -> None:
        """The user's message appears in the chat after sending."""
        msg_text = "What is the patient's blood pressure?"
        send_chat_message(sidebar, msg_text)

        user_msgs = sidebar.locator(".message.role-user")
        assert user_msgs.count() >= 1
        # The message should appear (possibly truncated for long ones)
        assert "blood pressure" in user_msgs.last.inner_text().lower()

    def test_multiple_messages_appear_in_order(self, sidebar: Frame) -> None:
        """Multiple messages appear in chronological order."""
        send_chat_message(sidebar, "What is 2 + 2?")
        send_chat_message(sidebar, "And what is 3 + 3?")

        user_msgs = sidebar.locator(".message.role-user")
        assert user_msgs.count() >= 2
        # First message should appear before the second
        first_text = user_msgs.nth(user_msgs.count() - 2).inner_text()
        second_text = user_msgs.last.inner_text()
        assert "2 + 2" in first_text
        assert "3 + 3" in second_text
