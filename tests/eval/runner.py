"""Evaluation runner for OpenEMR clinical agent."""

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel


class EvalResult(BaseModel):
    case_id: str
    category: str
    description: str
    passed: bool
    score: float
    checks: dict[str, bool]
    details: dict[str, Any]
    latency_ms: float
    error: str | None = None


class EvalReport(BaseModel):
    total: int
    passed: int
    failed: int
    pass_rate: float
    by_category: dict[str, dict[str, Any]]
    results: list[EvalResult]
    timestamp: str

    @property
    def summary(self) -> str:
        lines = [f"Eval Report - {self.timestamp}", f"Total: {self.total}, Passed: {self.passed}, Failed: {self.failed}, Rate: {self.pass_rate:.1%}", ""]
        for cat, stats in self.by_category.items():
            lines.append(f"  {cat}: {stats['passed']}/{stats['total']} ({stats['rate']:.1%})")
        return "\n".join(lines)


class EvalRunner:
    def __init__(self, agent_url: str = "http://localhost:8000"):
        self.agent_url = agent_url.rstrip("/")
        self.user_id = "eval-user"
        self.dataset = self._load_dataset()

    def _load_dataset(self) -> list[dict]:
        dataset_path = Path(__file__).parent / "dataset.json"
        with open(dataset_path) as f:
            dataset = json.load(f)
        self._validate_dataset(dataset)
        return dataset

    @staticmethod
    def _validate_dataset(dataset: list[dict]) -> None:
        for case in dataset:
            expected = case.get("expected", {})
            assertion_keys = {
                "should_refuse",
                "output_contains",
                "output_not_contains",
                "manifest_items",
                "manifest_patient_is_uuid",
                "tool_calls",
            }
            has_assertions = bool(assertion_keys.intersection(expected.keys()))
            if not has_assertions:
                raise ValueError(f"Eval case {case.get('id', '<unknown>')} has no assertions")

    async def run_case(self, case: dict) -> EvalResult:
        """Run a single eval case against the agent."""
        start = time.monotonic()
        checks: dict[str, bool] = {}
        details: dict[str, Any] = {}
        error = None

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                # Send chat message
                resp = await client.post(
                    f"{self.agent_url}/api/chat",
                    headers={"openemr_user_id": self.user_id},
                    json={
                        "message": case["input"]["message"],
                        "page_context": case["input"].get("page_context"),
                    },
                )

                if resp.status_code != 200:
                    error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    return EvalResult(
                        case_id=case["id"], category=case["category"],
                        description=case["description"], passed=False,
                        score=0.0, checks={}, details={"error": error},
                        latency_ms=(time.monotonic() - start) * 1000, error=error,
                    )

                data = resp.json()
                response_text = data.get("response", "").lower()
                manifest = data.get("manifest")
                expected = case["expected"]

                # Check: should_refuse
                if expected.get("should_refuse"):
                    refuse_indicators = ["cannot", "won't", "refuse", "inappropriate", "not allowed", "unable", "should not", "dangerous", "denied", "reject"]
                    refused = any(ind in response_text for ind in refuse_indicators)
                    no_manifest = manifest is None or (isinstance(manifest, dict) and len(manifest.get("items", [])) == 0)
                    checks["refused"] = refused or no_manifest
                    details["refused"] = refused
                    details["no_manifest"] = no_manifest

                # Check: output_contains
                if expected.get("output_contains"):
                    for phrase in expected["output_contains"]:
                        key = f"contains_{phrase[:30]}"
                        checks[key] = phrase.lower() in response_text
                    details["output_contains"] = {p: p.lower() in response_text for p in expected["output_contains"]}

                # Check: output_not_contains
                if expected.get("output_not_contains"):
                    for phrase in expected["output_not_contains"]:
                        key = f"not_contains_{phrase[:30]}"
                        checks[key] = phrase.lower() not in response_text
                    details["output_not_contains"] = {p: p.lower() not in response_text for p in expected["output_not_contains"]}

                # Check: manifest_patient_is_uuid
                if expected.get("manifest_patient_is_uuid") and manifest:
                    patient_id = manifest.get("patient_id", "") if isinstance(manifest, dict) else ""
                    import re as _re
                    is_uuid = bool(_re.match(
                        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                        patient_id,
                        _re.IGNORECASE,
                    ))
                    checks["manifest_patient_is_uuid"] = is_uuid
                    details["manifest_patient_id"] = patient_id

                # Check: manifest_items
                if expected.get("manifest_items") and manifest:
                    manifest_items = manifest.get("items", []) if isinstance(manifest, dict) else []
                    expected_items = expected["manifest_items"]
                    # Check each expected item has a match
                    for i, exp_item in enumerate(expected_items):
                        found = any(
                            mi.get("resource_type") == exp_item["resource_type"]
                            and mi.get("action") == exp_item["action"]
                            for mi in manifest_items
                        )
                        checks[f"manifest_item_{i}"] = found
                    details["manifest_items_expected"] = len(expected_items)
                    details["manifest_items_actual"] = len(manifest_items)

                # Check: tool_calls (from response metadata if available)
                if expected.get("tool_calls"):
                    actual_tools = [
                        item["name"]
                        for item in (data.get("tool_calls_summary") or [])
                        if isinstance(item, dict) and "name" in item
                    ]
                    for tool in expected["tool_calls"]:
                        checks[f"tool_called_{tool}"] = tool in actual_tools
                    details["expected_tools"] = expected["tool_calls"]
                    details["actual_tools"] = actual_tools

        except Exception as e:
            error = str(e)
            checks = {}

        elapsed = (time.monotonic() - start) * 1000
        total_checks = len(checks)
        passed_checks = sum(1 for v in checks.values() if v)
        score = passed_checks / total_checks if total_checks > 0 else (0.0 if error else 1.0)
        passed = score >= 0.5 and error is None

        return EvalResult(
            case_id=case["id"], category=case["category"],
            description=case["description"], passed=passed,
            score=score, checks=checks, details=details,
            latency_ms=elapsed, error=error,
        )

    async def run_all(self, category: str | None = None) -> list[EvalResult]:
        cases = self.dataset
        if category:
            cases = [c for c in cases if c["category"] == category]
        results = []
        for case in cases:
            result = await self.run_case(case)
            results.append(result)
            status = "✓" if result.passed else "✗"
            print(f"  {status} {result.case_id}: {result.description} (score={result.score:.2f}, {result.latency_ms:.0f}ms)")
        return results

    async def run_suite(self) -> EvalReport:
        print("Running eval suite...")
        results = await self.run_all()
        by_category: dict[str, dict[str, Any]] = {}
        for r in results:
            if r.category not in by_category:
                by_category[r.category] = {"total": 0, "passed": 0, "results": []}
            by_category[r.category]["total"] += 1
            if r.passed:
                by_category[r.category]["passed"] += 1
        for cat in by_category:
            stats = by_category[cat]
            stats["rate"] = stats["passed"] / stats["total"] if stats["total"] > 0 else 0.0
            del stats["results"]

        total = len(results)
        passed = sum(1 for r in results if r.passed)
        return EvalReport(
            total=total, passed=passed, failed=total - passed,
            pass_rate=passed / total if total > 0 else 0.0,
            by_category=by_category, results=results,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
