from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.agent.models import (
    ChangeManifest,
    ManifestAction,
    ManifestItem,
    PageContext,
)

# Realistic FHIR UUIDs — use these everywhere instead of bare integers.
# OpenEMR's FHIR API always returns UUIDs as resource IDs.
PATIENT_FHIR_UUID = "bbb13f7a-966e-4c7c-aea5-4bac3ce98505"
ENCOUNTER_FHIR_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
CONDITION_FHIR_UUID = "cccccccc-1111-2222-3333-444444444444"


@pytest.fixture
def sample_manifest_item() -> ManifestItem:
    return ManifestItem(
        resource_type="Condition",
        action=ManifestAction.CREATE,
        proposed_value={
            "code": {"coding": [{"code": "E11.9", "system": "http://hl7.org/fhir/sid/icd-10-cm"}]},
            "subject": {"reference": f"Patient/{PATIENT_FHIR_UUID}"},
        },
        source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
        description="Add diabetes diagnosis",
    )


@pytest.fixture
def sample_change_manifest(sample_manifest_item: ManifestItem) -> ChangeManifest:
    return ChangeManifest(
        patient_id=PATIENT_FHIR_UUID,
        encounter_id=ENCOUNTER_FHIR_UUID,
        items=[sample_manifest_item],
    )


@pytest.fixture
def sample_page_context() -> PageContext:
    return PageContext(
        patient_id="5",
        encounter_id=ENCOUNTER_FHIR_UUID,
        page_type="encounter",
    )


@pytest.fixture
def mock_openemr_client() -> AsyncMock:
    client = AsyncMock()
    client.fhir_read = AsyncMock(return_value={
        "resourceType": "Bundle",
        "total": 1,
        "entry": [{"resource": {"resourceType": "Encounter", "id": ENCOUNTER_FHIR_UUID}}],
    })
    client.api_call = AsyncMock(return_value={"status": "ok"})
    return client
