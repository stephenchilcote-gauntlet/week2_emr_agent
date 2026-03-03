"""Message metadata E2E tests.

Tests that verify assistant message metadata display:
  - Latency badge (.meta element) shows "{seconds}s" after every reply
  - Latency value is a positive number
  - Tool activity details element appears when tools were called
  - Tool name is human-readable (display name, not raw tool name)
  - History item preview shows the first user message text
  - History item meta shows patient name
  - History item for current session has "active" class

These tests use real LLM calls for latency/tool metadata and evaluate()
for direct state inspection.
"""

from __future__ import annotations

import re

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
    """Sidebar with one assistant reply."""
    page.set_default_timeout(E2E_TIMEOUT_MS)
    openemr_login(page)
    select_patient(page, PATIENT_PID, PATIENT_NAME)
    sidebar = get_sidebar_frame(page)
    send_chat_message(sidebar, "What is 2 + 2?")
    return sidebar


@pytest.fixture
def sidebar_after_tool_reply(page: Page) -> Frame:
    """Sidebar with a reply that required tool calls (patient data lookup)."""
    page.set_default_timeout(E2E_TIMEOUT_MS)
    openemr_login(page)
    select_patient(page, PATIENT_PID, PATIENT_NAME)
    sidebar = get_sidebar_frame(page)
    # This message should trigger tool calls (patient data lookup)
    send_chat_message(sidebar, "What allergies does this patient have?")
    return sidebar


# ---------------------------------------------------------------------------
# Latency badge
# ---------------------------------------------------------------------------


class TestLatencyBadge:
    """Every assistant reply has a .meta element showing elapsed time."""

    def test_meta_element_present(self, sidebar_after_reply: Frame) -> None:
        """.meta element appears on the last assistant message."""
        meta = sidebar_after_reply.locator(".message.role-assistant .meta").last
        expect(meta).to_be_visible()

    def test_latency_format(self, sidebar_after_reply: Frame) -> None:
        """Latency is displayed as '{number}s' (e.g., '2.3s')."""
        meta_text = sidebar_after_reply.locator(".message.role-assistant .meta").last.inner_text()
        # Extract the latency part (first token before any "·")
        latency_part = meta_text.split("·")[0].strip()
        assert re.match(r"^\d+\.\d+s$", latency_part), (
            f"Expected latency like '1.5s', got: {latency_part!r} in {meta_text!r}"
        )

    def test_latency_is_positive(self, sidebar_after_reply: Frame) -> None:
        """Latency value is greater than zero."""
        meta_text = sidebar_after_reply.locator(".message.role-assistant .meta").last.inner_text()
        latency_part = meta_text.split("·")[0].strip()
        seconds = float(latency_part.rstrip("s"))
        assert seconds > 0, f"Latency should be positive, got: {seconds}"

    def test_meta_present_on_all_messages(self, sidebar_after_reply: Frame) -> None:
        """All assistant messages in a multi-reply session have .meta elements."""
        # sidebar_after_reply already has one message; send another
        send_chat_message(sidebar_after_reply, "And what is 3 + 3?")

        all_msgs = sidebar_after_reply.locator(".message.role-assistant")
        meta_divs = sidebar_after_reply.locator(".message.role-assistant .meta")
        assert all_msgs.count() == meta_divs.count(), (
            "Every assistant message should have exactly one .meta element"
        )


# ---------------------------------------------------------------------------
# Tool activity details
# ---------------------------------------------------------------------------


class TestToolActivityDetails:
    """Tool calls are visible in message metadata when tools were used."""

    def test_tool_activity_present_for_tool_call(
        self, sidebar_after_tool_reply: Frame,
    ) -> None:
        """When tools are called, the .activity details element appears."""
        activity = sidebar_after_tool_reply.locator(
            ".message.role-assistant .activity"
        ).last
        # The activity element should exist (the agent used tools for patient lookup)
        assert activity.count() > 0 or sidebar_after_tool_reply.evaluate("""() => {
            const msgs = document.querySelectorAll('.message.role-assistant')
            return Array.from(msgs).some(m => m.querySelector('.activity'))
        }"""), "Tool activity details should be present for a tool-calling response"

    def test_tool_name_in_meta_is_readable(
        self, sidebar_after_tool_reply: Frame,
    ) -> None:
        """Tool names in .meta are human-readable display names, not raw names."""
        meta_text = sidebar_after_tool_reply.locator(
            ".message.role-assistant .meta"
        ).last.inner_text()
        # Should NOT contain raw snake_case tool names
        raw_names = ["get_patient_info", "fhir_read", "openemr_api"]
        for raw in raw_names:
            assert raw not in meta_text, (
                f"Raw tool name '{raw}' should not appear in .meta, got: {meta_text!r}"
            )


# ---------------------------------------------------------------------------
# History item content
# ---------------------------------------------------------------------------


class TestHistoryItemContent:
    """History list items show the correct preview and metadata."""

    def test_history_item_preview_shows_first_message(
        self, sidebar_after_reply: Frame,
    ) -> None:
        """The history item for the current session shows the first user message."""
        sidebar_after_reply.locator("#history-toggle").dispatch_event("click")
        sidebar_after_reply.wait_for_timeout(500)

        items = sidebar_after_reply.locator(".history-item")
        assert items.count() >= 1

        # The current session should show the first message as preview
        first_item = items.first
        preview = first_item.locator(".history-item-preview").inner_text()
        assert len(preview.strip()) > 0, (
            "History item preview should not be empty after a message was sent"
        )
        assert preview != "(empty)", (
            "History item should show the actual message, not '(empty)'"
        )

    def test_history_item_meta_shows_patient(
        self, sidebar_after_reply: Frame,
    ) -> None:
        """History item meta line shows the patient name."""
        sidebar_after_reply.locator("#history-toggle").dispatch_event("click")
        sidebar_after_reply.wait_for_timeout(500)

        items = sidebar_after_reply.locator(".history-item")
        assert items.count() >= 1

        meta = items.first.locator(".history-item-meta").inner_text()
        assert PATIENT_NAME in meta or str(PATIENT_PID) in meta, (
            f"History item meta should contain patient name or ID, got: {meta!r}"
        )

    def test_current_session_has_active_class(
        self, sidebar_after_reply: Frame,
    ) -> None:
        """The current session in the history list has the 'active' CSS class.

        Note: loadSessionList() runs async after the chat response (in the
        try block of sendMessage, after renderMessage). We wait 3s after
        opening history to let the list refresh complete.
        """
        sidebar_after_reply.locator("#history-toggle").dispatch_event("click")
        # Wait for the async loadSessionList() to complete and re-render the list
        sidebar_after_reply.wait_for_timeout(3000)

        has_active = sidebar_after_reply.evaluate("""() => {
            const items = document.querySelectorAll('.history-item')
            return Array.from(items).some(item => item.classList.contains('active'))
        }""")
        assert has_active, (
            "Current session in history list should have 'active' class"
        )

    def test_history_item_click_loads_session(
        self, page: Page,
    ) -> None:
        """Clicking a history item loads that session's messages."""
        page.set_default_timeout(E2E_TIMEOUT_MS)
        openemr_login(page)
        select_patient(page, PATIENT_PID, PATIENT_NAME)
        sidebar = get_sidebar_frame(page)

        # Session A: send a message with a distinctive marker
        send_chat_message(sidebar, "What is 2 + 2? Reply with just the number.")
        session_a_id = sidebar.evaluate("() => window.__sidebarApp.state.sessionID")

        # Create session B
        sidebar.locator("#new-conversation").dispatch_event("click")
        sidebar.wait_for_timeout(2000)

        # Open history and click session A
        sidebar.locator("#history-toggle").dispatch_event("click")
        sidebar.wait_for_timeout(1000)

        items = sidebar.locator(".history-item")
        if items.count() < 2:
            pytest.skip("Need at least 2 history items")

        # Click the non-active session (session A)
        for i in range(items.count()):
            if not sidebar.evaluate(
                f"() => document.querySelectorAll('.history-item')[{i}].classList.contains('active')"
            ):
                items.nth(i).dispatch_event("click")
                break

        sidebar.wait_for_timeout(2000)

        # Session ID should have changed back to session A
        new_id = sidebar.evaluate("() => window.__sidebarApp.state.sessionID")
        assert new_id == session_a_id, (
            f"Should have loaded session A ({session_a_id}), got: {new_id}"
        )

        # Messages from session A should be visible
        msgs = sidebar.locator(".message.role-user")
        assert msgs.count() >= 1
        assert "2 + 2" in msgs.first.inner_text()
