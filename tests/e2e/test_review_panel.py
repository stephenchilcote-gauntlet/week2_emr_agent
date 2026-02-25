"""Review panel tests: manifest rendering, apply/reject workflow, execution.

These tests mock the chat API to return a manifest, then verify the review
panel UI: card rendering, per-card Apply/Reject/Undo, bulk actions, and
execution flow.  No real LLM calls are needed.
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
                "action": "create",
                "proposed_value": {"medication": "Amoxicillin", "dosage": "500mg"},
                "current_value": None,
                "source_reference": "Clinical guideline",
                "description": "Prescribe amoxicillin",
                "confidence": "high",
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
                    {"id": MOCK_ITEM_B_ID, "status": "completed", "execution_result": "Created"},
                ],
            }),
        ),
    )
    return sidebar_page


def _trigger_review_panel(page: Page) -> None:
    """Send a chat message via Enter key to trigger the mocked manifest response."""
    page.locator("#chat-input").fill("Add penicillin allergy")
    page.locator("#chat-input").press("Enter")
    # Wait for panel, cards, and async operations to fully settle
    expect(page.locator("#review-panel")).to_be_visible(timeout=10_000)
    expect(page.locator(".review-card").first).to_be_visible()
    page.wait_for_load_state("networkidle")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


class TestReviewPanelRendering:
    """Verify the review panel renders correctly when a manifest is returned."""

    def test_review_panel_appears(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        expect(review_page.locator("#review-panel")).to_be_visible()

    def test_two_cards_rendered(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        assert review_page.locator(".review-card").count() == 2

    def test_card_shows_resource_type_and_action(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        first = review_page.locator(".review-card").first
        assert "AllergyIntolerance" in first.inner_text()
        assert "create" in first.inner_text()

    def test_card_shows_description(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        first = review_page.locator(".review-card").first
        assert "Add penicillin allergy" in first.inner_text()

    def test_cards_start_pending(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        badges = review_page.locator(".review-card-status")
        for i in range(badges.count()):
            expect(badges.nth(i)).to_have_text("pending")

    def test_summary_shows_pending_count(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        expect(review_page.locator("#review-summary")).to_contain_text("Pending: 2")

    def test_status_pill_shows_reviewing(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        expect(review_page.locator("#status-text")).to_have_text("Review Changes")


# ---------------------------------------------------------------------------
# Per-card Apply / Reject / Undo
# ---------------------------------------------------------------------------


class TestPerCardButtons:
    """Verify per-card Apply, Reject, and Undo buttons update card state."""

    def test_apply_marks_approved(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        review_page.locator(".review-card").first.locator("button", has_text="Apply").click()

        first = review_page.locator(".review-card").first
        expect(first).to_have_class(re.compile("status-approved"))
        expect(first.locator(".review-card-status")).to_have_text("approved")

    def test_reject_marks_rejected(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        review_page.locator(".review-card").first.locator("button", has_text="Reject").click()

        first = review_page.locator(".review-card").first
        expect(first).to_have_class(re.compile("status-rejected"))
        expect(first.locator(".review-card-status")).to_have_text("rejected")

    def test_undo_resets_to_pending(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        review_page.locator(".review-card").first.locator("button", has_text="Apply").click()
        expect(review_page.locator(".review-card").first).to_have_class(
            re.compile("status-approved")
        )

        review_page.locator(".review-card").first.locator("button", has_text="Undo").click()
        first = review_page.locator(".review-card").first
        expect(first.locator(".review-card-status")).to_have_text("pending")
        expect(first).not_to_have_class(re.compile("status-approved"))

    def test_apply_updates_summary(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        review_page.locator(".review-card").first.locator("button", has_text="Apply").click()

        summary = review_page.locator("#review-summary")
        expect(summary).to_contain_text("Apply: 1")
        expect(summary).to_contain_text("Pending: 1")

    def test_only_clicked_card_changes(self, review_page: Page) -> None:
        """Applying the first card should leave the second card pending."""
        _trigger_review_panel(review_page)
        review_page.locator(".review-card").first.locator("button", has_text="Apply").click()

        second = review_page.locator(".review-card").nth(1)
        expect(second.locator(".review-card-status")).to_have_text("pending")
        expect(second).not_to_have_class(re.compile("status-approved"))


# ---------------------------------------------------------------------------
# Bulk Apply All / Reject All
# ---------------------------------------------------------------------------


class TestBulkReview:
    """Verify Apply All and Reject All header buttons."""

    def test_apply_all(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        review_page.locator("#apply-all").click()

        cards = review_page.locator(".review-card")
        for i in range(cards.count()):
            expect(cards.nth(i)).to_have_class(re.compile("status-approved"))
        expect(review_page.locator("#review-summary")).to_contain_text("Apply: 2")

    def test_reject_all(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        review_page.locator("#reject-all").click()

        cards = review_page.locator(".review-card")
        for i in range(cards.count()):
            expect(cards.nth(i)).to_have_class(re.compile("status-rejected"))
        expect(review_page.locator("#review-summary")).to_contain_text("Rejected: 2")

    def test_execute_button_text_reflects_state(self, review_page: Page) -> None:
        _trigger_review_panel(review_page)
        execute = review_page.locator("#execute-button")

        # No approved items → "Discard All"
        expect(execute).to_have_text("Discard All")

        # After apply all → "Execute Changes"
        review_page.locator("#apply-all").click()
        expect(execute).to_have_text("Execute Changes")


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
