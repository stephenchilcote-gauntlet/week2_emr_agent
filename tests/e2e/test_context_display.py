"""Context display E2E tests.

Tests that verify the sidebar context line (#context-line) shows the
correct text for all combinations of patient, encounter, and active tab:

  - No patient selected: "No patient selected"
  - Patient name only: "Maria Santos"
  - Patient name + encounter: "Maria Santos · Enc: 12"
  - Patient name + encounter + active tab: "Maria Santos · Enc: 12 · Demographics"
  - Patient ID only (no name): shows raw patient ID
  - Active tab with no patient: "Tab: Demographics"
  - Context updates when patient changes via postMessage
  - Context shows patient name from the sidebar's live state

Most tests inject context via evaluate() to avoid needing patient navigation
for each variant.
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
PATIENT_PID_B = PATIENT_MAP["James Kowalski"]
PATIENT_NAME_B = "James Kowalski"


@pytest.fixture
def sidebar(page: Page) -> Frame:
    """Logged-in OpenEMR, no patient selected."""
    page.set_default_timeout(E2E_TIMEOUT_MS)
    openemr_login(page)
    return get_sidebar_frame(page)


def _js(value: str | None) -> str:
    """Convert a Python string or None to its JavaScript literal representation."""
    if value is None:
        return "null"
    return repr(value)  # produces a quoted JS string like 'Maria Santos'


def _set_context(
    frame: Frame,
    *,
    patient_id: str | None = None,
    patient_name: str | None = None,
    encounter_id: str | None = None,
    active_tab: str | None = None,
) -> None:
    """Directly set the sidebar's context state and re-render the context line."""
    frame.evaluate(f"""() => {{
        const app = window.__sidebarApp
        app.state.patientID = {_js(patient_id)}
        app.state.patientName = {_js(patient_name)}
        app.state.encounterID = {_js(encounter_id)}
        app.state.activeTab = {_js(active_tab)}
        app.updateContextDisplay()
    }}""")


# ---------------------------------------------------------------------------
# Context line content
# ---------------------------------------------------------------------------


class TestContextLineContent:
    """Context line shows the correct text for each state combination."""

    def test_no_patient_shows_default(self, sidebar: Frame) -> None:
        """With no patient, context line shows 'No patient selected'."""
        _set_context(sidebar)
        expect(sidebar.locator("#context-line")).to_have_text("No patient selected")

    def test_patient_name_displayed(self, sidebar: Frame) -> None:
        """With a patient name, context line shows the patient name."""
        _set_context(sidebar, patient_id="42", patient_name="Jane Doe")
        expect(sidebar.locator("#context-line")).to_have_text("Jane Doe")

    def test_patient_id_shown_when_no_name(self, sidebar: Frame) -> None:
        """If no patient name is set, the patient ID is shown instead."""
        _set_context(sidebar, patient_id="42", patient_name=None)
        expect(sidebar.locator("#context-line")).to_have_text("42")

    def test_encounter_id_appended(self, sidebar: Frame) -> None:
        """Encounter ID is appended as '· Enc: {id}' when set."""
        _set_context(sidebar, patient_id="42", patient_name="Jane Doe", encounter_id="99")
        text = sidebar.locator("#context-line").inner_text()
        assert "Jane Doe" in text, f"Patient name missing: {text!r}"
        assert "Enc: 99" in text, f"Encounter missing: {text!r}"
        assert "·" in text, f"Separator missing: {text!r}"

    def test_no_encounter_no_enc_label(self, sidebar: Frame) -> None:
        """Without an encounter, 'Enc:' does not appear in context line."""
        _set_context(sidebar, patient_id="42", patient_name="Jane Doe")
        text = sidebar.locator("#context-line").inner_text()
        assert "Enc:" not in text, f"Unexpected 'Enc:' in context: {text!r}"

    def test_active_tab_appended(self, sidebar: Frame) -> None:
        """Active tab name is appended after patient and encounter."""
        _set_context(
            sidebar,
            patient_id="42",
            patient_name="Jane Doe",
            encounter_id="99",
            active_tab="Demographics",
        )
        text = sidebar.locator("#context-line").inner_text()
        assert "Jane Doe" in text
        assert "Enc: 99" in text
        assert "Demographics" in text

    def test_active_tab_without_patient(self, sidebar: Frame) -> None:
        """Active tab shown as 'Tab: {name}' when no patient is selected."""
        _set_context(sidebar, active_tab="Demographics")
        expect(sidebar.locator("#context-line")).to_have_text("Tab: Demographics")

    def test_patient_with_tab_no_encounter(self, sidebar: Frame) -> None:
        """Patient + active tab without encounter: no 'Enc:' in context."""
        _set_context(sidebar, patient_id="42", patient_name="Jane Doe", active_tab="Allergies")
        text = sidebar.locator("#context-line").inner_text()
        assert "Jane Doe" in text
        assert "Allergies" in text
        assert "Enc:" not in text


# ---------------------------------------------------------------------------
# Context updates when patient changes
# ---------------------------------------------------------------------------


class TestContextUpdatesOnPatientChange:
    """Context line updates in real-time when patient context changes."""

    def test_initial_context_no_patient(self, sidebar: Frame) -> None:
        """Without selecting a patient, context line says 'No patient selected'."""
        expect(sidebar.locator("#context-line")).to_have_text("No patient selected")

    def test_context_updates_after_patient_selection(self, page: Page) -> None:
        """Selecting a patient via OpenEMR left_nav updates the context line."""
        page.set_default_timeout(E2E_TIMEOUT_MS)
        openemr_login(page)
        sidebar = get_sidebar_frame(page)

        expect(sidebar.locator("#context-line")).to_have_text("No patient selected")

        select_patient(page, PATIENT_PID, PATIENT_NAME)
        expect(sidebar.locator("#context-line")).to_contain_text(
            PATIENT_NAME, timeout=10000,
        )

    def test_context_updates_on_patient_switch(self, page: Page) -> None:
        """Switching from one patient to another updates the context line."""
        page.set_default_timeout(E2E_TIMEOUT_MS)
        openemr_login(page)
        select_patient(page, PATIENT_PID, PATIENT_NAME)
        sidebar = get_sidebar_frame(page)

        expect(sidebar.locator("#context-line")).to_contain_text(PATIENT_NAME)

        select_patient(page, PATIENT_PID_B, PATIENT_NAME_B)
        expect(sidebar.locator("#context-line")).to_contain_text(
            PATIENT_NAME_B, timeout=10000,
        )

    def test_context_line_not_empty_after_patient_set(self, page: Page) -> None:
        """Context line is never empty — shows either patient or default text."""
        page.set_default_timeout(E2E_TIMEOUT_MS)
        openemr_login(page)
        select_patient(page, PATIENT_PID, PATIENT_NAME)
        sidebar = get_sidebar_frame(page)

        text = sidebar.locator("#context-line").inner_text()
        assert len(text.strip()) > 0, "Context line should never be empty"
        assert text.strip() != "No patient selected", (
            "Context line should show patient name after selection"
        )
