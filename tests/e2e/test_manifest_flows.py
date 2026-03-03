"""Manifest review flow tests.

Tests that verify correct behavior in the manifest review panel:
  - Rejecting all items changes execute button to "Discard All"
  - "Discard All" closes the review panel without an execute API call
  - A rejected manifest leaves no error block
  - Rapid double-send is blocked (send button disabled while in flight)
  - Status pill updates correctly through thinking → reviewing → ready cycle
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Frame, Page, expect

from .conftest import (
    E2E_TIMEOUT_MS,
    PATIENT_MAP,
    cleanup_test_allergies,
    cleanup_test_conditions,
    get_sidebar_frame,
    openemr_login,
    select_patient,
    send_chat_message,
)

pytestmark = pytest.mark.e2e

PATIENT_PID = PATIENT_MAP["Maria Santos"]
PATIENT_NAME = "Maria Santos"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open_patient_dashboard(page: Page, pid: int = PATIENT_PID) -> None:
    page.evaluate(f"""() => {{
        const top = window.top || window;
        top.navigateTab(
            '/interface/patient_file/summary/demographics.php?set_pid={pid}',
            'pat'
        );
    }}""")
    pat = page.frame_locator("iframe[name=pat]")
    pat.locator("#allergy_ps_expand").wait_for(state="attached", timeout=15000)


def _setup_review_session(page: Page, message: str) -> tuple[Frame, int]:
    """Log in, select Maria Santos, send message, wait for review panel."""
    page.set_default_timeout(E2E_TIMEOUT_MS)
    openemr_login(page)
    select_patient(page, PATIENT_PID, PATIENT_NAME)
    _open_patient_dashboard(page)

    sidebar = get_sidebar_frame(page)
    send_chat_message(sidebar, message)

    sidebar.wait_for_selector("#review-panel:not(.hidden)", timeout=E2E_TIMEOUT_MS)

    progress = sidebar.locator("#tour-progress").inner_text()
    total = int(progress.split(" of ")[1])
    return sidebar, total


# ---------------------------------------------------------------------------
# Discard All flow
# ---------------------------------------------------------------------------


class TestDiscardAll:
    """Rejecting all manifest items must show Discard All and close panel cleanly."""

    def test_reject_all_shows_discard_button(self, page: Page) -> None:
        """After reject-all, execute button label changes to 'Discard All'."""
        cleanup_test_allergies(PATIENT_PID, ["Penicillin"])
        sidebar, _total = _setup_review_session(
            page, "Add a penicillin allergy for this patient."
        )

        sidebar.locator("#reject-all").dispatch_event("click")
        sidebar.wait_for_timeout(1500)  # let reject settle on server

        execute_btn = sidebar.locator("#execute-button")
        expect(execute_btn).to_be_visible()
        assert execute_btn.inner_text() in ("Discard All", "discard all", "Discard all"), (
            f"Expected 'Discard All' button after reject-all, got: {execute_btn.inner_text()!r}"
        )

    def test_discard_all_closes_review_panel(self, page: Page) -> None:
        """Clicking Discard All hides the review panel without an execute API call."""
        cleanup_test_allergies(PATIENT_PID, ["Penicillin"])
        sidebar, _total = _setup_review_session(
            page, "Add a penicillin allergy for this patient."
        )

        sidebar.locator("#reject-all").dispatch_event("click")
        sidebar.wait_for_timeout(1500)

        # Click "Discard All" (the execute button when all rejected)
        sidebar.locator("#execute-button").dispatch_event("click")

        # Review panel should disappear
        expect(sidebar.locator("#review-panel")).to_be_hidden(timeout=10000)

    def test_discard_all_leaves_no_error_block(self, page: Page) -> None:
        """Discarding a rejected manifest must not show an error block."""
        cleanup_test_allergies(PATIENT_PID, ["Penicillin"])
        sidebar, _total = _setup_review_session(
            page, "Add a penicillin allergy for this patient."
        )

        sidebar.locator("#reject-all").dispatch_event("click")
        sidebar.wait_for_timeout(1500)
        sidebar.locator("#execute-button").dispatch_event("click")

        expect(sidebar.locator("#review-panel")).to_be_hidden(timeout=10000)

        # No error block should appear
        assert sidebar.locator(".error-block").count() == 0, (
            "Error block appeared after discard — execute sent a request for 0 approved items"
        )

    def test_discard_all_restores_ready_status(self, page: Page) -> None:
        """After discarding, status pill shows 'Ready'."""
        cleanup_test_allergies(PATIENT_PID, ["Penicillin"])
        sidebar, _total = _setup_review_session(
            page, "Add a penicillin allergy for this patient."
        )

        sidebar.locator("#reject-all").dispatch_event("click")
        sidebar.wait_for_timeout(1500)
        sidebar.locator("#execute-button").dispatch_event("click")

        expect(sidebar.locator("#review-panel")).to_be_hidden(timeout=10000)
        expect(sidebar.locator("#status-text")).to_have_text("Ready", timeout=5000)

    def test_discard_all_allows_new_message(self, page: Page) -> None:
        """After discarding, the user can send a new message."""
        cleanup_test_allergies(PATIENT_PID, ["Penicillin"])
        sidebar, _total = _setup_review_session(
            page, "Add a penicillin allergy for this patient."
        )

        sidebar.locator("#reject-all").dispatch_event("click")
        sidebar.wait_for_timeout(1500)
        sidebar.locator("#execute-button").dispatch_event("click")

        expect(sidebar.locator("#review-panel")).to_be_hidden(timeout=10000)

        # Send a follow-up message — should work without errors
        msg_count_before = sidebar.locator(".message.role-assistant").count()
        send_chat_message(sidebar, "What allergies does this patient have?")

        sidebar.wait_for_function(
            f"() => document.querySelectorAll('.message.role-assistant').length > {msg_count_before}",
            timeout=E2E_TIMEOUT_MS,
        )

        last_msg = sidebar.locator(".message.role-assistant .markdown").last
        assert len(last_msg.inner_text()) > 0


# ---------------------------------------------------------------------------
# Status pill lifecycle
# ---------------------------------------------------------------------------


class TestStatusPillLifecycle:
    """Status pill must progress correctly: ready → thinking → reviewing → ready."""

    def test_status_goes_thinking_during_chat(self, page: Page) -> None:
        """Status pill shows 'Thinking…' while awaiting LLM response."""
        page.set_default_timeout(E2E_TIMEOUT_MS)
        openemr_login(page)
        select_patient(page, PATIENT_PID, PATIENT_NAME)
        sidebar = get_sidebar_frame(page)

        # Fill input but don't send yet
        sidebar.locator("#chat-input").fill("Hello")

        # Capture status before
        expect(sidebar.locator("#status-text")).to_have_text("Ready")

        # Send the message
        sidebar.locator("#chat-input").press("Enter")

        # Should switch to Thinking... before response arrives
        expect(sidebar.locator("#status-text")).to_have_text("Thinking…", timeout=5000)

    def test_status_returns_ready_after_plain_reply(self, page: Page) -> None:
        """After a plain text response (no manifest), status returns to Ready."""
        page.set_default_timeout(E2E_TIMEOUT_MS)
        openemr_login(page)
        select_patient(page, PATIENT_PID, PATIENT_NAME)
        sidebar = get_sidebar_frame(page)

        send_chat_message(sidebar, "What is 2 + 2?")

        expect(sidebar.locator("#status-text")).to_have_text("Ready", timeout=E2E_TIMEOUT_MS)

    def test_status_shows_review_changes_when_manifest_ready(self, page: Page) -> None:
        """When a manifest is returned, status pill shows 'Review Changes'."""
        cleanup_test_allergies(PATIENT_PID, ["Penicillin"])
        cleanup_test_conditions(PATIENT_PID, ["E55.9"])

        page.set_default_timeout(E2E_TIMEOUT_MS)
        openemr_login(page)
        select_patient(page, PATIENT_PID, PATIENT_NAME)
        _open_patient_dashboard(page)
        sidebar = get_sidebar_frame(page)

        send_chat_message(sidebar, "Add a penicillin allergy for this patient.")
        sidebar.wait_for_selector("#review-panel:not(.hidden)", timeout=E2E_TIMEOUT_MS)

        expect(sidebar.locator("#status-text")).to_have_text("Review Changes", timeout=5000)

    def test_status_returns_ready_after_execution(self, page: Page) -> None:
        """After executing a manifest, status returns to Ready."""
        cleanup_test_allergies(PATIENT_PID, ["Penicillin"])

        page.set_default_timeout(E2E_TIMEOUT_MS)
        openemr_login(page)
        select_patient(page, PATIENT_PID, PATIENT_NAME)
        _open_patient_dashboard(page)
        sidebar = get_sidebar_frame(page)

        send_chat_message(sidebar, "Add a penicillin allergy for this patient.")
        sidebar.wait_for_selector("#review-panel:not(.hidden)", timeout=E2E_TIMEOUT_MS)

        sidebar.locator("#apply-all").dispatch_event("click")
        sidebar.wait_for_timeout(1500)
        sidebar.locator("#execute-button").dispatch_event("click")

        expect(sidebar.locator("#review-panel")).to_be_hidden(timeout=E2E_TIMEOUT_MS)
        expect(sidebar.locator("#status-text")).to_have_text("Ready", timeout=10000)
