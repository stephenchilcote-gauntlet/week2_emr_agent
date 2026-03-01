"""End-to-end write-loop test: chat → manifest → approve → execute → verify in DB.

This test closes the write loop that test_agent_evals.py does NOT cover.
It sends a real chat message through the agent API, approves the manifest,
executes it, and then verifies the record actually landed in the OpenEMR
database (lists table) with correct field values.

Run:
    pytest tests/e2e/test_write_loop.py -m e2e -v
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess

import pytest
from playwright.sync_api import Page

from .conftest import AGENT_BASE_URL, E2E_TIMEOUT_MS, openemr_login

pytestmark = pytest.mark.e2e

AGENT_USER_ID = "1"
PATIENT_PID = "4"
PATIENT_PID_KOWALSKI = "5"

# Remote execution via SSH for prod deployments.
# Set E2E_SSH_HOST to run DB queries on a remote VPS (e.g. "root@77.42.17.207").
E2E_SSH_HOST = os.environ.get("E2E_SSH_HOST", "root@77.42.17.207")
E2E_MYSQL_CONTAINER = os.environ.get("E2E_MYSQL_CONTAINER", "emr-agent-mysql-1")
E2E_MYSQL_PASS = os.environ.get("E2E_MYSQL_PASS", "mS6mi7EacAWCzdjzWV1dfuyNQJpuH9")


def _db_query(sql: str) -> str:
    """Run a SQL query against the OpenEMR database via docker exec.

    When E2E_SSH_HOST is set, runs the docker exec command on the remote
    host via SSH (for prod deployments).
    """
    docker_cmd = [
        "docker", "exec", E2E_MYSQL_CONTAINER,
        "mysql", f"-uopenemr", f"-p{E2E_MYSQL_PASS}", "openemr",
        "-e", sql,
    ]
    if E2E_SSH_HOST:
        remote_cmd = " ".join(shlex.quote(c) for c in docker_cmd)
        cmd = ["ssh", "-o", "StrictHostKeyChecking=no", E2E_SSH_HOST, remote_cmd]
    else:
        cmd = docker_cmd

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"MySQL query failed: {result.stderr}")
    return result.stdout


def _chat(api, message: str, page_context: dict) -> dict:
    """Send a chat message to the agent API and return the response JSON."""
    resp = api.post(
        f"{AGENT_BASE_URL}/api/chat",
        headers={"openemr_user_id": AGENT_USER_ID},
        data={
            "message": message,
            "page_context": page_context,
        },
    )
    assert resp.ok, f"Chat failed: {resp.status} {resp.text()}"
    return resp.json()


def _approve_and_execute(api, session_id: str, item_ids: list[str]) -> dict:
    """Approve all items and execute the manifest. Return execution result."""
    headers = {"openemr_user_id": AGENT_USER_ID}

    approve_resp = api.post(
        f"{AGENT_BASE_URL}/api/manifest/{session_id}/approve",
        headers=headers,
        data={
            "approved_items": item_ids,
            "rejected_items": [],
            "modified_items": [],
        },
    )
    assert approve_resp.ok, (
        f"Approve failed: {approve_resp.status} {approve_resp.text()}"
    )

    exec_resp = api.post(
        f"{AGENT_BASE_URL}/api/manifest/{session_id}/execute",
        headers=headers,
        data={},
    )
    assert exec_resp.ok, (
        f"Execute failed: {exec_resp.status} {exec_resp.text()}"
    )
    return exec_resp.json()


@pytest.fixture
def openemr_page(page: Page) -> Page:
    page.set_default_timeout(E2E_TIMEOUT_MS)
    if E2E_SSH_HOST:
        # Prod mode: skip OpenEMR login; navigate to agent UI for request context.
        page.goto(f"{AGENT_BASE_URL}/ui")
        page.wait_for_load_state("networkidle")
    else:
        openemr_login(page)
    return page


class TestWriteLoop:
    """Verify the full write loop: chat → manifest → approve → execute → DB."""

    def test_medication_create_lands_in_database(self, openemr_page: Page):
        """Add aspirin via the agent and verify it appears in the lists table."""
        # Clean up any leftover test data
        _db_query(
            f"DELETE FROM lists WHERE pid={PATIENT_PID} "
            "AND type='medication' AND title LIKE '%aspirin%'"
        )

        api = openemr_page.request

        # 1. Send chat message to create a medication
        chat_data = _chat(
            api,
            "Add aspirin 81mg daily for cardiovascular prevention.",
            {
                "patient_id": PATIENT_PID,
                "encounter_id": "90001",
                "page_type": "medications",
            },
        )

        session_id = chat_data["session_id"]
        manifest = chat_data.get("manifest")
        assert manifest is not None, (
            f"No manifest returned. Phase: {chat_data.get('phase')}. "
            f"Response: {chat_data.get('response', '')[:300]}"
        )

        items = manifest.get("items", [])
        med_items = [
            i for i in items
            if i["resource_type"] == "MedicationRequest"
            and i["action"] == "create"
        ]
        assert len(med_items) > 0, (
            f"No MedicationRequest create items. "
            f"Items: {json.dumps(items, indent=2)}"
        )

        # 2. Approve and execute
        all_ids = [i["id"] for i in items]
        exec_data = _approve_and_execute(api, session_id, all_ids)

        completed = [
            i for i in exec_data.get("items", [])
            if i.get("status") == "completed"
        ]
        assert len(completed) > 0, (
            f"No items completed: {json.dumps(exec_data.get('items', []), indent=2)}"
        )

        # 3. Verify the medication exists in the database
        rows = _db_query(
            "SELECT id, title, type, begdate, enddate "
            f"FROM lists WHERE pid={PATIENT_PID} AND type='medication' "
            "AND title LIKE '%aspirin%'"
        )
        assert "aspirin" in rows.lower(), (
            f"Medication not found in lists table. Query result:\n{rows}"
        )

        # 4. Verify enddate is NULL (required for visibility in OpenEMR UI)
        enddate_rows = _db_query(
            "SELECT enddate FROM lists "
            f"WHERE pid={PATIENT_PID} AND type='medication' "
            "AND title LIKE '%aspirin%'"
        )
        data_lines = [
            line.strip() for line in enddate_rows.strip().split("\n")
            if line.strip() and line.strip() != "enddate"
        ]
        for line in data_lines:
            assert line == "NULL", (
                f"enddate should be NULL but got '{line}'. "
                "Records with non-NULL enddate are invisible in the OpenEMR UI."
            )

        # Cleanup
        _db_query(
            f"DELETE FROM lists WHERE pid={PATIENT_PID} "
            "AND type='medication' AND title LIKE '%aspirin%'"
        )

    def test_condition_create_lands_in_database(self, openemr_page: Page):
        """Add an obesity diagnosis and verify it appears in the lists table."""
        _db_query(
            f"DELETE FROM lists WHERE pid={PATIENT_PID} "
            "AND type='medical_problem' AND diagnosis LIKE '%E66%'"
        )

        api = openemr_page.request

        chat_data = _chat(
            api,
            "The patient has a BMI of 32. Please add obesity to the "
            "problem list with ICD-10 code E66.01.",
            {
                "patient_id": PATIENT_PID,
                "encounter_id": "90001",
                "page_type": "problem_list",
            },
        )

        session_id = chat_data["session_id"]
        manifest = chat_data.get("manifest")
        assert manifest is not None, (
            f"No manifest returned. Phase: {chat_data.get('phase')}. "
            f"Response: {chat_data.get('response', '')[:300]}"
        )

        items = manifest.get("items", [])
        cond_items = [
            i for i in items
            if i["resource_type"] == "Condition"
            and i["action"] == "create"
        ]
        assert len(cond_items) > 0, (
            f"No Condition create items: {json.dumps(items, indent=2)}"
        )

        all_ids = [i["id"] for i in items]
        exec_data = _approve_and_execute(api, session_id, all_ids)

        completed = [
            i for i in exec_data.get("items", [])
            if i.get("status") == "completed"
        ]
        assert len(completed) > 0, (
            f"No items completed: {json.dumps(exec_data.get('items', []), indent=2)}"
        )

        rows = _db_query(
            "SELECT id, title, diagnosis, type, begdate, enddate "
            f"FROM lists WHERE pid={PATIENT_PID} AND type='medical_problem' "
            "AND diagnosis LIKE '%E66%'"
        )
        assert "e66" in rows.lower(), (
            f"Condition not found in lists table. Query result:\n{rows}"
        )

        # Cleanup
        _db_query(
            f"DELETE FROM lists WHERE pid={PATIENT_PID} "
            "AND type='medical_problem' AND diagnosis LIKE '%E66%'"
        )

    def test_medication_update_preserves_drug_name(self, openemr_page: Page):
        """Dose-only update keeps the drug name in the medication title.

        Regression test for the bug where updating dose via the agent
        overwrote the title with just the dose (e.g. "500mg twice daily"
        instead of "Metformin 500mg twice daily").
        """
        pid = PATIENT_PID_KOWALSKI
        api = openemr_page.request

        # Record the original Metformin title so we can restore it
        original_rows = _db_query(
            f"SELECT id, title FROM lists WHERE pid={pid} "
            "AND type='medication' AND title LIKE '%Metformin%' LIMIT 1"
        )
        assert "metformin" in original_rows.lower(), (
            f"Metformin not found for pid {pid}. Seed data may be missing."
        )
        # Parse the numeric id for cleanup
        original_lines = [
            l.strip() for l in original_rows.strip().split("\n")
            if l.strip() and not l.strip().startswith("id")
        ]
        original_id = original_lines[0].split("\t")[0]
        original_title = original_lines[0].split("\t")[1]

        chat_data = _chat(
            api,
            "Change the Metformin dose to 500mg twice daily for this patient.",
            {
                "patient_id": pid,
                "encounter_id": "90001",
                "page_type": "medications",
            },
        )

        session_id = chat_data["session_id"]
        manifest = chat_data.get("manifest")
        assert manifest is not None, (
            f"No manifest returned. Phase: {chat_data.get('phase')}. "
            f"Response: {chat_data.get('response', '')[:300]}"
        )

        items = manifest.get("items", [])
        update_items = [
            i for i in items
            if i["resource_type"] == "MedicationRequest"
            and i["action"] == "update"
        ]
        assert len(update_items) > 0, (
            f"No MedicationRequest update items. "
            f"Items: {json.dumps(items, indent=2)}"
        )

        # Approve and execute
        all_ids = [i["id"] for i in items]
        exec_data = _approve_and_execute(api, session_id, all_ids)

        completed = [
            i for i in exec_data.get("items", [])
            if i.get("status") == "completed"
        ]
        assert len(completed) > 0, (
            f"No items completed: {json.dumps(exec_data.get('items', []), indent=2)}"
        )

        # Verify the title in the DB still contains "Metformin"
        rows = _db_query(
            f"SELECT title FROM lists WHERE pid={pid} "
            "AND type='medication' AND title LIKE '%Metformin%'"
        )
        assert "metformin" in rows.lower(), (
            f"Drug name lost after update! DB result:\n{rows}"
        )
        # Verify the new dose is present
        assert "500mg" in rows.lower() or "500 mg" in rows.lower(), (
            f"Dose not updated. DB result:\n{rows}"
        )

        # Restore original title
        _db_query(
            f"UPDATE lists SET title='{original_title}' "
            f"WHERE id={original_id}"
        )

    def test_create_and_update_in_same_manifest(self, openemr_page: Page):
        """Create + update in a single manifest executes without cascade failure.

        Regression test for the bug where REST POST creates medication
        records without UUIDs, causing the list endpoint to 500 and
        breaking subsequent update operations in the same manifest.
        """
        pid = PATIENT_PID_KOWALSKI
        api = openemr_page.request

        # Record original Apixaban title for restore
        original_rows = _db_query(
            f"SELECT id, title FROM lists WHERE pid={pid} "
            "AND type='medication' AND title LIKE '%Apixaban%' LIMIT 1"
        )
        assert "apixaban" in original_rows.lower(), (
            f"Apixaban not found for pid {pid}. Seed data may be missing."
        )
        original_lines = [
            l.strip() for l in original_rows.strip().split("\n")
            if l.strip() and not l.strip().startswith("id")
        ]
        original_id = original_lines[0].split("\t")[0]
        original_title = original_lines[0].split("\t")[1]

        # Clean up any leftover test data
        _db_query(
            f"DELETE FROM lists WHERE pid={pid} "
            "AND type='medication' AND title LIKE '%Omeprazole%'"
        )
        _db_query(
            f"DELETE FROM lists WHERE pid={pid} "
            "AND type='medication' AND uuid IS NULL"
        )

        chat_data = _chat(
            api,
            "Please add Omeprazole 20mg daily for GERD, and also change "
            "the Apixaban dose to 2.5mg twice daily.",
            {
                "patient_id": pid,
                "encounter_id": "90001",
                "page_type": "medications",
            },
        )

        session_id = chat_data["session_id"]
        manifest = chat_data.get("manifest")
        assert manifest is not None, (
            f"No manifest returned. Phase: {chat_data.get('phase')}. "
            f"Response: {chat_data.get('response', '')[:300]}"
        )

        items = manifest.get("items", [])
        assert len(items) >= 2, (
            f"Expected at least 2 manifest items (create + update). "
            f"Got: {json.dumps(items, indent=2)}"
        )

        # Approve and execute
        all_ids = [i["id"] for i in items]
        exec_data = _approve_and_execute(api, session_id, all_ids)

        completed = [
            i for i in exec_data.get("items", [])
            if i.get("status") == "completed"
        ]
        assert len(completed) >= 2, (
            f"Expected at least 2 completed items. "
            f"Results: {json.dumps(exec_data.get('items', []), indent=2)}"
        )

        # Clean null-UUID records before querying (REST POST doesn't generate UUIDs)
        _db_query(
            f"DELETE FROM lists WHERE pid={pid} "
            "AND type='medication' AND uuid IS NULL"
        )

        # Verify Apixaban still has its drug name (the key regression check:
        # without the fix, the update would either fail or overwrite the
        # title with just the dose string)
        apixaban_rows = _db_query(
            f"SELECT title FROM lists WHERE pid={pid} "
            "AND type='medication' AND title LIKE '%Apixaban%'"
        )
        assert "apixaban" in apixaban_rows.lower(), (
            f"Apixaban drug name lost after update! DB result:\n{apixaban_rows}"
        )

        # Cleanup: restore original Apixaban, delete Omeprazole
        _db_query(
            f"UPDATE lists SET title='{original_title}' "
            f"WHERE id={original_id}"
        )
        _db_query(
            f"DELETE FROM lists WHERE pid={pid} "
            "AND type='medication' AND title LIKE '%Omeprazole%'"
        )
        _db_query(
            f"DELETE FROM lists WHERE pid={pid} "
            "AND type='medication' AND uuid IS NULL"
        )
