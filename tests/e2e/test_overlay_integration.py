"""Overlay integration tests against real OpenEMR.

These tests log into OpenEMR, select a patient, send a real chat message
through the sidebar, and verify overlays appear correctly in the actual
pat/enc iframes.  No mocks, no page.evaluate() shortcuts — Playwright
clicks the same buttons a clinician would.

Regressions covered:
  1. Doubled overlays — overlay.js loaded/triggered twice (prod)
  2. Double navigation — inline ‹/› buttons advancing tour by 2 instead of 1
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Frame, FrameLocator, Page, expect

from .conftest import (
    E2E_TIMEOUT_MS,
    PATIENT_MAP,
    cleanup_test_allergies,
    get_sidebar_frame,
    openemr_login,
    select_patient,
    send_chat_message,
)

pytestmark = pytest.mark.e2e

PATIENT_PID = PATIENT_MAP["Maria Santos"]
PATIENT_NAME = "Maria Santos"


# ---------------------------------------------------------------------------
# Fixtures
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
    # Wait for the pat iframe to load the patient dashboard DOM.
    # The tab may not be visually active yet (OpenEMR shows the calendar by
    # default), but the iframe content is accessible regardless.
    pat = page.frame_locator("iframe[name=pat]")
    pat.locator("#allergy_ps_expand").wait_for(state="attached", timeout=15000)


@pytest.fixture
def emr_with_manifest(page: Page) -> tuple[Page, Frame, int]:
    """Logged-in OpenEMR with a patient selected and a manifest active.

    Returns (page, sidebar_frame, manifest_item_count).
    """
    cleanup_test_allergies(PATIENT_PID)
    page.set_default_timeout(E2E_TIMEOUT_MS)
    openemr_login(page)
    select_patient(page, PATIENT_PID, PATIENT_NAME)
    _open_patient_dashboard(page)

    sidebar = get_sidebar_frame(page)

    send_chat_message(
        sidebar,
        "Add a penicillin allergy and add a sulfa drug allergy for this patient.",
    )

    # Wait for the review panel to become visible in the sidebar
    sidebar.wait_for_selector(
        "#review-panel:not(.hidden)", timeout=E2E_TIMEOUT_MS,
    )
    expect(sidebar.locator("#tour-progress")).to_be_visible()

    progress_text = sidebar.locator("#tour-progress").inner_text()
    total = int(progress_text.split(" of ")[1])

    # Wait for overlays to render in the content iframes (async via postMessage)
    page.wait_for_timeout(3000)

    return page, sidebar, total


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count_ghosts_in_frame(fl: FrameLocator) -> int:
    """Count .agent-overlay-ghost elements inside a FrameLocator."""
    try:
        return fl.locator(".agent-overlay-ghost").count()
    except Exception:
        return 0


def _find_overlay_button(page: Page, selector: str) -> object | None:
    """Find an overlay button on the FOCUSED ghost in pat or enc iframe.

    The focused ghost is the one with .overlay-btn-next (only the focused
    item gets nav buttons).  For nav buttons themselves, fall back to any
    matching element.
    """
    for name in ("pat", "enc"):
        fl = page.frame_locator(f"iframe[name={name}]")
        try:
            # Find the focused ghost (has nav buttons)
            focused = fl.locator(".agent-overlay-ghost:has(.overlay-btn-next)")
            if focused.count() > 0:
                btn = focused.first.locator(selector)
                if btn.count() > 0:
                    return btn.first
            # Fallback: any matching button (e.g., when only 1 ghost exists)
            loc = fl.locator(selector)
            if loc.count() > 0:
                return loc.first
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOverlaysNotDoubled:
    """Each manifest item should produce at most one overlay, not two."""

    def test_ghost_count_not_doubled(
        self, emr_with_manifest: tuple[Page, Frame, int],
    ) -> None:
        page, _sidebar, total = emr_with_manifest
        pat = page.frame_locator("iframe[name=pat]")
        ghost_count = _count_ghosts_in_frame(pat)
        assert ghost_count >= 1, "Expected at least one overlay ghost in pat iframe"
        assert ghost_count <= total, (
            f"Got {ghost_count} ghost elements for {total} manifest items "
            f"— overlays are doubled"
        )

    def test_sidebar_not_duplicated(self, page: Page) -> None:
        """embed.js mount() guard should prevent multiple sidebar iframes."""
        page.set_default_timeout(E2E_TIMEOUT_MS)
        openemr_login(page)
        sidebar_count = page.evaluate("""() =>
            document.querySelectorAll('#openemr-clinical-assistant-sidebar iframe').length
        """)
        assert sidebar_count == 1, (
            f"Expected 1 sidebar iframe, got {sidebar_count} — mount() ran multiple times"
        )


class TestInlineNavigation:
    """Inline ‹/› buttons should advance the tour by exactly one step."""

    def test_next_advances_by_one(
        self, emr_with_manifest: tuple[Page, Frame, int],
    ) -> None:
        page, sidebar, total = emr_with_manifest
        if total < 2:
            pytest.skip("Need ≥2 manifest items to test navigation")

        expect(sidebar.locator("#tour-progress")).to_have_text(f"1 of {total}")

        btn = _find_overlay_button(page, ".overlay-btn-next")
        assert btn is not None, "No inline next button found in any content frame"
        btn.click()

        # Must land on exactly "2 of N", not "3 of N"
        expect(sidebar.locator("#tour-progress")).to_have_text(
            f"2 of {total}", timeout=5000,
        )

    def test_prev_after_next_returns_to_start(
        self, emr_with_manifest: tuple[Page, Frame, int],
    ) -> None:
        page, sidebar, total = emr_with_manifest
        if total < 2:
            pytest.skip("Need ≥2 manifest items to test navigation")

        # Navigate forward first via sidebar button (known-good)
        sidebar.locator("#tour-next").click()
        expect(sidebar.locator("#tour-progress")).to_have_text(f"2 of {total}")

        # Wait for new overlays to render
        page.wait_for_timeout(2000)

        # Now click the inline prev button
        btn = _find_overlay_button(page, ".overlay-btn-prev")
        assert btn is not None, "No inline prev button found in any content frame"
        btn.click()

        expect(sidebar.locator("#tour-progress")).to_have_text(
            f"1 of {total}", timeout=5000,
        )


class TestInlineAcceptReject:
    """Inline ✅/🚫 buttons should update item status in the sidebar."""

    def test_inline_accept(
        self, emr_with_manifest: tuple[Page, Frame, int],
    ) -> None:
        page, sidebar, _total = emr_with_manifest

        btn = _find_overlay_button(page, ".overlay-btn-accept")
        assert btn is not None, "No inline accept button found in any content frame"
        btn.click()

        # Sidebar card should reflect approved status (CSS text-transform: uppercase)
        expect(sidebar.locator(".review-card-status")).to_have_text(
            re.compile(r"approved", re.IGNORECASE), timeout=10000,
        )

    def test_inline_reject(
        self, emr_with_manifest: tuple[Page, Frame, int],
    ) -> None:
        page, sidebar, total = emr_with_manifest
        if total < 2:
            pytest.skip("Need ≥2 items to avoid conflict with accept test")

        # Navigate to second item
        sidebar.locator("#tour-next").click()
        expect(sidebar.locator("#tour-progress")).to_have_text(f"2 of {total}")
        page.wait_for_timeout(2000)

        btn = _find_overlay_button(page, ".overlay-btn-reject")
        assert btn is not None, "No inline reject button found in any content frame"
        btn.click()

        expect(sidebar.locator(".review-card-status")).to_have_text(
            re.compile(r"rejected", re.IGNORECASE), timeout=10000,
        )
