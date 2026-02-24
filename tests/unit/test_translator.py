from __future__ import annotations

import pytest

from src.agent.dsl import DslItem
from src.agent.translator import (
    can_rest_write,
    dsl_item_to_proposed_value,
    get_rest_endpoint,
    to_openemr_rest,
)

PATIENT_UUID = "uuid-pat-001"


def _make_item(**overrides) -> DslItem:
    defaults = dict(
        action="add",
        resource_type="Condition",
        description="test item",
        source_reference="Encounter/5",
        item_id="item-1",
        confidence="high",
        depends_on=[],
        ref=None,
        attrs={},
    )
    defaults.update(overrides)
    return DslItem(**defaults)


# ---- 1. Condition → OpenEMR REST ----

class TestConditionRest:
    def test_basic_condition(self):
        item = _make_item(
            description="Add Type 2 diabetes",
            attrs={"code": "E11.9", "display": "Type 2 DM", "onset": "2024-01-15"},
        )
        result = to_openemr_rest(item, PATIENT_UUID)

        assert result["title"] == "Type 2 DM"
        assert result["diagnosis"] == "ICD10:E11.9"
        assert result["begdate"] == "2024-01-15"
        assert result["comments"] == "Add Type 2 diabetes"
        assert result["outcome"] == ""

    def test_unsupported_resource_raises(self):
        item = _make_item(resource_type="CarePlan")
        with pytest.raises(ValueError, match="No OpenEMR REST builder"):
            to_openemr_rest(item, PATIENT_UUID)


# ---- 2. MedicationRequest → OpenEMR REST ----

class TestMedicationRest:
    def test_basic_medication(self):
        item = _make_item(
            resource_type="MedicationRequest",
            description="Start metformin",
            attrs={"drug": "Metformin", "dose": "500mg", "route": "oral"},
        )
        result = to_openemr_rest(item, PATIENT_UUID)
        assert result["title"] == "Metformin 500mg oral"
        assert result["comments"] == "Start metformin"

    def test_stopped_medication_sets_enddate(self):
        item = _make_item(
            resource_type="MedicationRequest",
            description="Stop metformin",
            attrs={"drug": "Metformin", "status": "stopped"},
        )
        result = to_openemr_rest(item, PATIENT_UUID)
        assert result["enddate"]  # non-empty


# ---- 3. AllergyIntolerance → OpenEMR REST ----

class TestAllergyRest:
    def test_basic_allergy(self):
        item = _make_item(
            resource_type="AllergyIntolerance",
            description="Penicillin allergy",
            attrs={"substance": "Penicillin", "onset": "2020-01-01"},
        )
        result = to_openemr_rest(item, PATIENT_UUID)
        assert result["title"] == "Penicillin"
        assert result["begdate"] == "2020-01-01"
        assert result["comments"] == "Penicillin allergy"


# ---- 4. Encounter → OpenEMR REST ----

class TestEncounterRest:
    def test_basic_encounter(self):
        item = _make_item(
            resource_type="Encounter",
            description="Annual physical",
            attrs={"category": "5", "reason": "Checkup", "date": "2024-06-01"},
        )
        result = to_openemr_rest(item, PATIENT_UUID)
        assert result["pc_catid"] == "5"
        assert result["reason"] == "Checkup"
        assert result["date"] == "2024-06-01"
        assert result["sensitivity"] == "normal"

    def test_reason_defaults_to_description(self):
        item = _make_item(
            resource_type="Encounter",
            description="Follow-up visit",
            attrs={"date": "2024-06-01"},
        )
        result = to_openemr_rest(item, PATIENT_UUID)
        assert result["reason"] == "Follow-up visit"

    def test_onset_and_facility(self):
        item = _make_item(
            resource_type="Encounter",
            description="Visit",
            attrs={"onset": "2024-05-01", "facility": "Main Clinic"},
        )
        result = to_openemr_rest(item, PATIENT_UUID)
        assert result["onset_date"] == "2024-05-01"
        assert result["facility"] == "Main Clinic"


# ---- 5. can_rest_write ----

class TestCanRestWrite:
    def test_condition(self):
        assert can_rest_write("Condition") is True

    def test_medication(self):
        assert can_rest_write("MedicationRequest") is True

    def test_allergy(self):
        assert can_rest_write("AllergyIntolerance") is True

    def test_encounter(self):
        assert can_rest_write("Encounter") is True

    def test_unsupported(self):
        assert can_rest_write("CarePlan") is False

    def test_observation(self):
        assert can_rest_write("Observation") is False


# ---- 6. get_rest_endpoint ----

class TestGetRestEndpoint:
    def test_condition_endpoint(self):
        item = _make_item(resource_type="Condition")
        endpoint = get_rest_endpoint(item, PATIENT_UUID)
        assert endpoint == f"patient/{PATIENT_UUID}/medical_problem"

    def test_medication_endpoint(self):
        item = _make_item(resource_type="MedicationRequest")
        endpoint = get_rest_endpoint(item, PATIENT_UUID)
        assert endpoint == f"patient/{PATIENT_UUID}/medication"

    def test_allergy_endpoint(self):
        item = _make_item(resource_type="AllergyIntolerance")
        endpoint = get_rest_endpoint(item, PATIENT_UUID)
        assert endpoint == f"patient/{PATIENT_UUID}/allergy"

    def test_encounter_endpoint(self):
        item = _make_item(resource_type="Encounter")
        endpoint = get_rest_endpoint(item, PATIENT_UUID)
        assert endpoint == f"patient/{PATIENT_UUID}/encounter"

    def test_unknown_type_raises(self):
        item = _make_item(resource_type="CarePlan")
        with pytest.raises(ValueError, match="No REST endpoint"):
            get_rest_endpoint(item, PATIENT_UUID)


# ---- 7. dsl_item_to_proposed_value ----

class TestDslItemToProposedValue:
    def test_add_item(self):
        item = _make_item(
            action="add",
            resource_type="Condition",
            attrs={"code": "E11.9", "display": "Type 2 DM"},
        )
        result = dsl_item_to_proposed_value(item)
        assert result["type"] == "Condition"
        assert result["code"] == "E11.9"
        assert result["display"] == "Type 2 DM"
        assert "ref" not in result

    def test_edit_item_with_ref(self):
        item = _make_item(
            action="edit",
            ref="MedicationRequest/123",
            attrs={"dose": "1000mg"},
        )
        result = dsl_item_to_proposed_value(item)
        assert result["ref"] == "MedicationRequest/123"
        assert result["dose"] == "1000mg"
        assert "type" not in result

    def test_remove_item_with_ref(self):
        item = _make_item(
            action="remove",
            ref="Condition/456",
            attrs={},
        )
        result = dsl_item_to_proposed_value(item)
        assert result["ref"] == "Condition/456"
        assert "type" not in result

    def test_add_no_ref_in_result(self):
        item = _make_item(action="add", ref=None, attrs={"code": "X"})
        result = dsl_item_to_proposed_value(item)
        assert "ref" not in result

    def test_edit_no_ref_field_when_ref_is_none(self):
        item = _make_item(action="edit", ref=None, attrs={"dose": "500mg"})
        result = dsl_item_to_proposed_value(item)
        assert "ref" not in result
