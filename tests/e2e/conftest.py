"""Playwright E2E test configuration.

Tests run against the full OpenEMR + agent stack.  The browser logs into
OpenEMR, selects a patient via the left-nav API, then interacts with the
Clinical Assistant sidebar embedded in the OpenEMR UI as an iframe.

Environment variables:
    AGENT_BASE_URL  – Agent API root (default: http://localhost:8000)
    OPENEMR_URL     – OpenEMR root (default: http://localhost:80)
    OPENEMR_USER    – Login username (default: admin)
    OPENEMR_PASS    – Login password (default: pass)
    E2E_TIMEOUT_MS  – Per-action timeout in ms (default: 120000 for LLM calls)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import Frame, Page, expect


AGENT_BASE_URL = os.environ.get("AGENT_BASE_URL", "https://emragent.404.mn/agent-api")
OPENEMR_URL = os.environ.get("OPENEMR_URL", "https://emragent.404.mn")
OPENEMR_USER = os.environ.get("OPENEMR_USER", "admin")
OPENEMR_PASS = os.environ.get("OPENEMR_PASS", "pass")
E2E_TIMEOUT_MS = int(os.environ.get("E2E_TIMEOUT_MS", "120000"))
DEFAULT_USER_ID = "e2e-test-user"

# Patient name → pid mapping (matches seed_data.sql actual pids)
PATIENT_MAP: dict[str, int] = {
    "Maria Santos": 4,
    "James Kowalski": 5,
    "Aisha Patel": 6,
}


def _load_eval_dataset() -> list[dict]:
    dataset_path = Path(__file__).resolve().parents[1] / "eval" / "dataset.json"
    with open(dataset_path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# OpenEMR navigation helpers
# ---------------------------------------------------------------------------


def openemr_login(page: Page, url: str = OPENEMR_URL) -> None:
    """Log into OpenEMR and wait for the main UI to load."""
    # Use domcontentloaded to avoid waiting for external CDN scripts (marked.js)
    # loaded by sidebar_frame.php, which can block the "load" event.
    page.goto(url, wait_until="domcontentloaded")
    page.fill("#authUser", OPENEMR_USER)
    page.fill("#clearPass", OPENEMR_PASS)
    # Wait for navigation after login form submission
    with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
        page.click("#login-button")
    # Wait for the Knockout app_view_model (left_nav) to be available.
    # This is the reliable indicator that OpenEMR's main UI is ready.
    # Do NOT use wait_for_load_state("networkidle") — the sidebar makes
    # continuous proxy API calls that prevent networkidle from ever firing.
    page.wait_for_function(
        "() => !!(window.top || window).left_nav",
        timeout=30000,
    )


def select_patient(
    page: Page,
    patient_id: int | str,
    patient_name: str | None = None,
    encounter_id: str | None = None,
) -> None:
    """Select a patient in OpenEMR via left_nav.setPatient.

    This is the same mechanism OpenEMR uses when a clinician clicks on a
    patient in the search results — it sets the active patient in the
    Knockout view model, which the sidebar's embed.js picks up via polling.

    If ``encounter_id`` is provided, also sets the active encounter via
    the Knockout ``selectedEncounterID`` observable so that the sidebar
    picks it up during its context refresh poll.
    """
    pid = int(patient_id)
    name = patient_name or f"Patient {pid}"
    page.evaluate(
        f"""() => {{
            const top = window.top || window;
            top.left_nav.setPatient({json.dumps(name)}, {pid}, '', '');
        }}"""
    )
    if encounter_id:
        page.evaluate(
            f"""() => {{
                const top = window.top || window;
                if (typeof top.setEncounter === 'function') {{
                    top.setEncounter({json.dumps(encounter_id)});
                }} else if (top.app_view_model) {{
                    const patient = top.app_view_model.application_data.patient();
                    if (patient) {{
                        if (typeof patient.selectedEncounterID === 'function') {{
                            patient.selectedEncounterID({json.dumps(encounter_id)});
                        }} else if (typeof ko !== 'undefined') {{
                            patient.selectedEncounterID = ko.observable({json.dumps(encounter_id)});
                        }}
                    }}
                }}
            }}"""
        )
    # Directly write openemrAgentContext so the sidebar doesn't need to wait
    # for embed.js's next 2-second poll cycle.
    page.evaluate(
        f"""() => {{
            const top = window.top || window;
            if (!top.openemrAgentContext) {{ top.openemrAgentContext = {{}}; }}
            top.openemrAgentContext.pid = {json.dumps(str(pid))};
            top.openemrAgentContext.patient_name = {json.dumps(name)};
            top.openemrAgentContext.encounter = {json.dumps(encounter_id)};
        }}"""
    )
    # Brief pause for the sidebar iframe to read the updated context
    page.wait_for_timeout(500)


def get_sidebar_frame(page: Page) -> Frame:
    """Return the sidebar iframe's Frame object from the OpenEMR page."""
    # Wait for the embed.js to inject the iframe (it fires on DOMContentLoaded)
    page.wait_for_timeout(2000)
    for frame in page.frames:
        if (
            "clinical-assistant" in frame.url
            or "sidebar" in frame.url
            or "agent-api" in frame.url
        ):
            frame.wait_for_selector("#chat-input", state="visible", timeout=15000)
            return frame
    raise RuntimeError("Sidebar iframe not found in OpenEMR page")


def inject_patient_context(
    page: Page,
    patient_id: str | None = None,
    encounter_id: str | None = None,
    patient_name: str | None = None,
    active_tab: str | None = None,
) -> None:
    """Set patient context via OpenEMR's left_nav when running inside OpenEMR.

    Falls back to direct JS injection on the standalone sidebar page.
    """
    # Check if we're in OpenEMR (has left_nav) or standalone sidebar
    has_left_nav = page.evaluate("() => !!(window.top || window).left_nav")

    if has_left_nav and patient_id:
        select_patient(page, patient_id, patient_name)
    else:
        # Standalone sidebar fallback (smoke tests)
        ctx = {
            "pid": patient_id,
            "encounter": encounter_id,
            "patient_name": patient_name,
            "active_tab": active_tab,
            "active_tab_title": active_tab,
            "active_tab_url": None,
        }
        page.evaluate(f"window.openemrAgentContext = {json.dumps(ctx)}")


# ---------------------------------------------------------------------------
# Chat interaction helpers (work with both Frame and Page)
# ---------------------------------------------------------------------------


def send_chat_message(target: Page | Frame, message: str) -> None:
    """Type a message and click Send, then wait for the assistant reply."""
    existing_count = target.locator(".message.role-assistant").count()
    existing_error_count = target.locator(".error-block").count()

    target.locator("#chat-input").fill(message)
    # Use dispatch_event instead of click() to bypass Playwright's pointer-event
    # hit-testing, which fails for fixed-position iframes ("iframe intercepts
    # pointer events" error).
    target.locator("#send-button").dispatch_event("click")

    target.wait_for_function(
        f"""() => {{
            const assistants = document.querySelectorAll('.message.role-assistant');
            const errors = document.querySelectorAll('.error-block');
            return assistants.length > {existing_count} || errors.length > {existing_error_count};
        }}""",
        timeout=E2E_TIMEOUT_MS,
    )


def get_last_assistant_message(target: Page | Frame) -> str:
    """Return the text content of the most recent assistant message bubble."""
    messages = target.locator(".message.role-assistant .markdown")
    count = messages.count()
    if count == 0:
        return ""
    return messages.nth(count - 1).inner_text()


def get_all_assistant_messages(target: Page | Frame) -> list[str]:
    """Return text content of all assistant message bubbles."""
    messages = target.locator(".message.role-assistant .markdown")
    return [messages.nth(i).inner_text() for i in range(messages.count())]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def agent_url() -> str:
    return AGENT_BASE_URL


@pytest.fixture(scope="session")
def openemr_url() -> str:
    return OPENEMR_URL


@pytest.fixture(scope="session")
def eval_dataset() -> list[dict]:
    return _load_eval_dataset()


@pytest.fixture
def sidebar_page(page: Page, agent_url: str) -> Page:
    """Navigate to the standalone sidebar UI (for smoke tests)."""
    page.set_default_timeout(E2E_TIMEOUT_MS)
    page.goto(f"{agent_url}/ui")
    page.wait_for_selector("#chat-input", state="visible", timeout=15000)
    return page
