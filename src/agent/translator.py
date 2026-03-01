"""Translate parsed DSL items into OpenEMR REST API payloads.

The translator converts flat DSL attributes into OpenEMR REST API JSON
for all supported resource types.  This module is the single source of
truth for the mapping between the agent's compact DSL and the verbose
API payloads.
"""

from __future__ import annotations

import re
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
    "SoapNote": "soap_note",
    "Vital": "vital",
    "Surgery": "surgery",
    "Appointment": "appointment",
    "Referral": "transaction",
}

# OpenEMR REST endpoints that use numeric PID instead of UUID in the path.
# Most endpoints use :puuid but the medication endpoint uses :pid.
_PID_ENDPOINTS = {"MedicationRequest", "SoapNote", "Vital", "Surgery", "Appointment", "Referral"}

# Endpoints that require an encounter ID in the path.
_ENCOUNTER_ENDPOINTS = {"SoapNote", "Vital"}


def can_rest_write(resource_type: str) -> bool:
    """Return True if the given resource type has a REST write path."""
    return resource_type in _REST_PATH_MAP


def uses_pid(resource_type: str) -> bool:
    """Return True if the endpoint for this resource type uses numeric PID."""
    return resource_type in _PID_ENDPOINTS


def needs_encounter(resource_type: str) -> bool:
    """Return True if the endpoint requires an encounter ID in the path."""
    return resource_type in _ENCOUNTER_ENDPOINTS


def get_rest_endpoint(
    item: DslItem, patient_id: str, *, encounter_id: str | None = None,
) -> str:
    """Return the OpenEMR REST API endpoint path for this item.

    ``patient_id`` should be the numeric PID or FHIR UUID depending on
    the resource type.  Use :func:`uses_pid` to determine which to pass.

    For encounter-scoped types (SoapNote, Vital), ``encounter_id`` is
    required and the path includes ``encounter/{encounter_id}``.
    """
    rest_path = _REST_PATH_MAP.get(item.resource_type)
    if rest_path is None:
        raise ValueError(f"No REST endpoint defined for {item.resource_type}")
    if item.resource_type in _ENCOUNTER_ENDPOINTS:
        if not encounter_id:
            raise ValueError(
                f"{item.resource_type} requires an encounter_id but none was provided"
            )
        return f"patient/{patient_id}/encounter/{encounter_id}/{rest_path}"
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


_DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_DATETIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})")


def _as_date(value: str | None) -> str | None:
    """Normalize a date value to ``Y-m-d`` format (no time component).

    ConditionValidator uses ``datetime('Y-m-d')`` which performs a strict
    round-trip comparison — ``"2024-01-15 00:00:00"`` is rejected.
    """
    value = _date_or_none(value)
    if not value:
        return None
    m = _DATE_PREFIX_RE.match(value)
    return m.group(1) if m else value


def _as_datetime(value: str | None) -> str | None:
    """Normalize a date value to ``Y-m-d H:i:s`` format.

    AllergyIntoleranceValidator and ListService (medication) use
    ``datetime('Y-m-d H:i:s')`` which rejects bare ``"2024-01-15"``.
    """
    value = _date_or_none(value)
    if not value:
        return None
    m = _DATETIME_RE.match(value)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    m2 = _DATE_PREFIX_RE.match(value)
    if m2:
        return f"{m2.group(1)} 00:00:00"
    return value


def _build_condition_rest(item: DslItem, patient_uuid: str) -> dict[str, Any]:
    """Build an OpenEMR REST medical_problem payload from DSL attributes.

    Maps to: POST /apis/default/api/patient/{uuid}/medical_problem
    """
    attrs = item.attrs

    begdate = _as_date(attrs.get("onset"))
    if not begdate and item.action == "add":
        from datetime import date
        begdate = date.today().isoformat()

    # ConditionRestController whitelist: title, begdate, enddate, diagnosis.
    # ConditionValidator requires begdate in Y-m-d format (no time component).
    # Other fields (occurrence, outcome, comments) are silently dropped.
    return {
        "title": attrs.get("display", ""),
        "diagnosis": f"ICD10:{attrs.get('code', '')}",
        "begdate": begdate,
        "enddate": _as_date(attrs.get("enddate")),
    }


def _build_medication_rest(item: DslItem, patient_uuid: str) -> dict[str, Any]:
    """Build an OpenEMR REST medication payload from DSL attributes.

    Maps to: POST/PUT /apis/default/api/patient/{pid}/medication
    Schema: title (required), begdate (required), enddate, diagnosis.
    ListService uses ``datetime('Y-m-d H:i:s')`` for date validation.
    Note: ``comments`` is NOT stored by ListService — only the four
    fields above are written to the database.
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

    begdate = _as_datetime(attrs.get("begdate"))
    if not begdate and item.action == "add":
        from datetime import date
        begdate = f"{date.today().isoformat()} 00:00:00"

    enddate = _as_datetime(attrs.get("enddate"))
    if attrs.get("status") == "stopped" and not enddate:
        from datetime import date
        enddate = f"{date.today().isoformat()} 00:00:00"

    return {
        "title": title,
        "begdate": begdate,
        "enddate": enddate,
        "diagnosis": _date_or_none(attrs.get("diagnosis")),
    }


def _build_allergy_rest(item: DslItem, patient_uuid: str) -> dict[str, Any]:
    """Build an OpenEMR REST allergy payload from DSL attributes.

    Maps to: POST/PUT /apis/default/api/patient/{puuid}/allergy
    Schema: title (required), begdate, enddate, diagnosis, comments.
    AllergyIntoleranceValidator uses ``datetime('Y-m-d H:i:s')`` for dates.
    """
    attrs = item.attrs

    begdate = _as_datetime(attrs.get("begdate") or attrs.get("onset"))
    if not begdate and item.action == "add":
        from datetime import date
        begdate = f"{date.today().isoformat()} 00:00:00"

    return {
        "title": attrs.get("substance", "") or attrs.get("display", "") or attrs.get("title", ""),
        "begdate": begdate,
        "enddate": _as_datetime(attrs.get("enddate")),
        "comments": item.description,
    }


def _build_encounter_rest(item: DslItem, patient_uuid: str) -> dict[str, Any]:
    """Build an OpenEMR REST encounter payload from DSL attributes.

    Maps to: POST /apis/default/api/patient/{puuid}/encounter.
    EncounterValidator requires ``class_code`` (validated against the
    ``_ActEncounterCode`` list, e.g. ``AMB`` for ambulatory).
    """
    attrs = item.attrs
    description = item.description
    return {
        "pc_catid": attrs.get("category", ""),
        "class_code": attrs.get("class_code", "AMB"),
        "reason": attrs.get("reason", description),
        "date": _date_or_none(attrs.get("date")),
        "onset_date": _date_or_none(attrs.get("onset")),
        "facility": attrs.get("facility", ""),
        "sensitivity": attrs.get("sensitivity", "normal"),
    }


def _build_soap_note_rest(item: DslItem, patient_uuid: str) -> dict[str, Any]:
    """Build an OpenEMR REST soap_note payload from DSL attributes."""
    attrs = item.attrs
    return {
        "subjective": attrs.get("subjective", ""),
        "objective": attrs.get("objective", ""),
        "assessment": attrs.get("assessment", ""),
        "plan": attrs.get("plan", ""),
    }


def _build_vital_rest(item: DslItem, patient_uuid: str) -> dict[str, Any]:
    """Build an OpenEMR REST vital payload from DSL attributes."""
    attrs = item.attrs
    result = {}
    for field in ("bps", "bpd", "weight", "height", "temperature",
                  "temp_method", "pulse", "respiration", "note",
                  "waist_circ", "head_circ", "oxygen_saturation"):
        val = attrs.get(field)
        if val is not None:
            result[field] = val
    return result


def _build_surgery_rest(item: DslItem, patient_uuid: str) -> dict[str, Any]:
    """Build an OpenEMR REST surgery payload from DSL attributes.

    Maps to: POST/PUT /apis/default/api/patient/{pid}/surgery
    Same ListRestController pattern as medications.
    Schema: title (required), begdate (required), enddate, diagnosis.
    """
    attrs = item.attrs
    title = attrs.get("title", "") or attrs.get("display", "")

    begdate = _as_datetime(attrs.get("begdate"))
    if not begdate and item.action == "add":
        from datetime import date
        begdate = f"{date.today().isoformat()} 00:00:00"

    return {
        "title": title,
        "begdate": begdate,
        "enddate": _as_datetime(attrs.get("enddate")),
        "diagnosis": _date_or_none(attrs.get("diagnosis")),
    }


def _build_appointment_rest(item: DslItem, patient_uuid: str) -> dict[str, Any]:
    """Build an OpenEMR REST appointment payload from DSL attributes.

    Maps to: POST /apis/default/api/patient/{pid}/appointment
    OpenEMR uses ``pc_*`` prefixed fields with strict validation.
    """
    attrs = item.attrs
    result: dict[str, Any] = {}

    field_map = {
        "pc_catid": ("category", "pc_catid"),
        "pc_title": ("title", "pc_title"),
        "pc_duration": ("duration", "pc_duration"),
        "pc_hometext": ("reason", "pc_hometext"),
        "pc_apptstatus": ("status", "pc_apptstatus"),
        "pc_facility": ("facility", "pc_facility"),
        "pc_billing_location": ("billing_facility", "pc_billing_location"),
        "pc_aid": ("provider", "pc_aid"),
    }

    for pc_key, aliases in field_map.items():
        for alias in aliases:
            val = attrs.get(alias)
            if val is not None:
                result[pc_key] = val
                break

    event_date = _as_date(attrs.get("date") or attrs.get("pc_eventDate"))
    if event_date is not None:
        result["pc_eventDate"] = event_date

    start_time = attrs.get("start_time") or attrs.get("pc_startTime")
    if start_time is not None:
        result["pc_startTime"] = start_time

    return result


def _build_referral_rest(item: DslItem, patient_uuid: str) -> dict[str, Any]:
    """Build an OpenEMR REST transaction (referral) payload from DSL attributes.

    Maps to: POST /apis/default/api/patient/{pid}/transaction
    OpenEMR transactions with type ``LBTref`` are referrals.
    """
    attrs = item.attrs
    result: dict[str, Any] = {
        "type": "LBTref",
        "groupname": "Default",
    }

    referral_date = _as_date(attrs.get("referral_date"))
    if referral_date is not None:
        result["referralDate"] = referral_date

    body = attrs.get("body")
    if body is not None:
        result["body"] = body

    for dsl_key, api_key in (
        ("refer_by_npi", "referByNpi"),
        ("refer_to_npi", "referToNpi"),
        ("diagnosis", "referDiagnosis"),
        ("risk_level", "riskLevel"),
    ):
        val = attrs.get(dsl_key)
        if val is not None:
            result[api_key] = val

    return result


# ---------------------------------------------------------------------------
# Builder registry
# ---------------------------------------------------------------------------

_REST_BUILDERS = {
    "Condition": _build_condition_rest,
    "MedicationRequest": _build_medication_rest,
    "AllergyIntolerance": _build_allergy_rest,
    "Encounter": _build_encounter_rest,
    "SoapNote": _build_soap_note_rest,
    "Vital": _build_vital_rest,
    "Surgery": _build_surgery_rest,
    "Appointment": _build_appointment_rest,
    "Referral": _build_referral_rest,
}
