from __future__ import annotations

from pathlib import Path

import pytest

from src.eval.mutant_analysis import (
    MANUAL,
    MUST_FIX,
    NOISE,
    ContextResolver,
    MutantAnalyzer,
    MutantRecord,
    ScopeInfo,
    _is_literal_assignment,
    _is_message_only_mutation,
    _looks_like_config_line,
    _looks_like_logic_line,
    build_parser,
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


# ---------------------------------------------------------------------------
# classify_mutant — module-level config line → NOISE
# ---------------------------------------------------------------------------


def _make_module_config_record(source_line: str) -> MutantRecord:
    return MutantRecord(
        mutant_id=42,
        status="survived",
        filename="src/agent/loop.py",
        line_number=5,
        source_line=source_line,
        mutation_index=0,
    )


def test_classify_mutant_module_config_line_noise() -> None:
    """Module-level config-like line is classified as NOISE."""
    record = _make_module_config_record("MAX_TIMEOUT = 30")
    scope = ScopeInfo(
        scope_type="module",
        scope_name="<module>",
        node_type="Assign",
        module_level_constant=False,  # not ALL_CAPS constant check
    )
    classification, reasons = classify_mutant(
        record, scope,
        removed_line="MAX_TIMEOUT = 30",
        added_line="MAX_TIMEOUT = 29",
    )
    assert classification == NOISE
    assert any("config" in r for r in reasons)


# ---------------------------------------------------------------------------
# classify_mutant — function-level logic line → MUST_FIX
# ---------------------------------------------------------------------------


def test_classify_mutant_function_logic_line_must_fix() -> None:
    """Function-level line with logic tokens is classified MUST_FIX."""
    record = MutantRecord(
        mutant_id=10,
        status="survived",
        filename="src/verification/checks.py",
        line_number=50,
        source_line="return result if passed else None",
        mutation_index=0,
    )
    scope = ScopeInfo(
        scope_type="function",
        scope_name="check_grounding",
        node_type="Assign",   # Not in the MUST_FIX node_type set
        module_level_constant=False,
    )
    classification, reasons = classify_mutant(
        record, scope,
        removed_line="return result if passed else None",
        added_line="return result if not passed else None",
    )
    assert classification == MUST_FIX
    assert any("logic" in r or "control" in r for r in reasons)


# ---------------------------------------------------------------------------
# classify_mutant — fallthrough → MANUAL
# ---------------------------------------------------------------------------


def test_classify_mutant_fallthrough_returns_manual() -> None:
    """Mutant that doesn't match any rule falls through to MANUAL."""
    record = MutantRecord(
        mutant_id=99,
        status="survived",
        filename="src/agent/labels.py",
        line_number=5,
        source_line='x = "hello"',  # lowercase, not a constant
        mutation_index=0,
    )
    scope = ScopeInfo(
        scope_type="function",
        scope_name="some_func",
        node_type="Assign",  # Not in MUST_FIX node_type set
        module_level_constant=False,
    )
    classification, reasons = classify_mutant(
        record, scope,
        removed_line='x = "hello"',
        added_line='x = "world"',
    )
    assert classification == MANUAL
    assert any("manually" in r.lower() or "review" in r.lower() for r in reasons)


# ---------------------------------------------------------------------------
# MutantAnalyzer._load_records — FileNotFoundError when cache missing
# ---------------------------------------------------------------------------


def test_mutant_analyzer_load_records_raises_when_cache_missing(tmp_path: Path) -> None:
    """_load_records raises FileNotFoundError when the cache DB doesn't exist."""
    analyzer = MutantAnalyzer(cache_path=str(tmp_path / "nonexistent.db"))
    with pytest.raises(FileNotFoundError, match="not found"):
        analyzer._load_records(["survived"])


# ---------------------------------------------------------------------------
# build_parser — returns parser with expected defaults
# ---------------------------------------------------------------------------


def test_build_parser_returns_argparse_parser() -> None:
    """build_parser returns a parser that can parse known arguments."""
    parser = build_parser()
    args = parser.parse_args([])  # use defaults
    assert args.cache == ".mutmut-cache"
    assert "survived" in args.status


def test_build_parser_accepts_cache_and_status() -> None:
    """build_parser correctly handles --cache and --status arguments."""
    parser = build_parser()
    args = parser.parse_args(["--cache", "/tmp/cache.db", "--status", "killed"])
    assert args.cache == "/tmp/cache.db"
    assert "killed" in args.status


# ---------------------------------------------------------------------------
# MutantAnalyzer constructor defaults
# ---------------------------------------------------------------------------


def test_mutant_analyzer_default_cmd() -> None:
    """MutantAnalyzer uses uv run mutmut as the default command."""
    analyzer = MutantAnalyzer()
    assert "mutmut" in " ".join(analyzer.mutmut_cmd)
    assert "uv" in analyzer.mutmut_cmd[0]


def test_mutant_analyzer_custom_cmd() -> None:
    """MutantAnalyzer accepts a custom mutmut_cmd."""
    analyzer = MutantAnalyzer(mutmut_cmd=["python", "-m", "mutmut"])
    assert analyzer.mutmut_cmd == ["python", "-m", "mutmut"]


# ---------------------------------------------------------------------------
# ContextResolver — exception / out-of-range / class scope / AnnAssign
# ---------------------------------------------------------------------------


def test_context_resolver_nonexistent_file_returns_unknown_scope() -> None:
    """resolve() on a missing file returns ScopeInfo with scope_type='unknown'."""
    resolver = ContextResolver()
    scope = resolver.resolve("/nonexistent/path/that/does/not/exist.py", 1)
    assert scope.scope_type == "unknown"
    assert scope.scope_name == "unknown"
    assert scope.module_level_constant is False


def test_context_resolver_line_out_of_range_returns_module_scope(tmp_path: Path) -> None:
    """When line_number exceeds all nodes, _scope_path returns [] → module ScopeInfo."""
    source = "x = 1\n"
    file_path = tmp_path / "tiny.py"
    file_path.write_text(source)

    resolver = ContextResolver()
    scope = resolver.resolve(str(file_path), 9999)
    assert scope.scope_type == "module"
    assert scope.scope_name == "<module>"


def test_context_resolver_class_scope_detected(tmp_path: Path) -> None:
    """Line inside a class body (but not a function) gets scope_type='class'."""
    source = "class MyClass:\n    VALUE = 42\n"
    file_path = tmp_path / "cls.py"
    file_path.write_text(source)

    resolver = ContextResolver()
    scope = resolver.resolve(str(file_path), 2)
    assert scope.scope_type == "class"
    assert scope.scope_name == "MyClass"


def test_context_resolver_annotated_constant_detected(tmp_path: Path) -> None:
    """AnnAssign with an isupper() name at module level is a module-level constant."""
    source = "CONST: int = 5\n\ndef run() -> None:\n    pass\n"
    file_path = tmp_path / "annassign.py"
    file_path.write_text(source)

    resolver = ContextResolver()
    scope = resolver.resolve(str(file_path), 1)
    assert scope.scope_type == "module"
    assert scope.module_level_constant is True


def test_context_resolver_tree_cache_reuses_parsed_tree(tmp_path: Path) -> None:
    """Second resolve() call for the same file returns cached tree (no re-read)."""
    source = "X = 1\n"
    file_path = tmp_path / "cached.py"
    file_path.write_text(source)

    resolver = ContextResolver()
    resolver.resolve(str(file_path), 1)
    assert str(file_path) in resolver._tree_cache

    # Overwrite file — cache should prevent re-read so result is stable
    file_path.write_text("import os\n")
    scope = resolver.resolve(str(file_path), 1)
    # Still uses cached tree (X=1 line), so module_level_constant is True
    assert scope.module_level_constant is True


def test_context_resolver_scope_path_bypasses_cache_when_missing(tmp_path: Path) -> None:
    """_scope_path falls back to walking tree when node_cache entry is absent."""
    source = "Y = 2\n"
    file_path = tmp_path / "nocache.py"
    file_path.write_text(source)

    resolver = ContextResolver()
    import ast as _ast
    tree = _ast.parse(source)
    # _node_cache intentionally not populated
    path = resolver._scope_path(tree, str(file_path), 1)
    # Should still return non-empty list (Assign node covers line 1)
    assert len(path) > 0


# ---------------------------------------------------------------------------
# classify_mutant — module_level_constant=True but non-literal → MANUAL
# ---------------------------------------------------------------------------


def test_classify_mutant_module_constant_non_literal_falls_to_manual() -> None:
    """module_level_constant=True but dict RHS is not a literal → MANUAL (no early NOISE return)."""
    record = MutantRecord(
        mutant_id=50,
        status="survived",
        filename="src/agent/config.py",
        line_number=3,
        source_line="DATA = {}",
        mutation_index=0,
    )
    scope = ScopeInfo(
        scope_type="module",
        scope_name="<module>",
        node_type="Assign",
        module_level_constant=True,
    )
    classification, reasons = classify_mutant(
        record, scope,
        removed_line="DATA = {}",
        added_line="DATA = {'x': 1}",
    )
    # _is_literal_assignment("DATA = {}") is False → no early NOISE return
    # Falls through to MANUAL (none of the later checks match either)
    assert classification == MANUAL
    assert any("constant" in r for r in reasons)


# ---------------------------------------------------------------------------
# classify_mutant — _is_message_only_mutation path → NOISE
# ---------------------------------------------------------------------------


def test_classify_mutant_message_only_mutation_returns_noise() -> None:
    """Logger message-string-only change is classified as NOISE."""
    record = MutantRecord(
        mutant_id=51,
        status="survived",
        filename="src/agent/loop.py",
        line_number=100,
        source_line="logger.info('Operation started')",
        mutation_index=0,
    )
    scope = ScopeInfo(
        scope_type="function",
        scope_name="run",
        node_type="Expr",
        module_level_constant=False,
    )
    classification, reasons = classify_mutant(
        record, scope,
        removed_line="logger.info('Operation started')",
        added_line="logger.info('Operation completed')",
    )
    assert classification == NOISE
    assert any("message" in r or "log" in r for r in reasons)


# ---------------------------------------------------------------------------
# classify_mutant — node_type in MUST_FIX set → MUST_FIX (non-If variant)
# ---------------------------------------------------------------------------


def test_classify_mutant_node_type_compare_returns_must_fix() -> None:
    """node_type='Compare' triggers the MUST_FIX set check."""
    record = MutantRecord(
        mutant_id=52,
        status="survived",
        filename="src/verification/checks.py",
        line_number=80,
        source_line="count == 0",
        mutation_index=0,
    )
    scope = ScopeInfo(
        scope_type="function",
        scope_name="validate",
        node_type="Compare",
        module_level_constant=False,
    )
    classification, _reasons = classify_mutant(
        record, scope,
        removed_line="count == 0",
        added_line="count != 0",
    )
    assert classification == MUST_FIX


# ---------------------------------------------------------------------------
# MutantAnalyzer._show_mutation — non-zero exit raises RuntimeError
# ---------------------------------------------------------------------------


def test_mutant_analyzer_show_mutation_nonzero_exit_raises() -> None:
    """_show_mutation raises RuntimeError when mutmut show exits non-zero."""
    from unittest.mock import patch, MagicMock

    analyzer = MutantAnalyzer(mutmut_cmd=["false"])
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stderr = "mutmut error: not found"

    with patch("subprocess.run", return_value=mock_proc):
        with pytest.raises(RuntimeError, match="mutmut show"):
            analyzer._show_mutation(42)


# ---------------------------------------------------------------------------
# build_parser — --output and --mutmut-cmd arguments
# ---------------------------------------------------------------------------


def test_build_parser_output_default() -> None:
    """build_parser --output defaults to 'mutant-analysis.json'."""
    parser = build_parser()
    args = parser.parse_args([])
    assert args.output == "mutant-analysis.json"


def test_build_parser_accepts_output_path() -> None:
    """build_parser accepts --output /tmp/report.json."""
    parser = build_parser()
    args = parser.parse_args(["--output", "/tmp/report.json"])
    assert args.output == "/tmp/report.json"


def test_build_parser_accepts_mutmut_cmd() -> None:
    """build_parser accepts --mutmut-cmd argument."""
    parser = build_parser()
    args = parser.parse_args(["--mutmut-cmd", "python -m mutmut"])
    assert "mutmut" in args.mutmut_cmd
