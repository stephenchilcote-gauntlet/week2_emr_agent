from __future__ import annotations

import pytest

from src.agent.dsl import DslItem
from src.agent.translator import (
    can_rest_write,
    dsl_item_to_proposed_value,
    get_rest_endpoint,
    needs_encounter,
    to_openemr_rest,
    uses_pid,
)

PATIENT_UUID = "bbb13f7a-966e-4c7c-aea5-4bac3ce98505"
ENCOUNTER_FHIR_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
MEDREQ_FHIR_UUID = "dddddddd-2222-3333-4444-555555555555"
CONDITION_FHIR_UUID = "cccccccc-1111-2222-3333-444444444444"
ENCOUNTER_UUID = "eeeeeeee-1111-2222-3333-444444444444"


def _make_item(**overrides) -> DslItem:
    defaults = dict(
        action="add",
        resource_type="Condition",
        description="test item",
        source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
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
        # ConditionRestController only whitelists: title, begdate, enddate, diagnosis
        assert "comments" not in result
        assert "occurrence" not in result
        assert "outcome" not in result

    def test_condition_default_begdate_is_date_only(self):
        """ConditionValidator requires Y-m-d format, not Y-m-d H:i:s."""
        from datetime import date
        item = _make_item(
            action="add",
            description="Add condition",
            attrs={"code": "I10", "display": "Hypertension"},
        )
        result = to_openemr_rest(item, PATIENT_UUID)
        assert result["begdate"] == date.today().isoformat()
        assert " " not in result["begdate"]  # no time component

    def test_condition_strips_time_from_onset(self):
        """If onset includes a time component, it should be stripped."""
        item = _make_item(
            attrs={"code": "I10", "display": "HTN", "onset": "2024-01-15 00:00:00"},
        )
        result = to_openemr_rest(item, PATIENT_UUID)
        assert result["begdate"] == "2024-01-15"

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
        # comments is NOT stored by ListService — only title, begdate,
        # enddate, diagnosis are written to the database
        assert "comments" not in result

    def test_new_medication_defaults_begdate_to_today(self):
        from datetime import date
        item = _make_item(
            action="add",
            resource_type="MedicationRequest",
            description="Start aspirin",
            attrs={"drug": "Aspirin", "dose": "81mg"},
        )
        result = to_openemr_rest(item, PATIENT_UUID)
        assert result["begdate"] == date.today().isoformat() + " 00:00:00"

    def test_edit_medication_no_default_begdate(self):
        item = _make_item(
            action="edit",
            resource_type="MedicationRequest",
            description="Update dose",
            attrs={"drug": "Aspirin", "dose": "325mg"},
        )
        result = to_openemr_rest(item, PATIENT_UUID)
        assert result["begdate"] is None

    def test_stopped_medication_sets_enddate(self):
        from datetime import date
        item = _make_item(
            resource_type="MedicationRequest",
            description="Stop metformin",
            attrs={"drug": "Metformin", "status": "stopped"},
        )
        result = to_openemr_rest(item, PATIENT_UUID)
        assert result["enddate"] == date.today().isoformat() + " 00:00:00"


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
        # AllergyIntoleranceValidator requires Y-m-d H:i:s format
        assert result["begdate"] == "2020-01-01 00:00:00"
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
        assert result["class_code"] == "AMB"
        assert result["reason"] == "Checkup"
        assert result["date"] == "2024-06-01"
        assert result["sensitivity"] == "normal"

    def test_custom_class_code(self):
        item = _make_item(
            resource_type="Encounter",
            description="ER visit",
            attrs={"class_code": "EMER", "reason": "Chest pain"},
        )
        result = to_openemr_rest(item, PATIENT_UUID)
        assert result["class_code"] == "EMER"

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


# ---- 5b. uses_pid ----

class TestUsesPid:
    def test_medication_uses_pid(self):
        assert uses_pid("MedicationRequest") is True

    def test_condition_uses_uuid(self):
        assert uses_pid("Condition") is False

    def test_allergy_uses_uuid(self):
        assert uses_pid("AllergyIntolerance") is False

    def test_encounter_uses_uuid(self):
        assert uses_pid("Encounter") is False


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
            ref=f"MedicationRequest/{MEDREQ_FHIR_UUID}",
            attrs={"dose": "1000mg"},
        )
        result = dsl_item_to_proposed_value(item)
        assert result["ref"] == f"MedicationRequest/{MEDREQ_FHIR_UUID}"
        assert result["dose"] == "1000mg"
        assert "type" not in result

    def test_remove_item_with_ref(self):
        item = _make_item(
            action="remove",
            ref=f"Condition/{CONDITION_FHIR_UUID}",
            attrs={},
        )
        result = dsl_item_to_proposed_value(item)
        assert result["ref"] == f"Condition/{CONDITION_FHIR_UUID}"
        assert "type" not in result

    def test_add_no_ref_in_result(self):
        item = _make_item(action="add", ref=None, attrs={"code": "X"})
        result = dsl_item_to_proposed_value(item)
        assert "ref" not in result

    def test_edit_no_ref_field_when_ref_is_none(self):
        item = _make_item(action="edit", ref=None, attrs={"dose": "500mg"})
        result = dsl_item_to_proposed_value(item)
        assert "ref" not in result


# ---- 8. SoapNote → OpenEMR REST ----

class TestSoapNoteRest:
    def test_basic_soap_note(self):
        item = _make_item(
            resource_type="SoapNote",
            description="Visit note",
            attrs={
                "subjective": "Patient reports headache",
                "objective": "BP 120/80",
                "assessment": "Tension headache",
                "plan": "OTC analgesics",
            },
        )
        result = to_openemr_rest(item, PATIENT_UUID)
        assert result["subjective"] == "Patient reports headache"
        assert result["objective"] == "BP 120/80"
        assert result["assessment"] == "Tension headache"
        assert result["plan"] == "OTC analgesics"

    def test_missing_fields_default_to_empty(self):
        item = _make_item(
            resource_type="SoapNote",
            description="Partial note",
            attrs={"subjective": "Cough for 3 days"},
        )
        result = to_openemr_rest(item, PATIENT_UUID)
        assert result["subjective"] == "Cough for 3 days"
        assert result["objective"] == ""
        assert result["assessment"] == ""
        assert result["plan"] == ""


# ---- 9. Vital → OpenEMR REST ----

class TestVitalRest:
    def test_basic_vitals(self):
        item = _make_item(
            resource_type="Vital",
            description="Record vitals",
            attrs={"bps": "120", "bpd": "80", "pulse": "72"},
        )
        result = to_openemr_rest(item, PATIENT_UUID)
        assert result["bps"] == "120"
        assert result["bpd"] == "80"
        assert result["pulse"] == "72"

    def test_missing_fields_omitted(self):
        item = _make_item(
            resource_type="Vital",
            description="Weight only",
            attrs={"weight": "75"},
        )
        result = to_openemr_rest(item, PATIENT_UUID)
        assert result["weight"] == "75"
        assert "bps" not in result
        assert "height" not in result
        assert "temperature" not in result

    def test_all_vital_fields(self):
        all_fields = {
            "bps": "120", "bpd": "80", "weight": "75", "height": "170",
            "temperature": "98.6", "temp_method": "oral", "pulse": "72",
            "respiration": "16", "note": "Normal", "waist_circ": "32",
            "head_circ": "22", "oxygen_saturation": "99",
        }
        item = _make_item(
            resource_type="Vital",
            description="Full vitals",
            attrs=all_fields,
        )
        result = to_openemr_rest(item, PATIENT_UUID)
        for key, val in all_fields.items():
            assert result[key] == val

    def test_empty_attrs_returns_empty_dict(self):
        item = _make_item(resource_type="Vital", description="No data", attrs={})
        result = to_openemr_rest(item, PATIENT_UUID)
        assert result == {}


# ---- 10. needs_encounter ----

class TestNeedsEncounter:
    def test_soap_note_needs_encounter(self):
        assert needs_encounter("SoapNote") is True

    def test_vital_needs_encounter(self):
        assert needs_encounter("Vital") is True

    def test_condition_no_encounter(self):
        assert needs_encounter("Condition") is False

    def test_medication_no_encounter(self):
        assert needs_encounter("MedicationRequest") is False


# ---- 11. can_rest_write / uses_pid for SoapNote & Vital ----

class TestSoapNoteVitalFlags:
    def test_soap_note_can_rest_write(self):
        assert can_rest_write("SoapNote") is True

    def test_vital_can_rest_write(self):
        assert can_rest_write("Vital") is True

    def test_soap_note_uses_pid(self):
        assert uses_pid("SoapNote") is True

    def test_vital_uses_pid(self):
        assert uses_pid("Vital") is True


# ---- 12. get_rest_endpoint for encounter-scoped types ----

class TestEncounterScopedEndpoints:
    def test_soap_note_endpoint(self):
        item = _make_item(resource_type="SoapNote")
        endpoint = get_rest_endpoint(item, PATIENT_UUID, encounter_id=ENCOUNTER_UUID)
        assert endpoint == f"patient/{PATIENT_UUID}/encounter/{ENCOUNTER_UUID}/soap_note"

    def test_vital_endpoint(self):
        item = _make_item(resource_type="Vital")
        endpoint = get_rest_endpoint(item, PATIENT_UUID, encounter_id=ENCOUNTER_UUID)
        assert endpoint == f"patient/{PATIENT_UUID}/encounter/{ENCOUNTER_UUID}/vital"

    def test_soap_note_missing_encounter_raises(self):
        item = _make_item(resource_type="SoapNote")
        with pytest.raises(ValueError, match="requires an encounter_id"):
            get_rest_endpoint(item, PATIENT_UUID)

    def test_vital_missing_encounter_raises(self):
        item = _make_item(resource_type="Vital")
        with pytest.raises(ValueError, match="requires an encounter_id"):
            get_rest_endpoint(item, PATIENT_UUID)
