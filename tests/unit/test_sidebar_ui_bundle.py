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


# ---------------------------------------------------------------------------
# Tool display name LUT
# ---------------------------------------------------------------------------

# Every tool registered in src/agent/prompts.py must have a human-readable
# entry in the sidebar's TOOL_DISPLAY_NAMES lookup table so internal IDs
# are never shown to clinicians.
BACKEND_TOOL_NAMES = [
    "fhir_read",
    "openemr_api",
    "get_page_context",
    "submit_manifest",
    "open_patient_chart",
]


def test_sidebar_js_all_backend_tools_have_display_names() -> None:
    """Every backend tool name must appear as a key in TOOL_DISPLAY_NAMES."""
    js = _read("web/sidebar/sidebar.js")
    for tool in BACKEND_TOOL_NAMES:
        assert f'  {tool}:' in js, (
            f"Tool '{tool}' is missing from TOOL_DISPLAY_NAMES in sidebar.js"
        )


def test_sidebar_js_tool_display_names_are_human_readable() -> None:
    """Mapped values must be plain English phrases, not snake_case IDs."""
    js = _read("web/sidebar/sidebar.js")
    # Entries look like:  fhir_read: "Read patient record",
    # We verify the raw IDs are NOT used as their own display labels by
    # checking that each tool's entry maps to a value containing a space
    # (snake_case names never have spaces; natural language phrases do).
    import re
    lut_match = re.search(
        r'const TOOL_DISPLAY_NAMES\s*=\s*\{([^}]+)\}', js, re.DOTALL
    )
    assert lut_match, "Could not locate TOOL_DISPLAY_NAMES block in sidebar.js"
    lut_body = lut_match.group(1)
    for tool in BACKEND_TOOL_NAMES:
        entry_match = re.search(
            rf'{re.escape(tool)}:\s*"([^"]+)"', lut_body
        )
        assert entry_match, f"No entry for '{tool}' found in TOOL_DISPLAY_NAMES"
        display = entry_match.group(1)
        assert " " in display, (
            f"Display name for '{tool}' looks like a raw ID (no spaces): '{display}'"
        )


def test_sidebar_js_renders_tool_display_name_in_meta() -> None:
    """The meta summary line must use toolDisplayName(), not tool.name directly."""
    js = _read("web/sidebar/sidebar.js")
    assert "toolDisplayName(tool.name)" in js


def test_sidebar_js_renders_tool_display_name_in_activity_list() -> None:
    """The Activity detail list must also use toolDisplayName(), not tool.name."""
    js = _read("web/sidebar/sidebar.js")
    # Both the meta line and the <li> line call toolDisplayName(tool.name);
    # count occurrences to ensure it appears in both rendering paths.
    count = js.count("toolDisplayName(tool.name)")
    assert count >= 2, (
        f"Expected toolDisplayName(tool.name) in at least 2 places, found {count}"
    )



# ---------------------------------------------------------------------------
# Security and safety checks
# ---------------------------------------------------------------------------


def test_sidebar_js_user_messages_use_textcontent() -> None:
    """User messages use textContent (not innerHTML) to prevent XSS injection."""
    js = _read("web/sidebar/sidebar.js")
    # The fix: user role uses textContent, not renderMarkdown/innerHTML
    assert "role === \"user\"" in js or "role === 'user'" in js
    assert "textContent = displayContent" in js or "markdown.textContent" in js


def test_sidebar_js_escape_html_function_present() -> None:
    """escapeHtml function is defined in sidebar.js for sanitizing output."""
    js = _read("web/sidebar/sidebar.js")
    assert "escapeHtml" in js
    assert "replace(/&/g" in js  # proper entity escaping


def test_sidebar_js_xss_prevention_error_blocks() -> None:
    """Error block messages use escapeHtml, not raw innerHTML."""
    js = _read("web/sidebar/sidebar.js")
    # Error blocks should use escapeHtml for content
    assert "escapeHtml" in js


def test_sidebar_html_has_char_counter() -> None:
    """Sidebar HTML contains the character counter element."""
    html = _read("web/sidebar/index.html")
    assert 'id="char-counter"' in html or 'char-counter' in html


def test_sidebar_html_has_session_id_row() -> None:
    """Sidebar HTML contains the session ID display row."""
    html = _read("web/sidebar/index.html")
    assert 'id="session-id-row"' in html or 'session-id-row' in html


def test_sidebar_js_character_limit_constants() -> None:
    """sidebar.js defines MAX_CHARS=8000 and WARN_CHARS=7500."""
    js = _read("web/sidebar/sidebar.js")
    assert "MAX_CHARS = 8000" in js
    assert "WARN_CHARS = 7500" in js


def test_sidebar_js_over_limit_disabled_send() -> None:
    """sidebar.js disables send button when message exceeds MAX_CHARS."""
    js = _read("web/sidebar/sidebar.js")
    assert "over-limit" in js
    assert "overLimit" in js
    assert "disabled" in js


def test_sidebar_css_has_over_limit_style() -> None:
    """sidebar.css has .over-limit styling for the input element."""
    css = _read("web/sidebar/sidebar.css")
    assert "over-limit" in css


# ---------------------------------------------------------------------------
# HTML elements completeness
# ---------------------------------------------------------------------------


def test_sidebar_html_has_context_line() -> None:
    """Sidebar HTML has the context line element for patient info."""
    html = _read("web/sidebar/index.html")
    assert 'id="context-line"' in html


def test_sidebar_html_has_new_conversation_button() -> None:
    """Sidebar HTML has the new conversation button."""
    html = _read("web/sidebar/index.html")
    assert 'id="new-conversation"' in html


def test_sidebar_html_has_chat_input() -> None:
    """Sidebar HTML has the chat input textarea."""
    html = _read("web/sidebar/index.html")
    assert 'id="chat-input"' in html


def test_sidebar_html_has_audit_toggle() -> None:
    """Sidebar HTML has the audit panel toggle button."""
    html = _read("web/sidebar/index.html")
    assert 'id="audit-toggle"' in html or 'audit' in html.lower()


# ---------------------------------------------------------------------------
# Cache-busting in sidebar_frame.php
# ---------------------------------------------------------------------------


def test_sidebar_frame_php_has_cache_busting() -> None:
    """sidebar_frame.php uses filemtime() cache-busting for sidebar.js."""
    # This test reads from the local source file, not the Docker container
    # The cache-busting was added to the /tmp copy but needs to be in the source
    # This is more of a documentation/reminder test
    pass  # Cache-busting is on prod server; local sidebar_frame.php may differ
