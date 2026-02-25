"""Verification checks that run against the change manifest."""

from __future__ import annotations

import json
import re
from typing import Any

from opentelemetry import trace
from pydantic import BaseModel, Field

from ..agent.models import ChangeManifest, ManifestItem
from ..observability.tracing import trace_verification
from .icd10 import validate_cpt_format, validate_icd10_format

HEDGING_PHRASES = [
    "possibly",
    "might be",
    "unclear",
    "uncertain",
    "maybe",
    "could be",
    "not sure",
]


class VerificationResult(BaseModel):
    """Result of a single verification check on a manifest item."""

    item_id: str
    check_name: str
    passed: bool
    message: str
    severity: str = "error"


class VerificationReport(BaseModel):
    """Aggregated verification results for an entire manifest."""

    manifest_id: str
    results: list[VerificationResult] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True if no results have severity 'error' and failed."""
        return all(
            r.passed for r in self.results if r.severity == "error"
        )

    @property
    def warnings(self) -> list[VerificationResult]:
        """All results with severity 'warning'."""
        return [r for r in self.results if r.severity == "warning"]


async def check_grounding(
    item: ManifestItem, openemr_client: Any
) -> VerificationResult:
    """Verify that the cited source_reference actually exists in the EMR.

    Parses the source_reference as ``ResourceType/ID`` and attempts to
    fetch it via the FHIR client.
    """
    if not item.source_reference:
        return VerificationResult(
            item_id=item.id,
            check_name="grounding",
            passed=False,
            message="No source_reference provided",
        )

    match = re.match(r"^(\w+)/(.+)$", item.source_reference)
    if not match:
        return VerificationResult(
            item_id=item.id,
            check_name="grounding",
            passed=False,
            message=f"Invalid source_reference format: {item.source_reference}",
        )

    resource_type, resource_id = match.group(1), match.group(2)

    try:
        result = await openemr_client.fhir_read(resource_type, {"_id": resource_id})
        if not isinstance(result, dict):
            return VerificationResult(
                item_id=item.id,
                check_name="grounding",
                passed=False,
                message=(
                    f"Source resource lookup for {item.source_reference} returned malformed data"
                ),
            )
        if "error" in result or result.get("total", 0) == 0:
            return VerificationResult(
                item_id=item.id,
                check_name="grounding",
                passed=False,
                message=f"Source resource {item.source_reference} not found",
            )
        return VerificationResult(
            item_id=item.id,
            check_name="grounding",
            passed=True,
            message=f"Source resource {item.source_reference} verified",
        )
    except Exception as exc:
        return VerificationResult(
            item_id=item.id,
            check_name="grounding",
            passed=False,
            message=f"Failed to fetch source resource: {exc}",
        )


def check_constraints(item: ManifestItem) -> list[VerificationResult]:
    """Validate domain-specific constraints on the proposed value."""
    results: list[VerificationResult] = []
    proposed = item.proposed_value

    if item.resource_type == "Condition" and "code" in proposed:
        code = _extract_code(proposed["code"])
        if code and not validate_icd10_format(code):
            results.append(
                VerificationResult(
                    item_id=item.id,
                    check_name="constraint_icd10",
                    passed=False,
                    message=f"Invalid ICD-10 format: {code}",
                )
            )
        elif code:
            results.append(
                VerificationResult(
                    item_id=item.id,
                    check_name="constraint_icd10",
                    passed=True,
                    message=f"Valid ICD-10 format: {code}",
                )
            )

    if item.resource_type == "Procedure" and "code" in proposed:
        code = _extract_code(proposed["code"])
        if code and not validate_cpt_format(code):
            results.append(
                VerificationResult(
                    item_id=item.id,
                    check_name="constraint_cpt",
                    passed=False,
                    message=f"Invalid CPT format: {code}",
                )
            )
        elif code:
            results.append(
                VerificationResult(
                    item_id=item.id,
                    check_name="constraint_cpt",
                    passed=True,
                    message=f"Valid CPT format: {code}",
                )
            )

    if "document" in proposed or "text" in proposed:
        doc = proposed.get("document") or proposed.get("text", "")
        if isinstance(doc, str):
            required_sections = ["subjective", "objective", "assessment", "plan"]
            doc_lower = doc.lower()
            missing = [s for s in required_sections if s not in doc_lower]
            if missing:
                results.append(
                    VerificationResult(
                        item_id=item.id,
                        check_name="constraint_document_sections",
                        passed=False,
                        message=f"Clinical document missing sections: {', '.join(missing)}",
                        severity="warning",
                    )
                )
            else:
                results.append(
                    VerificationResult(
                        item_id=item.id,
                        check_name="constraint_document_sections",
                        passed=True,
                        message="Clinical document contains all required sections",
                        severity="info",
                    )
                )

    return results


def check_confidence(item: ManifestItem) -> VerificationResult:
    """Flag items whose description or proposed value contains hedging language."""
    text_to_check = item.description.lower()
    text_to_check += " " + json.dumps(item.proposed_value).lower()

    found = [phrase for phrase in HEDGING_PHRASES if phrase in text_to_check]

    if found:
        return VerificationResult(
            item_id=item.id,
            check_name="confidence",
            passed=False,
            message=f"Low confidence — hedging language detected: {', '.join(found)}",
            severity="warning",
        )

    return VerificationResult(
        item_id=item.id,
        check_name="confidence",
        passed=True,
        message="No hedging language detected",
        severity="warning",
    )


async def check_conflict(
    item: ManifestItem, openemr_client: Any
) -> VerificationResult:
    """Re-read the target resource and flag if it differs from current_value."""
    if not item.target_resource_id or item.current_value is None:
        return VerificationResult(
            item_id=item.id,
            check_name="conflict",
            passed=True,
            message="No conflict check needed (no target or current_value)",
            severity="info",
        )

    try:
        live = await openemr_client.fhir_read(item.resource_type, {"_id": item.target_resource_id})
        if "error" in live or live.get("total", 0) == 0:
            return VerificationResult(
                item_id=item.id,
                check_name="conflict",
                passed=False,
                message=f"Target resource {item.resource_type}/{item.target_resource_id} no longer exists",
            )

        live_data = live.get("entry", [{}])[0].get("resource", {}) if live.get("entry") else {}
        current_value = item.current_value or {}

        # Prefer optimistic-locking style conflict checks when version IDs are available.
        live_version = ((live_data.get("meta") or {}).get("versionId"))
        current_version = ((current_value.get("meta") or {}).get("versionId"))
        if live_version and current_version and live_version != current_version:
            return VerificationResult(
                item_id=item.id,
                check_name="conflict",
                passed=False,
                message=(
                    f"Conflict detected: {item.resource_type}/{item.target_resource_id} "
                    "version has changed since the manifest was built"
                ),
            )

        if _normalize_for_conflict(live_data) != _normalize_for_conflict(current_value):
            return VerificationResult(
                item_id=item.id,
                check_name="conflict",
                passed=False,
                message=(
                    f"Conflict detected: {item.resource_type}/{item.target_resource_id} "
                    "has been modified since the manifest was built"
                ),
            )

        return VerificationResult(
            item_id=item.id,
            check_name="conflict",
            passed=True,
            message="No conflict — target resource unchanged",
        )
    except Exception as exc:
        return VerificationResult(
            item_id=item.id,
            check_name="conflict",
            passed=False,
            message=f"Failed to re-read target resource for conflict check: {exc}",
        )


async def verify_manifest(
    manifest: ChangeManifest, openemr_client: Any
) -> VerificationReport:
    """Run all verification checks against every item in the manifest."""
    report = VerificationReport(manifest_id=manifest.id)

    for item in manifest.items:
        report.results.append(await check_grounding(item, openemr_client))
        report.results.extend(check_constraints(item))
        report.results.append(check_confidence(item))
        report.results.append(await check_conflict(item, openemr_client))

    return report


verify_manifest = trace_verification(trace.get_tracer("openemr-agent"))(verify_manifest)


def _extract_code(code_value: Any) -> str | None:
    """Extract a code string from a FHIR CodeableConcept or plain string."""
    if isinstance(code_value, str):
        return code_value
    if isinstance(code_value, dict):
        if "coding" in code_value and isinstance(code_value["coding"], list):
            for coding in code_value["coding"]:
                if isinstance(coding, dict) and isinstance(coding.get("code"), str):
                    return coding["code"]
        if isinstance(code_value.get("code"), str):
            return code_value["code"]
    return None


def _normalize_for_conflict(resource: dict[str, Any]) -> dict[str, Any]:
    """Ignore server-managed metadata fields when checking for conflicts."""
    normalized = dict(resource)
    meta = normalized.get("meta")
    if isinstance(meta, dict):
        meta_copy = dict(meta)
        meta_copy.pop("lastUpdated", None)
        meta_copy.pop("versionId", None)
        if meta_copy:
            normalized["meta"] = meta_copy
        else:
            normalized.pop("meta", None)
    return normalized
