SYSTEM_PROMPT = """\
You are a clinical workflow assistant embedded in OpenEMR, an open-source \
electronic medical records system. You help clinicians with documentation, \
diagnosis coding, care transitions, medication management, and other \
clinical workflows.

## Core Principles

1. **Patient Safety First** — NEVER fabricate clinical facts. Only use data \
retrieved from patient records via the tools provided.
2. **Read Before Write** — ALWAYS read relevant patient data (conditions, \
medications, allergies, encounters) before proposing any changes.
3. **Manifest-Driven Changes** — Build a complete change manifest before any \
writes. Each item in the manifest must cite its source FHIR resource.
4. **Confidence Transparency** — When uncertain, flag manifest items as \
"medium" or "low" confidence. Explain your reasoning.
5. **Minimal Scope** — Only propose changes directly relevant to the \
clinician's request. Do not add unrelated items.

## Workflow

1. Understand the clinician's request.
2. Use `fhir_read` to retrieve relevant patient data (demographics, \
conditions, medications, allergies, encounters, observations).
3. Use `get_page_context` if you need to understand the current UI context.
4. Reason about the clinical situation using ONLY retrieved data.
5. Build a change manifest with `submit_manifest` containing every proposed \
change, each with a source reference and description.
6. Wait for clinician review before any writes are executed.

## Change Manifest Rules

- Every item must have a `source_reference` pointing to the FHIR resource \
that justifies it (e.g., "Condition/123", "MedicationRequest/456").
- Every item must have a human-readable `description` explaining what will \
change and why.
- Items with dependencies must declare them in `depends_on`.
- Use `confidence: "low"` for inferred or uncertain items.
- NEVER bypass the manifest — all changes must be reviewed first.

## Safety Constraints

- Do NOT diagnose conditions — suggest possible codes for clinician review.
- Do NOT prescribe medications — propose medication entries for review.
- Flag potential drug interactions or allergy conflicts.
- If data is missing or ambiguous, ask the clinician for clarification \
rather than guessing.
"""

FHIR_RESOURCE_TYPES = [
    "Patient",
    "Encounter",
    "Condition",
    "MedicationRequest",
    "AllergyIntolerance",
    "Observation",
    "Procedure",
    "DiagnosticReport",
    "Immunization",
    "CarePlan",
    "DocumentReference",
]

TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "fhir_read",
        "description": (
            "Read FHIR resources from the OpenEMR server. Use this to "
            "retrieve patient data such as conditions, medications, "
            "allergies, encounters, observations, and other clinical records. "
            "Always read relevant data before proposing changes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "resource_type": {
                    "type": "string",
                    "enum": FHIR_RESOURCE_TYPES,
                    "description": "The FHIR resource type to query.",
                },
                "params": {
                    "type": "object",
                    "description": (
                        "Optional search parameters as key-value pairs. "
                        "Example: {\"patient\": \"123\", \"status\": \"active\"}"
                    ),
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["resource_type"],
        },
    },
    {
        "name": "fhir_write",
        "description": (
            "Write a FHIR resource to the OpenEMR server. This tool should "
            "ONLY be used during the execution phase after the clinician has "
            "approved the change manifest. Each write must reference an "
            "approved manifest item."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "resource_type": {
                    "type": "string",
                    "enum": FHIR_RESOURCE_TYPES,
                    "description": "The FHIR resource type to create or update.",
                },
                "payload": {
                    "type": "object",
                    "description": "The FHIR resource payload to write.",
                },
                "manifest_item_id": {
                    "type": "string",
                    "description": (
                        "The ID of the approved manifest item that "
                        "authorizes this write."
                    ),
                },
            },
            "required": ["resource_type", "payload", "manifest_item_id"],
        },
    },
    {
        "name": "openemr_api",
        "description": (
            "Call an OpenEMR REST API endpoint directly for operations not "
            "covered by FHIR resources, such as billing, scheduling, or "
            "system configuration."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "endpoint": {
                    "type": "string",
                    "description": (
                        "The API endpoint path, e.g. "
                        "\"/api/patient/1/appointment\"."
                    ),
                },
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST", "PUT", "DELETE"],
                    "description": "The HTTP method.",
                },
                "payload": {
                    "type": "object",
                    "description": "Optional request body for POST/PUT.",
                },
            },
            "required": ["endpoint", "method"],
        },
    },
    {
        "name": "get_page_context",
        "description": (
            "Retrieve the current UI context including the active patient, "
            "encounter, page type, and any form data. Use this to understand "
            "what the clinician is currently viewing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "submit_manifest",
        "description": (
            "Submit a change manifest for clinician review. The manifest "
            "contains all proposed changes with source references and "
            "descriptions. The clinician must approve items before they are "
            "executed. Call this once you have gathered all relevant data and "
            "determined the changes needed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "patient_id": {
                    "type": "string",
                    "description": "The patient ID the changes apply to.",
                },
                "encounter_id": {
                    "type": "string",
                    "description": "Optional encounter ID.",
                },
                "items": {
                    "type": "array",
                    "description": "List of proposed change items.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "resource_type": {
                                "type": "string",
                                "description": "FHIR resource type.",
                            },
                            "action": {
                                "type": "string",
                                "enum": ["create", "update", "delete"],
                                "description": "The type of change.",
                            },
                            "proposed_value": {
                                "type": "object",
                                "description": "The proposed resource value.",
                            },
                            "current_value": {
                                "type": "object",
                                "description": (
                                    "The current value if updating/deleting."
                                ),
                            },
                            "source_reference": {
                                "type": "string",
                                "description": (
                                    "FHIR resource reference that justifies "
                                    "this change."
                                ),
                            },
                            "description": {
                                "type": "string",
                                "description": (
                                    "Human-readable description of the change."
                                ),
                            },
                            "confidence": {
                                "type": "string",
                                "enum": ["high", "medium", "low"],
                                "description": "Confidence level.",
                            },
                            "depends_on": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "IDs of prerequisite manifest items."
                                ),
                            },
                        },
                        "required": [
                            "resource_type",
                            "action",
                            "proposed_value",
                            "source_reference",
                            "description",
                        ],
                    },
                },
            },
            "required": ["patient_id", "items"],
        },
    },
]
