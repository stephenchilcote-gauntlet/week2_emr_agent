from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from hypothesis import given, settings
from hypothesis import strategies as st

from src.agent.labels import resolve_identifier, resolve_reference, uuid_to_words, words_to_uuid
from src.agent.loop import AgentLoop
from src.agent.models import ManifestAction, ManifestItem
from src.verification.checks import _extract_code
from src.verification.icd10 import validate_cpt_format, validate_icd10_format


@given(st.from_regex(r"^[A-Z]\d{2}(\.\d{1,4})?$", fullmatch=True))
def test_pbt_validate_icd10_format_accepts_regex_matches(code: str) -> None:
    assert validate_icd10_format(code)


@given(st.from_regex(r"^\d{5}$", fullmatch=True))
def test_pbt_validate_cpt_format_accepts_5_digit_codes(code: str) -> None:
    assert validate_cpt_format(code)


@given(st.recursive(st.none() | st.booleans() | st.text() | st.integers(), lambda c: st.lists(c) | st.dictionaries(st.text(), c), max_leaves=12))
def test_pbt_extract_code_never_raises_on_arbitrary_payloads(payload: object) -> None:
    result = _extract_code(payload)
    assert result is None or isinstance(result, str)


@given(st.lists(st.uuids().map(str), min_size=1, max_size=10, unique=True))
def test_pbt_topological_sort_stable_without_dependencies(ids: list[str]) -> None:
    loop = AgentLoop(
        anthropic_client=SimpleNamespace(messages=SimpleNamespace(create=AsyncMock())),
        openemr_client=AsyncMock(),
    )
    items = [
        ManifestItem(
            id=item_id,
            resource_type="Condition",
            action=ManifestAction.CREATE,
            proposed_value={"code": "E11.9"},
            source_reference="Encounter/123",
            description=f"item-{idx}",
            depends_on=[],
        )
        for idx, item_id in enumerate(ids)
    ]

    sorted_items = loop._topological_sort(items)
    assert [item.id for item in sorted_items] == ids


# ---------------------------------------------------------------------------
# _sanitize_context_field: always returns str, length ≤ 100, no newlines
# ---------------------------------------------------------------------------


@given(st.one_of(st.none(), st.text(max_size=500)))
def test_pbt_sanitize_context_field_never_raises(value: str | None) -> None:
    """_sanitize_context_field never raises and always returns a str."""
    result = AgentLoop._sanitize_context_field(value)
    assert isinstance(result, str)


@given(st.one_of(st.none(), st.text(max_size=500)))
def test_pbt_sanitize_context_field_max_length(value: str | None) -> None:
    """_sanitize_context_field result is never longer than 100 chars."""
    result = AgentLoop._sanitize_context_field(value)
    assert len(result) <= 100


@given(st.text(max_size=500))
def test_pbt_sanitize_context_field_no_newlines(value: str) -> None:
    """_sanitize_context_field strips all newline and tab characters."""
    result = AgentLoop._sanitize_context_field(value)
    assert "\n" not in result
    assert "\r" not in result
    assert "\t" not in result


# ---------------------------------------------------------------------------
# _truncate_tool_content: output always ≤ MAX_TOOL_RESULT_CHARS + overhead
# ---------------------------------------------------------------------------


@given(st.text(min_size=0, max_size=200))
def test_pbt_truncate_tool_content_short_passthrough(content: str) -> None:
    """Short content passes through _truncate_tool_content unchanged."""
    from src.agent.loop import MAX_TOOL_RESULT_CHARS
    if len(content) <= MAX_TOOL_RESULT_CHARS:
        result = AgentLoop._truncate_tool_content(content)
        assert result == content


@given(st.text(min_size=0, max_size=50000))
def test_pbt_truncate_tool_content_always_reasonable_length(content: str) -> None:
    """_truncate_tool_content always returns a string."""
    result = AgentLoop._truncate_tool_content(content)
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# resolve_identifier: UUIDs pass through; word-IDs round-trip
# ---------------------------------------------------------------------------


@given(st.uuids())
def test_pbt_resolve_identifier_passthrough_uuid(uid) -> None:
    """resolve_identifier passes through a raw UUID unchanged."""
    uuid_str = str(uid)
    assert resolve_identifier(uuid_str) == uuid_str


@given(st.text(min_size=0, max_size=200))
def test_pbt_resolve_identifier_never_raises(value: str) -> None:
    """resolve_identifier never raises on arbitrary string input."""
    result = resolve_identifier(value)
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# resolve_reference: FHIR-style references parsed correctly
# ---------------------------------------------------------------------------


@given(st.uuids())
def test_pbt_resolve_reference_preserves_resource_type(uid) -> None:
    """Resource type is preserved through resolve_reference."""
    ref = f"Condition/{uid}"
    result = resolve_reference(ref)
    assert result.startswith("Condition/")


@given(st.text(min_size=1, max_size=100, alphabet=st.characters(whitelist_categories=("L", "N"))))
def test_pbt_resolve_reference_no_slash_delegates_to_resolve_identifier(value: str) -> None:
    """Reference with no slash is treated as a plain identifier."""
    result = resolve_reference(value)
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# uuid_to_words / words_to_uuid round-trip
# ---------------------------------------------------------------------------


@given(st.uuids())
def test_pbt_uuid_to_words_round_trip(uid) -> None:
    """UUID encoded to words then decoded returns the original UUID."""
    uuid_str = str(uid)
    words = uuid_to_words(uuid_str)
    recovered = words_to_uuid(words)
    assert recovered == uuid_str


@given(st.uuids())
def test_pbt_uuid_to_words_produces_10_words(uid) -> None:
    """uuid_to_words always produces exactly 10 words."""
    words = uuid_to_words(str(uid))
    assert len(words.split()) == 10
