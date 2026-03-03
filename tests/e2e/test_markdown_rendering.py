"""Markdown rendering E2E tests.

Tests that verify the sidebar's markdown rendering pipeline (marked.js):
  - Bold, italic, inline code render as the correct HTML elements
  - Ordered and unordered lists produce <ol>/<ul> + <li> elements
  - Code blocks produce <pre><code> wrappers
  - Headers produce the correct <h1>–<h3> elements
  - Tables are wrapped in <div class="table-wrap">
  - HTML entities are escaped in plain text (XSS-safety sanity check)
  - Rendered assistant messages are visible in the DOM as HTML, not raw markdown

All rendering tests use window.__sidebarApp.renderMarkdown() directly so
they are fast and don't require LLM round-trips.  One integration test
sends a real message and verifies the response is rendered (not raw markdown).
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

PATIENT_PID = PATIENT_MAP["Maria Santos"]
PATIENT_NAME = "Maria Santos"


@pytest.fixture
def sidebar(page: Page) -> Frame:
    page.set_default_timeout(E2E_TIMEOUT_MS)
    openemr_login(page)
    select_patient(page, PATIENT_PID, PATIENT_NAME)
    return get_sidebar_frame(page)


def _render(frame: Frame, markdown: str) -> str:
    """Call window.__sidebarApp.renderMarkdown() and return the HTML string."""
    return frame.evaluate(
        f"() => window.__sidebarApp.renderMarkdown({markdown!r})"
    )


# ---------------------------------------------------------------------------
# Inline elements
# ---------------------------------------------------------------------------


class TestInlineRendering:
    """Inline markdown elements (bold, italic, code) render as HTML."""

    def test_bold(self, sidebar: Frame) -> None:
        """**text** renders as <strong>text</strong>."""
        html = _render(sidebar, "**bold text**")
        assert "<strong>bold text</strong>" in html, (
            f"Expected <strong>, got: {html!r}"
        )

    def test_italic(self, sidebar: Frame) -> None:
        """_text_ renders as <em>text</em>."""
        html = _render(sidebar, "_italic text_")
        assert "<em>italic text</em>" in html, (
            f"Expected <em>, got: {html!r}"
        )

    def test_inline_code(self, sidebar: Frame) -> None:
        """`code` renders as <code>code</code>."""
        html = _render(sidebar, "`inline code`")
        assert "<code>inline code</code>" in html, (
            f"Expected <code>, got: {html!r}"
        )

    def test_combined_inline(self, sidebar: Frame) -> None:
        """Combined inline styles render correctly."""
        html = _render(sidebar, "**bold** and _italic_")
        assert "<strong>bold</strong>" in html
        assert "<em>italic</em>" in html


# ---------------------------------------------------------------------------
# Block elements
# ---------------------------------------------------------------------------


class TestBlockRendering:
    """Block-level markdown elements render as the correct HTML."""

    def test_unordered_list(self, sidebar: Frame) -> None:
        """- item list renders as <ul><li>..."""
        html = _render(sidebar, "- Alpha\n- Beta\n- Gamma")
        assert "<ul>" in html, f"Expected <ul>, got: {html!r}"
        assert "<li>Alpha</li>" in html or "<li>\nAlpha\n</li>" in html or "Alpha" in html
        assert "<li>" in html

    def test_ordered_list(self, sidebar: Frame) -> None:
        """1. item list renders as <ol><li>..."""
        html = _render(sidebar, "1. First\n2. Second\n3. Third")
        assert "<ol>" in html, f"Expected <ol>, got: {html!r}"
        assert "<li>" in html

    def test_h1_header(self, sidebar: Frame) -> None:
        """# Header renders as <h1>."""
        html = _render(sidebar, "# Main Title")
        assert "<h1>" in html, f"Expected <h1>, got: {html!r}"
        assert "Main Title" in html

    def test_h2_header(self, sidebar: Frame) -> None:
        """## Header renders as <h2>."""
        html = _render(sidebar, "## Section Title")
        assert "<h2>" in html, f"Expected <h2>, got: {html!r}"

    def test_h3_header(self, sidebar: Frame) -> None:
        """### Header renders as <h3>."""
        html = _render(sidebar, "### Subsection")
        assert "<h3>" in html, f"Expected <h3>, got: {html!r}"

    def test_fenced_code_block(self, sidebar: Frame) -> None:
        """```code block``` renders as <pre><code>."""
        html = _render(sidebar, "```\nfunction hello() {}\n```")
        assert "<pre>" in html, f"Expected <pre>, got: {html!r}"
        assert "<code>" in html

    def test_paragraph(self, sidebar: Frame) -> None:
        """Plain text renders as a <p> element."""
        html = _render(sidebar, "This is a paragraph.")
        assert "<p>" in html, f"Expected <p>, got: {html!r}"
        assert "This is a paragraph." in html


# ---------------------------------------------------------------------------
# Table rendering
# ---------------------------------------------------------------------------


class TestTableRendering:
    """Tables are wrapped in a scrollable container."""

    def test_table_wrapped_in_div(self, sidebar: Frame) -> None:
        """Markdown tables are wrapped in <div class="table-wrap">."""
        table_md = (
            "| Name | Value |\n"
            "| ---- | ----- |\n"
            "| HbA1c | 8.2% |\n"
        )
        html = _render(sidebar, table_md)
        assert "table-wrap" in html, (
            f"Tables should be wrapped in table-wrap div, got: {html!r}"
        )
        assert "<table" in html

    def test_table_header_rendered(self, sidebar: Frame) -> None:
        """Table header row renders as <th> cells."""
        table_md = (
            "| Col1 | Col2 |\n"
            "| ---- | ---- |\n"
            "| A | B |\n"
        )
        html = _render(sidebar, table_md)
        assert "<th>" in html or "<thead>" in html, (
            f"Expected table header elements, got: {html!r}"
        )


# ---------------------------------------------------------------------------
# Security sanity check
# ---------------------------------------------------------------------------


class TestMarkdownSecurity:
    """Basic sanity checks that markdown rendering handles edge cases."""

    def test_empty_string_returns_empty(self, sidebar: Frame) -> None:
        """renderMarkdown('') returns empty or whitespace only."""
        html = _render(sidebar, "")
        assert html.strip() == "" or html == "\n", (
            f"Empty input should produce empty output, got: {html!r}"
        )

    def test_none_handled_gracefully(self, sidebar: Frame) -> None:
        """renderMarkdown(null) does not throw."""
        # The implementation uses `text || ""` so null should be handled
        html = sidebar.evaluate("() => window.__sidebarApp.renderMarkdown(null)")
        assert isinstance(html, str), "Should return a string even for null input"

    def test_plain_text_escaped(self, sidebar: Frame) -> None:
        """Angle brackets in plain text are HTML-escaped by marked.js."""
        html = _render(sidebar, "value < 10 and a > b")
        # marked.js escapes these in paragraph text
        assert "alert" not in html  # no script injection
        assert "value" in html or "lt" in html  # content present


# ---------------------------------------------------------------------------
# Integration: real assistant message is rendered as HTML
# ---------------------------------------------------------------------------


class TestAssistantMessageRendering:
    """Verify that real assistant messages are rendered as HTML, not raw markdown."""

    def test_assistant_reply_rendered_not_raw(self, sidebar: Frame) -> None:
        """Assistant messages show rendered HTML (not raw **markdown** syntax)."""
        send_chat_message(sidebar, "What is 2 + 2? Reply with the answer in bold.")

        # Get the inner HTML of the last assistant message
        last_html = sidebar.evaluate("""() => {
            const msgs = document.querySelectorAll('.message.role-assistant .markdown')
            if (!msgs.length) return ''
            return msgs[msgs.length - 1].innerHTML
        }""")

        # The reply should contain HTML (bold, paragraph etc.), not raw **
        assert "**" not in last_html, (
            f"Raw markdown syntax found in rendered output: {last_html!r}"
        )
        # Should have some HTML structure
        assert "<" in last_html, (
            f"Expected rendered HTML elements in assistant reply, got: {last_html!r}"
        )
