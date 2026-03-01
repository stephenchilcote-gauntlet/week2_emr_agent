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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def emr_with_manifest(page: Page) -> tuple[Page, Frame, int]:
    """Logged-in OpenEMR with a patient selected and a manifest active.

    Returns (page, sidebar_frame, manifest_item_count).
    """
    page.set_default_timeout(E2E_TIMEOUT_MS)
    openemr_login(page)
    select_patient(page, PATIENT_MAP["Maria Santos"], "Maria Santos")

    sidebar = get_sidebar_frame(page)

    send_chat_message(
        sidebar,
        "Add a penicillin allergy and add hypertension to the problem list.",
    )

    # Wait for the review panel to become visible in the sidebar
    sidebar.wait_for_selector(
        "#review-panel:not(.hidden)", timeout=E2E_TIMEOUT_MS,
    )
    expect(sidebar.locator("#tour-progress")).to_be_visible()

    progress_text = sidebar.locator("#tour-progress").inner_text()
    total = int(progress_text.split(" of ")[1])

    # Wait for overlays to render in the content iframes (async via postMessage)
    page.wait_for_timeout(2000)

    return page, sidebar, total


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count_ghosts(page: Page) -> int:
    """Count .agent-overlay-ghost elements across all content frames."""
    count = 0
    for frame in page.frames:
        url = frame.url
        # Skip the sidebar and top-level frames — only count content iframes
        if "sidebar" in url or "clinical-assistant" in url:
            continue
        try:
            count += frame.locator(".agent-overlay-ghost").count()
        except Exception:
            pass
    return count


def _find_overlay_button(page: Page, selector: str) -> tuple[Frame, object] | None:
    """Find an overlay button across all content frames."""
    for frame in page.frames:
        url = frame.url
        if "sidebar" in url or "clinical-assistant" in url:
            continue
        try:
            loc = frame.locator(selector)
            if loc.count() > 0:
                return frame, loc.first
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
        ghost_count = _count_ghosts(page)
        assert ghost_count >= 1, "Expected at least one overlay ghost"
        assert ghost_count <= total, (
            f"Got {ghost_count} ghost elements for {total} manifest items "
            f"— overlays are doubled"
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

        result = _find_overlay_button(page, ".overlay-btn-next")
        assert result is not None, "No inline next button found in any frame"
        _frame, btn = result
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
        page.wait_for_timeout(1500)

        # Now click the inline prev button
        result = _find_overlay_button(page, ".overlay-btn-prev")
        assert result is not None, "No inline prev button found in any frame"
        _frame, btn = result
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

        result = _find_overlay_button(page, ".overlay-btn-accept")
        assert result is not None, "No inline accept button found in any frame"
        _frame, btn = result
        btn.click()

        # Sidebar card should reflect approved status
        expect(sidebar.locator(".review-card-status")).to_have_text(
            "approved", timeout=10000,
        )

    def test_inline_reject(
        self, emr_with_manifest: tuple[Page, Frame, int],
    ) -> None:
        page, sidebar, total = emr_with_manifest
        if total < 2:
            pytest.skip("Need ≥2 items — using second to avoid conflict with accept test")

        # Navigate to second item so we don't conflict with accept test
        sidebar.locator("#tour-next").click()
        expect(sidebar.locator("#tour-progress")).to_have_text(f"2 of {total}")
        page.wait_for_timeout(1500)

        result = _find_overlay_button(page, ".overlay-btn-reject")
        assert result is not None, "No inline reject button found in any frame"
        _frame, btn = result
        btn.click()

        expect(sidebar.locator(".review-card-status")).to_have_text(
            "rejected", timeout=10000,
        )
