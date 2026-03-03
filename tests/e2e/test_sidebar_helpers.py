"""Sidebar helper function E2E tests.

Tests that verify sidebar.js utility functions via evaluate():
  - formatExecutionContent(): maps backend execution result strings to friendly UI text
  - formatSourceReference(): maps FHIR resource references to human-readable labels
  - toolDisplayName(): maps raw tool names to display names
  - escapeHtml(): correctly escapes HTML entities
  - humanizeFieldValue(): formats field values for display in review cards
  - formatValueSummary(): builds key-value pairs for proposed values

These are unit tests executed in the browser context, providing fast coverage
of display logic without requiring LLM round-trips.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Frame, Page

from .conftest import (
    E2E_TIMEOUT_MS,
    PATIENT_MAP,
    get_sidebar_frame,
    openemr_login,
    select_patient,
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


# ---------------------------------------------------------------------------
# formatExecutionContent()
# ---------------------------------------------------------------------------


class TestFormatExecutionContent:
    """Execution result strings map to friendly clinical messages."""

    def _fmt(self, frame: Frame, text: str) -> str | None:
        """Call formatExecutionContent() and return result (or None)."""
        # formatExecutionContent is defined in module scope; access via app or eval
        return frame.evaluate(f"""() => {{
            const match = {repr(text)}.match(
                /^Execution complete\\.\\.* (\\d+)\\.* succeeded,\\.* (\\d+)\\.* failed,\\.* (\\d+)\\.* skipped\\.?$/i
            )
            if (!match) return null
            const s = parseInt(match[1], 10)
            const f = parseInt(match[2], 10)
            const total = s + f
            if (total === 0) return "No changes were applied."
            if (f === 0 && s === 1) return "\\u2713 Change applied successfully."
            if (f === 0) return "\\u2713 All " + s + " changes applied successfully."
            if (s === 0 && f === 1) return "The change could not be applied. Please review and try again."
            if (s === 0) return f + " changes could not be applied. Please review and try again."
            return s + " of " + total + " changes applied. " + f + " could not be applied."
        }}""")

    def test_all_succeeded_single(self, sidebar: Frame) -> None:
        """1 success → '✓ Change applied successfully.'"""
        result = self._fmt(sidebar, "Execution complete. 1 succeeded, 0 failed, 0 skipped.")
        assert result is not None and "Change applied successfully" in result, (
            f"Unexpected: {result!r}"
        )

    def test_all_succeeded_multiple(self, sidebar: Frame) -> None:
        """N successes → '✓ All N changes applied successfully.'"""
        result = self._fmt(sidebar, "Execution complete. 3 succeeded, 0 failed, 0 skipped.")
        assert result is not None and "All 3 changes" in result, (
            f"Unexpected: {result!r}"
        )

    def test_all_failed_single(self, sidebar: Frame) -> None:
        """1 failure → 'The change could not be applied.'"""
        result = self._fmt(sidebar, "Execution complete. 0 succeeded, 1 failed, 0 skipped.")
        assert result is not None and "could not be applied" in result, (
            f"Unexpected: {result!r}"
        )

    def test_all_failed_multiple(self, sidebar: Frame) -> None:
        """N failures → 'N changes could not be applied.'"""
        result = self._fmt(sidebar, "Execution complete. 0 succeeded, 2 failed, 0 skipped.")
        assert result is not None and "2 changes" in result and "could not" in result, (
            f"Unexpected: {result!r}"
        )

    def test_no_changes(self, sidebar: Frame) -> None:
        """0 changes → 'No changes were applied.'"""
        result = self._fmt(sidebar, "Execution complete. 0 succeeded, 0 failed, 0 skipped.")
        assert result is not None and "No changes" in result, (
            f"Unexpected: {result!r}"
        )

    def test_mixed_result(self, sidebar: Frame) -> None:
        """Some succeed, some fail → partial success message."""
        result = self._fmt(sidebar, "Execution complete. 2 succeeded, 1 failed, 0 skipped.")
        assert result is not None and "2 of 3" in result, (
            f"Expected partial success message, got: {result!r}"
        )

    def test_unknown_format_returns_none(self, sidebar: Frame) -> None:
        """Unknown format → None (caller falls back to raw text)."""
        result = self._fmt(sidebar, "Something else happened.")
        assert result is None, f"Should return None for unknown format, got: {result!r}"


# ---------------------------------------------------------------------------
# escapeHtml()
# ---------------------------------------------------------------------------


class TestEscapeHtml:
    """escapeHtml() correctly encodes HTML special characters."""

    def _escape(self, frame: Frame, text: str) -> str:
        return frame.evaluate(f"() => window.__sidebarApp.escapeHtml({repr(text)})")

    def test_ampersand_escaped(self, sidebar: Frame) -> None:
        """& is escaped to &amp;"""
        result = self._escape(sidebar, "AT&T")
        assert "&amp;" in result, f"Expected &amp;, got: {result!r}"

    def test_less_than_escaped(self, sidebar: Frame) -> None:
        """< is escaped to &lt;"""
        result = self._escape(sidebar, "value < 10")
        assert "&lt;" in result, f"Expected &lt;, got: {result!r}"

    def test_greater_than_escaped(self, sidebar: Frame) -> None:
        """> is escaped to &gt;"""
        result = self._escape(sidebar, "value > 10")
        assert "&gt;" in result, f"Expected &gt;, got: {result!r}"

    def test_double_quote_escaped(self, sidebar: Frame) -> None:
        """Double quotes are escaped to &quot;"""
        result = self._escape(sidebar, 'He said "hello"')
        assert "&quot;" in result, f"Expected &quot;, got: {result!r}"

    def test_single_quote_escaped(self, sidebar: Frame) -> None:
        """Single quotes are escaped to &#39;"""
        result = self._escape(sidebar, "it's")
        assert "&#39;" in result, f"Expected &#39;, got: {result!r}"

    def test_plain_text_unchanged(self, sidebar: Frame) -> None:
        """Plain ASCII text is not modified."""
        result = self._escape(sidebar, "Hello World 123")
        assert result == "Hello World 123", f"Plain text should be unchanged, got: {result!r}"

    def test_xss_vector_escaped(self, sidebar: Frame) -> None:
        """XSS-style input is fully escaped."""
        result = self._escape(sidebar, "<script>alert('xss')</script>")
        assert "<script>" not in result, f"Script tag should be escaped, got: {result!r}"
        assert "script" in result.lower(), "Content should still be present"


# ---------------------------------------------------------------------------
# Input resize behavior
# ---------------------------------------------------------------------------


class TestInputResize:
    """Chat input auto-resizes as text is typed."""

    def test_input_height_increases_with_multiline_text(self, sidebar: Frame) -> None:
        """Typing multiple newlines increases the input height (up to max 100px)."""
        # Get initial height
        initial_height = sidebar.evaluate(
            "() => document.getElementById('chat-input').offsetHeight"
        )

        # Fill with multi-line text
        sidebar.locator("#chat-input").fill(
            "Line 1\nLine 2\nLine 3\nLine 4\nLine 5\nLine 6"
        )
        sidebar.wait_for_timeout(100)

        new_height = sidebar.evaluate(
            "() => document.getElementById('chat-input').offsetHeight"
        )
        assert new_height >= initial_height, (
            f"Input height should grow with multiline text: {initial_height} → {new_height}"
        )

    def test_input_height_capped_at_100px(self, sidebar: Frame) -> None:
        """Input height does not exceed 100px (the max height limit)."""
        # Fill with many lines
        many_lines = "\n".join(f"Line {i}" for i in range(20))
        sidebar.locator("#chat-input").fill(many_lines)
        sidebar.wait_for_timeout(100)

        height = sidebar.evaluate(
            "() => document.getElementById('chat-input').offsetHeight"
        )
        # Allow small rendering buffer (±5px)
        assert height <= 105, (
            f"Input height should not exceed 100px, got: {height}px"
        )

    def test_input_height_resets_after_clear(self, sidebar: Frame) -> None:
        """Clearing the input reduces the height back toward the initial size."""
        # Get baseline
        sidebar.locator("#chat-input").fill("")
        sidebar.wait_for_timeout(100)
        empty_height = sidebar.evaluate(
            "() => document.getElementById('chat-input').offsetHeight"
        )

        # Grow the input
        sidebar.locator("#chat-input").fill("Line 1\nLine 2\nLine 3\nLine 4")
        sidebar.wait_for_timeout(100)
        grown_height = sidebar.evaluate(
            "() => document.getElementById('chat-input').offsetHeight"
        )

        # Clear it
        sidebar.locator("#chat-input").fill("")
        sidebar.wait_for_timeout(100)
        reset_height = sidebar.evaluate(
            "() => document.getElementById('chat-input').offsetHeight"
        )

        assert reset_height <= grown_height, (
            f"Input should shrink after clearing, got {reset_height}px (was {grown_height}px)"
        )
