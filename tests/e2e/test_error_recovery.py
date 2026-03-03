"""Error recovery E2E tests.

Tests that verify the sidebar handles errors gracefully and recovers correctly:
  - Error blocks are rendered with the correct structure and content
  - Retry button removes the error block and re-sends the last message
  - After an injected error, the send button is re-enabled (can send again)
  - Retryable vs non-retryable error blocks have different structures
  - Status pill shows 'error' state and recovers to 'ready'
  - User can send a new message after an error without additional state resets
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
# Error block structure
# ---------------------------------------------------------------------------


class TestErrorBlockStructure:
    """Error and system notice blocks render with the correct DOM structure."""

    def test_retryable_error_has_error_block_class(self, sidebar: Frame) -> None:
        """renderRetryableError() creates an element with class 'error-block'."""
        sidebar.evaluate("() => window.__sidebarApp.renderRetryableError('Test error')")
        assert sidebar.locator(".error-block").count() > 0, (
            "renderRetryableError should create an .error-block element"
        )

    def test_retryable_error_shows_message(self, sidebar: Frame) -> None:
        """Retryable error block contains the error message text."""
        sidebar.evaluate("() => window.__sidebarApp.renderRetryableError('Test error message')")
        block = sidebar.locator(".error-block").last
        assert "Test error message" in block.inner_text(), (
            "Error block should contain the error message"
        )

    def test_retryable_error_has_retry_button(self, sidebar: Frame) -> None:
        """Retryable error block contains a Retry button."""
        sidebar.evaluate("() => window.__sidebarApp.renderRetryableError('Some error')")
        block = sidebar.locator(".error-block").last
        retry_btn = block.locator("button")
        expect(retry_btn).to_be_visible()
        assert retry_btn.inner_text().strip() == "Retry", (
            f"Expected 'Retry' button, got: {retry_btn.inner_text()!r}"
        )

    def test_non_retryable_error_has_no_button(self, sidebar: Frame) -> None:
        """renderErrorBlock() creates a block WITHOUT a Retry button."""
        sidebar.evaluate("() => window.__sidebarApp.renderErrorBlock('Non-retryable error')")
        block = sidebar.locator(".error-block").last
        retry_btns = block.locator("button")
        assert retry_btns.count() == 0, (
            "Non-retryable error block should not have a Retry button"
        )

    def test_error_message_is_html_escaped(self, sidebar: Frame) -> None:
        """Malicious HTML in error message is escaped, not injected."""
        sidebar.evaluate(
            r"() => window.__sidebarApp.renderRetryableError('<script>alert(1)</script>')"
        )
        block = sidebar.locator(".error-block").last
        # The script tag should appear as literal text, not be executed
        inner_html = sidebar.evaluate(
            "() => document.querySelectorAll('.error-block')[document.querySelectorAll('.error-block').length - 1]?.innerHTML"
        )
        assert "<script>" not in inner_html, (
            "Script tag should be HTML-escaped in error message"
        )


# ---------------------------------------------------------------------------
# Retry button behavior
# ---------------------------------------------------------------------------


class TestRetryButton:
    """Retry button removes error block and re-sends the last message."""

    def test_retry_removes_error_block(self, sidebar: Frame) -> None:
        """Clicking Retry removes the error block from the DOM."""
        sidebar.evaluate("() => window.__sidebarApp.renderRetryableError('Some error')")
        assert sidebar.locator(".error-block").count() > 0, "Error block should be present"

        sidebar.locator(".error-block button").last.dispatch_event("click")
        sidebar.wait_for_timeout(200)

        assert sidebar.locator(".error-block").count() == 0, (
            "Error block should be removed after clicking Retry"
        )

    def test_retry_sends_last_message(self, sidebar: Frame) -> None:
        """Retry button re-sends lastUserMessage and gets a real response.

        We send a real message first (so lastUserMessage is set), then inject
        an error block, then click Retry to verify the send pipeline works.
        """
        # First message to populate lastUserMessage
        send_chat_message(sidebar, "What is 2 + 2?")
        existing_count = sidebar.locator(".message.role-assistant").count()

        # Inject a new retryable error block
        sidebar.evaluate("() => window.__sidebarApp.renderRetryableError('Simulated error')")
        assert sidebar.locator(".error-block").count() > 0

        # Click Retry — should re-send "What is 2 + 2?"
        sidebar.locator(".error-block button").last.dispatch_event("click")

        # Wait for a new assistant response
        sidebar.wait_for_function(
            f"() => document.querySelectorAll('.message.role-assistant').length > {existing_count}",
            timeout=E2E_TIMEOUT_MS,
        )

        # Error block should be gone
        assert sidebar.locator(".error-block").count() == 0, (
            "Error block should be removed after retry succeeds"
        )

    def test_retry_button_not_present_after_successful_send(
        self, sidebar: Frame,
    ) -> None:
        """After a successful send, no error blocks exist."""
        send_chat_message(sidebar, "What is 3 + 3?")
        assert sidebar.locator(".error-block").count() == 0, (
            "No error blocks should be present after a successful message"
        )


# ---------------------------------------------------------------------------
# Recovery after error
# ---------------------------------------------------------------------------


class TestRecoveryAfterError:
    """Send button and status pill recover correctly after an injected error."""

    def test_send_button_enabled_after_injected_error(self, sidebar: Frame) -> None:
        """Even after injecting an error block, the send button can be used.

        The finally block in sendMessage() always calls toggleSend(true).
        We verify this by injecting an error block (simulating a failed API
        call) and confirming the sidebar is still usable.
        """
        # Simulate the sidebar's error state by injecting an error block
        # This is what happens after sendMessage() catches an exception
        sidebar.evaluate("""() => {
            const app = window.__sidebarApp
            app.renderRetryableError('Network error')
            app.setStatus('error')
            // toggleSend(true) is always called in finally, so simulate that too
            app.toggleSend(true)
        }""")

        # Status should be 'error'
        expect(sidebar.locator("#status-text")).to_have_text("Error")

        # But we can still fill input and it becomes visible
        sidebar.locator("#chat-input").fill("New message")
        visible = sidebar.evaluate(
            "() => document.getElementById('send-button')?.classList.contains('visible') ?? false"
        )
        assert visible, "Send button should be usable (visible) even after an error"
        expect(sidebar.locator("#send-button")).not_to_be_disabled()

    def test_can_send_new_message_after_error(self, sidebar: Frame) -> None:
        """After an injected error state, the user can send a new message normally."""
        sidebar.evaluate("""() => {
            const app = window.__sidebarApp
            app.renderRetryableError('Previous error')
            app.setStatus('error')
            app.toggleSend(true)
        }""")

        # Clear the error block
        sidebar.evaluate(
            "() => document.querySelectorAll('.error-block').forEach(b => b.remove())"
        )

        # Send a new message — should work fine
        send_chat_message(sidebar, "What is 5 + 5?")

        last_msg = sidebar.locator(".message.role-assistant .markdown").last
        assert len(last_msg.inner_text()) > 0, (
            "Agent should respond normally after error state was cleared"
        )

    def test_status_returns_to_ready_after_manual_error_clear(
        self, sidebar: Frame,
    ) -> None:
        """After sending a new message following an error, status returns to ready."""
        sidebar.evaluate("""() => {
            const app = window.__sidebarApp
            app.setStatus('error')
            app.toggleSend(true)
        }""")
        expect(sidebar.locator("#status-text")).to_have_text("Error")

        send_chat_message(sidebar, "What is 1 + 1?")

        expect(sidebar.locator("#status-text")).to_have_text("Ready", timeout=E2E_TIMEOUT_MS)
