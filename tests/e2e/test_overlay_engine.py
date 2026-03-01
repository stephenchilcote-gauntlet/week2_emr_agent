"""Overlay engine e2e tests: ghost row structure, action buttons, tab activation.

These tests log into real OpenEMR, select a patient, send a chat message
through the sidebar to produce overlays, then inspect the actual pat iframe
DOM for overlay rendering correctness.

Complements test_overlay_integration.py which covers:
  - Doubled overlays regression
  - Inline button navigation (next/prev advancing tour)
  - Inline accept/reject updating sidebar status

This file focuses on overlay *rendering structure* in the real EMR DOM:
  1. Ghost row existence and DOM structure
  2. Action button presence and layout
  3. Tab activation (pat tab loaded with overlay content)
  4. Data attributes on overlay elements
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Frame, FrameLocator, Page, expect

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
    pat = page.frame_locator("iframe[name=pat]")
    pat.locator("#allergy_ps_expand").wait_for(state="attached", timeout=15000)


@pytest.fixture
def emr_with_overlays(page: Page) -> tuple[Page, Frame, FrameLocator]:
    """Logged-in OpenEMR with overlays rendered in the pat iframe.

    Sends a single-item chat message (add penicillin allergy) so we get
    at least one ghost row overlay in the allergy section.

    Returns (page, sidebar_frame, pat_frame_locator).
    """
    page.set_default_timeout(E2E_TIMEOUT_MS)
    openemr_login(page)
    select_patient(page, PATIENT_PID, PATIENT_NAME)
    _open_patient_dashboard(page)

    sidebar = get_sidebar_frame(page)

    send_chat_message(
        sidebar,
        "Add a penicillin allergy for this patient.",
    )

    # Wait for the review panel to become visible
    sidebar.wait_for_selector(
        "#review-panel:not(.hidden)", timeout=E2E_TIMEOUT_MS,
    )
    expect(sidebar.locator("#tour-progress")).to_be_visible()

    # Wait for overlays to render in content iframes (async via postMessage)
    page.wait_for_timeout(3000)

    pat = page.frame_locator("iframe[name=pat]")
    return page, sidebar, pat


# ---------------------------------------------------------------------------
# 1. Ghost row existence and structure
# ---------------------------------------------------------------------------


class TestGhostRowStructure:
    """Verify ghost rows appear in the real pat iframe with correct DOM structure."""

    def test_ghost_row_exists_in_allergy_section(
        self, emr_with_overlays: tuple[Page, Frame, FrameLocator],
    ) -> None:
        """At least one .agent-overlay-ghost should appear in the pat iframe."""
        _page, _sidebar, pat = emr_with_overlays
        ghosts = pat.locator(".agent-overlay-ghost")
        assert ghosts.count() >= 1, "No ghost row overlays found in pat iframe"

    def test_ghost_row_is_inside_allergy_container(
        self, emr_with_overlays: tuple[Page, Frame, FrameLocator],
    ) -> None:
        """Ghost for an allergy should be inside #allergy_ps_expand."""
        _page, _sidebar, pat = emr_with_overlays
        allergy_ghosts = pat.locator("#allergy_ps_expand .agent-overlay-ghost")
        assert allergy_ghosts.count() >= 1, (
            "Ghost row not found inside #allergy_ps_expand"
        )

    def test_ghost_row_has_data_item_id(
        self, emr_with_overlays: tuple[Page, Frame, FrameLocator],
    ) -> None:
        """Ghost rows should have data-item-id for identification."""
        _page, _sidebar, pat = emr_with_overlays
        ghost = pat.locator(".agent-overlay-ghost").first
        item_id = ghost.get_attribute("data-item-id")
        assert item_id is not None, "Ghost row missing data-item-id attribute"
        assert len(item_id) > 0, "data-item-id is empty"

    def test_ghost_row_has_bold_title(
        self, emr_with_overlays: tuple[Page, Frame, FrameLocator],
    ) -> None:
        """Ghost row should contain a .font-weight-bold title element."""
        _page, _sidebar, pat = emr_with_overlays
        ghost = pat.locator(".agent-overlay-ghost").first
        bold = ghost.locator(".font-weight-bold")
        assert bold.count() >= 1, "Ghost row missing .font-weight-bold title"

    def test_ghost_row_has_flex_fill_layout(
        self, emr_with_overlays: tuple[Page, Frame, FrameLocator],
    ) -> None:
        """Ghost row should use flex-fill layout matching EMR style."""
        _page, _sidebar, pat = emr_with_overlays
        ghost = pat.locator(".agent-overlay-ghost").first
        fill = ghost.locator(".flex-fill")
        assert fill.count() >= 1, "Ghost row missing .flex-fill layout element"


# ---------------------------------------------------------------------------
# 2. Action buttons on overlay elements
# ---------------------------------------------------------------------------


class TestOverlayActionButtons:
    """Verify overlay ghost rows have action button containers and buttons."""

    def test_ghost_has_actions_container(
        self, emr_with_overlays: tuple[Page, Frame, FrameLocator],
    ) -> None:
        """Ghost row should contain .agent-overlay-actions."""
        _page, _sidebar, pat = emr_with_overlays
        actions = pat.locator(".agent-overlay-ghost .agent-overlay-actions")
        assert actions.count() >= 1, "Ghost row missing .agent-overlay-actions container"

    def test_ghost_has_accept_button(
        self, emr_with_overlays: tuple[Page, Frame, FrameLocator],
    ) -> None:
        _page, _sidebar, pat = emr_with_overlays
        btn = pat.locator(".agent-overlay-ghost .overlay-btn-accept")
        assert btn.count() >= 1, "Ghost row missing accept button"

    def test_ghost_has_reject_button(
        self, emr_with_overlays: tuple[Page, Frame, FrameLocator],
    ) -> None:
        _page, _sidebar, pat = emr_with_overlays
        btn = pat.locator(".agent-overlay-ghost .overlay-btn-reject")
        assert btn.count() >= 1, "Ghost row missing reject button"

    def test_focused_ghost_has_nav_buttons(
        self, emr_with_overlays: tuple[Page, Frame, FrameLocator],
    ) -> None:
        """The focused ghost (current tour item) should have prev/next buttons."""
        _page, _sidebar, pat = emr_with_overlays
        prev_btn = pat.locator(".agent-overlay-ghost .overlay-btn-prev")
        next_btn = pat.locator(".agent-overlay-ghost .overlay-btn-next")
        assert prev_btn.count() >= 1, "Focused ghost missing prev button"
        assert next_btn.count() >= 1, "Focused ghost missing next button"

    def test_accept_button_has_data_item_id(
        self, emr_with_overlays: tuple[Page, Frame, FrameLocator],
    ) -> None:
        """Action buttons should carry data-item-id for message routing."""
        _page, _sidebar, pat = emr_with_overlays
        btn = pat.locator(".agent-overlay-ghost .overlay-btn-accept").first
        item_id = btn.get_attribute("data-item-id")
        assert item_id is not None, "Accept button missing data-item-id"
        assert len(item_id) > 0, "data-item-id on accept button is empty"

    def test_buttons_are_inside_grid(
        self, emr_with_overlays: tuple[Page, Frame, FrameLocator],
    ) -> None:
        """Action buttons should be laid out in a grid container."""
        _page, _sidebar, pat = emr_with_overlays
        # The grid is a child div inside .agent-overlay-actions
        actions = pat.locator(".agent-overlay-ghost .agent-overlay-actions").first
        # Check that accept and reject are both present inside
        assert actions.locator(".overlay-btn-accept").count() >= 1
        assert actions.locator(".overlay-btn-reject").count() >= 1


# ---------------------------------------------------------------------------
# 3. Tab activation
# ---------------------------------------------------------------------------


class TestTabActivation:
    """Verify the pat tab is loaded and has overlay content after chat."""

    def test_pat_iframe_has_patient_dashboard(
        self, emr_with_overlays: tuple[Page, Frame, FrameLocator],
    ) -> None:
        """The pat iframe should have loaded the patient dashboard."""
        _page, _sidebar, pat = emr_with_overlays
        # Patient dashboard has the allergy section
        allergy_section = pat.locator("#allergy_ps_expand")
        assert allergy_section.count() >= 1, (
            "pat iframe missing #allergy_ps_expand — dashboard not loaded"
        )

    def test_pat_iframe_has_medication_section(
        self, emr_with_overlays: tuple[Page, Frame, FrameLocator],
    ) -> None:
        """The pat iframe should also have the medication section."""
        _page, _sidebar, pat = emr_with_overlays
        med_section = pat.locator("#medication_ps_expand")
        assert med_section.count() >= 1, (
            "pat iframe missing #medication_ps_expand"
        )

    def test_pat_iframe_has_conditions_section(
        self, emr_with_overlays: tuple[Page, Frame, FrameLocator],
    ) -> None:
        """The pat iframe should have the medical problems section."""
        _page, _sidebar, pat = emr_with_overlays
        cond_section = pat.locator("#medical_problem_ps_expand")
        assert cond_section.count() >= 1, (
            "pat iframe missing #medical_problem_ps_expand"
        )


# ---------------------------------------------------------------------------
# 4. Overlay engine loaded in parent frame
# ---------------------------------------------------------------------------


class TestOverlayEngineLoaded:
    """Verify overlay.js is injected and the engine is available."""

    def test_overlay_engine_exists_on_window(
        self, emr_with_overlays: tuple[Page, Frame, FrameLocator],
    ) -> None:
        """window.__overlayEngine should exist on the top-level page."""
        page, _sidebar, _pat = emr_with_overlays
        has_engine = page.evaluate(
            "() => typeof window.__overlayEngine === 'object' && window.__overlayEngine !== null"
        )
        assert has_engine, "__overlayEngine not found on window — overlay.js not loaded"

    def test_overlay_engine_has_apply_overlay(
        self, emr_with_overlays: tuple[Page, Frame, FrameLocator],
    ) -> None:
        """Engine should expose applyOverlay function."""
        page, _sidebar, _pat = emr_with_overlays
        has_fn = page.evaluate(
            "() => typeof window.__overlayEngine.applyOverlay === 'function'"
        )
        assert has_fn, "applyOverlay not found on __overlayEngine"

    def test_overlay_engine_has_apply_all(
        self, emr_with_overlays: tuple[Page, Frame, FrameLocator],
    ) -> None:
        """Engine should expose applyAllOverlays function."""
        page, _sidebar, _pat = emr_with_overlays
        has_fn = page.evaluate(
            "() => typeof window.__overlayEngine.applyAllOverlays === 'function'"
        )
        assert has_fn, "applyAllOverlays not found on __overlayEngine"

    def test_overlay_engine_has_clear_all(
        self, emr_with_overlays: tuple[Page, Frame, FrameLocator],
    ) -> None:
        """Engine should expose clearAllOverlays function."""
        page, _sidebar, _pat = emr_with_overlays
        has_fn = page.evaluate(
            "() => typeof window.__overlayEngine.clearAllOverlays === 'function'"
        )
        assert has_fn, "clearAllOverlays not found on __overlayEngine"

    def test_overlay_engine_has_navigate_to_tab(
        self, emr_with_overlays: tuple[Page, Frame, FrameLocator],
    ) -> None:
        """Engine should expose navigateToTab function."""
        page, _sidebar, _pat = emr_with_overlays
        has_fn = page.evaluate(
            "() => typeof window.__overlayEngine.navigateToTab === 'function'"
        )
        assert has_fn, "navigateToTab not found on __overlayEngine"
