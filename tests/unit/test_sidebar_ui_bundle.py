from __future__ import annotations

from pathlib import Path


def _read(path: str) -> str:
    root = Path(__file__).resolve().parents[2]
    return (root / path).read_text(encoding="utf-8")


def test_sidebar_html_contains_required_sections() -> None:
    html = _read("web/sidebar/index.html")
    assert "id=\"history-toggle\"" in html
    assert "id=\"history-panel\"" in html
    assert "id=\"chat-area\"" in html
    assert "id=\"review-panel\"" in html
    assert "id=\"send-button\"" in html


def test_sidebar_css_has_interactive_styles() -> None:
    css = _read("web/sidebar/sidebar.css")
    assert ".header-btn:hover" in css
    assert "@keyframes pulse" in css


def test_sidebar_js_persists_session_and_handles_review() -> None:
    js = _read("web/sidebar/sidebar.js")
    assert "openemr_agent_session_id" in js
    assert "renderReviewPanel" in js
    assert "executeManifest" in js


def test_sidebar_js_has_tour_mode() -> None:
    js = _read("web/sidebar/sidebar.js")
    assert "tourIndex" in js
    assert "tourNavigate" in js
    assert "renderTourCard" in js
    assert "postOverlayMessage" in js


def test_sidebar_html_has_tour_navigation() -> None:
    html = _read("web/sidebar/index.html")
    assert 'id="tour-prev"' in html
    assert 'id="tour-next"' in html
    assert 'id="tour-progress"' in html


def test_embed_js_injects_sidebar_frame() -> None:
    js = _read("web/sidebar/embed.js")
    assert "openemr-clinical-assistant-sidebar" in js
    # Path is constructed via a moduleRoot variable — check both parts
    assert "oe-module-clinical-assistant/public" in js
    assert "sidebar_frame.php" in js


def test_embed_js_loads_overlay_script() -> None:
    js = _read("web/sidebar/embed.js")
    assert "overlay.js" in js


def test_overlay_js_has_resource_map_and_message_handler() -> None:
    js = _read("web/sidebar/overlay.js")
    assert "RESOURCE_PAGE_MAP" in js
    assert "overlay:apply" in js
    assert "overlay:clear" in js
    assert "applyOverlay" in js
    assert "clearAllOverlays" in js
    assert "agent-overlay-ghost" in js
    assert "#medical_problem_ps_expand" in js
    assert "#allergy_ps_expand" in js
    assert "#medication_ps_expand" in js


def test_overlay_js_handles_create_update_delete() -> None:
    js = _read("web/sidebar/overlay.js")
    assert "applyCreateOverlay" in js
    assert "applyUpdateOverlay" in js
    assert "applyDeleteOverlay" in js
    assert "#ECFDF5" in js  # green tint for create/update preview
    assert "#FEE2E2" in js  # red for delete
    assert "line-through" in js  # strikethrough for delete


def test_overlay_js_navigates_to_correct_tab() -> None:
    js = _read("web/sidebar/overlay.js")
    assert "navigateToTab" in js
    assert "activateTabByName" in js


def test_twig_templates_have_data_uuid() -> None:
    root = Path(__file__).resolve().parents[2]
    for path in [
        "openemr/templates/patient/card/medical_problems.html.twig",
        "openemr/templates/patient/card/allergies.html.twig",
        "openemr/templates/patient/card/medication.html.twig",
    ]:
        full = root / path
        if not full.exists():
            import pytest
            pytest.skip(f"Twig override not committed to repo: {path}")
        content = full.read_text(encoding="utf-8")
        assert "data-uuid=" in content


def test_sidebar_css_has_tour_and_confidence_styles() -> None:
    css = _read("web/sidebar/sidebar.css")
    assert ".tour-arrow" in css
    assert ".tour-progress" in css
    assert ".confidence-badge" in css
    assert ".review-card-action-icon" in css
    assert ".review-card-description" in css

