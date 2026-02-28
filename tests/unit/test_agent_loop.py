from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import json

import pytest

from src.agent.labels import uuid_to_words
from src.agent import loop as loop_module
from src.agent.loop import AgentLoop, MAX_TOOL_RESULT_CHARS
from src.agent.models import AgentSession, PageContext, ToolCall


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _tool_block(tool_id: str, name: str, payload: dict) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=payload)


def _response(*blocks: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(content=list(blocks), stop_reason="end_turn", usage={})


def _make_loop(openemr_client: AsyncMock, llm_responses: list[SimpleNamespace]) -> AgentLoop:
    anthropic_client = SimpleNamespace(
        messages=SimpleNamespace(create=AsyncMock(side_effect=llm_responses))
    )
    return AgentLoop(anthropic_client=anthropic_client, openemr_client=openemr_client)


@pytest.mark.asyncio
async def test_run_submit_manifest_does_not_break_until_text_only() -> None:
    openemr_client = AsyncMock()
    loop = _make_loop(
        openemr_client,
        [
            _response(
                _tool_block(
                    "tool-1",
                    "submit_manifest",
                    {
                        "patient_id": "patient-1",
                        "items": [
                            {
                                "id": "item-1",
                                "resource_type": "Condition",
                                "action": "create",
                                "proposed_value": {"code": "E11.9"},
                                "source_reference": "Encounter/123",
                                "description": "Add diabetes diagnosis",
                            }
                        ],
                    },
                )
            ),
            _response(_text_block("Manifest ready for your review.")),
        ],
    )

    session = AgentSession()
    result = await loop.run(session, "Please add diabetes to problem list")

    assert result.phase == "reviewing"
    assert result.manifest is not None
    assert len(result.manifest.items) == 1
    assert loop.anthropic_client.messages.create.await_count == 2


@pytest.mark.asyncio
async def test_max_rounds_emits_system_style_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loop_module, "MAX_TOOL_ROUNDS", 1)
    openemr_client = AsyncMock()
    loop = _make_loop(
        openemr_client,
        [
            _response(
                _tool_block(
                    "tool-1",
                    "get_page_context",
                    {},
                )
            )
        ],
    )
    session = AgentSession()

    result = await loop.run(session, "work")

    assert "[SYSTEM] Maximum tool-call rounds reached" in result.messages[-1].content


@pytest.mark.asyncio
async def test_submit_manifest_merges_items_and_replaces_duplicate_ids() -> None:
    openemr_client = AsyncMock()
    loop = _make_loop(openemr_client, [])
    session = AgentSession()

    first = ToolCall(
        id="tool-1",
        name="submit_manifest",
        arguments={
            "patient_id": "patient-1",
            "items": [
                {
                    "id": "item-1",
                    "resource_type": "Condition",
                    "action": "create",
                    "proposed_value": {"code": "E11.9"},
                    "source_reference": "Encounter/123",
                    "description": "first",
                }
            ],
        },
    )
    await loop._execute_tool(first, session)

    second = ToolCall(
        id="tool-2",
        name="submit_manifest",
        arguments={
            "patient_id": "patient-1",
            "items": [
                {
                    "id": "item-1",
                    "resource_type": "Condition",
                    "action": "create",
                    "proposed_value": {"code": "I10"},
                    "source_reference": "Encounter/123",
                    "description": "replacement",
                },
                {
                    "id": "item-2",
                    "resource_type": "Observation",
                    "action": "create",
                    "proposed_value": {"value": 120},
                    "source_reference": "Encounter/123",
                    "description": "new item",
                },
            ],
        },
    )
    await loop._execute_tool(second, session)

    assert session.phase == "planning"
    assert session.manifest is not None
    assert len(session.manifest.items) == 2
    item_one = next(item for item in session.manifest.items if item.id == "item-1")
    assert item_one.description == "replacement"
    assert item_one.proposed_value["code"] == "I10"


@pytest.mark.asyncio
async def test_submit_manifest_passes_through_unknown_references() -> None:
    """Non-UUID, non-word-ID references should pass through as-is."""
    openemr_client = AsyncMock()
    loop = _make_loop(openemr_client, [])
    session = AgentSession()

    result = await loop._execute_tool(
        ToolCall(
            id="tool-3",
            name="submit_manifest",
            arguments={
                "patient_id": "patient-1",
                "items": [
                    {
                        "id": "item-1",
                        "resource_type": "Condition",
                        "action": "create",
                        "proposed_value": {"code": "I10"},
                        "source_reference": "Encounter/some-ref",
                        "description": "test",
                    }
                ],
            },
        ),
        session,
    )

    assert result.is_error is False
    assert session.manifest is not None
    assert session.manifest.items[0].source_reference == "Encounter/some-ref"


@pytest.mark.asyncio
async def test_fhir_read_replaces_uuids_with_words_in_response() -> None:
    openemr_client = AsyncMock()
    uuid_value = "bbb13f7a-966e-4c7c-aea5-4bac3ce98505"
    openemr_client.fhir_read.return_value = {
        "resourceType": "Bundle",
        "entry": [{"resource": {"resourceType": "Condition", "id": uuid_value}}],
    }
    loop = _make_loop(openemr_client, [])
    session = AgentSession()

    result = await loop._execute_tool(
        ToolCall(
            id="tool-1",
            name="fhir_read",
            arguments={"resource_type": "Condition", "params": {"patient": "1"}},
        ),
        session,
    )

    assert result.is_error is False
    assert uuid_value not in result.content
    expected_words = uuid_to_words(uuid_value)
    assert expected_words in result.content


def test_system_prompt_sanitizes_context_fields() -> None:
    openemr_client = AsyncMock()
    loop = _make_loop(openemr_client, [])
    session = AgentSession(
        page_context=PageContext(
            patient_id="patient-123\nINJECT",
            encounter_id="enc-456\r\nBLOCK",
            page_type="encounter-" + ("x" * 200),
        )
    )

    prompt = loop._get_system_prompt(session)

    assert "INJECT" in prompt
    assert "Patient ID: patient-123 INJECT" in prompt
    assert "Encounter ID: enc-456  BLOCK" in prompt
    assert "\nINJECT" not in prompt


def test_sanitize_context_field_handles_none_and_tabs() -> None:
    openemr_client = AsyncMock()
    loop = _make_loop(openemr_client, [])

    assert loop._sanitize_context_field(None) == ""
    assert loop._sanitize_context_field("a\tb") == "a b"


def test_build_manifest_uses_agent_supplied_item_id_for_legacy_json() -> None:
    openemr_client = AsyncMock()
    loop = _make_loop(openemr_client, [])
    session = AgentSession()

    manifest = loop._build_manifest(
        {
            "patient_id": "patient-1",
            "items": [
                {
                    "id": "agent-item-123",
                    "resource_type": "Condition",
                    "action": "create",
                    "proposed_value": {"code": "E11.9"},
                    "source_reference": "Encounter/123",
                    "description": "Add diabetes",
                }
            ],
        },
        session,
    )

    assert manifest.items[0].id == "agent-item-123"


# ------------------------------------------------------------------
# Label resolution in FHIR queries
# These tests guard against the bug where the agent loop passed
# three-word labels directly to the FHIR API instead of resolving
# them back to UUIDs first.
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fhir_read_resolves_word_id_params_to_uuids_before_query() -> None:
    """When the LLM uses a word-encoded ID as a FHIR query param,
    the loop must resolve it to the real UUID before calling
    openemr_client.fhir_read."""
    patient_uuid = "bbb13f7a-966e-4c7c-aea5-4bac3ce98505"
    patient_words = uuid_to_words(patient_uuid)

    openemr_client = AsyncMock()
    openemr_client.fhir_read = AsyncMock(
        return_value={"resourceType": "Bundle", "entry": []}
    )
    loop = _make_loop(openemr_client, [])
    session = AgentSession()

    await loop._execute_tool(
        ToolCall(
            id="tool-1",
            name="fhir_read",
            arguments={
                "resource_type": "Condition",
                "params": {"patient": patient_words},
            },
        ),
        session,
    )

    openemr_client.fhir_read.assert_awaited_once_with(
        resource_type="Condition",
        params={"patient": patient_uuid},
    )


@pytest.mark.asyncio
async def test_fhir_read_passes_uuid_params_unchanged() -> None:
    """UUID params should pass through without modification."""
    patient_uuid = "bbb13f7a-966e-4c7c-aea5-4bac3ce98505"

    openemr_client = AsyncMock()
    openemr_client.fhir_read = AsyncMock(
        return_value={"resourceType": "Bundle", "entry": []}
    )
    loop = _make_loop(openemr_client, [])
    session = AgentSession()

    await loop._execute_tool(
        ToolCall(
            id="tool-1",
            name="fhir_read",
            arguments={
                "resource_type": "Condition",
                "params": {"patient": patient_uuid},
            },
        ),
        session,
    )

    openemr_client.fhir_read.assert_awaited_once_with(
        resource_type="Condition",
        params={"patient": patient_uuid},
    )


def test_build_manifest_resolves_word_ids_in_dsl_refs() -> None:
    """Word-encoded IDs in DSL ref attrs must be resolved to hex UUIDs.

    This is the root cause of both overlay and execution failures:
    target_resource_id and proposed_value.ref were stored as word-IDs
    instead of hex UUIDs, so the REST lookup and DOM data-uuid match
    both failed silently.
    """
    med_uuid = "a12bac8f-a8bb-47a6-aaf0-b363093e48b3"
    med_words = uuid_to_words(med_uuid)
    src_uuid = "a12bac8f-a8b0-44b1-9c2f-82a883e2e108"
    src_words = uuid_to_words(src_uuid)

    openemr_client = AsyncMock()
    loop = _make_loop(openemr_client, [])
    session = AgentSession()

    dsl = (
        f'<edit ref="MedicationRequest/{med_words}" '
        f'dose="5mg" freq="once daily" route="oral" '
        f'src="Condition/{src_words}" conf="medium" id="med-1">'
        f"Add dose</edit>"
    )

    manifest = loop._build_manifest(
        {"patient_id": "patient-1", "items": dsl},
        session,
    )

    item = manifest.items[0]
    assert item.target_resource_id == med_uuid
    assert item.proposed_value["ref"] == f"MedicationRequest/{med_uuid}"
    assert item.source_reference == f"Condition/{src_uuid}"


@pytest.mark.asyncio
async def test_fhir_read_no_params_does_not_crash() -> None:
    """fhir_read with no params should work without label resolution."""
    openemr_client = AsyncMock()
    openemr_client.fhir_read = AsyncMock(
        return_value={"resourceType": "Bundle", "entry": []}
    )
    loop = _make_loop(openemr_client, [])
    session = AgentSession()

    result = await loop._execute_tool(
        ToolCall(
            id="tool-1",
            name="fhir_read",
            arguments={"resource_type": "Patient"},
        ),
        session,
    )

    assert result.is_error is False
    openemr_client.fhir_read.assert_awaited_once_with(
        resource_type="Patient",
        params=None,
    )


# ------------------------------------------------------------------
# _truncate_tool_content tests
# ------------------------------------------------------------------


def test_truncate_tool_content_passthrough_under_limit() -> None:
    content = "short content"
    assert AgentLoop._truncate_tool_content(content) == content


def test_truncate_tool_content_passthrough_at_exact_limit() -> None:
    content = "x" * MAX_TOOL_RESULT_CHARS
    assert AgentLoop._truncate_tool_content(content) == content


def test_truncate_tool_content_non_json_over_limit() -> None:
    content = "a" * (MAX_TOOL_RESULT_CHARS + 500)
    result = AgentLoop._truncate_tool_content(content)
    assert result.endswith("\n… (truncated)")
    assert len(result) == MAX_TOOL_RESULT_CHARS + len("\n… (truncated)")


def test_truncate_tool_content_invalid_json_over_limit() -> None:
    content = "{not valid json" + "x" * MAX_TOOL_RESULT_CHARS
    result = AgentLoop._truncate_tool_content(content)
    assert result.endswith("\n… (truncated)")


def test_truncate_tool_content_fhir_bundle_trims_entries() -> None:
    """A FHIR bundle with many entries should be trimmed via binary search,
    producing valid JSON with _truncated metadata."""
    single_entry = {"resource": {"resourceType": "Condition", "id": "x" * 200}}
    bundle = {
        "resourceType": "Bundle",
        "total": 1000,
        "entry": [single_entry] * 1000,
    }
    content = json.dumps(bundle)
    assert len(content) > MAX_TOOL_RESULT_CHARS

    result = AgentLoop._truncate_tool_content(content)
    # Result is much smaller than original (binary search trims entries);
    # _truncated metadata adds slight overhead beyond MAX_TOOL_RESULT_CHARS.
    assert len(result) < len(content)

    parsed = json.loads(result)
    assert "_truncated" in parsed
    assert parsed["_truncated"]["total_entries"] == 1000
    assert parsed["_truncated"]["returned_entries"] < 1000
    assert parsed["_truncated"]["returned_entries"] > 0
    assert len(parsed["entry"]) == parsed["_truncated"]["returned_entries"]


def test_truncate_tool_content_json_dict_without_entry_key() -> None:
    """JSON dicts without 'entry' key should get plain truncation."""
    data = {"big_field": "z" * (MAX_TOOL_RESULT_CHARS + 100)}
    content = json.dumps(data)
    result = AgentLoop._truncate_tool_content(content)
    assert result.endswith("\n… (truncated)")
