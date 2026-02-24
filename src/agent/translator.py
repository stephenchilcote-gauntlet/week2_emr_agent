"""Translate parsed DSL items into OpenEMR REST API payloads.

The translator converts flat DSL attributes into OpenEMR REST API JSON
for all supported resource types.  This module is the single source of
truth for the mapping between the agent's compact DSL and the verbose
API payloads.
"""

from __future__ import annotations

from typing import Any

from .dsl import DslItem


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def to_openemr_rest(item: DslItem, patient_uuid: str) -> dict[str, Any]:
    """Convert a DslItem to an OpenEMR REST API payload."""
    resource_type = item.resource_type
    builder = _REST_BUILDERS.get(resource_type)
    if builder:
        return builder(item, patient_uuid)
    raise ValueError(
        f"No OpenEMR REST builder for {resource_type}. "
        "Supported types: " + ", ".join(sorted(_REST_BUILDERS))
    )


_REST_PATH_MAP = {
    "Condition": "medical_problem",
    "MedicationRequest": "medication",
    "AllergyIntolerance": "allergy",
    "Encounter": "encounter",
}


def can_rest_write(resource_type: str) -> bool:
    """Return True if the given resource type has a REST write path."""
    return resource_type in _REST_PATH_MAP


def get_rest_endpoint(item: DslItem, patient_uuid: str) -> str:
    """Return the OpenEMR REST API endpoint path for this item."""
    rest_path = _REST_PATH_MAP.get(item.resource_type)
    if rest_path is None:
        raise ValueError(f"No REST endpoint defined for {item.resource_type}")
    return f"patient/{patient_uuid}/{rest_path}"


# ---------------------------------------------------------------------------
# Flat DSL attrs → proposed_value dict (for storage in ManifestItem)
# ---------------------------------------------------------------------------

def dsl_item_to_proposed_value(item: DslItem) -> dict[str, Any]:
    """Convert a DslItem to a flat proposed_value dict for ManifestItem storage.

    This is the compact representation stored in the manifest. The full
    REST expansion happens at execution time via to_openemr_rest().
    """
    result: dict[str, Any] = {}

    if item.action in ("edit", "remove") and item.ref:
        result["ref"] = item.ref

    if item.action == "add":
        result["type"] = item.resource_type

    # Copy all DSL attributes
    result.update(item.attrs)

    return result


# ---------------------------------------------------------------------------
# OpenEMR REST builders
# ---------------------------------------------------------------------------

def _build_condition_rest(item: DslItem, patient_uuid: str) -> dict[str, Any]:
    """Build an OpenEMR REST medical_problem payload from DSL attributes.

    Maps to: POST /apis/default/api/patient/{uuid}/medical_problem
    """
    attrs = item.attrs
    return {
        "title": attrs.get("display", ""),
        "diagnosis": f"ICD10:{attrs.get('code', '')}",
        "begdate": attrs.get("onset", ""),
        "enddate": attrs.get("enddate", ""),
        "occurrence": attrs.get("occurrence", ""),
        "outcome": "",
        "comments": item.description,
    }


def _build_medication_rest(item: DslItem, patient_uuid: str) -> dict[str, Any]:
    """Build an OpenEMR REST medication payload from DSL attributes.

    Maps to: POST/PUT /apis/default/api/patient/{uuid}/medication
    Schema: title (required), begdate (required), enddate, diagnosis
    """
    attrs = item.attrs
    title = attrs.get("drug", "") or attrs.get("display", "") or attrs.get("title", "")
    dose = attrs.get("dose", "")
    route = attrs.get("route", "")
    if dose:
        title = f"{title} {dose}".strip()
    if route:
        title = f"{title} {route}".strip()

    result: dict[str, Any] = {
        "title": title,
        "begdate": attrs.get("begdate", ""),
        "enddate": attrs.get("enddate", ""),
        "comments": item.description,
    }

    if attrs.get("status") == "stopped":
        from datetime import date
        result["enddate"] = result["enddate"] or date.today().isoformat()

    return result


def _build_allergy_rest(item: DslItem, patient_uuid: str) -> dict[str, Any]:
    """Build an OpenEMR REST allergy payload from DSL attributes.

    Maps to: POST/PUT /apis/default/api/patient/{uuid}/allergy
    Schema: title (required), begdate (required), enddate, diagnosis
    """
    attrs = item.attrs
    return {
        "title": attrs.get("substance", "") or attrs.get("display", "") or attrs.get("title", ""),
        "begdate": attrs.get("begdate", attrs.get("onset", "")),
        "enddate": attrs.get("enddate", ""),
        "comments": item.description,
    }


def _build_encounter_rest(item: DslItem, patient_uuid: str) -> dict[str, Any]:
    """Build an OpenEMR REST encounter payload from DSL attributes.

    Maps to: POST /apis/default/api/patient/{uuid}/encounter
    """
    attrs = item.attrs
    description = item.description
    return {
        "pc_catid": attrs.get("category", ""),
        "reason": attrs.get("reason", description),
        "date": attrs.get("date", ""),
        "onset_date": attrs.get("onset", ""),
        "facility": attrs.get("facility", ""),
        "sensitivity": attrs.get("sensitivity", "normal"),
    }


# ---------------------------------------------------------------------------
# Builder registry
# ---------------------------------------------------------------------------

_REST_BUILDERS = {
    "Condition": _build_condition_rest,
    "MedicationRequest": _build_medication_rest,
    "AllergyIntolerance": _build_allergy_rest,
    "Encounter": _build_encounter_rest,
}
