"""Chat lifecycle E2E tests.

Tests that verify the state machine around sending and receiving messages:
  - Typing indicator appears while waiting for the LLM and disappears on reply
  - Send button is disabled during thinking and re-enabled after response
  - Chat input is cleared after the message is submitted
  - Status pill progresses: Ready → Thinking… → Ready
  - Multiple sequential messages work (conversation continues correctly)
  - Enter key is blocked while in thinking phase (double-send guard)
  - Sending whitespace-only input is blocked
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
# Typing indicator
# ---------------------------------------------------------------------------


class TestTypingIndicator:
    """Typing indicator appears while LLM is responding and vanishes after."""

    def test_typing_indicator_appears_while_thinking(self, sidebar: Frame) -> None:
        """After sending a message, the typing indicator shows up."""
        sidebar.locator("#chat-input").fill("Hello")
        sidebar.locator("#chat-input").press("Enter")
        expect(sidebar.locator(".typing-indicator")).to_be_visible(timeout=10000)

    def test_typing_indicator_disappears_after_response(self, sidebar: Frame) -> None:
        """Typing indicator is gone once the assistant reply is rendered."""
        send_chat_message(sidebar, "What is 2 + 2?")
        # After send_chat_message returns, the response is complete
        expect(sidebar.locator(".typing-indicator")).to_be_hidden()

    def test_typing_indicator_not_present_before_sending(self, sidebar: Frame) -> None:
        """Typing indicator is absent on initial load (before any message)."""
        # Either hidden or not in DOM at all is acceptable
        count = sidebar.locator(".typing-indicator").count()
        if count > 0:
            expect(sidebar.locator(".typing-indicator")).to_be_hidden()


# ---------------------------------------------------------------------------
# Send button state during thinking
# ---------------------------------------------------------------------------


class TestSendButtonDuringThinking:
    """Send button must be disabled while the LLM is thinking."""

    def test_send_disabled_while_thinking(self, sidebar: Frame) -> None:
        """Send button becomes disabled immediately after submitting a message."""
        sidebar.locator("#chat-input").fill("Hello")
        sidebar.locator("#chat-input").press("Enter")
        # Should be disabled before the response arrives
        expect(sidebar.locator("#send-button")).to_be_disabled(timeout=10000)

    def test_send_reenabled_after_response(self, sidebar: Frame) -> None:
        """Send button is re-enabled (and visible with text) after response arrives."""
        send_chat_message(sidebar, "What is 2 + 2?")

        # Type something so the button becomes visible
        sidebar.locator("#chat-input").fill("Follow-up question")
        expect(sidebar.locator("#send-button")).not_to_be_disabled()

    def test_enter_blocked_while_thinking(self, sidebar: Frame) -> None:
        """Pressing Enter while in thinking phase does not send a second message."""
        # Send first message and immediately press Enter again with a second message
        sidebar.locator("#chat-input").fill("First message")
        sidebar.locator("#chat-input").press("Enter")

        # Now the phase is 'thinking'; try to send a second message via Enter
        sidebar.locator("#chat-input").fill("Second message")
        sidebar.locator("#chat-input").press("Enter")

        # Wait for first response
        sidebar.wait_for_function(
            "() => document.querySelectorAll('.message.role-assistant').length > 0",
            timeout=E2E_TIMEOUT_MS,
        )

        # Should be exactly ONE user message (second Enter was blocked)
        user_msgs = sidebar.locator(".message.role-user")
        assert user_msgs.count() == 1, (
            f"Expected 1 user message (Enter blocked), got {user_msgs.count()}"
        )


# ---------------------------------------------------------------------------
# Chat input state after sending
# ---------------------------------------------------------------------------


class TestChatInputAfterSend:
    """Chat input is cleared and send button hidden after submitting."""

    def test_input_cleared_after_send(self, sidebar: Frame) -> None:
        """Chat input textarea is empty right after pressing Enter."""
        sidebar.locator("#chat-input").fill("Hello there")
        sidebar.locator("#chat-input").press("Enter")

        # Input should be cleared immediately on submit
        sidebar.wait_for_timeout(200)
        value = sidebar.locator("#chat-input").input_value()
        assert value == "", f"Input should be cleared after send, got: {value!r}"

    def test_send_button_hidden_after_input_cleared(self, sidebar: Frame) -> None:
        """After input is cleared by sending, send button loses 'visible' class."""
        sidebar.locator("#chat-input").fill("Hello there")
        sidebar.locator("#chat-input").press("Enter")

        sidebar.wait_for_timeout(200)
        visible = sidebar.evaluate(
            "() => document.getElementById('send-button')?.classList.contains('visible') ?? false"
        )
        assert not visible, "Send button should not be visible right after input is cleared"


# ---------------------------------------------------------------------------
# Status pill during chat
# ---------------------------------------------------------------------------


class TestStatusPillDuringChat:
    """Status pill reflects the correct phase during a full send/receive cycle."""

    def test_status_starts_ready(self, sidebar: Frame) -> None:
        """Status pill is 'Ready' before any messages are sent."""
        expect(sidebar.locator("#status-text")).to_have_text("Ready")

    def test_status_thinking_immediately_after_send(self, sidebar: Frame) -> None:
        """Status changes to 'Thinking…' right after sending a message."""
        sidebar.locator("#chat-input").fill("Hello")
        sidebar.locator("#chat-input").press("Enter")
        expect(sidebar.locator("#status-text")).to_have_text("Thinking…", timeout=5000)

    def test_status_returns_to_ready_after_plain_reply(self, sidebar: Frame) -> None:
        """Status returns to 'Ready' once a plain text response arrives."""
        send_chat_message(sidebar, "What is 2 + 2?")
        expect(sidebar.locator("#status-text")).to_have_text("Ready", timeout=E2E_TIMEOUT_MS)


# ---------------------------------------------------------------------------
# Sequential messages
# ---------------------------------------------------------------------------


class TestSequentialMessages:
    """Multiple messages can be sent in sequence without issues."""

    def test_two_messages_produce_two_responses(self, sidebar: Frame) -> None:
        """Sending two messages in sequence yields two assistant replies."""
        send_chat_message(sidebar, "What is 2 + 2?")
        send_chat_message(sidebar, "And what is 3 + 3?")

        assistant_msgs = sidebar.locator(".message.role-assistant")
        assert assistant_msgs.count() == 2, (
            f"Expected 2 assistant messages, got {assistant_msgs.count()}"
        )

    def test_second_response_is_distinct(self, sidebar: Frame) -> None:
        """The second response is different from the first (not a duplicate)."""
        send_chat_message(sidebar, "What is 2 + 2?")
        send_chat_message(sidebar, "What is 10 + 10?")

        msgs = sidebar.locator(".message.role-assistant")
        assert msgs.count() == 2
        first_text = msgs.nth(0).inner_text()
        second_text = msgs.nth(1).inner_text()
        assert first_text != second_text, (
            "Both assistant responses are identical — second may be cached or duplicated"
        )

    def test_user_messages_preserved_in_order(self, sidebar: Frame) -> None:
        """User messages appear in the correct order in the chat area."""
        send_chat_message(sidebar, "First question: what is 1 + 1?")
        send_chat_message(sidebar, "Second question: what is 2 + 2?")

        user_msgs = sidebar.locator(".message.role-user")
        assert user_msgs.count() == 2
        first_text = user_msgs.nth(0).inner_text()
        second_text = user_msgs.nth(1).inner_text()
        assert "First question" in first_text, f"Wrong order, first msg: {first_text!r}"
        assert "Second question" in second_text, f"Wrong order, second msg: {second_text!r}"
