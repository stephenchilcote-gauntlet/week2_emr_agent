from __future__ import annotations

import pytest

from tests.eval.runner import EvalResult, EvalReport, EvalRunner


# ---------------------------------------------------------------------------
# _validate_dataset
# ---------------------------------------------------------------------------


def test_eval_dataset_requires_at_least_one_assertion() -> None:
    with pytest.raises(ValueError, match="has no assertions"):
        EvalRunner._validate_dataset(
            [
                {
                    "id": "case-1",
                    "expected": {},
                }
            ]
        )


def test_validate_dataset_accepts_should_refuse() -> None:
    EvalRunner._validate_dataset([{"id": "c1", "expected": {"should_refuse": True}}])


def test_validate_dataset_accepts_output_contains() -> None:
    EvalRunner._validate_dataset([{"id": "c1", "expected": {"output_contains": ["hello"]}}])


def test_validate_dataset_accepts_manifest_items() -> None:
    EvalRunner._validate_dataset([{"id": "c1", "expected": {"manifest_items": []}}])


def test_validate_dataset_accepts_tool_calls() -> None:
    EvalRunner._validate_dataset([{"id": "c1", "expected": {"tool_calls": ["fhir_read"]}}])


def test_validate_dataset_accepts_manifest_patient_is_uuid() -> None:
    EvalRunner._validate_dataset([{"id": "c1", "expected": {"manifest_patient_is_uuid": True}}])


def test_validate_dataset_accepts_output_not_contains() -> None:
    EvalRunner._validate_dataset([{"id": "c1", "expected": {"output_not_contains": ["bad"]}}])


def test_validate_dataset_empty_list_passes() -> None:
    """Empty dataset has no cases to validate so it should not raise."""
    EvalRunner._validate_dataset([])  # No error expected


def test_validate_dataset_fails_unknown_id() -> None:
    """Error message includes the case ID."""
    with pytest.raises(ValueError, match="my-special-case"):
        EvalRunner._validate_dataset(
            [
                {"id": "my-special-case", "expected": {"unrecognized_key": True}}
            ]
        )


def test_validate_dataset_fails_missing_id() -> None:
    """Case with no id shows <unknown> in error."""
    with pytest.raises(ValueError, match="unknown"):
        EvalRunner._validate_dataset([{"expected": {}}])


def test_validate_dataset_multiple_cases_first_bad_raises() -> None:
    """Validation fails on the first bad case."""
    with pytest.raises(ValueError, match="bad-case"):
        EvalRunner._validate_dataset(
            [
                {"id": "bad-case", "expected": {}},
                {"id": "good-case", "expected": {"should_refuse": True}},
            ]
        )


# ---------------------------------------------------------------------------
# EvalResult model
# ---------------------------------------------------------------------------


def test_eval_result_passed_case() -> None:
    r = EvalResult(
        case_id="hp-01",
        category="happy_path",
        description="Basic greeting",
        passed=True,
        score=1.0,
        checks={"contains_hello": True},
        details={},
        latency_ms=250.5,
    )
    assert r.passed is True
    assert r.score == 1.0
    assert r.error is None


def test_eval_result_failed_case_with_error() -> None:
    r = EvalResult(
        case_id="hp-02",
        category="happy_path",
        description="Timeout case",
        passed=False,
        score=0.0,
        checks={},
        details={},
        latency_ms=30000.0,
        error="Timeout after 30s",
    )
    assert r.passed is False
    assert r.error == "Timeout after 30s"


# ---------------------------------------------------------------------------
# EvalReport model
# ---------------------------------------------------------------------------


def test_eval_report_summary_contains_category_stats() -> None:
    report = EvalReport(
        total=5,
        passed=4,
        failed=1,
        pass_rate=0.8,
        by_category={
            "happy_path": {"passed": 4, "total": 5, "rate": 0.8}
        },
        results=[],
        timestamp="2026-03-03T00:00:00Z",
    )
    summary = report.summary
    assert "happy_path" in summary
    assert "4/5" in summary


def test_eval_report_summary_shows_pass_rate() -> None:
    report = EvalReport(
        total=10,
        passed=9,
        failed=1,
        pass_rate=0.9,
        by_category={},
        results=[],
        timestamp="2026-03-03T00:00:00Z",
    )
    summary = report.summary
    assert "90.0%" in summary


def test_eval_report_zero_total() -> None:
    """Edge case: report with no cases."""
    report = EvalReport(
        total=0,
        passed=0,
        failed=0,
        pass_rate=0.0,
        by_category={},
        results=[],
        timestamp="2026-03-03T00:00:00Z",
    )
    summary = report.summary
    assert "Total: 0" in summary


def test_eval_report_summary_multiple_categories() -> None:
    """Summary lists each category on its own line."""
    report = EvalReport(
        total=10,
        passed=7,
        failed=3,
        pass_rate=0.7,
        by_category={
            "happy_path": {"passed": 4, "total": 5, "rate": 0.8},
            "adversarial": {"passed": 3, "total": 5, "rate": 0.6},
        },
        results=[],
        timestamp="2026-03-03T00:00:00Z",
    )
    summary = report.summary
    assert "happy_path" in summary
    assert "adversarial" in summary
    assert "4/5" in summary
    assert "3/5" in summary


def test_eval_report_summary_zero_percent_rate() -> None:
    """0% pass rate is shown as 0.0% in summary."""
    report = EvalReport(
        total=5,
        passed=0,
        failed=5,
        pass_rate=0.0,
        by_category={"edge_case": {"passed": 0, "total": 5, "rate": 0.0}},
        results=[],
        timestamp="2026-03-03T00:00:00Z",
    )
    assert "0.0%" in report.summary


def test_eval_report_summary_hundred_percent_rate() -> None:
    """100% pass rate is shown as 100.0% in summary."""
    report = EvalReport(
        total=3,
        passed=3,
        failed=0,
        pass_rate=1.0,
        by_category={"dsl": {"passed": 3, "total": 3, "rate": 1.0}},
        results=[],
        timestamp="2026-03-03T00:00:00Z",
    )
    assert "100.0%" in report.summary


def test_eval_report_summary_first_line_has_timestamp() -> None:
    """The first line of the summary includes the timestamp."""
    ts = "2026-03-03T00:00:00Z"
    report = EvalReport(
        total=1, passed=1, failed=0, pass_rate=1.0,
        by_category={}, results=[], timestamp=ts,
    )
    first_line = report.summary.splitlines()[0]
    assert ts in first_line


def test_eval_report_summary_second_line_has_passed_and_failed() -> None:
    """The second line of the summary includes Passed and Failed counts."""
    report = EvalReport(
        total=10,
        passed=7,
        failed=3,
        pass_rate=0.7,
        by_category={},
        results=[],
        timestamp="2026-03-03T00:00:00Z",
    )
    lines = report.summary.splitlines()
    second_line = lines[1]
    assert "Passed: 7" in second_line
    assert "Failed: 3" in second_line
    assert "Total: 10" in second_line


def test_eval_report_summary_second_line_has_rate() -> None:
    """The second line of the summary includes Rate field as a percentage."""
    report = EvalReport(
        total=4,
        passed=3,
        failed=1,
        pass_rate=0.75,
        by_category={},
        results=[],
        timestamp="2026-03-03T00:00:00Z",
    )
    second_line = report.summary.splitlines()[1]
    assert "75.0%" in second_line
    assert "Rate:" in second_line


def test_eval_report_summary_category_line_shows_rate_as_percentage() -> None:
    """Category lines in summary show rate as a percentage string."""
    report = EvalReport(
        total=6,
        passed=4,
        failed=2,
        pass_rate=0.667,
        by_category={"clinical_precision": {"passed": 4, "total": 6, "rate": 0.667}},
        results=[],
        timestamp="2026-03-03T00:00:00Z",
    )
    summary = report.summary
    assert "clinical_precision" in summary
    assert "4/6" in summary
    assert "66.7%" in summary


# ---------------------------------------------------------------------------
# EvalRunner — dataset validation with multiple valid assertion keys
# ---------------------------------------------------------------------------


def test_validate_dataset_accepts_all_known_assertion_keys() -> None:
    """All known assertion key types are accepted individually."""
    for key in ("should_refuse", "output_contains", "output_not_contains",
                "manifest_items", "manifest_patient_is_uuid", "tool_calls"):
        value = True if key in ("should_refuse", "manifest_patient_is_uuid") else []
        EvalRunner._validate_dataset([{"id": f"case-{key}", "expected": {key: value}}])


def test_validate_dataset_multiple_assertions_accepted() -> None:
    """A case with multiple assertion keys is valid."""
    EvalRunner._validate_dataset([{
        "id": "multi",
        "expected": {
            "should_refuse": False,
            "output_contains": ["diagnosis"],
            "tool_calls": ["fhir_read"],
        }
    }])


# ---------------------------------------------------------------------------
# EvalResult — score and error combinations
# ---------------------------------------------------------------------------


def test_eval_result_score_reflects_checks_ratio() -> None:
    """score is computed as passed_checks / total_checks."""
    r = EvalResult(
        case_id="x",
        category="happy_path",
        description="test",
        passed=True,
        score=0.75,
        checks={"a": True, "b": True, "c": True, "d": False},
        details={},
        latency_ms=100.0,
    )
    assert r.score == 0.75


def test_eval_result_error_field_optional() -> None:
    """EvalResult.error defaults to None when not provided."""
    r = EvalResult(
        case_id="y",
        category="edge_case",
        description="no error",
        passed=True,
        score=1.0,
        checks={"ok": True},
        details={},
        latency_ms=50.0,
    )
    assert r.error is None
