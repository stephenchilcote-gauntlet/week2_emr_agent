"""ICD-10 and CPT code format validation."""

from __future__ import annotations

import re

ICD10_PATTERN = re.compile(r"^[A-Z]\d{2}(\.\d{1,4})?$")
CPT_PATTERN = re.compile(r"^\d{5}$")

COMMON_ICD10_CODES: dict[str, str] = {
    "E11.9": "Type 2 diabetes mellitus without complications",
    "I10": "Essential (primary) hypertension",
    "J06.9": "Acute upper respiratory infection, unspecified",
    "M54.5": "Low back pain",
    "K21.0": "Gastro-esophageal reflux disease with esophagitis",
    "F41.1": "Generalized anxiety disorder",
    "E78.5": "Hyperlipidemia, unspecified",
    "J45.909": "Unspecified asthma, uncomplicated",
    "N39.0": "Urinary tract infection, site not specified",
    "R10.9": "Unspecified abdominal pain",
}

ICD10_PREFIXES: set[str] = {
    "A", "B", "C", "D", "E", "F", "G", "H", "I", "J",
    "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T",
    "U", "V", "W", "X", "Y", "Z",
}


def validate_icd10_format(code: str) -> bool:
    """Check whether a string matches valid ICD-10 format.

    Format: one letter + two digits + optional dot + 1-4 more digits.
    Examples: E11.9, I10, J45.909
    """
    return bool(ICD10_PATTERN.match(code.strip().upper()))


def validate_cpt_format(code: str) -> bool:
    """Check whether a string matches valid CPT format (exactly 5 digits)."""
    return bool(CPT_PATTERN.match(code.strip()))
