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
    session.fhir_patient_id = "patient-1"
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
    session.fhir_patient_id = "patient-1"

    first = ToolCall(
        id="tool-1",
        name="submit_manifest",
        arguments={
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
    session.fhir_patient_id = "patient-1"

    result = await loop._execute_tool(
        ToolCall(
            id="tool-3",
            name="submit_manifest",
            arguments={
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
    session.fhir_patient_id = "patient-1"

    manifest = loop._build_manifest(
        {
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
    session.fhir_patient_id = "patient-1"

    dsl = (
        f'<edit ref="MedicationRequest/{med_words}" '
        f'dose="5mg" freq="once daily" route="oral" '
        f'src="Condition/{src_words}" conf="medium" id="med-1">'
        f"Add dose</edit>"
    )

    manifest = loop._build_manifest(
        {"items": dsl},
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
# _build_manifest additional tests
# ------------------------------------------------------------------


def test_build_manifest_dsl_remove_action() -> None:
    """DSL <remove> creates a ManifestItem with action='delete'."""
    condition_uuid = "cccccccc-1111-2222-3333-444444444444"
    enc_uuid = "eeeeeeee-1111-2222-3333-444444444444"

    loop = _make_loop(AsyncMock(), [])
    session = AgentSession()
    session.fhir_patient_id = "patient-1"

    dsl = (
        f'<remove ref="Condition/{condition_uuid}" '
        f'src="Encounter/{enc_uuid}" id="rm-1">'
        "Remove resolved condition"
        "</remove>"
    )
    manifest = loop._build_manifest({"items": dsl}, session)

    assert len(manifest.items) == 1
    item = manifest.items[0]
    assert item.action.value == "delete"
    assert item.resource_type == "Condition"
    assert item.target_resource_id == condition_uuid
    assert item.id == "rm-1"


def test_build_manifest_dsl_add_with_confidence_and_deps() -> None:
    """DSL <add> with conf and deps sets confidence and depends_on on item."""
    enc_uuid = "eeeeeeee-1111-2222-3333-444444444444"

    loop = _make_loop(AsyncMock(), [])
    session = AgentSession()
    session.fhir_patient_id = "patient-1"

    dsl = (
        f'<add type="Condition" code="E11.9" src="Encounter/{enc_uuid}" '
        f'conf="medium" deps="item-a,item-b" id="cond-1">'
        "Add diabetes"
        "</add>"
    )
    manifest = loop._build_manifest({"items": dsl}, session)

    assert len(manifest.items) == 1
    item = manifest.items[0]
    assert item.confidence == "medium"
    assert item.depends_on == ["item-a", "item-b"]


def test_build_manifest_merges_with_existing_manifest() -> None:
    """When existing manifest is provided, new items are merged (by ID)."""
    from src.agent.models import ChangeManifest, ManifestItem, ManifestAction

    loop = _make_loop(AsyncMock(), [])
    session = AgentSession()
    session.fhir_patient_id = "patient-1"

    # Existing manifest has item-1 only
    existing_item = ManifestItem(
        id="item-1",
        resource_type="Condition",
        action=ManifestAction.CREATE,
        proposed_value={"code": "E11.9"},
        source_reference="Encounter/123",
        description="Existing diabetes",
    )
    existing_manifest = ChangeManifest(
        patient_id="patient-1",
        items=[existing_item],
    )

    # New DSL adds item-2 and updates item-1
    dsl = (
        '<add type="Condition" code="I10" src="Encounter/123" id="item-2">'
        "Add hypertension"
        "</add>"
    )
    merged = loop._build_manifest(
        {"items": dsl}, session, existing=existing_manifest
    )

    assert len(merged.items) == 2
    item_ids = {i.id for i in merged.items}
    assert "item-1" in item_ids
    assert "item-2" in item_ids
    # Merged manifest keeps existing manifest's ID
    assert merged.id == existing_manifest.id


def test_build_manifest_patient_id_from_arguments_fallback() -> None:
    """When session has no fhir_patient_id, falls back to arguments patient_id."""
    enc_uuid = "eeeeeeee-1111-2222-3333-444444444444"
    expected_patient = "fallback-patient-uuid"

    loop = _make_loop(AsyncMock(), [])
    session = AgentSession()
    # No fhir_patient_id set
    assert session.fhir_patient_id is None

    dsl = (
        f'<add type="Condition" code="I10" src="Encounter/{enc_uuid}" id="c1">'
        "Add condition"
        "</add>"
    )
    manifest = loop._build_manifest(
        {"items": dsl, "patient_id": expected_patient}, session
    )

    assert manifest.patient_id == expected_patient


def test_resolve_fhir_params_resolves_word_ids() -> None:
    """_resolve_fhir_params replaces word-encoded IDs with hex UUIDs."""
    from src.agent.labels import uuid_to_words

    uuid = "bbb13f7a-966e-4c7c-aea5-4bac3ce98505"
    words = uuid_to_words(uuid)

    loop = _make_loop(AsyncMock(), [])
    session = AgentSession()

    params = {"patient": words, "category": "problem-list-item"}
    resolved = loop._resolve_fhir_params(params, session)

    assert resolved["patient"] == uuid
    assert resolved["category"] == "problem-list-item"  # non-word-id unchanged


def test_resolve_fhir_params_passes_through_regular_strings() -> None:
    """_resolve_fhir_params leaves non-word-id strings unchanged."""
    loop = _make_loop(AsyncMock(), [])
    session = AgentSession()

    params = {"category": "laboratory", "_count": "10"}
    resolved = loop._resolve_fhir_params(params, session)

    assert resolved == {"category": "laboratory", "_count": "10"}


def test_resolve_fhir_params_passes_uuid_unchanged() -> None:
    """_resolve_fhir_params passes raw UUIDs through unchanged."""
    uuid = "bbb13f7a-966e-4c7c-aea5-4bac3ce98505"

    loop = _make_loop(AsyncMock(), [])
    session = AgentSession()

    params = {"patient": uuid}
    resolved = loop._resolve_fhir_params(params, session)

    assert resolved["patient"] == uuid


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


# ------------------------------------------------------------------
# open_patient_chart tool
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_patient_chart_sets_session_state() -> None:
    """open_patient_chart should resolve FHIR UUID → OpenEMR PID and update session."""
    openemr_client = AsyncMock()
    openemr_client.fhir_read = AsyncMock(return_value={
        "resourceType": "Bundle",
        "type": "searchset",
        "total": 1,
        "entry": [{
            "resource": {
                "resourceType": "Patient",
                "id": "abc-123-uuid",
                "identifier": [
                    {
                        "type": {"coding": [{"code": "PT"}]},
                        "value": "7",
                    }
                ],
                "name": [{"given": ["Robert"], "family": "Chen"}],
                "birthDate": "1952-08-19",
            }
        }],
    })
    loop = _make_loop(openemr_client, [])
    session = AgentSession()

    tc = ToolCall(id="t1", name="open_patient_chart", arguments={"patient_uuid": "abc-123-uuid"})
    result = await loop._execute_tool(tc, session)

    parsed = json.loads(result.content)
    assert not result.is_error
    assert parsed["status"] == "patient_chart_opened"
    assert parsed["openemr_pid"] == "7"
    assert parsed["patient_name"] == "Robert Chen"
    assert session.fhir_patient_id == "abc-123-uuid"
    assert session.page_context is not None
    assert session.page_context.patient_id == "7"
    assert session.openemr_pid == "7"
    assert session.navigate_to_patient == {"pid": "7", "pname": "Robert Chen", "dob": "1952-08-19"}


@pytest.mark.asyncio
async def test_open_patient_chart_error_patient_not_found() -> None:
    """open_patient_chart should return error when patient UUID is invalid."""
    openemr_client = AsyncMock()
    openemr_client.fhir_read = AsyncMock(return_value={
        "error": "Resource not found",
        "status_code": 404,
    })
    loop = _make_loop(openemr_client, [])
    session = AgentSession()

    tc = ToolCall(id="t1", name="open_patient_chart", arguments={"patient_uuid": "bad-uuid"})
    result = await loop._execute_tool(tc, session)

    assert result.is_error
    parsed = json.loads(result.content)
    assert "error" in parsed


@pytest.mark.asyncio
async def test_open_patient_chart_fallback_identifier() -> None:
    """open_patient_chart should use first identifier when no PT-coded one exists."""
    openemr_client = AsyncMock()
    openemr_client.fhir_read = AsyncMock(return_value={
        "resourceType": "Bundle", "type": "searchset", "total": 1,
        "entry": [{"resource": {
            "resourceType": "Patient",
            "id": "xyz-uuid",
            "identifier": [{"value": "12"}],
            "name": [{"given": ["Linda"], "family": "Martinez"}],
        }}],
    })
    loop = _make_loop(openemr_client, [])
    session = AgentSession()

    tc = ToolCall(id="t1", name="open_patient_chart", arguments={"patient_uuid": "xyz-uuid"})
    result = await loop._execute_tool(tc, session)

    parsed = json.loads(result.content)
    assert not result.is_error
    assert parsed["openemr_pid"] == "12"
    assert session.openemr_pid == "12"


@pytest.mark.asyncio
async def test_open_patient_chart_no_identifiers() -> None:
    """open_patient_chart should error when FHIR Patient has no identifiers."""
    openemr_client = AsyncMock()
    openemr_client.fhir_read = AsyncMock(return_value={
        "resourceType": "Bundle", "type": "searchset", "total": 1,
        "entry": [{"resource": {
            "resourceType": "Patient",
            "id": "no-id-uuid",
            "identifier": [],
            "name": [{"given": ["Test"], "family": "Patient"}],
        }}],
    })
    loop = _make_loop(openemr_client, [])
    session = AgentSession()

    tc = ToolCall(id="t1", name="open_patient_chart", arguments={"patient_uuid": "no-id-uuid"})
    result = await loop._execute_tool(tc, session)

    assert result.is_error
    parsed = json.loads(result.content)
    assert "Could not resolve" in parsed["error"]


@pytest.mark.asyncio
async def test_open_patient_chart_updates_existing_page_context() -> None:
    """open_patient_chart should update existing page_context rather than replace it."""
    openemr_client = AsyncMock()
    openemr_client.fhir_read = AsyncMock(return_value={
        "resourceType": "Bundle", "type": "searchset", "total": 1,
        "entry": [{"resource": {
            "resourceType": "Patient",
            "id": "new-uuid",
            "identifier": [{"type": {"coding": [{"code": "PT"}]}, "value": "9"}],
            "name": [{"given": ["Michael"], "family": "Thompson"}],
        }}],
    })
    loop = _make_loop(openemr_client, [])
    session = AgentSession()
    session.page_context = PageContext(patient_id="4", page_type="patient_summary")

    tc = ToolCall(id="t1", name="open_patient_chart", arguments={"patient_uuid": "new-uuid"})
    result = await loop._execute_tool(tc, session)

    assert not result.is_error
    assert session.page_context.patient_id == "9"
    assert session.page_context.page_type == "patient_summary"  # preserved


# ---------------------------------------------------------------------------
# _truncate_messages
# ---------------------------------------------------------------------------


def test_truncate_messages_short_conversation_unchanged() -> None:
    """Fewer than 4 messages are returned unchanged."""
    loop = _make_loop(AsyncMock(), [])
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi"},
    ]
    result = loop._truncate_messages(messages)
    assert result == messages


def test_truncate_messages_preserves_first_user_message() -> None:
    """First user message is always in the result."""
    loop = _make_loop(AsyncMock(), [])
    messages = [
        {"role": "user", "content": "First user message"},
        {"role": "assistant", "content": "Reply 1"},
        {"role": "user", "content": "Second message"},
        {"role": "assistant", "content": "Reply 2"},
        {"role": "user", "content": "Third message"},
        {"role": "assistant", "content": "Reply 3"},
        {"role": "user", "content": "Fourth message"},
    ]
    result = loop._truncate_messages(messages)
    assert result[0]["content"] == "First user message"
    assert result[0]["role"] == "user"


def test_truncate_messages_includes_truncation_note() -> None:
    """When truncated, a summary note is included."""
    loop = _make_loop(AsyncMock(), [])
    messages = [{"role": "user", "content": f"msg {i}"} for i in range(20)]
    messages += [{"role": "assistant", "content": f"reply {i}"} for i in range(20)]
    result = loop._truncate_messages(messages)
    has_note = any(
        "summarized" in (m.get("content", "") or "")
        for m in result
    )
    assert has_note, "Truncated messages should include a summary note"


def test_truncate_messages_keeps_tail() -> None:
    """Last 10 messages are preserved in tail."""
    loop = _make_loop(AsyncMock(), [])
    messages = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i}"}
        for i in range(30)
    ]
    result = loop._truncate_messages(messages)
    # The last message from the original should appear in result
    assert messages[-1] in result


# ---------------------------------------------------------------------------
# _render_visible_data
# ---------------------------------------------------------------------------


def test_render_visible_data_simple_dict() -> None:
    """Dict content is rendered with key: value format."""
    loop = _make_loop(AsyncMock(), [])
    result = loop._render_visible_data({"patient_info": {"name": "Maria", "dob": "1970-01-01"}})
    assert "Patient Info" in result
    assert "name: Maria" in result
    assert "dob: 1970-01-01" in result


def test_render_visible_data_list_content() -> None:
    """List content renders each item as a bullet."""
    loop = _make_loop(AsyncMock(), [])
    result = loop._render_visible_data({
        "conditions": [
            {"code": "E11.9", "display": "Type 2 DM"},
            {"code": "I10", "display": "Hypertension"},
        ]
    })
    assert "Conditions" in result
    assert "E11.9" in result
    assert "Hypertension" in result


def test_render_visible_data_string_content() -> None:
    """String content is rendered as a quoted line."""
    loop = _make_loop(AsyncMock(), [])
    result = loop._render_visible_data({"note": "Patient is stable"})
    assert "Note" in result
    assert "Patient is stable" in result


def test_render_visible_data_truncated_when_too_long() -> None:
    """Very large input is truncated at 6000 chars."""
    loop = _make_loop(AsyncMock(), [])
    big_content = {"data": "x" * 10000}
    result = loop._render_visible_data(big_content)
    assert len(result) <= 6100  # some slack for truncation marker
    assert "truncated" in result


# ---------------------------------------------------------------------------
# _topological_sort
# ---------------------------------------------------------------------------


def test_topological_sort_no_dependencies() -> None:
    """Items with no dependencies are returned in original order."""
    from src.agent.models import ManifestItem, ManifestAction
    loop = _make_loop(AsyncMock(), [])
    items = [
        ManifestItem(id="a", resource_type="Condition", action=ManifestAction.CREATE,
                     proposed_value={}, source_reference="enc/1", description="A"),
        ManifestItem(id="b", resource_type="MedicationRequest", action=ManifestAction.CREATE,
                     proposed_value={}, source_reference="enc/1", description="B"),
    ]
    result = loop._topological_sort(items)
    assert [i.id for i in result] == ["a", "b"]


def test_topological_sort_respects_dependency() -> None:
    """Item with depends_on appears after the dependency."""
    from src.agent.models import ManifestItem, ManifestAction
    loop = _make_loop(AsyncMock(), [])
    item_a = ManifestItem(id="a", resource_type="Condition", action=ManifestAction.CREATE,
                          proposed_value={}, source_reference="enc/1", description="A")
    item_b = ManifestItem(id="b", resource_type="Condition", action=ManifestAction.CREATE,
                          proposed_value={}, source_reference="enc/1", description="B",
                          depends_on=["a"])
    result = loop._topological_sort([item_b, item_a])  # b before a in input
    ids = [i.id for i in result]
    assert ids.index("a") < ids.index("b"), "a should come before b (dependency)"


def test_topological_sort_chain_dependency() -> None:
    """Chain a→b→c is sorted correctly."""
    from src.agent.models import ManifestItem, ManifestAction
    loop = _make_loop(AsyncMock(), [])

    def make_item(item_id: str, deps: list[str]) -> ManifestItem:
        return ManifestItem(id=item_id, resource_type="Condition",
                            action=ManifestAction.CREATE, proposed_value={},
                            source_reference="enc/1", description=item_id,
                            depends_on=deps)

    items = [make_item("c", ["b"]), make_item("b", ["a"]), make_item("a", [])]
    result = loop._topological_sort(items)
    ids = [i.id for i in result]
    assert ids.index("a") < ids.index("b") < ids.index("c")


def test_topological_sort_handles_missing_dependency() -> None:
    """Item referencing a non-existent dependency doesn't crash."""
    from src.agent.models import ManifestItem, ManifestAction
    loop = _make_loop(AsyncMock(), [])
    item = ManifestItem(id="a", resource_type="Condition", action=ManifestAction.CREATE,
                        proposed_value={}, source_reference="enc/1", description="A",
                        depends_on=["nonexistent"])
    result = loop._topological_sort([item])
    assert len(result) == 1
    assert result[0].id == "a"


# ---------------------------------------------------------------------------
# _build_messages
# ---------------------------------------------------------------------------


def test_build_messages_user_message() -> None:
    """User messages become role=user with content string."""
    from src.agent.models import AgentMessage, AgentSession
    loop = _make_loop(AsyncMock(), [])
    session = AgentSession(
        messages=[AgentMessage(role="user", content="Hello there")]
    )
    result = loop._build_messages(session)
    assert len(result) == 1
    assert result[0]["role"] == "user"
    assert result[0]["content"] == "Hello there"


def test_build_messages_assistant_text_only() -> None:
    """Assistant message with text becomes text block in content list."""
    from src.agent.models import AgentMessage, AgentSession
    loop = _make_loop(AsyncMock(), [])
    session = AgentSession(
        messages=[AgentMessage(role="assistant", content="Here is the answer")]
    )
    result = loop._build_messages(session)
    assert len(result) == 1
    assert result[0]["role"] == "assistant"
    content = result[0]["content"]
    assert len(content) == 1
    assert content[0]["type"] == "text"
    assert content[0]["text"] == "Here is the answer"


def test_build_messages_assistant_with_tool_calls() -> None:
    """Assistant message with tool calls includes tool_use blocks."""
    from src.agent.models import AgentMessage, AgentSession, ToolCall
    loop = _make_loop(AsyncMock(), [])
    tc = ToolCall(id="tc-1", name="fhir_read", arguments={"resource_type": "Patient"})
    session = AgentSession(
        messages=[
            AgentMessage(role="assistant", content="Calling FHIR", tool_calls=[tc])
        ]
    )
    result = loop._build_messages(session)
    assert len(result) == 1
    content = result[0]["content"]
    assert len(content) == 2
    assert content[0] == {"type": "text", "text": "Calling FHIR"}
    assert content[1]["type"] == "tool_use"
    assert content[1]["id"] == "tc-1"
    assert content[1]["name"] == "fhir_read"
    assert content[1]["input"] == {"resource_type": "Patient"}


def test_build_messages_tool_result() -> None:
    """Tool result messages become role=user with tool_result blocks."""
    from src.agent.models import AgentMessage, AgentSession, ToolResult
    loop = _make_loop(AsyncMock(), [])
    tr = ToolResult(tool_call_id="tc-1", content='{"name": "Maria"}', is_error=False)
    session = AgentSession(
        messages=[
            AgentMessage(role="tool", content="", tool_results=[tr])
        ]
    )
    result = loop._build_messages(session)
    assert len(result) == 1
    assert result[0]["role"] == "user"
    content = result[0]["content"]
    assert len(content) == 1
    assert content[0]["type"] == "tool_result"
    assert content[0]["tool_use_id"] == "tc-1"
    assert content[0]["is_error"] is False


def test_build_messages_error_tool_result() -> None:
    """Tool result with is_error=True is preserved."""
    from src.agent.models import AgentMessage, AgentSession, ToolResult
    loop = _make_loop(AsyncMock(), [])
    tr = ToolResult(tool_call_id="tc-err", content="Not found", is_error=True)
    session = AgentSession(
        messages=[AgentMessage(role="tool", content="", tool_results=[tr])]
    )
    result = loop._build_messages(session)
    assert result[0]["content"][0]["is_error"] is True
    assert result[0]["content"][0]["content"] == "Not found"


def test_build_messages_multi_turn_conversation() -> None:
    """Full user→assistant→tool→assistant sequence maps correctly."""
    from src.agent.models import AgentMessage, AgentSession, ToolCall, ToolResult
    loop = _make_loop(AsyncMock(), [])
    tc = ToolCall(id="tc-1", name="fhir_read", arguments={})
    tr = ToolResult(tool_call_id="tc-1", content='{"result": "ok"}', is_error=False)
    session = AgentSession(
        messages=[
            AgentMessage(role="user", content="What is the patient age?"),
            AgentMessage(role="assistant", content="Let me check", tool_calls=[tc]),
            AgentMessage(role="tool", content="", tool_results=[tr]),
            AgentMessage(role="assistant", content="The patient is 55 years old"),
        ]
    )
    result = loop._build_messages(session)
    assert len(result) == 4
    assert result[0]["role"] == "user"
    assert result[1]["role"] == "assistant"
    assert result[2]["role"] == "user"  # tool results appear as user role
    assert result[3]["role"] == "assistant"


def test_build_messages_empty_session() -> None:
    """Empty session produces empty messages list."""
    from src.agent.models import AgentSession
    loop = _make_loop(AsyncMock(), [])
    session = AgentSession(messages=[])
    result = loop._build_messages(session)
    assert result == []


# ---------------------------------------------------------------------------
# _get_system_prompt
# ---------------------------------------------------------------------------


def test_get_system_prompt_no_context() -> None:
    """With no page context, system prompt is just the base SYSTEM_PROMPT."""
    from src.agent.models import AgentSession
    from src.agent.prompts import SYSTEM_PROMPT
    loop = _make_loop(AsyncMock(), [])
    session = AgentSession()
    result = loop._get_system_prompt(session)
    assert result == SYSTEM_PROMPT


def test_get_system_prompt_with_patient_id() -> None:
    """Patient ID appears in the system prompt when set."""
    from src.agent.models import AgentSession, PageContext
    loop = _make_loop(AsyncMock(), [])
    session = AgentSession(
        page_context=PageContext(patient_id="42")
    )
    result = loop._get_system_prompt(session)
    assert "Patient ID: 42" in result
    assert "Current Context" in result


def test_get_system_prompt_with_fhir_patient_id() -> None:
    """FHIR patient UUID appears in system prompt when set."""
    from src.agent.models import AgentSession, PageContext
    loop = _make_loop(AsyncMock(), [])
    session = AgentSession(
        page_context=PageContext(patient_id="42"),
        fhir_patient_id="abc-123-uuid"
    )
    result = loop._get_system_prompt(session)
    assert "abc-123-uuid" in result
    assert "FHIR Patient UUID" in result


def test_get_system_prompt_with_encounter_id() -> None:
    """Encounter ID is included in the prompt when set."""
    from src.agent.models import AgentSession, PageContext
    loop = _make_loop(AsyncMock(), [])
    session = AgentSession(
        page_context=PageContext(encounter_id="enc-999")
    )
    result = loop._get_system_prompt(session)
    assert "Encounter ID: enc-999" in result


def test_get_system_prompt_with_page_type() -> None:
    """Active tab (page_type) is included in the prompt."""
    from src.agent.models import AgentSession, PageContext
    loop = _make_loop(AsyncMock(), [])
    session = AgentSession(
        page_context=PageContext(page_type="enc")
    )
    result = loop._get_system_prompt(session)
    assert "Active Tab: enc" in result


def test_get_system_prompt_reviewing_phase_adds_manifest_info() -> None:
    """Reviewing phase adds active manifest section."""
    from src.agent.models import AgentSession, ChangeManifest, ManifestItem, ManifestAction
    loop = _make_loop(AsyncMock(), [])
    item = ManifestItem(
        id="i-1", resource_type="Condition", action=ManifestAction.CREATE,
        proposed_value={}, source_reference="enc/1", description="Test"
    )
    manifest = ChangeManifest(patient_id="42", items=[item])
    session = AgentSession(phase="reviewing", manifest=manifest)
    result = loop._get_system_prompt(session)
    assert "Active Manifest" in result
    assert "under review" in result
    assert "1 item" in result


def test_get_system_prompt_sanitizes_patient_id() -> None:
    """Newlines in patient_id are replaced with spaces (preventing new prompt lines)."""
    from src.agent.models import AgentSession, PageContext
    loop = _make_loop(AsyncMock(), [])
    session = AgentSession(
        page_context=PageContext(patient_id="42\nINJECTED")
    )
    result = loop._get_system_prompt(session)
    # The newline is sanitized — 'INJECTED' stays on the same line as Patient ID
    # (no raw \n present in the Patient ID section)
    patient_id_line = [ln for ln in result.splitlines() if "Patient ID" in ln]
    assert len(patient_id_line) == 1, "Patient ID should be on a single line after sanitization"
    assert "42" in patient_id_line[0]
    assert "INJECTED" in patient_id_line[0]  # On same line (not a new instruction line)


# ---------------------------------------------------------------------------
# _extract_text and _extract_tool_calls
# ---------------------------------------------------------------------------


def test_extract_text_single_block() -> None:
    """Single text block returns its text."""
    loop = _make_loop(AsyncMock(), [])
    response = _response(_text_block("Hello world"))
    assert loop._extract_text(response) == "Hello world"


def test_extract_text_multiple_blocks() -> None:
    """Multiple text blocks are joined with newlines."""
    loop = _make_loop(AsyncMock(), [])
    response = _response(_text_block("First"), _text_block("Second"))
    result = loop._extract_text(response)
    assert "First" in result
    assert "Second" in result


def test_extract_text_skips_tool_blocks() -> None:
    """Tool use blocks are not included in text extraction."""
    loop = _make_loop(AsyncMock(), [])
    response = _response(
        _tool_block("tc-1", "fhir_read", {}),
        _text_block("Here is the result"),
    )
    result = loop._extract_text(response)
    assert result == "Here is the result"
    assert "fhir_read" not in result


def test_extract_text_empty_response() -> None:
    """Empty content list returns empty string."""
    loop = _make_loop(AsyncMock(), [])
    response = _response()
    assert loop._extract_text(response) == ""


def test_extract_tool_calls_single_tool() -> None:
    """Single tool_use block is extracted as ToolCall."""
    loop = _make_loop(AsyncMock(), [])
    response = _response(_tool_block("tc-1", "fhir_read", {"resource_type": "Patient"}))
    tool_calls = loop._extract_tool_calls(response)
    assert len(tool_calls) == 1
    assert tool_calls[0].id == "tc-1"
    assert tool_calls[0].name == "fhir_read"
    assert tool_calls[0].arguments == {"resource_type": "Patient"}


def test_extract_tool_calls_multiple_tools() -> None:
    """Multiple tool_use blocks are all extracted."""
    loop = _make_loop(AsyncMock(), [])
    response = _response(
        _tool_block("tc-1", "fhir_read", {}),
        _tool_block("tc-2", "openemr_api", {"endpoint": "/patient"}),
    )
    tool_calls = loop._extract_tool_calls(response)
    assert len(tool_calls) == 2
    assert tool_calls[0].name == "fhir_read"
    assert tool_calls[1].name == "openemr_api"


def test_extract_tool_calls_skips_text_blocks() -> None:
    """Text blocks are not included in tool call extraction."""
    loop = _make_loop(AsyncMock(), [])
    response = _response(_text_block("Some explanation"), _tool_block("tc-1", "fhir_read", {}))
    tool_calls = loop._extract_tool_calls(response)
    assert len(tool_calls) == 1
    assert tool_calls[0].id == "tc-1"


def test_extract_tool_calls_empty_response() -> None:
    """Empty content list returns empty list of tool calls."""
    loop = _make_loop(AsyncMock(), [])
    response = _response()
    assert loop._extract_tool_calls(response) == []


# ------------------------------------------------------------------
# _execute_tool — send_developer_feedback
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_tool_send_developer_feedback_returns_confirmation() -> None:
    """send_developer_feedback returns feedback_submitted status."""
    loop = _make_loop(AsyncMock(), [])
    session = AgentSession()

    tc = ToolCall(
        id="t-fb-1",
        name="send_developer_feedback",
        arguments={"category": "bug", "message": "Search returns 500 on empty query"},
    )
    result = await loop._execute_tool(tc, session)

    assert not result.is_error
    parsed = json.loads(result.content)
    assert parsed["status"] == "feedback_submitted"
    assert parsed["category"] == "bug"


@pytest.mark.asyncio
async def test_execute_tool_send_developer_feedback_feature_request() -> None:
    """send_developer_feedback works for feature_request category."""
    loop = _make_loop(AsyncMock(), [])
    session = AgentSession()

    tc = ToolCall(
        id="t-fb-2",
        name="send_developer_feedback",
        arguments={"category": "feature_request", "message": "Add dark mode support"},
    )
    result = await loop._execute_tool(tc, session)

    assert not result.is_error
    parsed = json.loads(result.content)
    assert parsed["status"] == "feedback_submitted"
    assert parsed["category"] == "feature_request"


@pytest.mark.asyncio
async def test_execute_tool_send_developer_feedback_records_audit_when_store_set() -> None:
    """send_developer_feedback records an audit event when audit_store is configured."""
    from unittest.mock import MagicMock
    from src.observability.audit import AuditStore

    mock_audit = MagicMock(spec=AuditStore)
    loop = _make_loop(AsyncMock(), [])
    loop.audit_store = mock_audit
    session = AgentSession()
    session.openemr_user_id = "doc-1"

    tc = ToolCall(
        id="t-fb-3",
        name="send_developer_feedback",
        arguments={"category": "improvement", "message": "Better error messages"},
    )
    await loop._execute_tool(tc, session)

    mock_audit.record.assert_called_once()
    call_args = mock_audit.record.call_args[0][0]
    assert call_args.event_type == "developer_feedback"
    assert call_args.user_id == "doc-1"


# ------------------------------------------------------------------
# _execute_tool — get_page_context
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_tool_get_page_context_with_context_set() -> None:
    """get_page_context returns the session's page context when set."""
    loop = _make_loop(AsyncMock(), [])
    session = AgentSession()
    session.page_context = PageContext(
        patient_id="42",
        encounter_id="enc-99",
        page_type="encounter",
    )

    tc = ToolCall(id="t-ctx-1", name="get_page_context", arguments={})
    result = await loop._execute_tool(tc, session)

    assert not result.is_error
    parsed = json.loads(result.content)
    assert parsed["patient_id"] == "42"
    assert parsed["encounter_id"] == "enc-99"
    assert parsed["page_type"] == "encounter"


@pytest.mark.asyncio
async def test_execute_tool_get_page_context_without_context() -> None:
    """get_page_context returns a message when no context is set."""
    loop = _make_loop(AsyncMock(), [])
    session = AgentSession()
    # session.page_context is None by default

    tc = ToolCall(id="t-ctx-2", name="get_page_context", arguments={})
    result = await loop._execute_tool(tc, session)

    assert not result.is_error
    parsed = json.loads(result.content)
    assert "message" in parsed
    assert "No page context" in parsed["message"]


# ------------------------------------------------------------------
# _count_tokens
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_count_tokens_returns_zero_when_no_counter() -> None:
    """_count_tokens returns 0 when anthropic client has no count_tokens method."""
    from types import SimpleNamespace
    anthropic_client = SimpleNamespace(
        messages=SimpleNamespace()  # no count_tokens attribute
    )
    loop = AgentLoop(anthropic_client=anthropic_client, openemr_client=AsyncMock())
    result = await loop._count_tokens([], "system prompt")
    assert result == 0


@pytest.mark.asyncio
async def test_count_tokens_returns_token_count_from_counter() -> None:
    """_count_tokens returns input_tokens from the counter result."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock as AM

    counter_result = SimpleNamespace(input_tokens=1234)
    counter = AM(return_value=counter_result)
    anthropic_client = SimpleNamespace(
        messages=SimpleNamespace(count_tokens=counter)
    )
    loop = AgentLoop(anthropic_client=anthropic_client, openemr_client=AsyncMock())
    result = await loop._count_tokens(
        [{"role": "user", "content": "hello"}],
        "system prompt",
    )
    assert result == 1234


@pytest.mark.asyncio
async def test_count_tokens_falls_back_to_json_estimate_on_exception() -> None:
    """_count_tokens estimates from JSON size when counter raises an exception."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock as AM

    counter = AM(side_effect=ConnectionError("timeout"))
    anthropic_client = SimpleNamespace(
        messages=SimpleNamespace(count_tokens=counter)
    )
    loop = AgentLoop(anthropic_client=anthropic_client, openemr_client=AsyncMock())
    messages = [{"role": "user", "content": "hello world"}]
    system = "system prompt text"
    result = await loop._count_tokens(messages, system)
    # Estimate = (json size + system len) // 4 — should be > 0
    assert result > 0
    # Verify it matches the formula
    import json as json_mod
    expected = (len(json_mod.dumps(messages)) + len(system)) // 4
    assert result == expected


# ---------------------------------------------------------------------------
# _sanitize_context_field — long-string truncation
# ---------------------------------------------------------------------------


def test_sanitize_context_field_truncates_at_100_chars() -> None:
    """_sanitize_context_field truncates values longer than 100 characters."""
    loop = _make_loop(AsyncMock(), [])
    long_value = "A" * 200
    result = loop._sanitize_context_field(long_value)
    assert len(result) == 100
    assert result == "A" * 100


def test_sanitize_context_field_exactly_100_chars_not_truncated() -> None:
    """_sanitize_context_field preserves a value of exactly 100 characters."""
    loop = _make_loop(AsyncMock(), [])
    exactly_100 = "B" * 100
    result = loop._sanitize_context_field(exactly_100)
    assert result == exactly_100


def test_sanitize_context_field_replaces_carriage_return() -> None:
    """_sanitize_context_field replaces \\r with space."""
    loop = _make_loop(AsyncMock(), [])
    result = loop._sanitize_context_field("hello\rworld")
    assert "\r" not in result
    assert "hello world" == result


# ---------------------------------------------------------------------------
# _render_visible_data — list of non-dict items
# ---------------------------------------------------------------------------


def test_render_visible_data_list_of_strings() -> None:
    """List sections with string items use the > - prefix (not key: value)."""
    loop = _make_loop(AsyncMock(), [])
    result = loop._render_visible_data({
        "diagnoses": ["Type 2 DM", "Hypertension", "Obesity"]
    })
    assert "Diagnoses" in result
    assert "> - Type 2 DM" in result
    assert "> - Hypertension" in result
    assert "> - Obesity" in result


def test_render_visible_data_mixed_list() -> None:
    """Mixed list (dicts and strings) renders correctly for each item type."""
    loop = _make_loop(AsyncMock(), [])
    result = loop._render_visible_data({
        "items": [
            {"code": "I10"},   # dict → key: value
            "plain string",    # string → > - plain string
        ]
    })
    assert "code: I10" in result
    assert "> - plain string" in result


# ---------------------------------------------------------------------------
# _get_system_prompt — with visible_data in page_context
# ---------------------------------------------------------------------------


def test_get_system_prompt_with_visible_data() -> None:
    """visible_data in page_context is rendered into the system prompt."""
    from src.agent.models import AgentSession, PageContext
    loop = _make_loop(AsyncMock(), [])
    session = AgentSession(
        page_context=PageContext(
            patient_id="42",
            visible_data={"conditions": [{"code": "I10", "display": "Hypertension"}]},
        )
    )
    result = loop._get_system_prompt(session)
    assert "Conditions" in result
    assert "I10" in result
    assert "Hypertension" in result


# ---------------------------------------------------------------------------
# _build_messages — assistant with no text content and no tool calls
# ---------------------------------------------------------------------------


def test_build_messages_assistant_empty_content() -> None:
    """Assistant message with empty content and no tool calls produces empty content block."""
    from src.agent.models import AgentMessage, AgentSession
    loop = _make_loop(AsyncMock(), [])
    session = AgentSession(
        messages=[AgentMessage(role="assistant", content="")]
    )
    result = loop._build_messages(session)
    assert len(result) == 1
    # Empty content list when no text and no tool calls
    assert result[0]["role"] == "assistant"
    content = result[0]["content"]
    assert isinstance(content, list)
    assert len(content) == 0  # no text block, no tool_use blocks


# ---------------------------------------------------------------------------
# _truncate_messages — no user messages in conversation
# ---------------------------------------------------------------------------


def test_truncate_messages_no_user_message_uses_index_zero() -> None:
    """_truncate_messages defaults to first message when no user message found."""
    loop = _make_loop(AsyncMock(), [])
    messages = [
        {"role": "assistant", "content": "Hello"},
        {"role": "assistant", "content": "World"},
        {"role": "assistant", "content": "1"},
        {"role": "assistant", "content": "2"},
        {"role": "assistant", "content": "3"},
    ]
    result = loop._truncate_messages(messages)
    # First message should be preserved (index 0, since no user msg found)
    assert result[0] == {"role": "assistant", "content": "Hello"}
    assert len(result) >= 3  # first + note + tail
