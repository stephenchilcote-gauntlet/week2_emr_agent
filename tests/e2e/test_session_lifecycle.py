"""Session lifecycle E2E tests.

Tests that verify correct behavior during state transitions that are easy to
get wrong:
  - Double-clicking Execute should not produce an error (execute button must
    be disabled while the API call is in-flight)
  - New Conversation while review panel is open should clear the panel and
    chat area cleanly
  - Patient switch while review panel is open should clear the manifest and
    start a fresh session
  - Loading a history session should not leak a stale manifest into the UI
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
PATIENT_PID_B = PATIENT_MAP["James Kowalski"]
PATIENT_NAME_B = "James Kowalski"


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
    """Log in, select Maria Santos, send message, wait for review panel.

    Returns (sidebar_frame, manifest_item_count).
    """
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
# Double-execute guard
# ---------------------------------------------------------------------------


class TestDoubleExecuteGuard:
    """Execute button must be disabled while the API call is in-flight.

    Clicking it twice should not produce an error block — the second click
    must be ignored.
    """

    def test_execute_button_disabled_during_execution(self, page: Page) -> None:
        """Execute button is not clickable while execution is in progress."""
        cleanup_test_allergies(PATIENT_PID, ["Penicillin"])
        sidebar, _total = _setup_review_session(
            page, "Add a penicillin allergy for this patient."
        )

        sidebar.locator("#apply-all").dispatch_event("click")

        # Click execute — button should become disabled/hidden immediately
        sidebar.locator("#execute-button").dispatch_event("click")

        # The button should be disabled before the API call returns
        expect(sidebar.locator("#execute-button")).to_be_disabled(timeout=3000)

        # Wait for execution to fully complete so cleanup in subsequent tests
        # doesn't race with an in-flight execute request still writing to DB.
        expect(sidebar.locator("#review-panel")).to_be_hidden(timeout=E2E_TIMEOUT_MS)

    def test_double_execute_no_error(self, page: Page) -> None:
        """Clicking execute twice must not produce an error block.

        The second click fires while the first API call is in-flight.
        If the execute button stays enabled during execution, the server
        returns 409 Conflict and the sidebar shows an error.
        """
        cleanup_test_allergies(PATIENT_PID, ["Penicillin"])
        sidebar, _total = _setup_review_session(
            page, "Add a penicillin allergy for this patient."
        )

        sidebar.locator("#apply-all").dispatch_event("click")
        # Wait for the approve API call to settle before executing,
        # so the server has approved items when the execute arrives.
        sidebar.wait_for_timeout(1500)

        # Fire two clicks back-to-back while the first execute is in-flight
        sidebar.locator("#execute-button").dispatch_event("click")
        sidebar.locator("#execute-button").dispatch_event("click")

        # Review panel should hide (execution completed)
        expect(sidebar.locator("#review-panel")).to_be_hidden(timeout=E2E_TIMEOUT_MS)

        # No error block should appear
        assert sidebar.locator(".error-block").count() == 0, (
            "Error block appeared after double-click on execute — "
            "button was not disabled during execution"
        )


# ---------------------------------------------------------------------------
# New Conversation clears review panel
# ---------------------------------------------------------------------------


class TestNewConversationClearsManifest:
    """New Conversation button must clear the review panel immediately."""

    def test_new_conversation_hides_review_panel(self, page: Page) -> None:
        """Clicking New Conversation while review panel is open hides it."""
        cleanup_test_allergies(PATIENT_PID, ["Penicillin"])
        sidebar, _total = _setup_review_session(
            page, "Add a penicillin allergy for this patient."
        )

        expect(sidebar.locator("#review-panel")).to_be_visible()

        sidebar.locator("#new-conversation").dispatch_event("click")

        expect(sidebar.locator("#review-panel")).to_be_hidden(timeout=10000)

    def test_new_conversation_clears_chat(self, page: Page) -> None:
        """Clicking New Conversation clears the chat area."""
        cleanup_test_allergies(PATIENT_PID, ["Penicillin"])
        sidebar, _total = _setup_review_session(
            page, "Add a penicillin allergy for this patient."
        )

        # There should be at least one message before clearing
        assert sidebar.locator(".message").count() >= 1

        sidebar.locator("#new-conversation").dispatch_event("click")

        # Chat should clear within a moment (API call to create new session)
        sidebar.wait_for_function(
            "() => document.querySelectorAll('.message').length === 0",
            timeout=10000,
        )

    def test_new_conversation_changes_session_id(self, page: Page) -> None:
        """New Conversation creates a new session (different session ID)."""
        cleanup_test_allergies(PATIENT_PID, ["Penicillin"])
        sidebar, _total = _setup_review_session(
            page, "Add a penicillin allergy for this patient."
        )

        old_session_text = sidebar.locator("#session-id-row").inner_text()

        sidebar.locator("#new-conversation").dispatch_event("click")
        # Wait for new session to be created
        sidebar.wait_for_timeout(3000)

        new_session_text = sidebar.locator("#session-id-row").inner_text()
        assert new_session_text != old_session_text, (
            "Session ID did not change after New Conversation"
        )


# ---------------------------------------------------------------------------
# Patient switch clears manifest
# ---------------------------------------------------------------------------


class TestPatientSwitchClearsManifest:
    """Switching patient mid-review must discard the open manifest."""

    def test_patient_switch_hides_review_panel(self, page: Page) -> None:
        """Switching to a different patient hides the review panel."""
        cleanup_test_allergies(PATIENT_PID, ["Penicillin"])
        sidebar, _total = _setup_review_session(
            page, "Add a penicillin allergy for this patient."
        )

        expect(sidebar.locator("#review-panel")).to_be_visible()

        # Switch to a different patient
        select_patient(page, PATIENT_PID_B, PATIENT_NAME_B)

        # Review panel should disappear — manifest is for wrong patient
        expect(sidebar.locator("#review-panel")).to_be_hidden(timeout=10000)

    def test_patient_switch_updates_context_line(self, page: Page) -> None:
        """After switching patient, sidebar context line shows new patient."""
        cleanup_test_allergies(PATIENT_PID, ["Penicillin"])
        sidebar, _total = _setup_review_session(
            page, "Add a penicillin allergy for this patient."
        )

        select_patient(page, PATIENT_PID_B, PATIENT_NAME_B)

        expect(sidebar.locator("#context-line")).to_contain_text(
            PATIENT_NAME_B, timeout=10000,
        )

    def test_patient_switch_allows_new_chat(self, page: Page) -> None:
        """After patient switch, a new message goes to the new patient."""
        cleanup_test_allergies(PATIENT_PID, ["Penicillin"])
        sidebar, _total = _setup_review_session(
            page, "Add a penicillin allergy for this patient."
        )

        select_patient(page, PATIENT_PID_B, PATIENT_NAME_B)
        # Wait for new session to be created
        sidebar.wait_for_timeout(3000)

        # Send a simple context-checking message (no manifest action)
        send_chat_message(sidebar, "Who is this patient?")

        last_msg = sidebar.locator(".message.role-assistant .markdown").last
        text = last_msg.inner_text()
        # Response should mention the new patient (Kowalski), not Maria Santos
        assert "kowalski" in text.lower() or "james" in text.lower(), (
            f"Response doesn't mention new patient after switch: {text!r}"
        )


# ---------------------------------------------------------------------------
# History session loading
# ---------------------------------------------------------------------------


class TestHistorySessionLoad:
    """Loading a previous session from history should not show a stale manifest."""

    def test_loading_old_session_hides_review_panel(self, page: Page) -> None:
        """Switching to an old history session shows its chat but not a manifest."""
        cleanup_test_allergies(PATIENT_PID, ["Penicillin"])

        page.set_default_timeout(E2E_TIMEOUT_MS)
        openemr_login(page)
        select_patient(page, PATIENT_PID, PATIENT_NAME)
        _open_patient_dashboard(page)

        sidebar = get_sidebar_frame(page)

        # Session A: get a manifest (don't execute — leave panel open)
        send_chat_message(sidebar, "Add a penicillin allergy for this patient.")
        sidebar.wait_for_selector("#review-panel:not(.hidden)", timeout=E2E_TIMEOUT_MS)

        # Capture session A id
        session_a_text = sidebar.locator("#session-id-row").inner_text()

        # Create session B (blank slate)
        sidebar.locator("#new-conversation").dispatch_event("click")
        sidebar.wait_for_timeout(3000)

        # Session B should have no review panel
        expect(sidebar.locator("#review-panel")).to_be_hidden()

        # Open history panel and switch back to session A
        sidebar.locator("#history-toggle").dispatch_event("click")
        sidebar.wait_for_timeout(1000)

        # Click the first history item (session A, most recent before B)
        history_items = sidebar.locator(".history-item")
        if history_items.count() < 2:
            pytest.skip("Need ≥2 history items to test session switching")

        # The second item should be session A (B is current/first)
        history_items.nth(1).dispatch_event("click")
        sidebar.wait_for_timeout(2000)

        # After loading old session, review panel should NOT appear —
        # the manifest was never executed and should not re-surface
        expect(sidebar.locator("#review-panel")).to_be_hidden(timeout=5000)
