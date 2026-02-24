from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.agent.models import (
    ChangeManifest,
    ManifestAction,
    ManifestItem,
    PageContext,
)


@pytest.fixture
def sample_manifest_item() -> ManifestItem:
    return ManifestItem(
        resource_type="Condition",
        action=ManifestAction.CREATE,
        proposed_value={
            "code": {"coding": [{"code": "E11.9", "system": "http://hl7.org/fhir/sid/icd-10-cm"}]},
            "subject": {"reference": "Patient/1"},
        },
        source_reference="Encounter/5",
        description="Add diabetes diagnosis",
    )


@pytest.fixture
def sample_change_manifest(sample_manifest_item: ManifestItem) -> ChangeManifest:
    return ChangeManifest(
        patient_id="patient-1",
        encounter_id="encounter-5",
        items=[sample_manifest_item],
    )


@pytest.fixture
def sample_page_context() -> PageContext:
    return PageContext(
        patient_id="patient-1",
        encounter_id="encounter-5",
        page_type="encounter",
    )


@pytest.fixture
def mock_openemr_client() -> AsyncMock:
    client = AsyncMock()
    client.fhir_read = AsyncMock(return_value={"total": 1, "entry": [{"resource": {"resourceType": "Patient", "id": "1"}}]})
    client.api_call = AsyncMock(return_value={"status": "ok"})
    return client
