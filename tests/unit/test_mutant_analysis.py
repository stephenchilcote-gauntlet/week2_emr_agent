from __future__ import annotations

from pathlib import Path

from src.eval.mutant_analysis import (
    MUST_FIX,
    NOISE,
    ContextResolver,
    MutantRecord,
    ScopeInfo,
    classify_mutant,
    parse_mutmut_show_output,
)


def test_parse_mutmut_show_output_extracts_changed_lines() -> None:
    output = """--- a/src/foo.py
+++ b/src/foo.py
@@
-MAX_TOOL_ROUNDS = 15
+MAX_TOOL_ROUNDS = 14
"""
    removed, added = parse_mutmut_show_output(output)
    assert removed == "MAX_TOOL_ROUNDS = 15"
    assert added == "MAX_TOOL_ROUNDS = 14"


def test_classify_mutant_marks_module_constant_as_noise() -> None:
    record = MutantRecord(
        mutant_id=1,
        status="survived",
        filename="src/agent/loop.py",
        line_number=32,
        source_line="MAX_TOOL_ROUNDS = 15",
        mutation_index=0,
    )
    scope = ScopeInfo(
        scope_type="module",
        scope_name="<module>",
        node_type="Assign",
        module_level_constant=True,
    )
    classification, reasons = classify_mutant(
        record,
        scope,
        removed_line="MAX_TOOL_ROUNDS = 15",
        added_line="MAX_TOOL_ROUNDS = 14",
    )

    assert classification == NOISE
    assert any("constant" in reason for reason in reasons)


def test_classify_mutant_marks_if_logic_as_must_fix() -> None:
    record = MutantRecord(
        mutant_id=2,
        status="survived",
        filename="src/verification/checks.py",
        line_number=114,
        source_line="if code and not validate_icd10_format(code):",
        mutation_index=0,
    )
    scope = ScopeInfo(
        scope_type="function",
        scope_name="check_constraints",
        node_type="If",
        module_level_constant=False,
    )
    classification, _reasons = classify_mutant(
        record,
        scope,
        removed_line=record.source_line,
        added_line="if code and validate_icd10_format(code):",
    )
    assert classification == MUST_FIX


def test_context_resolver_detects_module_constant(tmp_path: Path) -> None:
    source = """MAX_RETRIES = 3

def run(x: int) -> bool:
    if x > 0:
        return True
    return False
"""
    file_path = tmp_path / "sample.py"
    file_path.write_text(source)

    resolver = ContextResolver()
    scope = resolver.resolve(str(file_path), 1)
    assert scope.scope_type == "module"
    assert scope.module_level_constant is True


def test_context_resolver_node_type_prefers_innermost_logic_node(tmp_path: Path) -> None:
    source = """def run(x: int) -> bool:
    if x > 0:
        return True
    return False
"""
    file_path = tmp_path / "sample_if.py"
    file_path.write_text(source)

    resolver = ContextResolver()
    scope = resolver.resolve(str(file_path), 2)
    assert scope.scope_type == "function"
    assert scope.node_type in {"If", "Compare"}
