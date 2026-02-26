"""Overlay engine e2e tests: tab navigation, ghost row preview, word-level diff.

These tests load overlay.js into a Playwright page with a mock DOM (iframes
simulating OpenEMR's frame structure) and verify the three key overlay
behaviors directly via ``__overlayEngine.applyOverlay()``.

Tested behaviors (added in 22e6baf):
  1. Tab navigation — ``navigateToTab`` calls ``activateTabByName(tabName, true)``
  2. Ghost row preview — create overlays render EMR-matching rows using actual
     proposed data (buildDisplayTitle), NOT the old badge + description style.
  3. Word-level diff — update overlays read the current text from the DOM row
     itself, build proposed text via ``buildProposedRowText``, then render a
     word-level diff with strikethrough for removed and green highlight for added.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Page

pytestmark = pytest.mark.e2e

OVERLAY_JS = (
    Path(__file__).resolve().parents[2] / "web" / "sidebar" / "overlay.js"
).read_text(encoding="utf-8")


@pytest.fixture
def overlay_page(page: Page) -> Page:
    """Page with overlay.js loaded and mock OpenEMR-style iframe DOM."""
    page.set_default_timeout(10_000)
    page.goto("about:blank")

    # Build mock iframe DOM matching OpenEMR's frame structure.
    # Iframes created via JS inherit the parent's origin (about:blank)
    # so contentDocument is accessible.
    page.evaluate("""() => {
        // --- pat iframe (Patient Summary) ---
        const patFrame = document.createElement('iframe');
        patFrame.name = 'pat';
        patFrame.style.width = '100%';
        patFrame.style.height = '400px';
        document.body.appendChild(patFrame);

        const pd = patFrame.contentDocument;
        pd.body.innerHTML = `
            <div id="allergy_ps_expand">
                <div class="card"><div class="collapse show">
                    <div class="list-group list-group-flush">
                        <div class="list-group-item p-1" data-uuid="allergy-uuid-1">
                            Aspirin (Moderate)
                        </div>
                    </div>
                </div></div>
            </div>
            <div id="medication_ps_expand">
                <div class="card"><div class="collapse show">
                    <div class="list-group list-group-flush">
                        <div class="list-group-item p-0 pl-1" data-uuid="med-uuid-123">
                            Amoxicillin 250mg oral daily
                        </div>
                    </div>
                </div></div>
            </div>
            <div id="medical_problem_ps_expand">
                <div class="card"><div class="collapse show">
                    <div class="list-group list-group-flush">
                        <div class="list-group-item py-1 px-1" data-uuid="cond-uuid-1">
                            Hypertension
                        </div>
                    </div>
                </div></div>
            </div>
        `;

        // --- enc iframe (Encounter) ---
        const encFrame = document.createElement('iframe');
        encFrame.name = 'enc';
        document.body.appendChild(encFrame);
        encFrame.contentDocument.body.innerHTML = '<div>Encounter frame</div>';

        // --- Mock activateTabByName on window.top ---
        window._tabNavCalls = [];
        window.activateTabByName = function(tabName, force) {
            window._tabNavCalls.push({ tabName: tabName, force: force });
        };
    }""")

    # Load overlay.js from disk content
    page.add_script_tag(content=OVERLAY_JS)
    page.wait_for_function("() => !!window.__overlayEngine")

    return page


# ---------------------------------------------------------------------------
# Helper items
# ---------------------------------------------------------------------------

ALLERGY_CREATE_ITEM = {
    "id": "item-allergy-create",
    "resource_type": "AllergyIntolerance",
    "action": "create",
    "proposed_value": {"code": "12345", "display": "Penicillin"},
    "current_value": None,
    "description": "Add penicillin allergy",
    "target_resource_id": None,
}

MED_CREATE_ITEM = {
    "id": "item-med-create",
    "resource_type": "MedicationRequest",
    "action": "create",
    "proposed_value": {
        "drug": "Lisinopril",
        "dose": "10mg",
        "route": "oral",
        "freq": "daily",
    },
    "current_value": None,
    "description": "Add lisinopril",
    "target_resource_id": None,
}

MED_UPDATE_ITEM = {
    "id": "item-med-update",
    "resource_type": "MedicationRequest",
    "action": "update",
    "proposed_value": {
        "drug": "Amoxicillin",
        "dose": "500mg",
        "route": "oral",
        "freq": "daily",
    },
    "current_value": None,  # intentionally null — engine reads from DOM
    "description": "Increase amoxicillin dosage",
    "target_resource_id": "MedicationRequest/med-uuid-123",
}

ENCOUNTER_CREATE_ITEM = {
    "id": "item-enc-create",
    "resource_type": "Encounter",
    "action": "create",
    "proposed_value": {"type": "office-visit"},
    "current_value": None,
    "description": "Create follow-up encounter",
    "target_resource_id": None,
}

ALLERGY_DELETE_ITEM = {
    "id": "item-allergy-delete",
    "resource_type": "AllergyIntolerance",
    "action": "delete",
    "proposed_value": None,
    "current_value": {"code": "aspirin", "display": "Aspirin"},
    "description": "Remove aspirin allergy",
    "target_resource_id": "AllergyIntolerance/allergy-uuid-1",
}


# ---------------------------------------------------------------------------
# 1. Tab navigation
# ---------------------------------------------------------------------------


class TestTabNavigation:
    """Verify overlay calls activateTabByName before applying."""

    def test_navigate_to_pat_tab_for_allergy(self, overlay_page: Page) -> None:
        """AllergyIntolerance maps to 'pat' tab — navigateToTab should fire."""
        overlay_page.evaluate("""(item) => {
            window.__overlayEngine.applyOverlay(item);
        }""", ALLERGY_CREATE_ITEM)

        calls = overlay_page.evaluate("() => window._tabNavCalls")
        assert len(calls) >= 1
        assert calls[-1]["tabName"] == "pat"
        assert calls[-1]["force"] is True

    def test_navigate_to_pat_tab_for_medication(self, overlay_page: Page) -> None:
        overlay_page.evaluate("""(item) => {
            window.__overlayEngine.applyOverlay(item);
        }""", MED_CREATE_ITEM)

        calls = overlay_page.evaluate("() => window._tabNavCalls")
        assert calls[-1]["tabName"] == "pat"

    def test_no_tab_navigation_for_unsupported_resource(self, overlay_page: Page) -> None:
        """Encounter has supportsRowTarget=false, so applyOverlay returns
        sidebar-only without navigating."""
        overlay_page.evaluate("() => { window._tabNavCalls = []; }")
        result = overlay_page.evaluate("""(item) => {
            return window.__overlayEngine.applyOverlay(item);
        }""", ENCOUNTER_CREATE_ITEM)

        assert result["applied"] is False
        assert result["reason"] == "sidebar-only"
        calls = overlay_page.evaluate("() => window._tabNavCalls")
        assert len(calls) == 0

    def test_navigate_to_tab_exposed_on_engine(self, overlay_page: Page) -> None:
        """navigateToTab should be available on __overlayEngine."""
        has_fn = overlay_page.evaluate(
            "() => typeof window.__overlayEngine.navigateToTab === 'function'"
        )
        assert has_fn is True


# ---------------------------------------------------------------------------
# 2. Ghost row preview (create overlays)
# ---------------------------------------------------------------------------


class TestGhostRowPreview:
    """Verify create overlays render EMR-matching rows, not badge+description."""

    def test_ghost_row_has_emr_structure(self, overlay_page: Page) -> None:
        """Ghost row should use summary/flex-fill/font-weight-bold structure."""
        overlay_page.evaluate("""(item) => {
            window.__overlayEngine.applyOverlay(item);
        }""", ALLERGY_CREATE_ITEM)

        ghost = overlay_page.frame("pat").locator(".agent-overlay-ghost")
        assert ghost.count() == 1
        # EMR-matching structure
        assert ghost.locator(".flex-fill").count() == 1
        assert ghost.locator(".font-weight-bold").count() == 1

    def test_ghost_row_shows_display_title_from_proposed_value(
        self, overlay_page: Page,
    ) -> None:
        """Title should come from proposed_value (display + code), not description."""
        overlay_page.evaluate("""(item) => {
            window.__overlayEngine.applyOverlay(item);
        }""", ALLERGY_CREATE_ITEM)

        title_el = overlay_page.frame("pat").locator(
            ".agent-overlay-ghost .font-weight-bold",
        )
        text = title_el.inner_text()
        # buildDisplayTitle: "Penicillin (12345)"
        assert "Penicillin" in text
        assert "12345" in text

    def test_ghost_row_shows_pending_status(self, overlay_page: Page) -> None:
        overlay_page.evaluate("""(item) => {
            window.__overlayEngine.applyOverlay(item);
        }""", ALLERGY_CREATE_ITEM)

        ghost_text = overlay_page.frame("pat").locator(
            ".agent-overlay-ghost",
        ).inner_text()
        assert "(Pending)" in ghost_text

    def test_ghost_row_has_green_background(self, overlay_page: Page) -> None:
        """New style uses green (#ECFDF5), not the old yellow (#FEF3C7)."""
        overlay_page.evaluate("""(item) => {
            window.__overlayEngine.applyOverlay(item);
        }""", ALLERGY_CREATE_ITEM)

        bg = overlay_page.frame("pat").locator(".agent-overlay-ghost").evaluate(
            "el => el.style.background",
        )
        # Browser normalizes #ECFDF5 → rgb(236, 253, 245)
        assert "236, 253, 245" in bg or "ecfdf5" in bg.lower()

    def test_ghost_row_has_no_suggested_badge(self, overlay_page: Page) -> None:
        """New style does NOT use the old 'Suggested' badge."""
        overlay_page.evaluate("""(item) => {
            window.__overlayEngine.applyOverlay(item);
        }""", ALLERGY_CREATE_ITEM)

        badges = overlay_page.frame("pat").locator(
            ".agent-overlay-ghost .agent-overlay-badge",
        )
        assert badges.count() == 0

    def test_medication_ghost_row_composes_drug_fields(
        self, overlay_page: Page,
    ) -> None:
        """Medication create should compose title from drug/dose/route/freq."""
        overlay_page.evaluate("""(item) => {
            window.__overlayEngine.applyOverlay(item);
        }""", MED_CREATE_ITEM)

        title_el = overlay_page.frame("pat").locator(
            ".agent-overlay-ghost .font-weight-bold",
        )
        text = title_el.inner_text()
        # buildDisplayTitle: "Lisinopril 10mg oral daily"
        assert "Lisinopril" in text
        assert "10mg" in text
        assert "oral" in text
        assert "daily" in text


# ---------------------------------------------------------------------------
# 3. Word-level diff for updates
# ---------------------------------------------------------------------------


class TestWordLevelDiff:
    """Verify update overlays show word-level diff from DOM text."""

    def test_update_overlay_has_green_background(self, overlay_page: Page) -> None:
        """Update rows should use green (#ECFDF5) background."""
        overlay_page.evaluate("""(item) => {
            window.__overlayEngine.applyOverlay(item);
        }""", MED_UPDATE_ITEM)

        row = overlay_page.frame("pat").locator('[data-uuid="med-uuid-123"]')
        bg = row.evaluate("el => el.style.background")
        # Browser normalizes #ECFDF5 → rgb(236, 253, 245)
        assert "236, 253, 245" in bg or "ecfdf5" in bg.lower()

    def test_update_overlay_has_border_left(self, overlay_page: Page) -> None:
        """New style adds a green left border."""
        overlay_page.evaluate("""(item) => {
            window.__overlayEngine.applyOverlay(item);
        }""", MED_UPDATE_ITEM)

        row = overlay_page.frame("pat").locator('[data-uuid="med-uuid-123"]')
        border = row.evaluate("el => el.style.borderLeft")
        # Browser normalizes #10b981 → rgb(16, 185, 129)
        assert "16, 185, 129" in border or "10b981" in border.lower()

    def test_update_overlay_shows_diff_element(self, overlay_page: Page) -> None:
        """An .agent-overlay-diff element should be appended to the row."""
        overlay_page.evaluate("""(item) => {
            window.__overlayEngine.applyOverlay(item);
        }""", MED_UPDATE_ITEM)

        diff = overlay_page.frame("pat").locator(
            '[data-uuid="med-uuid-123"] .agent-overlay-diff',
        )
        assert diff.count() == 1

    def test_diff_shows_removed_text_with_strikethrough(
        self, overlay_page: Page,
    ) -> None:
        """The old dosage '250mg' should appear with strikethrough."""
        overlay_page.evaluate("""(item) => {
            window.__overlayEngine.applyOverlay(item);
        }""", MED_UPDATE_ITEM)

        diff = overlay_page.frame("pat").locator(
            '[data-uuid="med-uuid-123"] .agent-overlay-diff',
        )
        # Find the strikethrough span
        del_span = diff.locator("span[style*='line-through']")
        assert del_span.count() >= 1
        assert "250mg" in del_span.inner_text()

    def test_diff_shows_added_text_with_green_highlight(
        self, overlay_page: Page,
    ) -> None:
        """The new dosage '500mg' should appear with green highlight (#D1FAE5)."""
        overlay_page.evaluate("""(item) => {
            window.__overlayEngine.applyOverlay(item);
        }""", MED_UPDATE_ITEM)

        diff = overlay_page.frame("pat").locator(
            '[data-uuid="med-uuid-123"] .agent-overlay-diff',
        )
        # Find the bold span (font-weight:600) that contains the added text
        add_span = diff.locator("span[style*='font-weight']")
        assert add_span.count() >= 1
        assert "500mg" in add_span.inner_text()
        # Verify it has the green highlight background
        bg = add_span.evaluate("el => el.style.background")
        assert "209, 250, 229" in bg or "d1fae5" in bg.lower()

    def test_diff_preserves_unchanged_words(self, overlay_page: Page) -> None:
        """Unchanged words (Amoxicillin, oral, daily) should appear normally."""
        overlay_page.evaluate("""(item) => {
            window.__overlayEngine.applyOverlay(item);
        }""", MED_UPDATE_ITEM)

        diff_text = overlay_page.frame("pat").locator(
            '[data-uuid="med-uuid-123"] .agent-overlay-diff',
        ).inner_text()
        assert "Amoxicillin" in diff_text

    def test_update_has_no_suggested_badge(self, overlay_page: Page) -> None:
        """New style does NOT use the old 'Suggested' badge on updates."""
        overlay_page.evaluate("""(item) => {
            window.__overlayEngine.applyOverlay(item);
        }""", MED_UPDATE_ITEM)

        badges = overlay_page.frame("pat").locator(
            '[data-uuid="med-uuid-123"] .agent-overlay-badge',
        )
        assert badges.count() == 0

    def test_update_reads_from_dom_not_current_value(
        self, overlay_page: Page,
    ) -> None:
        """Even with current_value=null, diff works by reading DOM text."""
        # MED_UPDATE_ITEM has current_value=None — old code would skip diff
        result = overlay_page.evaluate("""(item) => {
            return window.__overlayEngine.applyOverlay(item);
        }""", MED_UPDATE_ITEM)

        assert result["applied"] is True
        diff = overlay_page.frame("pat").locator(
            '[data-uuid="med-uuid-123"] .agent-overlay-diff',
        )
        # Diff element should exist because DOM text differs from proposed
        assert diff.count() == 1


# ---------------------------------------------------------------------------
# 4. clearAllOverlays restores state
# ---------------------------------------------------------------------------


class TestClearOverlays:
    """Verify clearAllOverlays properly restores modified rows."""

    def test_clear_removes_ghost_rows(self, overlay_page: Page) -> None:
        overlay_page.evaluate("""(item) => {
            window.__overlayEngine.applyOverlay(item);
        }""", ALLERGY_CREATE_ITEM)
        assert overlay_page.frame("pat").locator(".agent-overlay-ghost").count() == 1

        overlay_page.evaluate("() => window.__overlayEngine.clearAllOverlays()")
        assert overlay_page.frame("pat").locator(".agent-overlay-ghost").count() == 0

    def test_clear_restores_update_row_background(self, overlay_page: Page) -> None:
        overlay_page.evaluate("""(item) => {
            window.__overlayEngine.applyOverlay(item);
        }""", MED_UPDATE_ITEM)

        overlay_page.evaluate("() => window.__overlayEngine.clearAllOverlays()")

        row = overlay_page.frame("pat").locator('[data-uuid="med-uuid-123"]')
        bg = row.evaluate("el => el.style.background")
        assert bg == ""

    def test_clear_removes_diff_elements(self, overlay_page: Page) -> None:
        overlay_page.evaluate("""(item) => {
            window.__overlayEngine.applyOverlay(item);
        }""", MED_UPDATE_ITEM)

        overlay_page.evaluate("() => window.__overlayEngine.clearAllOverlays()")

        diffs = overlay_page.frame("pat").locator(
            '[data-uuid="med-uuid-123"] .agent-overlay-diff',
        )
        assert diffs.count() == 0


# ---------------------------------------------------------------------------
# 5. Apply all overlays (batch mode)
# ---------------------------------------------------------------------------


class TestApplyAllOverlays:
    """Verify applyAllOverlays shows all items simultaneously."""

    def test_apply_all_exposed_on_engine(self, overlay_page: Page) -> None:
        """applyAllOverlays should be a function on __overlayEngine."""
        has_fn = overlay_page.evaluate(
            "() => typeof window.__overlayEngine.applyAllOverlays === 'function'"
        )
        assert has_fn is True

    def test_apply_all_shows_ghost_and_update_simultaneously(
        self, overlay_page: Page,
    ) -> None:
        """Both allergy ghost row and med update diff visible at same time."""
        overlay_page.evaluate("""(items) => {
            window.__overlayEngine.applyAllOverlays(items);
        }""", [ALLERGY_CREATE_ITEM, MED_UPDATE_ITEM])

        pat = overlay_page.frame("pat")
        assert pat.locator(".agent-overlay-ghost").count() == 1
        assert pat.locator('[data-uuid="med-uuid-123"] .agent-overlay-diff').count() == 1

    def test_apply_all_clears_previous_overlays_first(
        self, overlay_page: Page,
    ) -> None:
        """Calling applyAllOverlays twice clears previous overlays."""
        overlay_page.evaluate("""(items) => {
            window.__overlayEngine.applyAllOverlays(items);
        }""", [ALLERGY_CREATE_ITEM])
        assert overlay_page.frame("pat").locator(".agent-overlay-ghost").count() == 1

        overlay_page.evaluate("""(items) => {
            window.__overlayEngine.applyAllOverlays(items);
        }""", [MED_CREATE_ITEM])

        pat = overlay_page.frame("pat")
        ghosts = pat.locator(".agent-overlay-ghost")
        assert ghosts.count() == 1
        assert "Lisinopril" in ghosts.inner_text()

    def test_apply_all_returns_results_for_each_item(
        self, overlay_page: Page,
    ) -> None:
        """Returns array of {itemId, applied, reason} for each item."""
        results = overlay_page.evaluate("""(items) => {
            return window.__overlayEngine.applyAllOverlays(items);
        }""", [ALLERGY_CREATE_ITEM, MED_UPDATE_ITEM, ENCOUNTER_CREATE_ITEM])

        assert len(results) == 3
        assert results[0]["itemId"] == "item-allergy-create"
        assert results[0]["applied"] is True
        assert results[1]["itemId"] == "item-med-update"
        assert results[1]["applied"] is True
        assert results[2]["itemId"] == "item-enc-create"
        assert results[2]["applied"] is False
        assert results[2]["reason"] == "sidebar-only"

    def test_apply_all_skips_unsupported_resources(
        self, overlay_page: Page,
    ) -> None:
        """Encounter items don't get overlaid but don't break the batch."""
        results = overlay_page.evaluate("""(items) => {
            return window.__overlayEngine.applyAllOverlays(items);
        }""", [ENCOUNTER_CREATE_ITEM])

        assert len(results) == 1
        assert results[0]["applied"] is False

    def test_apply_all_navigates_to_tab(self, overlay_page: Page) -> None:
        """Tab navigation fires for items in the batch."""
        overlay_page.evaluate("() => { window._tabNavCalls = []; }")
        overlay_page.evaluate("""(items) => {
            window.__overlayEngine.applyAllOverlays(items);
        }""", [ALLERGY_CREATE_ITEM])

        calls = overlay_page.evaluate("() => window._tabNavCalls")
        assert len(calls) >= 1
        assert calls[-1]["tabName"] == "pat"


# ---------------------------------------------------------------------------
# 6. Inline action buttons on overlay elements
# ---------------------------------------------------------------------------


class TestInlineActionButtons:
    """Verify each overlay element has accept/reject/prev/next buttons."""

    def test_create_overlay_has_action_buttons(self, overlay_page: Page) -> None:
        """Ghost row should contain .agent-overlay-actions container."""
        overlay_page.evaluate("""(item) => {
            window.__overlayEngine.applyOverlay(item);
        }""", ALLERGY_CREATE_ITEM)

        actions = overlay_page.frame("pat").locator(
            ".agent-overlay-ghost .agent-overlay-actions",
        )
        assert actions.count() == 1

    def test_update_overlay_has_action_buttons(self, overlay_page: Page) -> None:
        overlay_page.evaluate("""(item) => {
            window.__overlayEngine.applyOverlay(item);
        }""", MED_UPDATE_ITEM)

        actions = overlay_page.frame("pat").locator(
            '[data-uuid="med-uuid-123"] .agent-overlay-actions',
        )
        assert actions.count() == 1

    def test_delete_overlay_has_action_buttons(self, overlay_page: Page) -> None:
        overlay_page.evaluate("""(item) => {
            window.__overlayEngine.applyOverlay(item);
        }""", ALLERGY_DELETE_ITEM)

        actions = overlay_page.frame("pat").locator(
            '[data-uuid="allergy-uuid-1"] .agent-overlay-actions',
        )
        assert actions.count() == 1

    def test_action_buttons_has_four_buttons(self, overlay_page: Page) -> None:
        """Actions container should have exactly 4 buttons."""
        overlay_page.evaluate("""(item) => {
            window.__overlayEngine.applyOverlay(item);
        }""", ALLERGY_CREATE_ITEM)

        buttons = overlay_page.frame("pat").locator(
            ".agent-overlay-ghost .agent-overlay-actions button",
        )
        assert buttons.count() == 4

    def test_action_buttons_2x2_grid_layout(self, overlay_page: Page) -> None:
        """Actions container should use CSS grid with 2 columns."""
        overlay_page.evaluate("""(item) => {
            window.__overlayEngine.applyOverlay(item);
        }""", ALLERGY_CREATE_ITEM)

        grid = overlay_page.frame("pat").locator(
            ".agent-overlay-ghost .agent-overlay-actions",
        )
        display = grid.evaluate("el => getComputedStyle(el).display")
        cols = grid.evaluate(
            "el => getComputedStyle(el).gridTemplateColumns",
        )
        assert display == "grid"
        # 2 column tracks — browser reports something like "Xpx Ypx"
        assert len(cols.split()) >= 2

    def test_has_accept_button(self, overlay_page: Page) -> None:
        overlay_page.evaluate("""(item) => {
            window.__overlayEngine.applyOverlay(item);
        }""", ALLERGY_CREATE_ITEM)

        btn = overlay_page.frame("pat").locator(
            ".agent-overlay-ghost .overlay-btn-accept",
        )
        assert btn.count() == 1

    def test_has_reject_button(self, overlay_page: Page) -> None:
        overlay_page.evaluate("""(item) => {
            window.__overlayEngine.applyOverlay(item);
        }""", ALLERGY_CREATE_ITEM)

        btn = overlay_page.frame("pat").locator(
            ".agent-overlay-ghost .overlay-btn-reject",
        )
        assert btn.count() == 1

    def test_has_prev_button(self, overlay_page: Page) -> None:
        overlay_page.evaluate("""(item) => {
            window.__overlayEngine.applyOverlay(item);
        }""", ALLERGY_CREATE_ITEM)

        btn = overlay_page.frame("pat").locator(
            ".agent-overlay-ghost .overlay-btn-prev",
        )
        assert btn.count() == 1

    def test_has_next_button(self, overlay_page: Page) -> None:
        overlay_page.evaluate("""(item) => {
            window.__overlayEngine.applyOverlay(item);
        }""", ALLERGY_CREATE_ITEM)

        btn = overlay_page.frame("pat").locator(
            ".agent-overlay-ghost .overlay-btn-next",
        )
        assert btn.count() == 1

    def test_accept_button_has_data_item_id(self, overlay_page: Page) -> None:
        """Buttons should carry the item ID for identification."""
        overlay_page.evaluate("""(item) => {
            window.__overlayEngine.applyOverlay(item);
        }""", ALLERGY_CREATE_ITEM)

        item_id = overlay_page.frame("pat").locator(
            ".overlay-btn-accept",
        ).evaluate("el => el.dataset.itemId")
        assert item_id == "item-allergy-create"


# ---------------------------------------------------------------------------
# 7. Inline button postMessage communication
# ---------------------------------------------------------------------------


def _setup_action_listener(overlay_page: Page) -> None:
    """Record overlay action messages on the parent window."""
    overlay_page.evaluate("""() => {
        window._overlayActions = [];
        window.addEventListener('message', function handler(e) {
            if (e.data && e.data.type && (
                e.data.type === 'overlay:accept' ||
                e.data.type === 'overlay:reject' ||
                e.data.type === 'overlay:navigate'
            )) {
                window._overlayActions.push(e.data);
            }
        });
    }""")


class TestInlineButtonMessages:
    """Verify button clicks send correct postMessages to the parent window."""

    def test_accept_sends_overlay_accept_message(self, overlay_page: Page) -> None:
        _setup_action_listener(overlay_page)
        overlay_page.evaluate("""(item) => {
            window.__overlayEngine.applyOverlay(item);
        }""", ALLERGY_CREATE_ITEM)

        overlay_page.frame("pat").locator(".overlay-btn-accept").click()
        overlay_page.wait_for_function("() => window._overlayActions.length > 0")

        actions = overlay_page.evaluate("() => window._overlayActions")
        assert len(actions) == 1
        assert actions[0]["type"] == "overlay:accept"
        assert actions[0]["itemId"] == "item-allergy-create"

    def test_reject_sends_overlay_reject_message(self, overlay_page: Page) -> None:
        _setup_action_listener(overlay_page)
        overlay_page.evaluate("""(item) => {
            window.__overlayEngine.applyOverlay(item);
        }""", ALLERGY_CREATE_ITEM)

        overlay_page.frame("pat").locator(".overlay-btn-reject").click()
        overlay_page.wait_for_function("() => window._overlayActions.length > 0")

        actions = overlay_page.evaluate("() => window._overlayActions")
        assert actions[0]["type"] == "overlay:reject"
        assert actions[0]["itemId"] == "item-allergy-create"

    def test_prev_sends_overlay_navigate_minus_1(self, overlay_page: Page) -> None:
        _setup_action_listener(overlay_page)
        overlay_page.evaluate("""(item) => {
            window.__overlayEngine.applyOverlay(item);
        }""", ALLERGY_CREATE_ITEM)

        overlay_page.frame("pat").locator(".overlay-btn-prev").click()
        overlay_page.wait_for_function("() => window._overlayActions.length > 0")

        actions = overlay_page.evaluate("() => window._overlayActions")
        assert actions[0]["type"] == "overlay:navigate"
        assert actions[0]["delta"] == -1

    def test_next_sends_overlay_navigate_plus_1(self, overlay_page: Page) -> None:
        _setup_action_listener(overlay_page)
        overlay_page.evaluate("""(item) => {
            window.__overlayEngine.applyOverlay(item);
        }""", ALLERGY_CREATE_ITEM)

        overlay_page.frame("pat").locator(".overlay-btn-next").click()
        overlay_page.wait_for_function("() => window._overlayActions.length > 0")

        actions = overlay_page.evaluate("() => window._overlayActions")
        assert actions[0]["type"] == "overlay:navigate"
        assert actions[0]["delta"] == 1
