"""Review panel E2E tests against real OpenEMR.

These tests log into OpenEMR, select a patient, send a real chat message
through the Clinical Assistant sidebar, and verify the review panel workflow:
tour-mode rendering, navigation, per-card Apply/Reject/Undo, bulk actions,
and execution.

No mocked API routes — the manifest comes from a real LLM call.  Assertions
are structural (panel visible, card count, CSS classes) rather than
content-specific, since manifest items are non-deterministic.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Frame, Page, expect

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
    pat = page.frame_locator("iframe[name=pat]")
    pat.locator("#allergy_ps_expand").wait_for(state="attached", timeout=15000)


@pytest.fixture
def review_session(page: Page) -> tuple[Page, Frame, int]:
    """Logged-in OpenEMR with a patient selected and a manifest active.

    Returns (page, sidebar_frame, manifest_item_count).
    """
    # Remove any allergies left over from previous test executions so the
    # agent doesn't decide "allergy already exists" and skip the manifest.
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

    return page, sidebar, total


# ---------------------------------------------------------------------------
# Tour mode rendering
# ---------------------------------------------------------------------------


class TestTourModeRendering:
    """Verify the tour-mode review panel renders one card at a time."""

    def test_review_panel_appears(
        self, review_session: tuple[Page, Frame, int],
    ) -> None:
        _page, sidebar, _total = review_session
        expect(sidebar.locator("#review-panel")).to_be_visible()

    def test_one_card_visible_at_a_time(
        self, review_session: tuple[Page, Frame, int],
    ) -> None:
        """Tour mode renders exactly one card, not all manifest items."""
        _page, sidebar, _total = review_session
        assert sidebar.locator(".review-card").count() == 1

    def test_tour_progress_format(
        self, review_session: tuple[Page, Frame, int],
    ) -> None:
        _page, sidebar, total = review_session
        expect(sidebar.locator("#tour-progress")).to_have_text(f"1 of {total}")

    def test_prev_disabled_on_first_item(
        self, review_session: tuple[Page, Frame, int],
    ) -> None:
        _page, sidebar, _total = review_session
        expect(sidebar.locator("#tour-prev")).to_be_disabled()

    def test_next_enabled_when_multiple_items(
        self, review_session: tuple[Page, Frame, int],
    ) -> None:
        _page, sidebar, total = review_session
        if total < 2:
            pytest.skip("Need ≥2 manifest items to test next button")
        expect(sidebar.locator("#tour-next")).not_to_be_disabled()

    def test_card_has_action_icon(
        self, review_session: tuple[Page, Frame, int],
    ) -> None:
        _page, sidebar, _total = review_session
        icon = sidebar.locator(".review-card-action-icon")
        expect(icon).to_be_visible()
        # Icon text should be one of +, ✎, − (create/update/delete)
        text = icon.inner_text()
        assert text in ("+", "✎", "−"), f"Unexpected action icon: {text!r}"

    def test_card_has_confidence_badge(
        self, review_session: tuple[Page, Frame, int],
    ) -> None:
        _page, sidebar, _total = review_session
        badge = sidebar.locator(".confidence-badge")
        expect(badge).to_be_visible()
        text = badge.inner_text().lower()
        assert text in ("high", "medium", "low"), f"Unexpected confidence: {text!r}"

    def test_card_shows_pending_status(
        self, review_session: tuple[Page, Frame, int],
    ) -> None:
        _page, sidebar, _total = review_session
        status = sidebar.locator(".review-card-status")
        expect(status).to_have_text("pending")

    def test_card_has_description(
        self, review_session: tuple[Page, Frame, int],
    ) -> None:
        _page, sidebar, _total = review_session
        desc = sidebar.locator(".review-card-description")
        expect(desc).to_be_visible()
        assert len(desc.inner_text().strip()) > 0

    def test_card_has_proposed_value(
        self, review_session: tuple[Page, Frame, int],
    ) -> None:
        _page, sidebar, _total = review_session
        # Proposed value is shown as key-value rows, not a textarea
        value_section = sidebar.locator(".review-card-value-row")
        assert value_section.count() > 0, "No proposed value rows found in review card"

    def test_summary_shows_all_pending(
        self, review_session: tuple[Page, Frame, int],
    ) -> None:
        _page, sidebar, total = review_session
        summary = sidebar.locator("#review-summary")
        expect(summary).to_contain_text(f"Pending: {total}")

    def test_status_pill_shows_reviewing(
        self, review_session: tuple[Page, Frame, int],
    ) -> None:
        _page, sidebar, _total = review_session
        expect(sidebar.locator("#status-text")).to_have_text("Review Changes")


# ---------------------------------------------------------------------------
# Tour navigation
# ---------------------------------------------------------------------------


class TestTourNavigation:
    """Verify prev/next navigation through manifest items."""

    def test_next_shows_second_item(
        self, review_session: tuple[Page, Frame, int],
    ) -> None:
        _page, sidebar, total = review_session
        if total < 2:
            pytest.skip("Need ≥2 manifest items to test navigation")

        sidebar.locator("#tour-next").dispatch_event("click")
        expect(sidebar.locator("#tour-progress")).to_have_text(f"2 of {total}")
        assert sidebar.locator(".review-card").count() == 1

    def test_next_disabled_on_last_item(
        self, review_session: tuple[Page, Frame, int],
    ) -> None:
        _page, sidebar, total = review_session
        if total < 2:
            pytest.skip("Need ≥2 manifest items")

        # Navigate to last item
        for _ in range(total - 1):
            sidebar.locator("#tour-next").dispatch_event("click")

        expect(sidebar.locator("#tour-progress")).to_have_text(f"{total} of {total}")
        expect(sidebar.locator("#tour-next")).to_be_disabled()

    def test_prev_enabled_on_last_item(
        self, review_session: tuple[Page, Frame, int],
    ) -> None:
        _page, sidebar, total = review_session
        if total < 2:
            pytest.skip("Need ≥2 manifest items")

        for _ in range(total - 1):
            sidebar.locator("#tour-next").dispatch_event("click")

        expect(sidebar.locator("#tour-prev")).not_to_be_disabled()

    def test_prev_navigates_back(
        self, review_session: tuple[Page, Frame, int],
    ) -> None:
        _page, sidebar, total = review_session
        if total < 2:
            pytest.skip("Need ≥2 manifest items")

        sidebar.locator("#tour-next").dispatch_event("click")
        expect(sidebar.locator("#tour-progress")).to_have_text(f"2 of {total}")

        sidebar.locator("#tour-prev").dispatch_event("click")
        expect(sidebar.locator("#tour-progress")).to_have_text(f"1 of {total}")


# ---------------------------------------------------------------------------
# Per-card Apply / Reject / Undo
# ---------------------------------------------------------------------------


class TestPerCardButtons:
    """Verify per-card Apply, Reject, and Undo buttons update card state."""

    def test_apply_marks_approved(
        self, review_session: tuple[Page, Frame, int],
    ) -> None:
        _page, sidebar, _total = review_session
        sidebar.locator(".review-card").first.locator(
            "button", has_text="Apply",
        ).dispatch_event("click")

        card = sidebar.locator(".review-card").first
        expect(card).to_have_class(re.compile("status-approved"))
        expect(card.locator(".review-card-status")).to_have_text(
            re.compile(r"approved", re.IGNORECASE),
        )

    def test_reject_marks_rejected(
        self, review_session: tuple[Page, Frame, int],
    ) -> None:
        _page, sidebar, _total = review_session
        sidebar.locator(".review-card").first.locator(
            "button", has_text="Reject",
        ).dispatch_event("click")

        card = sidebar.locator(".review-card").first
        expect(card).to_have_class(re.compile("status-rejected"))
        expect(card.locator(".review-card-status")).to_have_text(
            re.compile(r"rejected", re.IGNORECASE),
        )

    def test_undo_resets_to_pending(
        self, review_session: tuple[Page, Frame, int],
    ) -> None:
        _page, sidebar, _total = review_session
        card = sidebar.locator(".review-card").first

        # Apply then Undo
        card.locator("button", has_text="Apply").dispatch_event("click")
        expect(card).to_have_class(re.compile("status-approved"))

        card.locator("button", has_text="Undo").dispatch_event("click")
        expect(card.locator(".review-card-status")).to_have_text("pending")
        expect(card).not_to_have_class(re.compile("status-approved"))

    def test_apply_updates_summary(
        self, review_session: tuple[Page, Frame, int],
    ) -> None:
        _page, sidebar, total = review_session
        sidebar.locator(".review-card").first.locator(
            "button", has_text="Apply",
        ).dispatch_event("click")

        summary = sidebar.locator("#review-summary")
        expect(summary).to_contain_text("Apply: 1")
        expect(summary).to_contain_text(f"Pending: {total - 1}")

    def test_status_persists_across_navigation(
        self, review_session: tuple[Page, Frame, int],
    ) -> None:
        """Apply first item, navigate away and back — status should persist."""
        _page, sidebar, total = review_session
        if total < 2:
            pytest.skip("Need ≥2 manifest items")

        sidebar.locator(".review-card").first.locator(
            "button", has_text="Apply",
        ).dispatch_event("click")

        # Navigate to second item
        sidebar.locator("#tour-next").dispatch_event("click")
        expect(sidebar.locator("#tour-progress")).to_have_text(f"2 of {total}")

        # Navigate back to first item
        sidebar.locator("#tour-prev").dispatch_event("click")
        card = sidebar.locator(".review-card").first
        expect(card).to_have_class(re.compile("status-approved"))
        expect(card.locator(".review-card-status")).to_have_text(
            re.compile(r"approved", re.IGNORECASE),
        )

    def test_apply_on_second_item_via_tour(
        self, review_session: tuple[Page, Frame, int],
    ) -> None:
        """Navigate to second item, apply it, verify only that item changes."""
        _page, sidebar, total = review_session
        if total < 2:
            pytest.skip("Need ≥2 manifest items")

        sidebar.locator("#tour-next").dispatch_event("click")
        sidebar.locator(".review-card").first.locator(
            "button", has_text="Apply",
        ).dispatch_event("click")

        card = sidebar.locator(".review-card").first
        expect(card).to_have_class(re.compile("status-approved"))

        # Navigate back — first item should still be pending
        sidebar.locator("#tour-prev").dispatch_event("click")
        first_card = sidebar.locator(".review-card").first
        expect(first_card.locator(".review-card-status")).to_have_text("pending")


# ---------------------------------------------------------------------------
# Bulk Apply All / Reject All
# ---------------------------------------------------------------------------


class TestBulkReview:
    """Verify Apply All and Reject All header buttons."""

    def test_apply_all(
        self, review_session: tuple[Page, Frame, int],
    ) -> None:
        _page, sidebar, total = review_session
        sidebar.locator("#apply-all").dispatch_event("click")

        card = sidebar.locator(".review-card").first
        expect(card).to_have_class(re.compile("status-approved"))
        expect(sidebar.locator("#review-summary")).to_contain_text(f"Apply: {total}")

    def test_apply_all_affects_all_items_via_tour(
        self, review_session: tuple[Page, Frame, int],
    ) -> None:
        """After Apply All, navigating through all items should show approved."""
        _page, sidebar, total = review_session
        sidebar.locator("#apply-all").dispatch_event("click")

        for i in range(total):
            card = sidebar.locator(".review-card").first
            expect(card).to_have_class(re.compile("status-approved"))
            if i < total - 1:
                sidebar.locator("#tour-next").dispatch_event("click")

    def test_reject_all(
        self, review_session: tuple[Page, Frame, int],
    ) -> None:
        _page, sidebar, total = review_session
        sidebar.locator("#reject-all").dispatch_event("click")

        card = sidebar.locator(".review-card").first
        expect(card).to_have_class(re.compile("status-rejected"))
        expect(sidebar.locator("#review-summary")).to_contain_text(f"Rejected: {total}")

    def test_reject_all_affects_all_items_via_tour(
        self, review_session: tuple[Page, Frame, int],
    ) -> None:
        """After Reject All, navigating through all items should show rejected."""
        _page, sidebar, total = review_session
        sidebar.locator("#reject-all").dispatch_event("click")

        for i in range(total):
            card = sidebar.locator(".review-card").first
            expect(card).to_have_class(re.compile("status-rejected"))
            if i < total - 1:
                sidebar.locator("#tour-next").dispatch_event("click")

    def test_execute_button_text_reflects_state(
        self, review_session: tuple[Page, Frame, int],
    ) -> None:
        _page, sidebar, _total = review_session
        execute = sidebar.locator("#execute-button")

        # No approved items → "Discard All"
        expect(execute).to_have_text("Discard All")

        # After apply all → "Execute Changes"
        sidebar.locator("#apply-all").dispatch_event("click")
        expect(execute).to_have_text("Execute Changes")


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


class TestExecution:
    """Verify executing or discarding the manifest."""

    def test_execute_hides_review_panel(
        self, review_session: tuple[Page, Frame, int],
    ) -> None:
        _page, sidebar, _total = review_session
        sidebar.locator("#apply-all").dispatch_event("click")
        expect(sidebar.locator("#execute-button")).to_have_text("Execute Changes")

        sidebar.locator("#execute-button").dispatch_event("click")
        expect(sidebar.locator("#review-panel")).to_be_hidden(timeout=E2E_TIMEOUT_MS)

    def test_execute_shows_completion_message(
        self, review_session: tuple[Page, Frame, int],
    ) -> None:
        _page, sidebar, _total = review_session
        sidebar.locator("#apply-all").dispatch_event("click")
        sidebar.locator("#execute-button").dispatch_event("click")
        expect(sidebar.locator("#review-panel")).to_be_hidden(timeout=E2E_TIMEOUT_MS)

        last_msg = sidebar.locator(".message.role-assistant").last
        expect(last_msg).to_contain_text(
            re.compile(r"completed|executed|applied|success", re.IGNORECASE),
            timeout=E2E_TIMEOUT_MS,
        )

    def test_discard_hides_review_panel(
        self, review_session: tuple[Page, Frame, int],
    ) -> None:
        """When no items are approved, Execute acts as Discard All."""
        _page, sidebar, _total = review_session
        sidebar.locator("#execute-button").dispatch_event("click")
        expect(sidebar.locator("#review-panel")).to_be_hidden()

    def test_status_returns_to_ready_after_execute(
        self, review_session: tuple[Page, Frame, int],
    ) -> None:
        _page, sidebar, _total = review_session
        sidebar.locator("#apply-all").dispatch_event("click")
        sidebar.locator("#execute-button").dispatch_event("click")
        expect(sidebar.locator("#review-panel")).to_be_hidden(timeout=E2E_TIMEOUT_MS)

        expect(sidebar.locator("#status-text")).to_have_text("Ready", timeout=E2E_TIMEOUT_MS)

    def test_partial_approve_then_execute(
        self, review_session: tuple[Page, Frame, int],
    ) -> None:
        """Approve first item, reject second — mixed state then execute."""
        _page, sidebar, total = review_session
        if total < 2:
            pytest.skip("Need ≥2 manifest items for partial approve test")

        # Approve first item
        sidebar.locator(".review-card").first.locator(
            "button", has_text="Apply",
        ).dispatch_event("click")

        # Navigate to second and reject
        sidebar.locator("#tour-next").dispatch_event("click")
        sidebar.locator(".review-card").first.locator(
            "button", has_text="Reject",
        ).dispatch_event("click")

        summary = sidebar.locator("#review-summary")
        expect(summary).to_contain_text("Apply: 1")
        expect(summary).to_contain_text("Rejected: 1")

        # Execute — should succeed since at least one item is approved
        expect(sidebar.locator("#execute-button")).to_have_text("Execute Changes")
        sidebar.locator("#execute-button").dispatch_event("click")
        expect(sidebar.locator("#review-panel")).to_be_hidden(timeout=E2E_TIMEOUT_MS)
