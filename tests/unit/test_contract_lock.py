"""Contract-lock test: verify prompt, backend, and eval dataset agree on writable types.

Both introspection documents identified prompt/backend/eval contract drift as a
top-3 failure causing 4+ rework cycles.  This test mechanically prevents that
drift by asserting all three sources declare the same set of writable resource
types.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _parse_prompt_writable_types() -> set[str]:
    """Extract writable resource types from the system prompt.

    Looks for the "Writable resource types" section in SYSTEM_PROMPT and
    parses the bold-delimited list (e.g. **Condition**, **MedicationRequest**).
    """
    prompts_path = ROOT / "src" / "agent" / "prompts.py"
    text = prompts_path.read_text(encoding="utf-8")

    match = re.search(
        r"Writable resource types.*?Only the following.*?:(.*?)(?:Other types|\.\\)",
        text,
        re.DOTALL,
    )
    assert match, "Could not find 'Writable resource types' section in prompts.py"
    return set(re.findall(r"\*\*(\w+)\*\*", match.group(1)))


def _parse_translator_writable_types() -> set[str]:
    """Extract writable resource types from the translator's REST path map."""
    from src.agent.translator import _REST_PATH_MAP

    return set(_REST_PATH_MAP.keys())


def _parse_eval_manifest_types() -> set[str]:
    """Extract resource types from eval dataset expected manifest_items."""
    dataset_path = ROOT / "tests" / "eval" / "dataset.json"
    data = json.loads(dataset_path.read_text(encoding="utf-8"))
    types: set[str] = set()
    for case in data:
        for item in case.get("expected", {}).get("manifest_items", []):
            rt = item.get("resource_type")
            if rt:
                types.add(rt)
    return types


def test_prompt_matches_translator() -> None:
    """System prompt writable types must match translator REST path map."""
    prompt_types = _parse_prompt_writable_types()
    translator_types = _parse_translator_writable_types()
    assert prompt_types == translator_types, (
        f"Prompt/translator drift!\n"
        f"  Prompt only: {prompt_types - translator_types}\n"
        f"  Translator only: {translator_types - prompt_types}"
    )


def test_eval_subset_of_translator() -> None:
    """Eval dataset manifest resource types must all be writable in translator."""
    eval_types = _parse_eval_manifest_types()
    translator_types = _parse_translator_writable_types()
    unsupported = eval_types - translator_types
    assert not unsupported, (
        f"Eval dataset expects writes to types the translator doesn't support: "
        f"{unsupported}"
    )


def test_all_three_agree() -> None:
    """Prompt, translator, and eval dataset must declare the same writable types."""
    prompt_types = _parse_prompt_writable_types()
    translator_types = _parse_translator_writable_types()
    eval_types = _parse_eval_manifest_types()
    assert prompt_types == translator_types == eval_types, (
        f"Contract drift detected!\n"
        f"  Prompt:     {sorted(prompt_types)}\n"
        f"  Translator: {sorted(translator_types)}\n"
        f"  Eval:       {sorted(eval_types)}"
    )
