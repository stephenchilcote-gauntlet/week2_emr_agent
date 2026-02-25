from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.agent.labels import uuid_to_label
from src.agent import loop as loop_module
from src.agent.loop import AgentLoop
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
async def test_submit_manifest_returns_error_for_unresolvable_label_reference() -> None:
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
                        "source_reference": "Encounter/tango golf potato",
                        "description": "test",
                    }
                ],
            },
        ),
        session,
    )

    assert result.is_error is True
    assert "not found" in result.content.lower()


@pytest.mark.asyncio
async def test_fhir_read_registers_bundle_ids_in_label_registry() -> None:
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
    assert session.label_registry.get_label(uuid_value) == uuid_to_label(uuid_value)


def test_system_prompt_sanitizes_context_fields_and_includes_label_table() -> None:
    openemr_client = AsyncMock()
    loop = _make_loop(openemr_client, [])
    session = AgentSession(
        page_context=PageContext(
            patient_id="patient-123\nINJECT",
            encounter_id="enc-456\r\nBLOCK",
            page_type="encounter-" + ("x" * 200),
        )
    )
    session.label_registry.register("bbb13f7a-966e-4c7c-aea5-4bac3ce98505")

    prompt = loop._get_system_prompt(session)

    assert "INJECT" in prompt
    assert "Patient ID: patient-123 INJECT" in prompt
    assert "Encounter ID: enc-456  BLOCK" in prompt
    assert "\nINJECT" not in prompt
    assert "Resource Labels (use these instead of UUIDs)" in prompt


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
async def test_fhir_read_resolves_label_params_to_uuids_before_query() -> None:
    """When the LLM uses a three-word label as a FHIR query param
    (e.g. patient='tango golf potato'), the loop must resolve it to
    the real UUID before calling openemr_client.fhir_read."""
    patient_uuid = "bbb13f7a-966e-4c7c-aea5-4bac3ce98505"
    patient_label = uuid_to_label(patient_uuid)

    openemr_client = AsyncMock()
    openemr_client.fhir_read = AsyncMock(
        return_value={"resourceType": "Bundle", "entry": []}
    )
    loop = _make_loop(openemr_client, [])
    session = AgentSession()
    session.label_registry.register(patient_uuid, "Patient")

    await loop._execute_tool(
        ToolCall(
            id="tool-1",
            name="fhir_read",
            arguments={
                "resource_type": "Condition",
                "params": {"patient": patient_label},
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
