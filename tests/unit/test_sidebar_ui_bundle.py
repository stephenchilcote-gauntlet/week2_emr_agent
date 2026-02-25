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


def test_embed_js_injects_sidebar_frame() -> None:
    js = _read("web/sidebar/embed.js")
    assert "openemr-clinical-assistant-sidebar" in js
    assert "/agent-api/ui" in js
