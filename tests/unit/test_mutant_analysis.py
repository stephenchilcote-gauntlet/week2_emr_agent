from __future__ import annotations

from pathlib import Path

from src.eval.mutant_analysis import (
    MUST_FIX,
    NOISE,
    ContextResolver,
    MutantRecord,
    ScopeInfo,
    _is_literal_assignment,
    _is_message_only_mutation,
    _looks_like_config_line,
    _looks_like_logic_line,
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


# ---------------------------------------------------------------------------
# _is_literal_assignment
# ---------------------------------------------------------------------------


def test_is_literal_assignment_integer() -> None:
    assert _is_literal_assignment("MAX_TOOL_ROUNDS = 15") is True


def test_is_literal_assignment_float() -> None:
    assert _is_literal_assignment("THRESHOLD = 0.95") is True


def test_is_literal_assignment_string() -> None:
    assert _is_literal_assignment("LABEL = 'hello'") is True


def test_is_literal_assignment_double_quoted_string() -> None:
    assert _is_literal_assignment('NAME = "world"') is True


def test_is_literal_assignment_boolean() -> None:
    assert _is_literal_assignment("ENABLED = True") is True
    assert _is_literal_assignment("DEBUG = False") is True


def test_is_literal_assignment_none() -> None:
    assert _is_literal_assignment("VALUE = None") is True


def test_is_literal_assignment_dict_rhs_not_literal() -> None:
    assert _is_literal_assignment("DATA = {}") is False


def test_is_literal_assignment_list_rhs_not_literal() -> None:
    assert _is_literal_assignment("ITEMS = []") is False


def test_is_literal_assignment_fstring_not_literal() -> None:
    assert _is_literal_assignment('LABEL = f"hello {name}"') is False


def test_is_literal_assignment_lowercase_not_matched() -> None:
    """Only all-caps names (module constants) are considered."""
    assert _is_literal_assignment("max_retries = 5") is False


# ---------------------------------------------------------------------------
# _looks_like_config_line
# ---------------------------------------------------------------------------


def test_looks_like_config_line_timeout() -> None:
    assert _looks_like_config_line("TIMEOUT = 30") is True


def test_looks_like_config_line_model() -> None:
    assert _looks_like_config_line("model = 'claude-sonnet-4-6'") is True


def test_looks_like_config_line_max_prefix() -> None:
    assert _looks_like_config_line("max_retries = 3") is True


def test_looks_like_config_line_url() -> None:
    assert _looks_like_config_line("base_url = 'http://localhost'") is True


def test_looks_like_config_line_no_equals_sign() -> None:
    assert _looks_like_config_line("timeout is 30") is False


def test_looks_like_config_line_unrelated_line() -> None:
    assert _looks_like_config_line("x = 5") is False


# ---------------------------------------------------------------------------
# _looks_like_logic_line
# ---------------------------------------------------------------------------


def test_looks_like_logic_line_if_statement() -> None:
    assert _looks_like_logic_line("if x > 0:") is True


def test_looks_like_logic_line_return() -> None:
    assert _looks_like_logic_line("return result") is True


def test_looks_like_logic_line_and_operator() -> None:
    assert _looks_like_logic_line("x and y") is True


def test_looks_like_logic_line_or_operator() -> None:
    assert _looks_like_logic_line("x or y") is True


def test_looks_like_logic_line_comparison() -> None:
    assert _looks_like_logic_line("count == 0") is True
    assert _looks_like_logic_line("count != 0") is True
    assert _looks_like_logic_line("count <= 10") is True


def test_looks_like_logic_line_plain_assignment() -> None:
    assert _looks_like_logic_line("x = 5") is False


def test_looks_like_logic_line_import_statement() -> None:
    assert _looks_like_logic_line("import json") is False


# ---------------------------------------------------------------------------
# _is_message_only_mutation
# ---------------------------------------------------------------------------


def test_is_message_only_mutation_logger_call() -> None:
    """Changes to log message string only are message-only mutations."""
    original = "logger.info('Operation started')"
    mutated = "logger.info('Operation completed')"
    assert _is_message_only_mutation(original, mutated) is True


def test_is_message_only_mutation_print_call() -> None:
    original = "print('error occurred')"
    mutated = "print('success occurred')"
    assert _is_message_only_mutation(original, mutated) is True


def test_is_message_only_mutation_structural_change() -> None:
    """Mutations that change non-string parts are NOT message-only."""
    original = "if logger.isEnabledFor(10):"
    mutated = "if logger.isEnabledFor(20):"
    assert _is_message_only_mutation(original, mutated) is False


def test_is_message_only_mutation_empty_strings() -> None:
    assert _is_message_only_mutation("", "something") is False
    assert _is_message_only_mutation("something", "") is False


def test_is_message_only_mutation_non_logger_line() -> None:
    original = "x = calculate(y)"
    mutated = "x = calculate(z)"
    assert _is_message_only_mutation(original, mutated) is False


def test_is_message_only_mutation_detail_keyword() -> None:
    original = 'raise HTTPException(status_code=404, detail="Not found")'
    mutated = 'raise HTTPException(status_code=404, detail="Missing resource")'
    assert _is_message_only_mutation(original, mutated) is True


# ---------------------------------------------------------------------------
# parse_mutmut_show_output edge cases
# ---------------------------------------------------------------------------


def test_parse_mutmut_show_output_returns_none_on_empty_output() -> None:
    """Empty input returns (None, None)."""
    removed, added = parse_mutmut_show_output("")
    assert removed is None
    assert added is None


def test_parse_mutmut_show_output_no_diff_lines() -> None:
    """Output without +/- lines returns (None, None)."""
    output = "--- a/src/foo.py\n+++ b/src/foo.py\n@@ -1 +1 @@\n"
    removed, added = parse_mutmut_show_output(output)
    assert removed is None
    assert added is None
