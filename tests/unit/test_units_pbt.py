from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from hypothesis import given
from hypothesis import strategies as st

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
