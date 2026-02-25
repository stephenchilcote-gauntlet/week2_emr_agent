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

# OpenEMR REST endpoints that use numeric PID instead of UUID in the path.
# Most endpoints use :puuid but the medication endpoint uses :pid.
_PID_ENDPOINTS = {"MedicationRequest"}


def can_rest_write(resource_type: str) -> bool:
    """Return True if the given resource type has a REST write path."""
    return resource_type in _REST_PATH_MAP


def uses_pid(resource_type: str) -> bool:
    """Return True if the endpoint for this resource type uses numeric PID."""
    return resource_type in _PID_ENDPOINTS


def get_rest_endpoint(item: DslItem, patient_id: str) -> str:
    """Return the OpenEMR REST API endpoint path for this item.

    ``patient_id`` should be the numeric PID or FHIR UUID depending on
    the resource type.  Use :func:`uses_pid` to determine which to pass.
    """
    rest_path = _REST_PATH_MAP.get(item.resource_type)
    if rest_path is None:
        raise ValueError(f"No REST endpoint defined for {item.resource_type}")
    return f"patient/{patient_id}/{rest_path}"


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

def _date_or_none(value: str | None) -> str | None:
    """Return the value if it's a non-empty date string, otherwise None.

    OpenEMR stores dates as DATETIME columns.  Sending an empty string
    causes MariaDB to insert ``0000-00-00 00:00:00`` which is invisible
    to the UI's ``dateEmptySql`` filter (it only matches NULL or
    ``0000-00-00``).  Sending None produces a JSON null which the PHP
    API binds as SQL NULL.
    """
    if value:
        return value
    return None


def _build_condition_rest(item: DslItem, patient_uuid: str) -> dict[str, Any]:
    """Build an OpenEMR REST medical_problem payload from DSL attributes.

    Maps to: POST /apis/default/api/patient/{uuid}/medical_problem
    """
    attrs = item.attrs

    begdate = _date_or_none(attrs.get("onset"))
    if not begdate and item.action == "add":
        from datetime import date
        begdate = date.today().isoformat() + " 00:00:00"

    # ConditionRestController whitelist: title, begdate, enddate, diagnosis.
    # Other fields (occurrence, outcome, comments) are silently dropped.
    return {
        "title": attrs.get("display", ""),
        "diagnosis": f"ICD10:{attrs.get('code', '')}",
        "begdate": begdate,
        "enddate": _date_or_none(attrs.get("enddate")),
    }


def _build_medication_rest(item: DslItem, patient_uuid: str) -> dict[str, Any]:
    """Build an OpenEMR REST medication payload from DSL attributes.

    Maps to: POST/PUT /apis/default/api/patient/{uuid}/medication
    Schema: title (required), begdate (required), enddate, diagnosis
    """
    attrs = item.attrs
    drug = attrs.get("drug", "") or attrs.get("display", "") or attrs.get("title", "")
    dose = attrs.get("dose", "")
    route = attrs.get("route", "")

    # Only build a title when we have a drug name, or when creating.
    # For edits without a drug name, title is omitted so the executor's
    # merge preserves the existing title from the cached record.
    title: str | None = None
    if drug or item.action == "add":
        title = drug
        if dose:
            title = f"{title} {dose}".strip()
        if route:
            title = f"{title} {route}".strip()

    begdate = _date_or_none(attrs.get("begdate"))
    if not begdate and item.action == "add":
        from datetime import date
        begdate = date.today().isoformat() + " 00:00:00"

    enddate = _date_or_none(attrs.get("enddate"))
    if attrs.get("status") == "stopped" and not enddate:
        from datetime import date
        enddate = date.today().isoformat() + " 00:00:00"

    return {
        "title": title,
        "begdate": begdate,
        "enddate": enddate,
        "comments": item.description,
    }


def _build_allergy_rest(item: DslItem, patient_uuid: str) -> dict[str, Any]:
    """Build an OpenEMR REST allergy payload from DSL attributes.

    Maps to: POST/PUT /apis/default/api/patient/{uuid}/allergy
    Schema: title (required), begdate (required), enddate, diagnosis
    """
    attrs = item.attrs

    begdate = _date_or_none(attrs.get("begdate") or attrs.get("onset"))
    if not begdate and item.action == "add":
        from datetime import date
        begdate = date.today().isoformat() + " 00:00:00"

    return {
        "title": attrs.get("substance", "") or attrs.get("display", "") or attrs.get("title", ""),
        "begdate": begdate,
        "enddate": _date_or_none(attrs.get("enddate")),
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
        "date": _date_or_none(attrs.get("date")),
        "onset_date": _date_or_none(attrs.get("onset")),
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
