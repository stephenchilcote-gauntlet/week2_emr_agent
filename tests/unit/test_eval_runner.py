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
