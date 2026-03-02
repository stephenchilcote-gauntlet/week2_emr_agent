"""Clinical manifest DSL parser.

Parses XML-based manifest DSL into structured ManifestItem objects.

Grammar:
  manifest = item+
  item     = add | edit | remove
  add      = '<add' ATTRS '>' DESCRIPTION '</add>'
  edit     = '<edit' ATTRS '>' DESCRIPTION '</edit>'
  remove   = '<remove' ATTRS '>' DESCRIPTION '</remove>'

Common attributes (all items):
  src   = FHIR resource reference justifying this change (required)
  id    = manifest item ID for depends_on references (optional, auto-generated)
  conf  = confidence level: high | medium | low (optional, default "high")
  deps  = comma-separated list of item IDs this item depends on (optional)

Element content = human-readable description of the change (required).

Action-specific attributes:

  <add> (create):
    type      = FHIR resource type (required)
    code      = clinical code (ICD-10, RxNorm, LOINC) (resource-dependent)
    display   = human-readable name of the clinical entity
    onset     = onset date (Condition)
    status    = clinical status (default: "active")
    drug      = drug name (MedicationRequest)
    dose      = dosage (MedicationRequest)
    freq      = frequency (MedicationRequest)
    route     = route of administration (MedicationRequest)
    substance = allergen name (AllergyIntolerance)
    reaction  = reaction description (AllergyIntolerance)
    severity  = reaction severity (AllergyIntolerance)
    criticality = allergy criticality (AllergyIntolerance)
    loinc     = LOINC code (Observation)
    value     = observation value (Observation)
    unit      = observation unit (Observation)
    doctype   = document type (DocumentReference)
    content   = document/note text content (DocumentReference)
    category  = plan category (CarePlan)

  <edit> (update):
    ref  = ResourceType/id of the resource to update (required)
    (any attribute = field to change)

  <remove> (delete):
    ref  = ResourceType/id of the resource to delete (required)

Example:
  <add type="Condition" code="E11.9" display="Type 2 diabetes mellitus"
       onset="2024-01-15" src="Encounter/5" id="item-1">
    Add Type 2 diabetes diagnosis based on HbA1c results
  </add>
  <edit ref="MedicationRequest/123" dose="1000mg BID" src="Encounter/5">
    Increase metformin dosage
  </edit>
  <remove ref="Condition/456" src="Encounter/5">
    Remove resolved URI from active problem list
  </remove>
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from uuid import uuid4


# -- Parsed item dataclass -------------------------------------------------

@dataclass
class DslItem:
    """A single parsed manifest item from the DSL."""

    action: str  # "add", "edit", "remove"
    resource_type: str  # FHIR resource type (full name)
    description: str  # human-readable description (element content)
    source_reference: str  # src attribute
    item_id: str  # id attribute or auto-generated
    confidence: str = "high"  # conf attribute
    depends_on: list[str] = field(default_factory=list)  # deps attribute
    ref: str | None = None  # edit/remove: ResourceType/id
    attrs: dict[str, str] = field(default_factory=dict)  # all other attributes


# -- Resource type abbreviation mapping ------------------------------------

_TYPE_ALIASES: dict[str, str] = {
    "condition": "Condition",
    "cond": "Condition",
    "medicationrequest": "MedicationRequest",
    "medreq": "MedicationRequest",
    "medication": "MedicationRequest",
    "med": "MedicationRequest",
    "allergyintolerance": "AllergyIntolerance",
    "allergy": "AllergyIntolerance",
    "appointment": "Appointment",
    "appt": "Appointment",
    "scheduling": "Appointment",
    "observation": "Observation",
    "obs": "Observation",
    "documentreference": "DocumentReference",
    "doc": "DocumentReference",
    "document": "DocumentReference",
    "careplan": "CarePlan",
    "plan": "CarePlan",
    "procedure": "Procedure",
    "proc": "Procedure",
    "encounter": "Encounter",
    "enc": "Encounter",
    "immunization": "Immunization",
    "imm": "Immunization",
    "diagnosticreport": "DiagnosticReport",
    "diagreport": "DiagnosticReport",
    "patient": "Patient",
    "referral": "Referral",
    "transaction": "Referral",
    "servicerequest": "ServiceRequest",
    "service": "ServiceRequest",
    "order": "ServiceRequest",
    "surgery": "Surgery",
    "surg": "Surgery",
    "soapnote": "SoapNote",
    "soap_note": "SoapNote",
    "soap": "SoapNote",
    "note": "SoapNote",
    "vital": "Vital",
    "vitals": "Vital",
    "vitalsigns": "Vital",
}


def _resolve_type(raw: str) -> str:
    """Resolve a resource type string, handling abbreviations."""
    return _TYPE_ALIASES.get(raw.lower(), raw)


# -- Sanitization ----------------------------------------------------------

def _sanitize_xml(text: str) -> str:
    """Escape characters that would break XML parsing.

    LLMs frequently emit:
    - Bare '&' in text/attributes (e.g., "Valid & Active")
    - Bare '<' or '>' in attribute values (e.g., assessment="A1c > 8%")
    - Bare '<' in text content (e.g., "Goal: A1c < 7%")

    Strategy: scan character by character tracking whether we're inside a tag
    or text content, and escape accordingly.
    """
    # First pass: escape bare & not already XML entities
    text = re.sub(
        r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[\da-fA-F]+;)",
        "&amp;",
        text,
    )

    # Second pass: escape bare < in attribute values (double-quoted)
    # Matches ="..." and escapes < > inside that are not already entities
    def _escape_attr(m: re.Match) -> str:
        val = m.group(1)
        val = val.replace("<", "&lt;").replace(">", "&gt;")
        return f'="{val}"'

    # Only escape < > inside ="..." (attribute value) — leave the = and quotes
    text = re.sub(r'="([^"]*)"', _escape_attr, text)

    # Third pass: escape bare < in text content (between closing > of one tag
    # and opening < of the next). Track state: in_tag means we're inside a
    # < ... > sequence where we should NOT escape anything.
    parts: list[str] = []
    in_tag = False
    j = 0
    while j < len(text):
        c = text[j]
        if c == "<":
            if in_tag:
                # < while already in a tag — malformed; escape it
                parts.append("&lt;")
            else:
                # Check if this looks like a real XML tag start
                # (letter, /, !, ?) vs bare < in text content
                peek = text[j + 1] if j + 1 < len(text) else ""
                if peek in ("/", "!", "?") or peek.isalpha() or peek == "_":
                    in_tag = True
                    parts.append(c)
                else:
                    # Bare < in text content
                    parts.append("&lt;")
        elif c == ">":
            in_tag = False
            parts.append(c)
        else:
            parts.append(c)
        j += 1

    return "".join(parts)


# -- Parser ----------------------------------------------------------------

def parse_manifest_dsl(text: str) -> list[DslItem]:
    """Parse a manifest DSL string into a list of DslItem objects.

    Accepts one or more <add>, <edit>, or <remove> XML elements.
    Wraps in a <root> element for parsing if not already wrapped.
    """
    text = text.strip()
    if not text:
        return []

    sanitized = _sanitize_xml(text)

    # Wrap in root element if needed
    if not sanitized.startswith("<manifest") and not sanitized.startswith("<root"):
        sanitized = f"<root>{sanitized}</root>"
    elif sanitized.startswith("<manifest"):
        # Replace <manifest ...> with <root ...> so we parse uniformly
        sanitized = re.sub(r"^<manifest(\s|>)", r"<root\1", sanitized, count=1)
        sanitized = re.sub(r"</manifest\s*>$", "</root>", sanitized)

    try:
        root = ET.fromstring(sanitized)
    except ET.ParseError as exc:
        raise ValueError(f"Invalid manifest DSL: {exc}") from exc

    items: list[DslItem] = []
    for elem in root:
        tag = elem.tag.lower()
        if tag not in ("add", "edit", "remove"):
            raise ValueError(
                f"Unknown DSL element <{elem.tag}>. "
                "Expected <add>, <edit>, or <remove>."
            )
        items.append(_parse_element(elem))

    return items


def _parse_element(elem: ET.Element) -> DslItem:
    """Parse a single <add>, <edit>, or <remove> element."""
    tag = elem.tag.lower()
    attribs = dict(elem.attrib)

    # Action
    action_map = {"add": "add", "edit": "edit", "remove": "remove"}
    action = action_map[tag]

    # Description = element text content (stripped)
    description = (elem.text or "").strip()

    # Common attributes
    source_reference = attribs.pop("src", "")
    item_id = attribs.pop("id", str(uuid4()))
    confidence = attribs.pop("conf", "high")

    deps_raw = attribs.pop("deps", "")
    depends_on = [d.strip() for d in deps_raw.split(",") if d.strip()] if deps_raw else []

    # Resource type and ref
    ref: str | None = None
    resource_type = ""

    if action == "add":
        resource_type = _resolve_type(attribs.pop("type", ""))
        if not resource_type:
            raise ValueError(
                f"<add> element missing required 'type' attribute: "
                f"{ET.tostring(elem, encoding='unicode')[:200]}"
            )
    elif action in ("edit", "remove"):
        ref = attribs.pop("ref", "")
        if not ref:
            raise ValueError(
                f"<{tag}> element missing required 'ref' attribute: "
                f"{ET.tostring(elem, encoding='unicode')[:200]}"
            )
        # Extract resource type from ref: "MedicationRequest/123" → "MedicationRequest"
        parts = ref.split("/", 1)
        resource_type = _resolve_type(parts[0])

    return DslItem(
        action=action,
        resource_type=resource_type,
        description=description,
        source_reference=source_reference,
        item_id=item_id,
        confidence=confidence,
        depends_on=depends_on,
        ref=ref,
        attrs=attribs,  # remaining attributes are resource-specific
    )
