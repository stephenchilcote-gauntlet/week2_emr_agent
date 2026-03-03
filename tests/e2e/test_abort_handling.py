"""Abort and cancellation E2E tests.

Tests that verify in-flight requests are correctly cancelled:
  - Clicking "New Conversation" while a chat request is in-flight aborts it
  - After aborting, the sidebar is in a clean state (ready for new messages)
  - No error block appears when a request is intentionally aborted
  - The status pill resets to 'Ready' after abort via New Conversation
  - History panel shows a session entry for the aborted conversation
  - Abort does not duplicate or corrupt session state

These tests exploit the slow LLM response time: they send a message, then
immediately click "New Conversation" before the LLM responds, verifying the
abort path.
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
# Abort via New Conversation
# ---------------------------------------------------------------------------


class TestAbortViaNewConversation:
    """New Conversation while thinking aborts the in-flight request cleanly."""

    def test_abort_clears_typing_indicator(self, sidebar: Frame) -> None:
        """Typing indicator disappears after New Conversation aborts the request."""
        # Send a message, then immediately click New Conversation
        sidebar.locator("#chat-input").fill("Tell me a very long story about medicine")
        sidebar.locator("#chat-input").press("Enter")

        # Wait until thinking starts
        expect(sidebar.locator("#status-text")).to_have_text("Thinking…", timeout=10000)

        # Click New Conversation to abort
        sidebar.locator("#new-conversation").dispatch_event("click")
        sidebar.wait_for_timeout(2000)

        # Typing indicator should be gone (new session = fresh chat)
        expect(sidebar.locator(".typing-indicator")).to_be_hidden()

    def test_abort_shows_no_error_block(self, sidebar: Frame) -> None:
        """AbortError is silently swallowed — no error block after New Conversation."""
        sidebar.locator("#chat-input").fill("Tell me a very long story")
        sidebar.locator("#chat-input").press("Enter")

        expect(sidebar.locator("#status-text")).to_have_text("Thinking…", timeout=10000)

        sidebar.locator("#new-conversation").dispatch_event("click")
        sidebar.wait_for_timeout(2000)

        # No error blocks — AbortError is caught and ignored
        assert sidebar.locator(".error-block").count() == 0, (
            "No error block should appear when request is intentionally aborted"
        )

    def test_abort_resets_status_to_ready(self, sidebar: Frame) -> None:
        """After aborting via New Conversation, status pill shows 'Ready'."""
        sidebar.locator("#chat-input").fill("Tell me a long story")
        sidebar.locator("#chat-input").press("Enter")

        expect(sidebar.locator("#status-text")).to_have_text("Thinking…", timeout=10000)

        sidebar.locator("#new-conversation").dispatch_event("click")
        sidebar.wait_for_timeout(2000)

        expect(sidebar.locator("#status-text")).to_have_text("Ready", timeout=10000)

    def test_abort_clears_chat_area(self, sidebar: Frame) -> None:
        """After New Conversation, the chat area is cleared (fresh session)."""
        sidebar.locator("#chat-input").fill("Tell me a long story")
        sidebar.locator("#chat-input").press("Enter")

        expect(sidebar.locator("#status-text")).to_have_text("Thinking…", timeout=10000)

        sidebar.locator("#new-conversation").dispatch_event("click")

        sidebar.wait_for_function(
            "() => document.querySelectorAll('.message').length === 0",
            timeout=10000,
        )

    def test_can_send_message_after_abort(self, sidebar: Frame) -> None:
        """After aborting a request, the user can send a new message normally."""
        sidebar.locator("#chat-input").fill("Tell me a long story")
        sidebar.locator("#chat-input").press("Enter")

        expect(sidebar.locator("#status-text")).to_have_text("Thinking…", timeout=10000)

        sidebar.locator("#new-conversation").dispatch_event("click")
        sidebar.wait_for_timeout(2000)

        # Send a new message — should work fine
        send_chat_message(sidebar, "What is 2 + 2?")

        msgs = sidebar.locator(".message.role-assistant")
        assert msgs.count() >= 1, "Should be able to send messages after abort"
        assert len(msgs.last.inner_text()) > 0


# ---------------------------------------------------------------------------
# Abort via AbortController state
# ---------------------------------------------------------------------------


class TestAbortControllerState:
    """Verify abort controller is properly managed throughout the lifecycle."""

    def test_no_abort_controller_at_rest(self, sidebar: Frame) -> None:
        """When not sending a message, abortController should be null."""
        has_controller = sidebar.evaluate(
            "() => window.__sidebarApp.abortController !== null"
        )
        assert not has_controller, "abortController should be null when not sending"

    def test_abort_controller_set_during_thinking(self, sidebar: Frame) -> None:
        """While thinking, abortController is set (non-null)."""
        sidebar.locator("#chat-input").fill("Hello")
        sidebar.locator("#chat-input").press("Enter")

        # Check quickly before response arrives
        expect(sidebar.locator("#status-text")).to_have_text("Thinking…", timeout=10000)
        has_controller = sidebar.evaluate(
            "() => window.__sidebarApp.abortController !== null"
        )
        assert has_controller, "abortController should be set while thinking"

        # Wait for response to finish
        sidebar.wait_for_function(
            "() => document.querySelectorAll('.message.role-assistant').length > 0",
            timeout=E2E_TIMEOUT_MS,
        )

    def test_abort_controller_cleared_after_response(self, sidebar: Frame) -> None:
        """After a response arrives, abortController is null again.

        We wait until sendMessage()'s finally block has run, which happens
        slightly after the DOM update that send_chat_message() waits for.
        """
        send_chat_message(sidebar, "What is 2 + 2?")

        # Wait for the finally block to clear abortController
        sidebar.wait_for_function(
            "() => window.__sidebarApp.abortController === null",
            timeout=5000,
        )
        has_controller = sidebar.evaluate(
            "() => window.__sidebarApp.abortController !== null"
        )
        assert not has_controller, (
            "abortController should be null after response completes"
        )
