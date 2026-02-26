"""Review panel tests: tour-mode manifest rendering, apply/reject workflow, execution.

These tests mock the chat API to return a manifest, then verify the tour-mode
review panel UI: single-card rendering, tour navigation (prev/next), per-card
Apply/Reject/Undo, bulk actions, overlay messaging, and execution flow.
No real LLM calls are needed.
"""

from __future__ import annotations

import json
import re

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e

MOCK_SESSION_ID = "test-review-session-00000001"
MOCK_MANIFEST_ID = "test-manifest-00000001"
MOCK_ITEM_A_ID = "test-item-aaa"
MOCK_ITEM_B_ID = "test-item-bbb"
MOCK_ITEM_C_ID = "test-item-ccc"


def _make_manifest() -> dict:
    return {
        "id": MOCK_MANIFEST_ID,
        "patient_id": "1",
        "encounter_id": None,
        "items": [
            {
                "id": MOCK_ITEM_A_ID,
                "resource_type": "AllergyIntolerance",
                "action": "create",
                "proposed_value": {"code": "12345", "display": "Penicillin"},
                "current_value": None,
                "source_reference": "Patient reported",
                "description": "Add penicillin allergy",
                "confidence": "high",
                "status": "pending",
                "target_resource_id": None,
                "depends_on": [],
                "execution_result": None,
            },
            {
                "id": MOCK_ITEM_B_ID,
                "resource_type": "MedicationRequest",
                "action": "update",
                "proposed_value": {"medication": "Amoxicillin", "dosage": "500mg"},
                "current_value": {"medication": "Amoxicillin", "dosage": "250mg"},
                "source_reference": "Clinical guideline",
                "description": "Update amoxicillin dosage",
                "confidence": "medium",
                "status": "pending",
                "target_resource_id": "MedicationRequest/abc-123",
                "depends_on": [],
                "execution_result": None,
            },
            {
                "id": MOCK_ITEM_C_ID,
                "resource_type": "Encounter",
                "action": "create",
                "proposed_value": {"type": "office-visit"},
                "current_value": None,
                "source_reference": "Scheduling system",
                "description": "Create follow-up encounter",
                "confidence": "low",
                "status": "pending",
                "target_resource_id": None,
                "depends_on": [],
                "execution_result": None,
            },
        ],
        "created_at": "2026-02-24T00:00:00",
        "status": "draft",
    }


@pytest.fixture
def review_page(sidebar_page: Page) -> Page:
    """Sidebar page with mocked API endpoints that return a manifest."""
    sidebar_page.set_default_timeout(10_000)

    sidebar_page.route(
        "**/api/chat",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "session_id": MOCK_SESSION_ID,
                "response": "I will make the following changes for your review.",
                "manifest": _make_manifest(),
                "phase": "reviewing",
                "tool_calls_summary": None,
            }),
        ),
    )
    sidebar_page.route(
        "**/api/manifest/*/approve",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "session_id": MOCK_SESSION_ID,
                "manifest_id": MOCK_MANIFEST_ID,
                "results": [],
                "passed": True,
            }),
        ),
    )
    sidebar_page.route(
        "**/api/manifest/*/execute",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "session_id": MOCK_SESSION_ID,
                "phase": "complete",
                "manifest_status": "completed",
                "items": [
                    {"id": MOCK_ITEM_A_ID, "status": "completed", "execution_result": "Created"},
                    {"id": MOCK_ITEM_B_ID, "status": "completed", "execution_result": "Updated"},
                    {"id": MOCK_ITEM_C_ID, "status": "completed", "execution_result": "Created"},
                ],
            }),
        ),
    )
    return sidebar_page


def _trigger_review_panel(page: Page) -> None:
    """Send a chat message via Enter key to trigger the mocked manifest response."""
    # Wait for start() → createSession() to finish; otherwise the late-arriving
    # createSession response nullifies pendingManifest and hides the panel.
    page.wait_for_load_state("networkidle")
    page.locator("#chat-input").fill("Add penicillin allergy")
    page.locator("#chat-input").press("Enter")
    # Wait for panel, card, and async operations to fully settle
    expect(page.locator("#review-panel")).to_be_visible(timeout=10_000)
    expect(page.locator(".review-card").first).to_be_visible()
    page.wait_for_load_state("networkidle")


# ---------------------------------------------------------------------------
# Tour mode rendering
# ---------------------------------------------------------------------------


class TestTourModeRendering:
    """Verify the tour-mode review panel renders one card at a time."""

    def test_review_panel_appears(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        expect(review_page.locator("#review-panel")).to_be_visible()

    def test_one_card_visible_at_a_time(self, review_page: Page) -> None:
        """Tour mode renders exactly one card, not all manifest items."""
        _trigger_review_panel(review_page)
        assert review_page.locator(".review-card").count() == 1

    def test_first_card_shows_first_item(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        card = review_page.locator(".review-card").first
        assert "AllergyIntolerance" in card.inner_text()
        assert "Add penicillin allergy" in card.inner_text()

    def test_tour_progress_shows_1_of_3(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        expect(review_page.locator("#tour-progress")).to_have_text("1 of 3")

    def test_prev_disabled_on_first_item(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        expect(review_page.locator("#tour-prev")).to_be_disabled()

    def test_next_enabled_on_first_item(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        expect(review_page.locator("#tour-next")).not_to_be_disabled()

    def test_card_shows_action_icon(self, review_page: Page) -> None:
        """First item is a create action — icon should be '+'."""
        _trigger_review_panel(review_page)
        icon = review_page.locator(".review-card-action-icon")
        expect(icon).to_have_text("+")
        expect(icon).to_have_class(re.compile("action-create"))

    def test_card_shows_confidence_badge(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        badge = review_page.locator(".confidence-badge")
        expect(badge).to_have_text("high")

    def test_card_shows_status_badge_pending(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        status = review_page.locator(".review-card-status")
        expect(status).to_have_text("pending")

    def test_card_shows_description(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        desc = review_page.locator(".review-card-description")
        expect(desc).to_have_text("Add penicillin allergy")

    def test_card_shows_proposed_value_textarea(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        textarea = review_page.locator(".review-card textarea")
        expect(textarea).to_be_visible()
        value = textarea.input_value()
        parsed = json.loads(value)
        assert parsed["code"] == "12345"
        assert parsed["display"] == "Penicillin"

    def test_card_shows_source_reference(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        source = review_page.locator(".review-card-source")
        expect(source).to_contain_text("Patient reported")

    def test_summary_shows_all_pending(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        summary = review_page.locator("#review-summary")
        expect(summary).to_contain_text("Pending: 3")

    def test_status_pill_shows_reviewing(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        expect(review_page.locator("#status-text")).to_have_text("Review Changes")

    def test_in_page_resource_has_no_sidebar_note(self, review_page: Page) -> None:
        """AllergyIntolerance is in-page — no 'Cannot preview' note."""
        _trigger_review_panel(review_page)
        notes = review_page.locator(".review-card-sidebar-note")
        assert notes.count() == 0


# ---------------------------------------------------------------------------
# Tour navigation
# ---------------------------------------------------------------------------


class TestTourNavigation:
    """Verify prev/next navigation through manifest items."""

    def test_next_shows_second_item(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        review_page.locator("#tour-next").click()

        card = review_page.locator(".review-card").first
        assert "MedicationRequest" in card.inner_text()
        assert "Update amoxicillin dosage" in card.inner_text()
        expect(review_page.locator("#tour-progress")).to_have_text("2 of 3")

    def test_next_shows_update_action_icon(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        review_page.locator("#tour-next").click()

        icon = review_page.locator(".review-card-action-icon")
        expect(icon).to_have_text("✎")
        expect(icon).to_have_class(re.compile("action-update"))

    def test_second_item_shows_current_value(self, review_page: Page) -> None:
        """Update action should show the current value section."""
        _trigger_review_panel(review_page)
        review_page.locator("#tour-next").click()

        current = review_page.locator(".review-card-current")
        expect(current).to_be_visible()
        assert "250mg" in current.inner_text()

    def test_second_item_shows_medium_confidence(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        review_page.locator("#tour-next").click()

        badge = review_page.locator(".confidence-badge")
        expect(badge).to_have_text("medium")

    def test_navigate_to_third_item(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        review_page.locator("#tour-next").click()
        review_page.locator("#tour-next").click()

        card = review_page.locator(".review-card").first
        assert "Encounter" in card.inner_text()
        expect(review_page.locator("#tour-progress")).to_have_text("3 of 3")

    def test_next_disabled_on_last_item(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        review_page.locator("#tour-next").click()
        review_page.locator("#tour-next").click()

        expect(review_page.locator("#tour-next")).to_be_disabled()

    def test_prev_enabled_on_last_item(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        review_page.locator("#tour-next").click()
        review_page.locator("#tour-next").click()

        expect(review_page.locator("#tour-prev")).not_to_be_disabled()

    def test_prev_navigates_back(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        review_page.locator("#tour-next").click()
        review_page.locator("#tour-prev").click()

        card = review_page.locator(".review-card").first
        assert "AllergyIntolerance" in card.inner_text()
        expect(review_page.locator("#tour-progress")).to_have_text("1 of 3")

    def test_non_in_page_resource_shows_sidebar_note(self, review_page: Page) -> None:
        """Encounter is not in-page — should show 'Cannot preview' note."""
        _trigger_review_panel(review_page)
        # Navigate to 3rd item (Encounter)
        review_page.locator("#tour-next").click()
        review_page.locator("#tour-next").click()

        note = review_page.locator(".review-card-sidebar-note")
        expect(note).to_be_visible()
        expect(note).to_contain_text("Cannot preview in-page")

    def test_third_item_shows_low_confidence(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        review_page.locator("#tour-next").click()
        review_page.locator("#tour-next").click()

        badge = review_page.locator(".confidence-badge")
        expect(badge).to_have_text("low")


# ---------------------------------------------------------------------------
# Per-card Apply / Reject / Undo (tour mode)
# ---------------------------------------------------------------------------


class TestPerCardButtons:
    """Verify per-card Apply, Reject, and Undo buttons update card state."""

    def test_apply_marks_approved(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        review_page.locator(".review-card").first.locator("button", has_text="Apply").click()

        card = review_page.locator(".review-card").first
        expect(card).to_have_class(re.compile("status-approved"))
        expect(card.locator(".review-card-status")).to_have_text("approved")

    def test_reject_marks_rejected(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        review_page.locator(".review-card").first.locator("button", has_text="Reject").click()

        card = review_page.locator(".review-card").first
        expect(card).to_have_class(re.compile("status-rejected"))
        expect(card.locator(".review-card-status")).to_have_text("rejected")

    def test_undo_resets_to_pending(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        review_page.locator(".review-card").first.locator("button", has_text="Apply").click()
        expect(review_page.locator(".review-card").first).to_have_class(
            re.compile("status-approved")
        )

        review_page.locator(".review-card").first.locator("button", has_text="Undo").click()
        card = review_page.locator(".review-card").first
        expect(card.locator(".review-card-status")).to_have_text("pending")
        expect(card).not_to_have_class(re.compile("status-approved"))

    def test_apply_updates_summary(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        review_page.locator(".review-card").first.locator("button", has_text="Apply").click()

        summary = review_page.locator("#review-summary")
        expect(summary).to_contain_text("Apply: 1")
        expect(summary).to_contain_text("Pending: 2")

    def test_apply_on_second_item_via_tour(self, review_page: Page) -> None:
        """Navigate to second item, apply it, verify only that item changes."""
        _trigger_review_panel(review_page)
        review_page.locator("#tour-next").click()
        review_page.locator(".review-card").first.locator("button", has_text="Apply").click()

        card = review_page.locator(".review-card").first
        expect(card).to_have_class(re.compile("status-approved"))

        # Navigate back — first item should still be pending
        review_page.locator("#tour-prev").click()
        first_card = review_page.locator(".review-card").first
        expect(first_card.locator(".review-card-status")).to_have_text("pending")

    def test_status_persists_across_navigation(self, review_page: Page) -> None:
        """Apply first item, navigate away and back — status should persist."""
        _trigger_review_panel(review_page)
        review_page.locator(".review-card").first.locator("button", has_text="Apply").click()

        # Navigate to second item
        review_page.locator("#tour-next").click()
        expect(review_page.locator("#tour-progress")).to_have_text("2 of 3")

        # Navigate back to first item
        review_page.locator("#tour-prev").click()
        card = review_page.locator(".review-card").first
        expect(card).to_have_class(re.compile("status-approved"))
        expect(card.locator(".review-card-status")).to_have_text("approved")


# ---------------------------------------------------------------------------
# Bulk Apply All / Reject All
# ---------------------------------------------------------------------------


class TestBulkReview:
    """Verify Apply All and Reject All header buttons."""

    def test_apply_all(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        review_page.locator("#apply-all").click()

        # Current card should show approved
        card = review_page.locator(".review-card").first
        expect(card).to_have_class(re.compile("status-approved"))
        expect(review_page.locator("#review-summary")).to_contain_text("Apply: 3")

    def test_apply_all_affects_all_items_via_tour(self, review_page: Page) -> None:
        """After Apply All, navigating through all items should show approved."""
        _trigger_review_panel(review_page)
        review_page.locator("#apply-all").click()

        for i in range(3):
            card = review_page.locator(".review-card").first
            expect(card).to_have_class(re.compile("status-approved"))
            if i < 2:
                review_page.locator("#tour-next").click()

    def test_reject_all(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        review_page.locator("#reject-all").click()

        card = review_page.locator(".review-card").first
        expect(card).to_have_class(re.compile("status-rejected"))
        expect(review_page.locator("#review-summary")).to_contain_text("Rejected: 3")

    def test_reject_all_affects_all_items_via_tour(self, review_page: Page) -> None:
        """After Reject All, navigating through all items should show rejected."""
        _trigger_review_panel(review_page)
        review_page.locator("#reject-all").click()

        for i in range(3):
            card = review_page.locator(".review-card").first
            expect(card).to_have_class(re.compile("status-rejected"))
            if i < 2:
                review_page.locator("#tour-next").click()

    def test_execute_button_text_reflects_state(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        execute = review_page.locator("#execute-button")

        # No approved items → "Discard All"
        expect(execute).to_have_text("Discard All")

        # After apply all → "Execute Changes"
        review_page.locator("#apply-all").click()
        expect(execute).to_have_text("Execute Changes")


# ---------------------------------------------------------------------------
# Overlay messaging
# ---------------------------------------------------------------------------


class TestOverlayMessaging:
    """Verify the sidebar sends postMessage commands for overlay highlighting."""

    def test_overlay_apply_sent_on_tour_navigate(self, review_page: Page) -> None:
        """Navigating through items should send overlay:apply postMessages."""
        messages: list[dict] = []
        # In standalone mode window.parent === window, so postOverlayMessage
        # short-circuits.  Replace window.parent with a proxy that records
        # overlay messages while still forwarding postMessage to the real window.
        review_page.evaluate("""() => {
            window._overlayMessages = [];
            const fakeParent = {
                postMessage(msg, target) {
                    if (msg && msg.type && msg.type.startsWith('overlay:')) {
                        window._overlayMessages.push(msg);
                    }
                    return window.postMessage(msg, target);
                }
            };
            Object.defineProperty(window, 'parent', {
                get() { return fakeParent; },
                configurable: true,
            });
        }""")

        _trigger_review_panel(review_page)

        msgs = review_page.evaluate("() => window._overlayMessages")
        # At least one overlay:apply should have been sent for the first item
        apply_msgs = [m for m in msgs if m.get("type") == "overlay:apply"]
        assert len(apply_msgs) >= 1
        assert apply_msgs[-1]["item"]["resource_type"] == "AllergyIntolerance"


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


class TestExecution:
    """Verify executing or discarding the manifest."""

    def test_execute_hides_review_panel(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        review_page.locator("#apply-all").click()
        expect(review_page.locator("#execute-button")).to_have_text("Execute Changes")

        review_page.locator("#execute-button").click()
        expect(review_page.locator("#review-panel")).to_be_hidden()

    def test_execute_shows_completion_message(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        review_page.locator("#apply-all").click()
        review_page.locator("#execute-button").click()
        expect(review_page.locator("#review-panel")).to_be_hidden()

        last_msg = review_page.locator(".message.role-assistant").last
        expect(last_msg).to_contain_text("completed")

    def test_discard_hides_review_panel(self, review_page: Page) -> None:
        """When no items are approved, Execute acts as Discard All."""
        _trigger_review_panel(review_page)
        review_page.locator("#execute-button").click()
        expect(review_page.locator("#review-panel")).to_be_hidden()

    def test_status_returns_to_ready_after_execute(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        review_page.locator("#apply-all").click()
        review_page.locator("#execute-button").click()

        expect(review_page.locator("#status-text")).to_have_text("Ready")

    def test_partial_approve_then_execute(self, review_page: Page) -> None:
        """Approve only the first item, reject second, leave third pending."""
        _trigger_review_panel(review_page)

        # Approve first item
        review_page.locator(".review-card").first.locator("button", has_text="Apply").click()

        # Navigate to second and reject
        review_page.locator("#tour-next").click()
        review_page.locator(".review-card").first.locator("button", has_text="Reject").click()

        summary = review_page.locator("#review-summary")
        expect(summary).to_contain_text("Apply: 1")
        expect(summary).to_contain_text("Rejected: 1")
        expect(summary).to_contain_text("Pending: 1")

        # Execute — should succeed since at least one item is approved
        expect(review_page.locator("#execute-button")).to_have_text("Execute Changes")
        review_page.locator("#execute-button").click()
        expect(review_page.locator("#review-panel")).to_be_hidden()

    def test_tour_index_resets_on_new_manifest(self, review_page: Page) -> None:
        """After execution, sending a new message should reset tour to index 0."""
        _trigger_review_panel(review_page)
        # Navigate to item 3
        review_page.locator("#tour-next").click()
        review_page.locator("#tour-next").click()
        expect(review_page.locator("#tour-progress")).to_have_text("3 of 3")

        # Apply all and execute to clear the manifest
        review_page.locator("#apply-all").click()
        review_page.locator("#execute-button").click()
        expect(review_page.locator("#review-panel")).to_be_hidden()

        # Trigger a new manifest
        _trigger_review_panel(review_page)
        expect(review_page.locator("#tour-progress")).to_have_text("1 of 3")


# ---------------------------------------------------------------------------
# Verification results in review cards
# ---------------------------------------------------------------------------


@pytest.fixture
def review_page_with_verification(sidebar_page: Page) -> Page:
    """Sidebar page where approve returns verification failures."""
    sidebar_page.set_default_timeout(10_000)

    sidebar_page.route(
        "**/api/chat",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "session_id": MOCK_SESSION_ID,
                "response": "Here are the changes.",
                "manifest": _make_manifest(),
                "phase": "reviewing",
                "tool_calls_summary": None,
            }),
        ),
    )
    sidebar_page.route(
        "**/api/manifest/*/approve",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "session_id": MOCK_SESSION_ID,
                "manifest_id": MOCK_MANIFEST_ID,
                "results": [
                    {
                        "item_id": MOCK_ITEM_A_ID,
                        "check_name": "allergy_duplicate",
                        "passed": False,
                        "severity": "warning",
                        "message": "Penicillin allergy already exists",
                    },
                ],
                "passed": False,
            }),
        ),
    )
    return sidebar_page


class TestVerificationResults:
    """Verify verification check results are shown on review cards."""

    def test_verification_failure_disables_execute(
        self, review_page_with_verification: Page,
    ) -> None:
        page = review_page_with_verification
        _trigger_review_panel(page)
        page.locator(".review-card").first.locator("button", has_text="Apply").click()

        execute = page.locator("#execute-button")
        expect(execute).to_be_disabled()

    def test_verification_failure_shows_warning_summary(
        self, review_page_with_verification: Page,
    ) -> None:
        page = review_page_with_verification
        _trigger_review_panel(page)
        page.locator(".review-card").first.locator("button", has_text="Apply").click()

        summary = page.locator("#review-summary .verification-summary")
        expect(summary).to_contain_text("Verification failed")

    def test_verification_check_shown_on_card(
        self, review_page_with_verification: Page,
    ) -> None:
        page = review_page_with_verification
        _trigger_review_panel(page)
        page.locator(".review-card").first.locator("button", has_text="Apply").click()

        checks = page.locator(".verification-check")
        expect(checks.first).to_be_visible()
        expect(checks.first).to_contain_text("Penicillin allergy already exists")
