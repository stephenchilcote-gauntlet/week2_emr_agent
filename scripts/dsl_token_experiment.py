"""Token counting experiment: compare manifest representations.

Compares JSON baseline vs 3 DSL candidates for the same set of
representative clinical manifest items.

Usage:
    python -m scripts.dsl_token_experiment
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

import anthropic

# ---------------------------------------------------------------------------
# Representative manifest items (6 items covering common clinical ops)
# ---------------------------------------------------------------------------

# --- BASELINE: Current FHIR JSON format -----------------------------------

JSON_MANIFEST = {
    "patient_id": "1",
    "encounter_id": "5",
    "items": [
        {
            "id": "item-1",
            "resource_type": "Condition",
            "action": "create",
            "proposed_value": {
                "resourceType": "Condition",
                "subject": {"reference": "Patient/1"},
                "code": {
                    "coding": [
                        {
                            "system": "http://hl7.org/fhir/sid/icd-10-cm",
                            "code": "E11.9",
                            "display": "Type 2 diabetes mellitus without complications",
                        }
                    ]
                },
                "clinicalStatus": {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                            "code": "active",
                        }
                    ]
                },
                "verificationStatus": {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/condition-ver-status",
                            "code": "confirmed",
                        }
                    ]
                },
                "onsetDateTime": "2024-01-15",
                "encounter": {"reference": "Encounter/5"},
            },
            "source_reference": "Encounter/5",
            "description": "Add Type 2 diabetes diagnosis based on HbA1c results",
            "confidence": "high",
        },
        {
            "id": "item-2",
            "resource_type": "Condition",
            "action": "create",
            "proposed_value": {
                "resourceType": "Condition",
                "subject": {"reference": "Patient/1"},
                "code": {
                    "coding": [
                        {
                            "system": "http://hl7.org/fhir/sid/icd-10-cm",
                            "code": "E66.01",
                            "display": "Morbid (severe) obesity due to excess calories",
                        }
                    ]
                },
                "clinicalStatus": {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                            "code": "active",
                        }
                    ]
                },
                "verificationStatus": {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/condition-ver-status",
                            "code": "confirmed",
                        }
                    ]
                },
                "onsetDateTime": "2024-01-15",
                "encounter": {"reference": "Encounter/5"},
            },
            "source_reference": "Observation/bmi-32",
            "description": "Add obesity diagnosis — BMI 32 documented",
            "confidence": "high",
        },
        {
            "id": "item-3",
            "resource_type": "MedicationRequest",
            "action": "update",
            "proposed_value": {
                "resourceType": "MedicationRequest",
                "id": "med-metformin-123",
                "subject": {"reference": "Patient/1"},
                "medicationCodeableConcept": {
                    "coding": [
                        {
                            "system": "http://www.nlm.nih.gov/research/umls/rxnorm",
                            "code": "860975",
                            "display": "Metformin 1000mg",
                        }
                    ]
                },
                "dosageInstruction": [
                    {
                        "text": "1000mg twice daily",
                        "timing": {"repeat": {"frequency": 2, "period": 1, "periodUnit": "d"}},
                        "doseAndRate": [
                            {
                                "doseQuantity": {"value": 1000, "unit": "mg", "system": "http://unitsofmeasure.org"}
                            }
                        ],
                    }
                ],
                "status": "active",
                "intent": "order",
                "encounter": {"reference": "Encounter/5"},
            },
            "current_value": {
                "resourceType": "MedicationRequest",
                "id": "med-metformin-123",
                "dosageInstruction": [{"text": "500mg twice daily"}],
            },
            "source_reference": "Observation/hba1c-latest",
            "description": "Increase metformin from 500mg to 1000mg BID due to worsening A1c",
            "confidence": "high",
            "depends_on": ["item-1"],
        },
        {
            "id": "item-4",
            "resource_type": "AllergyIntolerance",
            "action": "create",
            "proposed_value": {
                "resourceType": "AllergyIntolerance",
                "patient": {"reference": "Patient/1"},
                "code": {
                    "coding": [
                        {
                            "system": "http://www.nlm.nih.gov/research/umls/rxnorm",
                            "code": "723",
                            "display": "Penicillin",
                        }
                    ]
                },
                "clinicalStatus": {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
                            "code": "active",
                        }
                    ]
                },
                "type": "allergy",
                "category": ["medication"],
                "criticality": "high",
                "reaction": [
                    {
                        "manifestation": [{"coding": [{"display": "Anaphylaxis"}]}],
                        "severity": "severe",
                    }
                ],
            },
            "source_reference": "Patient/1",
            "description": "Document penicillin allergy with anaphylaxis history",
            "confidence": "high",
        },
        {
            "id": "item-5",
            "resource_type": "DocumentReference",
            "action": "create",
            "proposed_value": {
                "resourceType": "DocumentReference",
                "status": "current",
                "type": {
                    "coding": [
                        {
                            "system": "http://loinc.org",
                            "code": "11506-3",
                            "display": "Progress note",
                        }
                    ]
                },
                "subject": {"reference": "Patient/1"},
                "content": [
                    {
                        "attachment": {
                            "contentType": "text/plain",
                            "data": "Subjective: Patient reports increased thirst and frequent urination x2 weeks.\nObjective: BMI 32, BP 138/88, HbA1c 8.2%.\nAssessment: Uncontrolled T2DM with new obesity diagnosis.\nPlan: Increase metformin to 1000mg BID, endocrinology referral, recheck A1c in 3 months.",
                        }
                    }
                ],
                "context": {"encounter": [{"reference": "Encounter/5"}]},
            },
            "source_reference": "Encounter/5",
            "description": "SOAP note for diabetes follow-up visit",
            "confidence": "high",
        },
        {
            "id": "item-6",
            "resource_type": "Condition",
            "action": "delete",
            "proposed_value": {
                "resourceType": "Condition",
                "id": "cond-resolved-789",
            },
            "current_value": {
                "resourceType": "Condition",
                "id": "cond-resolved-789",
                "code": {
                    "coding": [
                        {
                            "system": "http://hl7.org/fhir/sid/icd-10-cm",
                            "code": "J06.9",
                            "display": "Acute upper respiratory infection, unspecified",
                        }
                    ]
                },
            },
            "source_reference": "Encounter/5",
            "description": "Remove resolved URI from active problem list",
            "confidence": "high",
        },
    ],
}

# --- SYNTAX A: Sigil-compact (one line per item) --------------------------

SYNTAX_A = """\
+Condition E11.9 "Type 2 diabetes mellitus without complications" onset=2024-01-15 src=Encounter/5 id=item-1 "Add Type 2 diabetes diagnosis based on HbA1c results"
+Condition E66.01 "Morbid (severe) obesity due to excess calories" onset=2024-01-15 src=Observation/bmi-32 id=item-2 "Add obesity diagnosis — BMI 32 documented"
~MedicationRequest/med-metformin-123 drug="Metformin 1000mg" dose="1000mg BID" src=Observation/hba1c-latest id=item-3 deps=item-1 "Increase metformin from 500mg to 1000mg BID due to worsening A1c"
+AllergyIntolerance "Penicillin" reaction="Anaphylaxis" severity=severe criticality=high src=Patient/1 id=item-4 "Document penicillin allergy with anaphylaxis history"
+DocumentReference type="Progress note" content="S: Patient reports increased thirst and frequent urination x2 weeks.\\nO: BMI 32, BP 138/88, HbA1c 8.2%.\\nA: Uncontrolled T2DM with new obesity diagnosis.\\nP: Increase metformin to 1000mg BID, endocrinology referral, recheck A1c in 3 months." src=Encounter/5 id=item-5 "SOAP note for diabetes follow-up visit"
-Condition/cond-resolved-789 src=Encounter/5 id=item-6 "Remove resolved URI from active problem list"
"""

# --- SYNTAX B: XML flat-attribute ------------------------------------------

SYNTAX_B = """\
<manifest patient="1" encounter="5">
<add type="Condition" code="E11.9" display="Type 2 diabetes mellitus without complications" onset="2024-01-15" src="Encounter/5" id="item-1">Add Type 2 diabetes diagnosis based on HbA1c results</add>
<add type="Condition" code="E66.01" display="Morbid (severe) obesity due to excess calories" onset="2024-01-15" src="Observation/bmi-32" id="item-2">Add obesity diagnosis — BMI 32 documented</add>
<edit ref="MedicationRequest/med-metformin-123" drug="Metformin 1000mg" dose="1000mg BID" src="Observation/hba1c-latest" id="item-3" deps="item-1">Increase metformin from 500mg to 1000mg BID due to worsening A1c</edit>
<add type="AllergyIntolerance" substance="Penicillin" reaction="Anaphylaxis" severity="severe" criticality="high" src="Patient/1" id="item-4">Document penicillin allergy with anaphylaxis history</add>
<add type="DocumentReference" doctype="Progress note" src="Encounter/5" id="item-5">SOAP note for diabetes follow-up visit
S: Patient reports increased thirst and frequent urination x2 weeks.
O: BMI 32, BP 138/88, HbA1c 8.2%.
A: Uncontrolled T2DM with new obesity diagnosis.
P: Increase metformin to 1000mg BID, endocrinology referral, recheck A1c in 3 months.</add>
<remove ref="Condition/cond-resolved-789" src="Encounter/5" id="item-6">Remove resolved URI from active problem list</remove>
</manifest>
"""

# --- SYNTAX C: Pipe-delimited compact (hybrid) -----------------------------

SYNTAX_C = """\
patient=1 encounter=5
+Cond | E11.9 | Type 2 diabetes mellitus without complications | onset=2024-01-15 | src=Encounter/5 | id=item-1 | Add Type 2 diabetes diagnosis based on HbA1c results
+Cond | E66.01 | Morbid (severe) obesity due to excess calories | onset=2024-01-15 | src=Observation/bmi-32 | id=item-2 | Add obesity diagnosis — BMI 32 documented
~MedReq/med-metformin-123 | drug=Metformin 1000mg | dose=1000mg BID | src=Observation/hba1c-latest | id=item-3 | deps=item-1 | Increase metformin from 500mg to 1000mg BID due to worsening A1c
+Allergy | Penicillin | reaction=Anaphylaxis | severity=severe | criticality=high | src=Patient/1 | id=item-4 | Document penicillin allergy with anaphylaxis history
+Doc | type=Progress note | src=Encounter/5 | id=item-5 | SOAP note for diabetes follow-up visit | S: Increased thirst x2wk. O: BMI 32, BP 138/88, A1c 8.2%. A: Uncontrolled T2DM, obesity. P: Metformin 1000mg BID, endo referral, recheck 3mo.
-Cond/cond-resolved-789 | src=Encounter/5 | id=item-6 | Remove resolved URI from active problem list
"""

# --- SYNTAX D: Flat JSON (no FHIR nesting, but proper JSON objects) --------

SYNTAX_D = json.dumps({
    "patient_id": "1",
    "encounter_id": "5",
    "items": [
        {"id": "item-1", "action": "add", "type": "Condition", "code": "E11.9", "display": "Type 2 diabetes mellitus without complications", "onset": "2024-01-15", "src": "Encounter/5", "desc": "Add Type 2 diabetes diagnosis based on HbA1c results"},
        {"id": "item-2", "action": "add", "type": "Condition", "code": "E66.01", "display": "Morbid (severe) obesity due to excess calories", "onset": "2024-01-15", "src": "Observation/bmi-32", "desc": "Add obesity diagnosis — BMI 32 documented"},
        {"id": "item-3", "action": "edit", "ref": "MedicationRequest/med-metformin-123", "drug": "Metformin 1000mg", "dose": "1000mg BID", "src": "Observation/hba1c-latest", "deps": ["item-1"], "desc": "Increase metformin from 500mg to 1000mg BID due to worsening A1c"},
        {"id": "item-4", "action": "add", "type": "AllergyIntolerance", "substance": "Penicillin", "reaction": "Anaphylaxis", "severity": "severe", "criticality": "high", "src": "Patient/1", "desc": "Document penicillin allergy with anaphylaxis history"},
        {"id": "item-5", "action": "add", "type": "DocumentReference", "doctype": "Progress note", "content": "S: Patient reports increased thirst and frequent urination x2 weeks.\nO: BMI 32, BP 138/88, HbA1c 8.2%.\nA: Uncontrolled T2DM with new obesity diagnosis.\nP: Increase metformin to 1000mg BID, endocrinology referral, recheck A1c in 3 months.", "src": "Encounter/5", "desc": "SOAP note for diabetes follow-up visit"},
        {"id": "item-6", "action": "remove", "ref": "Condition/cond-resolved-789", "src": "Encounter/5", "desc": "Remove resolved URI from active problem list"},
    ],
})

# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------


def count_tokens_anthropic(text: str, client: anthropic.Anthropic) -> int:
    """Count tokens using Anthropic's tokenizer."""
    result = client.messages.count_tokens(
        model="claude-sonnet-4-20250514",
        messages=[{"role": "user", "content": text}],
    )
    return result.input_tokens


def main() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set. Add it to .env")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    json_text = json.dumps(JSON_MANIFEST, indent=2)

    candidates = {
        "JSON baseline (FHIR)": json_text,
        "Syntax A (sigil-compact)": SYNTAX_A,
        "Syntax B (XML flat-attr)": SYNTAX_B,
        "Syntax C (pipe-delimited)": SYNTAX_C,
        "Syntax D (flat JSON)": SYNTAX_D,
    }

    print("=" * 70)
    print("MANIFEST DSL TOKEN EXPERIMENT")
    print("=" * 70)
    print(f"\nScenario: 6 manifest items (2 Condition creates, 1 MedReq update,")
    print(f"          1 Allergy create, 1 DocumentRef create, 1 Condition delete)")
    print()

    results = {}
    baseline_tokens = None

    for name, text in candidates.items():
        tokens = count_tokens_anthropic(text, client)
        chars = len(text)
        lines = text.strip().count("\n") + 1
        results[name] = {"tokens": tokens, "chars": chars, "lines": lines}

        if baseline_tokens is None:
            baseline_tokens = tokens

        reduction = ((baseline_tokens - tokens) / baseline_tokens * 100) if baseline_tokens else 0
        print(f"  {name}:")
        print(f"    Tokens: {tokens:>6}  |  Chars: {chars:>6}  |  Lines: {lines:>3}  |  vs JSON: {reduction:+.1f}%")
        print()

    print("-" * 70)
    print("RAW TEXT OF EACH CANDIDATE:")
    print("-" * 70)
    for name, text in candidates.items():
        print(f"\n### {name} ###\n")
        print(text)

    # Also measure just the tool call argument portion (what the LLM actually generates)
    # For JSON, it's the full JSON. For DSL, it's the DSL string passed as a single argument.
    print("\n" + "=" * 70)
    print("TOOL CALL ARGUMENT SIZE (what the LLM generates)")
    print("=" * 70)
    print()

    # JSON: the LLM generates the full JSON as tool arguments
    json_tool_arg = json.dumps(JSON_MANIFEST)  # no indent - actual tool call format
    json_arg_tokens = count_tokens_anthropic(json_tool_arg, client)

    # DSL: the LLM generates a single string
    for name, text in candidates.items():
        if name == "JSON baseline (FHIR)":
            tok = json_arg_tokens
            print(f"  {name} (compact JSON): {tok} tokens, {len(json_tool_arg)} chars")
        else:
            tok = count_tokens_anthropic(text.strip(), client)
            reduction = ((json_arg_tokens - tok) / json_arg_tokens * 100)
            print(f"  {name}: {tok} tokens, {len(text.strip())} chars ({reduction:+.1f}% vs JSON)")
    print()


if __name__ == "__main__":
    main()
