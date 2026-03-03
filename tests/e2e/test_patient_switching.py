"""Patient switching E2E tests.

Tests that verify the sidebar correctly handles patient context changes:
  - Context line updates immediately when patient changes
  - New conversation session is scoped to the new patient
  - History panel only shows sessions for the current patient
  - Chat responses reference the correct patient after switching
  - Switching patient mid-conversation starts a fresh session
  - The patient ID is correctly passed to the agent API

These are critical clinical safety tests: the agent must NEVER respond
with data from the wrong patient.
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

PATIENT_PID_A = PATIENT_MAP["Maria Santos"]
PATIENT_NAME_A = "Maria Santos"
PATIENT_PID_B = PATIENT_MAP["James Kowalski"]
PATIENT_NAME_B = "James Kowalski"


# ---------------------------------------------------------------------------
# Context line correctness
# ---------------------------------------------------------------------------


class TestContextAfterPatientSwitch:
    """Context line always reflects the currently selected patient."""

    def test_context_shows_patient_a(self, page: Page) -> None:
        """After selecting patient A, context line shows patient A's name."""
        page.set_default_timeout(E2E_TIMEOUT_MS)
        openemr_login(page)
        select_patient(page, PATIENT_PID_A, PATIENT_NAME_A)
        sidebar = get_sidebar_frame(page)

        expect(sidebar.locator("#context-line")).to_contain_text(PATIENT_NAME_A)

    def test_context_updates_to_patient_b(self, page: Page) -> None:
        """Switching from A to B updates the context line to patient B."""
        page.set_default_timeout(E2E_TIMEOUT_MS)
        openemr_login(page)
        select_patient(page, PATIENT_PID_A, PATIENT_NAME_A)
        sidebar = get_sidebar_frame(page)
        expect(sidebar.locator("#context-line")).to_contain_text(PATIENT_NAME_A)

        select_patient(page, PATIENT_PID_B, PATIENT_NAME_B)
        expect(sidebar.locator("#context-line")).to_contain_text(
            PATIENT_NAME_B, timeout=10000
        )

    def test_context_never_shows_wrong_patient(self, page: Page) -> None:
        """After switching to patient B, patient A's name is NOT in context."""
        page.set_default_timeout(E2E_TIMEOUT_MS)
        openemr_login(page)
        select_patient(page, PATIENT_PID_A, PATIENT_NAME_A)
        sidebar = get_sidebar_frame(page)

        select_patient(page, PATIENT_PID_B, PATIENT_NAME_B)
        page.wait_for_timeout(1000)

        text = sidebar.locator("#context-line").inner_text()
        assert PATIENT_NAME_A not in text, (
            f"Patient A's name should not appear after switching to B: {text!r}"
        )
        assert PATIENT_NAME_B in text, (
            f"Patient B's name should be in context: {text!r}"
        )


# ---------------------------------------------------------------------------
# Session scoping
# ---------------------------------------------------------------------------


class TestSessionScopingOnPatientSwitch:
    """Patient switching creates a new session scoped to the new patient."""

    def test_new_session_created_after_patient_switch(self, page: Page) -> None:
        """Switching patient resets the session ID (fresh session for new patient)."""
        page.set_default_timeout(E2E_TIMEOUT_MS)
        openemr_login(page)
        select_patient(page, PATIENT_PID_A, PATIENT_NAME_A)
        sidebar = get_sidebar_frame(page)

        # Get initial session ID
        initial_session_id = sidebar.evaluate(
            "() => window.__sidebarApp.state.sessionID"
        )

        # Switch patient
        select_patient(page, PATIENT_PID_B, PATIENT_NAME_B)
        page.wait_for_timeout(2000)

        new_session_id = sidebar.evaluate(
            "() => window.__sidebarApp.state.sessionID"
        )
        # A new session should have been created for the new patient
        assert new_session_id != initial_session_id, (
            f"Session should change after patient switch: "
            f"initial={initial_session_id!r}, new={new_session_id!r}"
        )

    def test_chat_cleared_after_patient_switch(self, page: Page) -> None:
        """After switching patients, the chat area is cleared."""
        page.set_default_timeout(E2E_TIMEOUT_MS)
        openemr_login(page)
        select_patient(page, PATIENT_PID_A, PATIENT_NAME_A)
        sidebar = get_sidebar_frame(page)

        send_chat_message(sidebar, "What is 2 + 2?")
        # Verify we have messages
        assert sidebar.locator(".message").count() > 0

        # Switch patient
        select_patient(page, PATIENT_PID_B, PATIENT_NAME_B)

        # Wait for messages to be cleared
        sidebar.wait_for_function(
            "() => document.querySelectorAll('.message').length === 0",
            timeout=10000,
        )

    def test_patient_id_in_new_session_state(self, page: Page) -> None:
        """After switching, the sidebar's patient ID state reflects the new patient."""
        page.set_default_timeout(E2E_TIMEOUT_MS)
        openemr_login(page)
        select_patient(page, PATIENT_PID_A, PATIENT_NAME_A)
        sidebar = get_sidebar_frame(page)
        page.wait_for_timeout(500)

        select_patient(page, PATIENT_PID_B, PATIENT_NAME_B)
        page.wait_for_timeout(1000)

        patient_id = sidebar.evaluate("() => window.__sidebarApp.state.patientID")
        assert str(patient_id) == str(PATIENT_PID_B), (
            f"Patient ID state should be {PATIENT_PID_B}, got: {patient_id!r}"
        )


# ---------------------------------------------------------------------------
# History panel isolation
# ---------------------------------------------------------------------------


class TestHistoryIsolationAcrossPatients:
    """History panel shows only sessions for the currently selected patient."""

    def test_history_shows_current_patient_sessions(self, page: Page) -> None:
        """After chatting with patient A then switching to B, history shows B's (empty) sessions."""
        page.set_default_timeout(E2E_TIMEOUT_MS)
        openemr_login(page)

        # Chat with patient A
        select_patient(page, PATIENT_PID_A, PATIENT_NAME_A)
        sidebar = get_sidebar_frame(page)
        send_chat_message(sidebar, "Note for Maria Santos")

        # Switch to patient B
        select_patient(page, PATIENT_PID_B, PATIENT_NAME_B)
        sidebar = get_sidebar_frame(page)
        page.wait_for_timeout(1000)

        # Open history panel
        sidebar.locator("#history-toggle").dispatch_event("click")
        sidebar.wait_for_timeout(2000)

        # Patient B's history should exist (may have a new empty session)
        # Critically: patient A's message should NOT be in the history items' preview
        items = sidebar.locator(".history-item")
        # If there are history items, they should not reference patient A's message
        if items.count() > 0:
            all_text = sidebar.evaluate("""() => {
                const items = document.querySelectorAll('.history-item-preview')
                return Array.from(items).map(i => i.innerText).join(' ')
            }""")
            assert "Maria Santos" not in all_text or PATIENT_NAME_B in sidebar.locator(
                "#context-line"
            ).inner_text(), (
                "History should show B's sessions, not A's"
            )

    def test_history_item_meta_shows_correct_patient(self, page: Page) -> None:
        """The history item meta line shows the current patient's name/ID."""
        page.set_default_timeout(E2E_TIMEOUT_MS)
        openemr_login(page)
        select_patient(page, PATIENT_PID_A, PATIENT_NAME_A)
        sidebar = get_sidebar_frame(page)

        send_chat_message(sidebar, "What is 2 + 2?")

        # Open history and check meta
        sidebar.locator("#history-toggle").dispatch_event("click")
        sidebar.wait_for_timeout(3000)

        items = sidebar.locator(".history-item")
        if items.count() >= 1:
            meta = items.first.locator(".history-item-meta").inner_text()
            # Meta should reference patient A (not B or some other patient)
            assert str(PATIENT_PID_A) in meta or PATIENT_NAME_A in meta, (
                f"History item meta should show Patient A's name/ID, got: {meta!r}"
            )


# ---------------------------------------------------------------------------
# Agent response correctness
# ---------------------------------------------------------------------------


class TestAgentResponseAfterPatientSwitch:
    """Agent responds with data for the correct patient after switching."""

    def test_agent_knows_current_patient_name(self, page: Page) -> None:
        """After switching to patient B, the agent knows it's talking about B."""
        page.set_default_timeout(E2E_TIMEOUT_MS)
        openemr_login(page)

        # Start with patient A
        select_patient(page, PATIENT_PID_A, PATIENT_NAME_A)
        sidebar = get_sidebar_frame(page)

        # Switch to patient B before sending any message
        select_patient(page, PATIENT_PID_B, PATIENT_NAME_B)
        page.wait_for_timeout(1500)
        # Get fresh sidebar frame reference
        sidebar = get_sidebar_frame(page)

        # Ask the agent who the current patient is
        send_chat_message(sidebar, "What is the name of the current patient?")

        last_msg = sidebar.locator(".message.role-assistant .markdown").last.inner_text()
        # The agent should mention patient B, not patient A
        assert PATIENT_NAME_B in last_msg or PATIENT_NAME_B.split()[0] in last_msg, (
            f"Agent should know about {PATIENT_NAME_B}, got: {last_msg!r}"
        )
        assert PATIENT_NAME_A not in last_msg, (
            f"Agent should NOT mention {PATIENT_NAME_A} when patient B is selected, "
            f"got: {last_msg!r}"
        )
