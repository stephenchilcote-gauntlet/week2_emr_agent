from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.agent.models import ChangeManifest, ManifestAction, ManifestItem
from src.verification.checks import (
    VerificationReport,
    VerificationResult,
    _extract_allergen_name,
    _extract_code,
    _extract_medication_name,
    _normalize_for_conflict,
    check_confidence,
    check_conflict,
    check_constraints,
    check_dose_sanity,
    check_grounding,
    check_medication_safety,
    verify_manifest,
)
from src.verification.icd10 import validate_cpt_format, validate_icd10_format

CONDITION_FHIR_UUID = "cccccccc-1111-2222-3333-444444444444"
ENCOUNTER_FHIR_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


# ------------------------------------------------------------------
# ICD-10 format validation
# ------------------------------------------------------------------

class TestICD10Validation:
    @pytest.mark.parametrize("code", [
        "E11.9", "I10", "J45.909", "A00", "Z99.89", "M54.5", "R10.9",
    ])
    def test_valid_codes(self, code):
        assert validate_icd10_format(code) is True

    @pytest.mark.parametrize("code", [
        "11.9",       # missing letter prefix
        "EE1.9",      # two letters
        "E1",         # only one digit
        "E11.",       # trailing dot, no digits
        "E11.99999",  # too many decimal digits (5)
        "",           # empty
        "123",        # no letter
        "e11.9",      # lowercase — should still pass (upper() in impl)
    ])
    def test_invalid_codes(self, code):
        if code == "e11.9":
            assert validate_icd10_format(code) is True  # implementation upper-cases
        else:
            assert validate_icd10_format(code) is False


# ------------------------------------------------------------------
# CPT format validation
# ------------------------------------------------------------------

class TestCPTValidation:
    @pytest.mark.parametrize("code", ["99213", "00100", "12345"])
    def test_valid_codes(self, code):
        assert validate_cpt_format(code) is True

    @pytest.mark.parametrize("code", [
        "9921",    # 4 digits
        "992133",  # 6 digits
        "ABCDE",   # letters
        "",        # empty
        "9921A",   # mixed
    ])
    def test_invalid_codes(self, code):
        assert validate_cpt_format(code) is False


# ------------------------------------------------------------------
# Confidence / hedging detection
# ------------------------------------------------------------------

class TestConfidenceCheck:
    def test_no_hedging(self, sample_manifest_item):
        result = check_confidence(sample_manifest_item)
        assert result.passed is True
        assert result.severity == "info"

    def test_hedging_in_description(self):
        item = ManifestItem(
            resource_type="Condition",
            action=ManifestAction.CREATE,
            proposed_value={"code": "I10"},
            source_reference="Encounter/1",
            description="Patient possibly has hypertension",
        )
        result = check_confidence(item)
        assert result.passed is False
        assert result.severity == "warning"
        assert "possibly" in result.message

    def test_hedging_in_proposed_value(self):
        item = ManifestItem(
            resource_type="Condition",
            action=ManifestAction.CREATE,
            proposed_value={"note": "might be related to diet"},
            source_reference="Encounter/1",
            description="Add note",
        )
        result = check_confidence(item)
        assert result.passed is False
        assert "might be" in result.message

    @pytest.mark.parametrize("phrase", [
        "possibly", "might be", "unclear", "uncertain",
        "maybe", "could be", "not sure",
    ])
    def test_all_hedging_phrases(self, phrase):
        item = ManifestItem(
            resource_type="Condition",
            action=ManifestAction.CREATE,
            proposed_value={},
            source_reference="Encounter/1",
            description=f"The patient {phrase} has this condition",
        )
        result = check_confidence(item)
        assert result.passed is False


# ------------------------------------------------------------------
# Constraint validation
# ------------------------------------------------------------------

class TestConstraintValidation:
    def test_condition_valid_icd10(self):
        item = ManifestItem(
            resource_type="Condition",
            action=ManifestAction.CREATE,
            proposed_value={"code": {"coding": [{"code": "E11.9"}]}},
            source_reference="Encounter/1",
            description="Diabetes",
        )
        results = check_constraints(item)
        assert len(results) == 1
        assert results[0].passed is True
        assert results[0].check_name == "constraint_icd10"

    def test_condition_invalid_icd10(self):
        item = ManifestItem(
            resource_type="Condition",
            action=ManifestAction.CREATE,
            proposed_value={"code": "INVALID"},
            source_reference="Encounter/1",
            description="Bad code",
        )
        results = check_constraints(item)
        assert len(results) == 1
        assert results[0].passed is False
        assert "Invalid ICD-10" in results[0].message

    def test_procedure_valid_cpt(self):
        item = ManifestItem(
            resource_type="Procedure",
            action=ManifestAction.CREATE,
            proposed_value={"code": "99213"},
            source_reference="Encounter/1",
            description="Office visit",
        )
        results = check_constraints(item)
        assert len(results) == 1
        assert results[0].passed is True
        assert results[0].check_name == "constraint_cpt"

    def test_procedure_invalid_cpt(self):
        item = ManifestItem(
            resource_type="Procedure",
            action=ManifestAction.CREATE,
            proposed_value={"code": "ABC"},
            source_reference="Encounter/1",
            description="Bad CPT",
        )
        results = check_constraints(item)
        assert len(results) == 1
        assert results[0].passed is False
        assert "Invalid CPT" in results[0].message

    def test_no_code_no_results(self):
        item = ManifestItem(
            resource_type="Condition",
            action=ManifestAction.CREATE,
            proposed_value={"subject": "Patient/1"},
            source_reference="Encounter/1",
            description="No code field",
        )
        results = check_constraints(item)
        assert results == []

    def test_document_missing_sections(self):
        item = ManifestItem(
            resource_type="DocumentReference",
            action=ManifestAction.CREATE,
            proposed_value={"text": "Just some notes about the patient"},
            source_reference="Encounter/1",
            description="Clinical note",
        )
        results = check_constraints(item)
        assert any(r.check_name == "constraint_document_sections" for r in results)
        doc_result = next(r for r in results if r.check_name == "constraint_document_sections")
        assert doc_result.passed is False
        assert doc_result.severity == "warning"

    def test_document_all_sections_present(self):
        item = ManifestItem(
            resource_type="DocumentReference",
            action=ManifestAction.CREATE,
            proposed_value={
                "text": "Subjective: headache. Objective: BP 120/80. Assessment: migraine. Plan: rest."
            },
            source_reference="Encounter/1",
            description="SOAP note",
        )
        results = check_constraints(item)
        doc_result = next(r for r in results if r.check_name == "constraint_document_sections")
        assert doc_result.passed is True

    def test_document_key_triggers_document_section_check(self):
        item = ManifestItem(
            resource_type="DocumentReference",
            action=ManifestAction.CREATE,
            proposed_value={"document": "Subjective only"},
            source_reference="Encounter/1",
            description="Clinical note",
        )
        results = check_constraints(item)
        doc_result = next(r for r in results if r.check_name == "constraint_document_sections")
        assert doc_result.passed is False
        assert "objective" in doc_result.message

    def test_document_key_with_full_sections_passes(self):
        item = ManifestItem(
            resource_type="DocumentReference",
            action=ManifestAction.CREATE,
            proposed_value={
                "document": "Subjective: headache. Objective: BP 120/80. Assessment: migraine. Plan: rest."
            },
            source_reference="Encounter/1",
            description="Clinical note",
        )
        results = check_constraints(item)
        doc_result = next(r for r in results if r.check_name == "constraint_document_sections")
        assert doc_result.passed is True


# ------------------------------------------------------------------
# Grounding check
# ------------------------------------------------------------------

class TestGroundingCheck:
    @pytest.mark.asyncio
    async def test_grounding_passes(self, sample_manifest_item, mock_openemr_client):
        result = await check_grounding(sample_manifest_item, mock_openemr_client)
        assert result.passed is True
        assert result.check_name == "grounding"
        mock_openemr_client.fhir_read.assert_called_once_with("Encounter", {"_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"})

    @pytest.mark.asyncio
    async def test_grounding_not_found(self, sample_manifest_item, mock_openemr_client):
        mock_openemr_client.fhir_read.return_value = {"total": 0, "entry": []}
        result = await check_grounding(sample_manifest_item, mock_openemr_client)
        assert result.passed is False
        assert "not found" in result.message

    @pytest.mark.asyncio
    async def test_grounding_treats_error_payload_as_missing(self, sample_manifest_item, mock_openemr_client):
        mock_openemr_client.fhir_read.return_value = {"error": "bad gateway", "total": 1}
        result = await check_grounding(sample_manifest_item, mock_openemr_client)
        assert result.passed is False
        assert "not found" in result.message

    @pytest.mark.asyncio
    async def test_grounding_defaults_missing_total_to_not_found(self, sample_manifest_item, mock_openemr_client):
        mock_openemr_client.fhir_read.return_value = {}
        result = await check_grounding(sample_manifest_item, mock_openemr_client)
        assert result.passed is False
        assert "not found" in result.message

    @pytest.mark.asyncio
    async def test_grounding_no_source_reference(self):
        item = ManifestItem(
            resource_type="Condition",
            action=ManifestAction.CREATE,
            proposed_value={},
            source_reference="",
            description="No ref",
        )
        result = await check_grounding(item, AsyncMock())
        assert result.passed is False
        assert "No source_reference" in result.message

    @pytest.mark.asyncio
    async def test_grounding_invalid_format(self):
        item = ManifestItem(
            resource_type="Condition",
            action=ManifestAction.CREATE,
            proposed_value={},
            source_reference="not-a-valid-ref",
            description="Bad ref format",
        )
        result = await check_grounding(item, AsyncMock())
        assert result.passed is False
        assert "Invalid source_reference format" in result.message

    @pytest.mark.asyncio
    async def test_grounding_client_error(self, sample_manifest_item, mock_openemr_client):
        mock_openemr_client.fhir_read.side_effect = Exception("connection refused")
        result = await check_grounding(sample_manifest_item, mock_openemr_client)
        assert result.passed is False
        assert "Failed to fetch" in result.message


# ------------------------------------------------------------------
# Conflict detection
# ------------------------------------------------------------------

class TestConflictCheck:
    @pytest.mark.asyncio
    async def test_no_conflict_when_no_target(self, mock_openemr_client):
        """Items without target_resource_id skip conflict check."""
        item = ManifestItem(
            resource_type="Condition",
            action=ManifestAction.CREATE,
            proposed_value={"code": "I10"},
            source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
            description="New condition",
        )
        result = await check_conflict(item, mock_openemr_client)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_no_conflict_when_current_value_missing(self, mock_openemr_client):
        item = ManifestItem(
            resource_type="Condition",
            action=ManifestAction.UPDATE,
            proposed_value={"code": "I10"},
            source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
            description="Update condition",
            target_resource_id=CONDITION_FHIR_UUID,
            current_value=None,
        )
        result = await check_conflict(item, mock_openemr_client)
        assert result.passed is True
        assert "No conflict check needed" in result.message

    @pytest.mark.asyncio
    async def test_conflict_fails_when_live_read_returns_error(self, mock_openemr_client):
        item = ManifestItem(
            resource_type="Condition",
            action=ManifestAction.UPDATE,
            proposed_value={"code": "I10"},
            current_value={"resourceType": "Condition", "id": CONDITION_FHIR_UUID, "code": "E11.9"},
            source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
            description="Update condition",
            target_resource_id=CONDITION_FHIR_UUID,
        )
        mock_openemr_client.fhir_read.return_value = {"error": "upstream timeout", "total": 1}
        result = await check_conflict(item, mock_openemr_client)
        assert result.passed is False
        assert "no longer exists" in result.message

    @pytest.mark.asyncio
    async def test_conflict_defaults_missing_total_to_missing_resource(self, mock_openemr_client):
        item = ManifestItem(
            resource_type="Condition",
            action=ManifestAction.UPDATE,
            proposed_value={"code": "I10"},
            current_value={"resourceType": "Condition", "id": CONDITION_FHIR_UUID, "code": "E11.9"},
            source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
            description="Update condition",
            target_resource_id=CONDITION_FHIR_UUID,
        )
        mock_openemr_client.fhir_read.return_value = {"entry": [{"resource": {"resourceType": "Condition", "id": CONDITION_FHIR_UUID}}]}
        result = await check_conflict(item, mock_openemr_client)
        assert result.passed is False
        assert "no longer exists" in result.message

    @pytest.mark.asyncio
    async def test_conflict_detected_on_version_change(self, mock_openemr_client):
        item = ManifestItem(
            resource_type="Condition",
            action=ManifestAction.UPDATE,
            proposed_value={"code": "I10"},
            current_value={"resourceType": "Condition", "id": CONDITION_FHIR_UUID, "meta": {"versionId": "1"}},
            source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
            description="Update diagnosis",
            target_resource_id=CONDITION_FHIR_UUID,
        )
        mock_openemr_client.fhir_read.return_value = {
            "resourceType": "Bundle",
            "total": 1,
            "entry": [{"resource": {"resourceType": "Condition", "id": CONDITION_FHIR_UUID, "meta": {"versionId": "2"}}}],
        }
        result = await check_conflict(item, mock_openemr_client)
        assert result.passed is False
        assert "version has changed" in result.message

    @pytest.mark.asyncio
    async def test_conflict_detected(self, mock_openemr_client):
        """When live data differs from current_value, conflict is detected."""
        item = ManifestItem(
            resource_type="Condition",
            action=ManifestAction.UPDATE,
            proposed_value={"code": "I10"},
            current_value={"resourceType": "Condition", "code": "E11.9", "id": CONDITION_FHIR_UUID},
            source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
            description="Update diagnosis",
            target_resource_id=CONDITION_FHIR_UUID,
        )
        mock_openemr_client.fhir_read.return_value = {"resourceType": "Bundle", "total": 1, "entry": [{"resource": {"resourceType": "Condition", "code": "J45.909", "id": CONDITION_FHIR_UUID}}]}
        result = await check_conflict(item, mock_openemr_client)
        assert result.passed is False
        assert "Conflict detected" in result.message
        mock_openemr_client.fhir_read.assert_called_once_with("Condition", {"_id": CONDITION_FHIR_UUID})

    @pytest.mark.asyncio
    async def test_no_conflict(self, mock_openemr_client):
        """When live data matches current_value, no conflict."""
        current = {"resourceType": "Condition", "code": "E11.9", "id": CONDITION_FHIR_UUID}
        item = ManifestItem(
            resource_type="Condition",
            action=ManifestAction.UPDATE,
            proposed_value={"code": "I10"},
            current_value=current,
            source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
            description="Update diagnosis",
            target_resource_id=CONDITION_FHIR_UUID,
        )
        mock_openemr_client.fhir_read.return_value = {"resourceType": "Bundle", "total": 1, "entry": [{"resource": current}]}
        result = await check_conflict(item, mock_openemr_client)
        assert result.passed is True
        assert "unchanged" in result.message


# ------------------------------------------------------------------
# Full manifest verification
# ------------------------------------------------------------------

class TestVerifyManifest:
    @pytest.mark.asyncio
    async def test_full_verification(self, sample_change_manifest, mock_openemr_client):
        report = await verify_manifest(sample_change_manifest, mock_openemr_client)
        assert report.manifest_id == sample_change_manifest.id
        assert len(report.results) > 0
        # Should contain grounding, constraint, confidence, and conflict checks
        check_names = {r.check_name for r in report.results}
        assert "grounding" in check_names
        assert "confidence" in check_names
        assert "conflict" in check_names


# ------------------------------------------------------------------
# VerificationReport properties
# ------------------------------------------------------------------

class TestVerificationReport:
    def test_passed_all_errors_pass(self):
        report = VerificationReport(
            manifest_id="m1",
            results=[
                VerificationResult(item_id="i1", check_name="a", passed=True, message="ok"),
                VerificationResult(item_id="i1", check_name="b", passed=True, message="ok"),
            ],
        )
        assert report.passed is True

    def test_passed_error_failure(self):
        report = VerificationReport(
            manifest_id="m1",
            results=[
                VerificationResult(item_id="i1", check_name="a", passed=True, message="ok"),
                VerificationResult(
                    item_id="i1", check_name="b", passed=False, message="fail", severity="error"
                ),
            ],
        )
        assert report.passed is False

    def test_passed_warning_failure_still_passes(self):
        report = VerificationReport(
            manifest_id="m1",
            results=[
                VerificationResult(item_id="i1", check_name="a", passed=True, message="ok"),
                VerificationResult(
                    item_id="i1", check_name="b", passed=False, message="warn", severity="warning"
                ),
            ],
        )
        assert report.passed is True

    def test_warnings_property(self):
        report = VerificationReport(
            manifest_id="m1",
            results=[
                VerificationResult(item_id="i1", check_name="a", passed=True, message="ok"),
                VerificationResult(
                    item_id="i1", check_name="b", passed=False, message="warn1", severity="warning"
                ),
                VerificationResult(
                    item_id="i1", check_name="c", passed=True, message="warn2", severity="warning"
                ),
            ],
        )
        warnings = report.warnings
        assert len(warnings) == 2
        assert all(w.severity == "warning" for w in warnings)

    def test_empty_report_passes(self):
        report = VerificationReport(manifest_id="m1")
        assert report.passed is True
        assert report.warnings == []


class TestExtractCode:
    def test_extract_code_from_direct_code_key(self):
        assert _extract_code({"code": "E11.9"}) == "E11.9"

    def test_extract_code_missing_key_returns_none(self):
        assert _extract_code({"display": "Hypertension"}) is None

    def test_extract_code_ignores_non_dict_coding_entries(self):
        assert _extract_code({"coding": ["code"]}) is None


class TestNormalizeForConflict:
    def test_normalize_removes_only_server_managed_meta_fields(self):
        normalized = _normalize_for_conflict(
            {
                "id": CONDITION_FHIR_UUID,
                "meta": {
                    "versionId": "2",
                    "lastUpdated": "2024-01-01T00:00:00Z",
                    "source": "upstream",
                },
            }
        )
        assert normalized["meta"] == {"source": "upstream"}

    def test_normalize_drops_empty_meta_after_cleanup(self):
        normalized = _normalize_for_conflict(
            {
                "id": CONDITION_FHIR_UUID,
                "meta": {
                    "versionId": "2",
                    "lastUpdated": "2024-01-01T00:00:00Z",
                },
            }
        )
        assert "meta" not in normalized


# ------------------------------------------------------------------
# Medication safety checks
# ------------------------------------------------------------------

class TestMedicationSafety:
    @pytest.mark.asyncio
    async def test_skips_non_medication_items(self, mock_openemr_client):
        item = ManifestItem(
            resource_type="Condition",
            action=ManifestAction.CREATE,
            proposed_value={"code": "I10"},
            source_reference="Encounter/1",
            description="Not a medication",
        )
        results = await check_medication_safety(item, mock_openemr_client, "patient-123")
        assert results == []

    @pytest.mark.asyncio
    async def test_missing_drug_name_on_create(self, mock_openemr_client):
        item = ManifestItem(
            resource_type="MedicationRequest",
            action=ManifestAction.CREATE,
            proposed_value={"dose": "500mg"},
            source_reference="Encounter/1",
            description="Add medication",
        )
        results = await check_medication_safety(item, mock_openemr_client, "patient-123")
        assert len(results) == 1
        assert results[0].check_name == "medication_required_fields"
        assert results[0].passed is False

    @pytest.mark.asyncio
    async def test_high_risk_drug_detected(self, mock_openemr_client):
        mock_openemr_client.fhir_read.return_value = {"entry": []}
        item = ManifestItem(
            resource_type="MedicationRequest",
            action=ManifestAction.CREATE,
            proposed_value={"drug": "Warfarin 5mg"},
            source_reference="Encounter/1",
            description="Start warfarin",
        )
        results = await check_medication_safety(item, mock_openemr_client, "patient-123")
        high_risk = [r for r in results if r.check_name == "medication_high_risk"]
        assert len(high_risk) == 1
        assert high_risk[0].passed is False
        assert high_risk[0].severity == "error"

    @pytest.mark.asyncio
    async def test_duplicate_therapy_detected(self, mock_openemr_client):
        mock_openemr_client.fhir_read.side_effect = [
            {  # MedicationRequest query
                "entry": [{"resource": {"drug": "Metformin 500mg", "title": "Metformin 500mg"}}]
            },
            {"entry": []},  # AllergyIntolerance query
        ]
        item = ManifestItem(
            resource_type="MedicationRequest",
            action=ManifestAction.CREATE,
            proposed_value={"drug": "Metformin"},
            source_reference="Encounter/1",
            description="Add metformin",
        )
        results = await check_medication_safety(item, mock_openemr_client, "patient-123")
        dup = [r for r in results if r.check_name == "medication_duplicate"]
        assert len(dup) == 1
        assert dup[0].passed is False
        assert dup[0].severity == "warning"

    @pytest.mark.asyncio
    async def test_allergy_conflict_detected(self, mock_openemr_client):
        mock_openemr_client.fhir_read.side_effect = [
            {"entry": []},  # MedicationRequest query
            {  # AllergyIntolerance query
                "entry": [{"resource": {"substance": "Penicillin", "display": "Penicillin allergy"}}]
            },
        ]
        item = ManifestItem(
            resource_type="MedicationRequest",
            action=ManifestAction.CREATE,
            proposed_value={"drug": "Amoxicillin/Penicillin"},
            source_reference="Encounter/1",
            description="Add amoxicillin",
        )
        results = await check_medication_safety(item, mock_openemr_client, "patient-123")
        allergy = [r for r in results if r.check_name == "medication_allergy_conflict"]
        assert len(allergy) == 1
        assert allergy[0].passed is False
        assert allergy[0].severity == "error"

    @pytest.mark.asyncio
    async def test_safe_medication_passes(self, mock_openemr_client):
        mock_openemr_client.fhir_read.return_value = {"entry": []}
        item = ManifestItem(
            resource_type="MedicationRequest",
            action=ManifestAction.CREATE,
            proposed_value={"drug": "Acetaminophen 500mg"},
            source_reference="Encounter/1",
            description="Add acetaminophen",
        )
        results = await check_medication_safety(item, mock_openemr_client, "patient-123")
        # No errors or warnings expected for a safe, non-duplicate, non-allergic drug
        assert all(r.passed for r in results) or len(results) == 0

    @pytest.mark.asyncio
    async def test_skips_fhir_checks_without_patient_id(self, mock_openemr_client):
        item = ManifestItem(
            resource_type="MedicationRequest",
            action=ManifestAction.CREATE,
            proposed_value={"drug": "Warfarin 5mg"},
            source_reference="Encounter/1",
            description="Start warfarin",
        )
        results = await check_medication_safety(item, mock_openemr_client, "")
        # Should still catch high-risk but skip FHIR-based checks
        assert any(r.check_name == "medication_high_risk" for r in results)
        mock_openemr_client.fhir_read.assert_not_called()

    @pytest.mark.asyncio
    async def test_fhir_error_handled_gracefully(self, mock_openemr_client):
        mock_openemr_client.fhir_read.side_effect = Exception("connection failed")
        item = ManifestItem(
            resource_type="MedicationRequest",
            action=ManifestAction.CREATE,
            proposed_value={"drug": "Lisinopril 10mg"},
            source_reference="Encounter/1",
            description="Add lisinopril",
        )
        results = await check_medication_safety(item, mock_openemr_client, "patient-123")
        # Should get warnings for failed checks, not crash
        warnings = [r for r in results if r.severity == "warning"]
        assert len(warnings) >= 1


# ------------------------------------------------------------------
# Dose sanity checks
# ------------------------------------------------------------------

class TestDoseSanity:
    def test_skips_non_medication(self):
        item = ManifestItem(
            resource_type="Condition",
            action=ManifestAction.CREATE,
            proposed_value={"dose": "50000mg"},
            source_reference="Encounter/1",
            description="Not a med",
        )
        assert check_dose_sanity(item) == []

    def test_skips_no_dose(self):
        item = ManifestItem(
            resource_type="MedicationRequest",
            action=ManifestAction.CREATE,
            proposed_value={"drug": "Lisinopril"},
            source_reference="Encounter/1",
            description="No dose",
        )
        assert check_dose_sanity(item) == []

    def test_normal_dose_passes(self):
        item = ManifestItem(
            resource_type="MedicationRequest",
            action=ManifestAction.CREATE,
            proposed_value={"drug": "Metformin", "dose": "500mg BID"},
            source_reference="Encounter/1",
            description="Normal dose",
        )
        assert check_dose_sanity(item) == []

    def test_high_dose_flagged(self):
        item = ManifestItem(
            resource_type="MedicationRequest",
            action=ManifestAction.CREATE,
            proposed_value={"drug": "Something", "dose": "50000mg"},
            source_reference="Encounter/1",
            description="Very high dose",
        )
        results = check_dose_sanity(item)
        assert len(results) == 1
        assert results[0].check_name == "dose_sanity"
        assert results[0].passed is False
        assert "50000" in results[0].message

    def test_boundary_dose_passes(self):
        item = ManifestItem(
            resource_type="MedicationRequest",
            action=ManifestAction.CREATE,
            proposed_value={"drug": "Something", "dose": "10000mg"},
            source_reference="Encounter/1",
            description="Boundary dose",
        )
        assert check_dose_sanity(item) == []

    def test_dose_with_spaces(self):
        item = ManifestItem(
            resource_type="MedicationRequest",
            action=ManifestAction.CREATE,
            proposed_value={"drug": "Something", "dose": "15000 mg daily"},
            source_reference="Encounter/1",
            description="High dose with spaces",
        )
        results = check_dose_sanity(item)
        assert len(results) == 1


# ------------------------------------------------------------------
# Realistic FHIR response shape tests
#
# OpenEMR's FHIR API returns MedicationRequest with
# medicationCodeableConcept (not flat "drug"/"title"/"display" keys)
# and AllergyIntolerance with code.coding[].display (not flat
# "substance"/"display"/"code.text" keys).  These tests verify our
# checks work against the real response shape.
# ------------------------------------------------------------------


class TestMedicationDuplicateRealisticFHIR:
    """Duplicate-therapy check against realistic OpenEMR FHIR MedicationRequest."""

    @pytest.mark.asyncio
    async def test_duplicate_detected_with_real_fhir_shape(self, mock_openemr_client):
        """OpenEMR returns medicationCodeableConcept, not flat 'drug' field.

        The check should detect a duplicate when the existing medication
        uses the standard FHIR structure that OpenEMR actually returns.
        """
        mock_openemr_client.fhir_read.side_effect = [
            {  # MedicationRequest bundle — realistic OpenEMR shape
                "resourceType": "Bundle",
                "total": 1,
                "entry": [{
                    "resource": {
                        "resourceType": "MedicationRequest",
                        "id": "med-uuid-1",
                        "status": "active",
                        "intent": "order",
                        "medicationCodeableConcept": {
                            "coding": [{
                                "system": "http://www.nlm.nih.gov/research/umls/rxnorm",
                                "code": "860975",
                                "display": "Metformin Hydrochloride 500 MG Oral Tablet",
                            }],
                        },
                    },
                }],
            },
            {"entry": []},  # AllergyIntolerance — empty
        ]
        item = ManifestItem(
            resource_type="MedicationRequest",
            action=ManifestAction.CREATE,
            proposed_value={"drug": "Metformin"},
            source_reference="Encounter/1",
            description="Add metformin",
        )
        results = await check_medication_safety(item, mock_openemr_client, "patient-123")
        dup = [r for r in results if r.check_name == "medication_duplicate"]
        assert len(dup) == 1, (
            "Duplicate check must detect 'Metformin' in medicationCodeableConcept.coding[].display"
        )
        assert dup[0].passed is False

    @pytest.mark.asyncio
    async def test_duplicate_detected_with_text_only_fhir_shape(self, mock_openemr_client):
        """OpenEMR falls back to medicationCodeableConcept.text when no RxNorm code."""
        mock_openemr_client.fhir_read.side_effect = [
            {
                "resourceType": "Bundle",
                "total": 1,
                "entry": [{
                    "resource": {
                        "resourceType": "MedicationRequest",
                        "id": "med-uuid-2",
                        "status": "active",
                        "intent": "order",
                        "medicationCodeableConcept": {
                            "text": "Lisinopril 10mg",
                        },
                    },
                }],
            },
            {"entry": []},
        ]
        item = ManifestItem(
            resource_type="MedicationRequest",
            action=ManifestAction.CREATE,
            proposed_value={"drug": "Lisinopril"},
            source_reference="Encounter/1",
            description="Add lisinopril",
        )
        results = await check_medication_safety(item, mock_openemr_client, "patient-123")
        dup = [r for r in results if r.check_name == "medication_duplicate"]
        assert len(dup) == 1, (
            "Duplicate check must detect 'Lisinopril' in medicationCodeableConcept.text"
        )
        assert dup[0].passed is False


class TestAllergyConflictRealisticFHIR:
    """Allergy cross-check against realistic OpenEMR FHIR AllergyIntolerance."""

    @pytest.mark.asyncio
    async def test_allergy_conflict_with_real_fhir_shape(self, mock_openemr_client):
        """OpenEMR returns allergen in code.coding[].display, not flat 'substance'."""
        mock_openemr_client.fhir_read.side_effect = [
            {"entry": []},  # MedicationRequest — empty
            {  # AllergyIntolerance bundle — realistic OpenEMR shape
                "resourceType": "Bundle",
                "total": 1,
                "entry": [{
                    "resource": {
                        "resourceType": "AllergyIntolerance",
                        "id": "allergy-uuid-1",
                        "clinicalStatus": {
                            "coding": [{"code": "active"}],
                        },
                        "category": ["medication"],
                        "criticality": "high",
                        "code": {
                            "coding": [{
                                "system": "http://snomed.info/sct",
                                "code": "372687004",
                                "display": "Amoxicillin",
                            }],
                        },
                        "patient": {"reference": "Patient/patient-123"},
                    },
                }],
            },
        ]
        item = ManifestItem(
            resource_type="MedicationRequest",
            action=ManifestAction.CREATE,
            proposed_value={"drug": "Amoxicillin 500mg"},
            source_reference="Encounter/1",
            description="Add amoxicillin",
        )
        results = await check_medication_safety(item, mock_openemr_client, "patient-123")
        allergy = [r for r in results if r.check_name == "medication_allergy_conflict"]
        assert len(allergy) == 1, (
            "Allergy check must detect 'Amoxicillin' in code.coding[].display"
        )
        assert allergy[0].passed is False

    @pytest.mark.asyncio
    async def test_allergy_conflict_no_coding_only_narrative(self, mock_openemr_client):
        """When code has no coding entries, OpenEMR uses data-absent-unknown.

        In this case the allergy title is only in text.div (narrative HTML).
        The check should gracefully handle this without crashing, even if
        it can't detect the conflict.
        """
        mock_openemr_client.fhir_read.side_effect = [
            {"entry": []},
            {
                "resourceType": "Bundle",
                "total": 1,
                "entry": [{
                    "resource": {
                        "resourceType": "AllergyIntolerance",
                        "id": "allergy-uuid-2",
                        "code": {
                            "coding": [{
                                "system": "http://terminology.hl7.org/CodeSystem/data-absent-reason",
                                "code": "unknown",
                                "display": "Unknown",
                            }],
                        },
                        "text": {
                            "status": "additional",
                            "div": "<div>Penicillin</div>",
                        },
                        "patient": {"reference": "Patient/patient-123"},
                    },
                }],
            },
        ]
        item = ManifestItem(
            resource_type="MedicationRequest",
            action=ManifestAction.CREATE,
            proposed_value={"drug": "Penicillin V 500mg"},
            source_reference="Encounter/1",
            description="Add penicillin",
        )
        # Should not crash regardless of whether it detects the conflict
        results = await check_medication_safety(item, mock_openemr_client, "patient-123")
        assert isinstance(results, list)


# ------------------------------------------------------------------
# _extract_medication_name helper
# ------------------------------------------------------------------


class TestExtractMedicationName:
    def test_returns_coding_display(self) -> None:
        resource = {
            "medicationCodeableConcept": {
                "coding": [{"display": "Metformin 500mg", "system": "http://rxnav.nlm.nih.gov/"}]
            }
        }
        assert _extract_medication_name(resource) == "Metformin 500mg"

    def test_falls_back_to_text(self) -> None:
        resource = {
            "medicationCodeableConcept": {
                "coding": [{"system": "http://rxnav.nlm.nih.gov/"}],  # no display
                "text": "Metformin 500mg oral tablet",
            }
        }
        assert _extract_medication_name(resource) == "Metformin 500mg oral tablet"

    def test_falls_back_to_drug_field(self) -> None:
        resource = {"drug": "Lisinopril"}
        assert _extract_medication_name(resource) == "Lisinopril"

    def test_falls_back_to_title_field(self) -> None:
        resource = {"title": "Atorvastatin 20mg"}
        assert _extract_medication_name(resource) == "Atorvastatin 20mg"

    def test_falls_back_to_display_field(self) -> None:
        resource = {"display": "Aspirin 81mg"}
        assert _extract_medication_name(resource) == "Aspirin 81mg"

    def test_returns_empty_string_for_unknown(self) -> None:
        resource = {}
        assert _extract_medication_name(resource) == ""

    def test_skips_coding_without_display(self) -> None:
        """Coding entries with no display field are skipped."""
        resource = {
            "medicationCodeableConcept": {
                "coding": [{"code": "860975", "system": "http://rxnav.nlm.nih.gov/"}],
                "text": "Metformin",
            }
        }
        assert _extract_medication_name(resource) == "Metformin"


# ------------------------------------------------------------------
# _extract_allergen_name helper
# ------------------------------------------------------------------


class TestExtractAllergenName:
    def test_returns_coding_display(self) -> None:
        resource = {
            "code": {
                "coding": [{"display": "Penicillin G"}]
            }
        }
        assert _extract_allergen_name(resource) == "Penicillin G"

    def test_skips_unknown_display(self) -> None:
        """'unknown' display is skipped and falls back to text."""
        resource = {
            "code": {
                "coding": [{"display": "Unknown"}],
                "text": "Amoxicillin",
            }
        }
        assert _extract_allergen_name(resource) == "Amoxicillin"

    def test_falls_back_to_text(self) -> None:
        resource = {
            "code": {
                "coding": [],
                "text": "Sulfonamides",
            }
        }
        assert _extract_allergen_name(resource) == "Sulfonamides"

    def test_falls_back_to_substance_field(self) -> None:
        resource = {"substance": "Latex"}
        assert _extract_allergen_name(resource) == "Latex"

    def test_falls_back_to_display_field(self) -> None:
        resource = {"display": "NSAIDs"}
        assert _extract_allergen_name(resource) == "NSAIDs"

    def test_returns_empty_string_for_unknown(self) -> None:
        resource = {}
        assert _extract_allergen_name(resource) == ""

    def test_code_not_a_dict(self) -> None:
        """If 'code' is not a dict, fall back to substance/display."""
        resource = {"code": "Penicillin", "substance": "penicillin_fallback"}
        assert _extract_allergen_name(resource) == "penicillin_fallback"


# ------------------------------------------------------------------
# _extract_code edge cases
# ------------------------------------------------------------------


class TestExtractCodeExtended:
    def test_string_passthrough(self) -> None:
        assert _extract_code("E11.9") == "E11.9"

    def test_none_returns_none(self) -> None:
        assert _extract_code(None) is None

    def test_integer_returns_none(self) -> None:
        assert _extract_code(42) is None

    def test_coding_list_first_code_returned(self) -> None:
        assert _extract_code({"coding": [{"code": "I10"}, {"code": "I11"}]}) == "I10"

    def test_coding_list_with_non_dict_entries_skipped(self) -> None:
        assert _extract_code({"coding": ["not-a-dict", {"code": "E11.9"}]}) == "E11.9"

    def test_empty_dict_returns_none(self) -> None:
        assert _extract_code({}) is None


# ---------------------------------------------------------------------------
# Additional grounding edge cases
# ---------------------------------------------------------------------------


class TestGroundingCheckMalformedData:
    @pytest.mark.asyncio
    async def test_grounding_non_dict_list_response_returns_malformed(self):
        """check_grounding returns malformed when fhir_read returns a list."""
        item = ManifestItem(
            resource_type="Condition",
            action=ManifestAction.CREATE,
            proposed_value={},
            source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
            description="Need encounter",
        )
        client = AsyncMock()
        client.fhir_read.return_value = []  # list instead of dict

        result = await check_grounding(item, client)

        assert result.passed is False
        assert "malformed data" in result.message

    @pytest.mark.asyncio
    async def test_grounding_non_dict_string_response_returns_malformed(self):
        """check_grounding returns malformed when fhir_read returns a string."""
        item = ManifestItem(
            resource_type="Condition",
            action=ManifestAction.CREATE,
            proposed_value={},
            source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
            description="Need encounter",
        )
        client = AsyncMock()
        client.fhir_read.return_value = "not a dict"

        result = await check_grounding(item, client)

        assert result.passed is False
        assert "malformed data" in result.message
        assert "Encounter/" in result.message

    @pytest.mark.asyncio
    async def test_grounding_non_dict_none_response_returns_malformed(self):
        """check_grounding returns malformed when fhir_read returns None."""
        item = ManifestItem(
            resource_type="Condition",
            action=ManifestAction.CREATE,
            proposed_value={},
            source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
            description="Need encounter",
        )
        client = AsyncMock()
        client.fhir_read.return_value = None

        result = await check_grounding(item, client)

        assert result.passed is False
        assert "malformed data" in result.message


# ---------------------------------------------------------------------------
# Conflict check exception path
# ---------------------------------------------------------------------------


class TestConflictCheckException:
    @pytest.mark.asyncio
    async def test_conflict_exception_returns_failed_with_message(self):
        """check_conflict returns failed when fhir_read raises an exception."""
        item = ManifestItem(
            resource_type="Condition",
            action=ManifestAction.UPDATE,
            proposed_value={"code": "I10"},
            current_value={"resourceType": "Condition", "code": "E11.9"},
            source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
            description="Update condition",
            target_resource_id=CONDITION_FHIR_UUID,
        )
        client = AsyncMock()
        client.fhir_read.side_effect = ConnectionError("network timeout")

        result = await check_conflict(item, client)

        assert result.passed is False
        assert "Failed to re-read" in result.message
        assert "network timeout" in result.message

    @pytest.mark.asyncio
    async def test_conflict_valueerror_also_caught(self):
        """check_conflict catches ValueError as well."""
        item = ManifestItem(
            resource_type="Condition",
            action=ManifestAction.UPDATE,
            proposed_value={"code": "I10"},
            current_value={"resourceType": "Condition"},
            source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
            description="Update condition",
            target_resource_id=CONDITION_FHIR_UUID,
        )
        client = AsyncMock()
        client.fhir_read.side_effect = ValueError("bad value")

        result = await check_conflict(item, client)

        assert result.passed is False
        assert "Failed to re-read" in result.message


# ---------------------------------------------------------------------------
# _normalize_for_conflict — non-dict meta
# ---------------------------------------------------------------------------


class TestNormalizeForConflictEdgeCases:
    def test_meta_not_a_dict_left_unchanged(self):
        """When meta is a string, it should be left as-is (not processed)."""
        resource = {
            "id": "some-id",
            "meta": "not-a-dict",
            "code": "E11.9",
        }
        normalized = _normalize_for_conflict(resource)
        # Non-dict meta is left unchanged
        assert normalized["meta"] == "not-a-dict"
        assert normalized["code"] == "E11.9"

    def test_meta_is_list_left_unchanged(self):
        """When meta is a list (malformed), it is left unchanged."""
        resource = {
            "id": "some-id",
            "meta": [{"versionId": "1"}],
        }
        normalized = _normalize_for_conflict(resource)
        assert normalized["meta"] == [{"versionId": "1"}]

    def test_no_meta_key_unchanged(self):
        """Resources without meta field are returned with other fields intact."""
        resource = {"id": "abc", "code": "I10"}
        normalized = _normalize_for_conflict(resource)
        assert "meta" not in normalized
        assert normalized["code"] == "I10"

    def test_original_resource_not_mutated(self):
        """_normalize_for_conflict must not mutate the original dict."""
        resource = {
            "id": "x",
            "meta": {"versionId": "3", "lastUpdated": "2024-01-01"},
        }
        original_meta = resource["meta"].copy()
        _normalize_for_conflict(resource)
        # Original should still have its keys
        assert resource["meta"] == original_meta


# ---------------------------------------------------------------------------
# verify_manifest — empty manifest
# ---------------------------------------------------------------------------


class TestVerifyManifestEmpty:
    @pytest.mark.asyncio
    async def test_empty_manifest_produces_empty_results(self, mock_openemr_client):
        """Manifest with no items produces an empty verification report."""
        manifest = ChangeManifest(
            patient_id=CONDITION_FHIR_UUID,
            items=[],
        )
        report = await verify_manifest(manifest, mock_openemr_client)

        assert report.manifest_id == manifest.id
        assert report.results == []
        assert report.passed is True
        assert report.warnings == []

    @pytest.mark.asyncio
    async def test_multiple_items_produce_results_for_each(self, mock_openemr_client):
        """Each item generates grounding, confidence, and conflict check results."""
        items = [
            ManifestItem(
                resource_type="Condition",
                action=ManifestAction.CREATE,
                proposed_value={"code": "E11.9"},
                source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
                description="Add diabetes",
            ),
            ManifestItem(
                resource_type="Condition",
                action=ManifestAction.CREATE,
                proposed_value={"code": "I10"},
                source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
                description="Add hypertension",
            ),
        ]
        manifest = ChangeManifest(patient_id=CONDITION_FHIR_UUID, items=items)
        report = await verify_manifest(manifest, mock_openemr_client)

        # Each item produces at minimum grounding + confidence + conflict results
        item_ids = {r.item_id for r in report.results}
        assert item_ids == {items[0].id, items[1].id}
