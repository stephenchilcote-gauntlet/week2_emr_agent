"""Refresh tests: pat iframe reloads after manifest execution.

Full E2E tests (real LLM, ~60 s each) that log into OpenEMR, select a
patient, send a chat message, approve + execute the manifest, and verify
the pat iframe actually reloads with updated data.

Run:
    pytest tests/e2e/test_refresh.py -m e2e -v -s
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Frame, Page

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


def _open_patient_dashboard(page: Page) -> None:
    """Navigate to the patient dashboard so the pat iframe exists."""
    page.evaluate(f"""() => {{
        const top = window.top || window;
        top.navigateTab(
            '/interface/patient_file/summary/demographics.php?set_pid={PATIENT_PID}',
            'pat'
        );
    }}""")
    pat = page.frame_locator("iframe[name=pat]")
    pat.locator("#allergy_ps_expand").wait_for(state="attached", timeout=15000)


def _inject_sentinel(page: Page) -> bool:
    """Inject a sentinel property into the pat iframe's contentWindow.

    Returns True if the sentinel was successfully injected, False otherwise.
    The sentinel disappears when the iframe reloads, which is how we detect
    that overlay:refresh triggered a reload.
    """
    page.evaluate("""() => {
        var f = (window.top || window).document.querySelector("iframe[name='pat']");
        if (f && f.contentWindow) { f.contentWindow.__refreshSentinel = true; }
    }""")
    return page.evaluate("""() => {
        var f = (window.top || window).document.querySelector("iframe[name='pat']");
        return !!(f && f.contentWindow && f.contentWindow.__refreshSentinel);
    }""")


def _wait_for_sentinel_gone(page: Page, timeout: int = 30_000) -> None:
    """Wait until the pat iframe sentinel disappears (i.e., iframe reloaded)."""
    page.wait_for_function(
        """() => {
            var f = (window.top || window).document.querySelector("iframe[name='pat']");
            return !(f && f.contentWindow && f.contentWindow.__refreshSentinel);
        }""",
        timeout=timeout,
    )


def _setup_emr_with_patient(page: Page) -> Frame:
    """Log in, select patient, open dashboard, return sidebar frame."""
    page.set_default_timeout(E2E_TIMEOUT_MS)
    openemr_login(page)
    select_patient(page, PATIENT_PID, PATIENT_NAME)
    _open_patient_dashboard(page)
    return get_sidebar_frame(page)


def _ensure_pat_iframe_ready(page: Page) -> None:
    """Wait for the pat iframe to exist, or skip if it doesn't."""
    try:
        page.wait_for_function(
            "() => !!(window.top || window).document.querySelector(\"iframe[name='pat']\")",
            timeout=15_000,
        )
    except Exception:
        pytest.skip("pat iframe not present")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFullRefreshFlow:
    """End-to-end: chat → manifest → approve → execute → pat tab auto-reloads.

    Requires a live agent with seed patient data.
    """

    def test_pat_tab_reloads_after_manifest_executed(self, page: Page) -> None:
        """After executing a manifest that creates a Condition, the pat
        iframe reloads so the clinician sees the new data."""
        sidebar = _setup_emr_with_patient(page)
        _ensure_pat_iframe_ready(page)

        if not _inject_sentinel(page):
            pytest.skip("Could not inject sentinel (cross-origin or iframe not loaded)")

        cleanup_test_conditions(PATIENT_PID, ["E55.9"])
        send_chat_message(
            sidebar,
            "Add vitamin D deficiency to this patient's problem list (ICD-10: E55.9).",
        )

        sidebar.wait_for_selector("#review-panel:not(.hidden)", timeout=E2E_TIMEOUT_MS)

        sidebar.locator("#apply-all").dispatch_event("click")
        sidebar.wait_for_timeout(500)
        sidebar.locator("#execute-button").dispatch_event("click")

        _wait_for_sentinel_gone(page)

    def test_pat_tab_reloads_after_allergy_created(self, page: Page) -> None:
        """After executing a manifest that creates an AllergyIntolerance,
        the pat iframe reloads (allergies are shown on the patient dashboard)."""
        sidebar = _setup_emr_with_patient(page)
        _ensure_pat_iframe_ready(page)

        if not _inject_sentinel(page):
            pytest.skip("Could not inject sentinel (cross-origin or iframe not loaded)")

        cleanup_test_allergies(PATIENT_PID, ["Penicillin"])
        send_chat_message(
            sidebar,
            "Add a penicillin allergy for this patient.",
        )

        sidebar.wait_for_selector("#review-panel:not(.hidden)", timeout=E2E_TIMEOUT_MS)

        sidebar.locator("#apply-all").dispatch_event("click")
        sidebar.wait_for_timeout(500)
        sidebar.locator("#execute-button").dispatch_event("click")

        _wait_for_sentinel_gone(page)

    def test_execution_summary_shown_in_chat(self, page: Page) -> None:
        """After execution, a summary message appears in the chat area."""
        sidebar = _setup_emr_with_patient(page)

        cleanup_test_conditions(PATIENT_PID, ["D50.9"])
        send_chat_message(
            sidebar,
            "Add iron deficiency anemia to this patient's problem list (ICD-10: D50.9).",
        )

        sidebar.wait_for_selector("#review-panel:not(.hidden)", timeout=E2E_TIMEOUT_MS)

        sidebar.locator("#apply-all").dispatch_event("click")
        sidebar.wait_for_timeout(500)
        sidebar.locator("#execute-button").dispatch_event("click")

        # After execution, review panel should disappear and a summary message
        # should appear in the chat area
        sidebar.wait_for_selector(
            ".message.role-assistant:last-child",
            timeout=E2E_TIMEOUT_MS,
        )

        # The execution summary contains "applied" or "change" language
        last_msg = sidebar.locator(".message.role-assistant .markdown").last
        text = last_msg.inner_text()
        assert "applied" in text.lower() or "change" in text.lower(), (
            f"Expected execution summary message, got: {text}"
        )
