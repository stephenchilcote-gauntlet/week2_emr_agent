from __future__ import annotations

import pytest

from src.agent.dsl import (
    DslItem,
    _TYPE_ALIASES,
    _resolve_type,
    _sanitize_xml,
    parse_manifest_dsl,
)


class TestSingleAdd:
    def test_add_condition_all_fields(self):
        xml = (
            '<add type="Condition" code="E11.9" display="Type 2 diabetes mellitus"'
            ' onset="2024-01-15" src="Encounter/5" id="item-1">'
            "Add Type 2 diabetes diagnosis based on HbA1c results"
            "</add>"
        )
        items = parse_manifest_dsl(xml)
        assert len(items) == 1
        item = items[0]
        assert item.action == "add"
        assert item.resource_type == "Condition"
        assert item.description == "Add Type 2 diabetes diagnosis based on HbA1c results"
        assert item.source_reference == "Encounter/5"
        assert item.item_id == "item-1"
        assert item.confidence == "high"
        assert item.depends_on == []
        assert item.ref is None
        assert item.attrs["code"] == "E11.9"
        assert item.attrs["display"] == "Type 2 diabetes mellitus"
        assert item.attrs["onset"] == "2024-01-15"
        # src, id, type are consumed — not in attrs
        assert "src" not in item.attrs
        assert "id" not in item.attrs
        assert "type" not in item.attrs


class TestSingleEdit:
    def test_edit_medication_request(self):
        xml = (
            '<edit ref="MedicationRequest/123" dose="1000mg BID" src="Encounter/5">'
            "Increase metformin dosage"
            "</edit>"
        )
        items = parse_manifest_dsl(xml)
        assert len(items) == 1
        item = items[0]
        assert item.action == "edit"
        assert item.resource_type == "MedicationRequest"
        assert item.ref == "MedicationRequest/123"
        assert item.description == "Increase metformin dosage"
        assert item.source_reference == "Encounter/5"
        assert item.attrs["dose"] == "1000mg BID"
        assert "ref" not in item.attrs


class TestSingleRemove:
    def test_remove_condition(self):
        xml = (
            '<remove ref="Condition/456" src="Encounter/5">'
            "Remove resolved URI from active problem list"
            "</remove>"
        )
        items = parse_manifest_dsl(xml)
        assert len(items) == 1
        item = items[0]
        assert item.action == "remove"
        assert item.resource_type == "Condition"
        assert item.ref == "Condition/456"
        assert item.source_reference == "Encounter/5"
        assert item.description == "Remove resolved URI from active problem list"


class TestMultipleElements:
    def test_three_items_in_order(self):
        xml = (
            '<add type="Condition" code="E11.9" src="Encounter/5" id="i1">'
            "Add diabetes"
            "</add>"
            '<edit ref="MedicationRequest/10" dose="500mg" src="Encounter/5" id="i2">'
            "Change dose"
            "</edit>"
            '<remove ref="Condition/99" src="Encounter/5" id="i3">'
            "Remove old condition"
            "</remove>"
        )
        items = parse_manifest_dsl(xml)
        assert len(items) == 3
        assert items[0].action == "add"
        assert items[0].item_id == "i1"
        assert items[1].action == "edit"
        assert items[1].item_id == "i2"
        assert items[2].action == "remove"
        assert items[2].item_id == "i3"


class TestManifestWrapper:
    def test_manifest_wrapper_handled(self):
        xml = (
            '<manifest patient="1">'
            '<add type="Condition" code="J06.9" src="Encounter/1" id="m1">'
            "Add URI"
            "</add>"
            "</manifest>"
        )
        items = parse_manifest_dsl(xml)
        assert len(items) == 1
        assert items[0].action == "add"
        assert items[0].resource_type == "Condition"
        assert items[0].item_id == "m1"


class TestResourceTypeAliases:
    @pytest.mark.parametrize(
        "alias,expected",
        [
            ("Cond", "Condition"),
            ("MedReq", "MedicationRequest"),
            ("Allergy", "AllergyIntolerance"),
            ("Obs", "Observation"),
            ("Doc", "DocumentReference"),
            ("Plan", "CarePlan"),
            ("Proc", "Procedure"),
            ("Enc", "Encounter"),
            ("Imm", "Immunization"),
            ("DiagReport", "DiagnosticReport"),
            ("Med", "MedicationRequest"),
            ("Medication", "MedicationRequest"),
            ("Document", "DocumentReference"),
            ("Patient", "Patient"),
        ],
    )
    def test_alias_resolved(self, alias: str, expected: str):
        xml = (
            f'<add type="{alias}" src="Encounter/1">'
            "Description"
            "</add>"
        )
        items = parse_manifest_dsl(xml)
        assert items[0].resource_type == expected

    def test_all_aliases_in_map(self):
        """Every alias in _TYPE_ALIASES resolves to a known FHIR type."""
        known_types = {
            "Condition",
            "MedicationRequest",
            "AllergyIntolerance",
            "Observation",
            "DocumentReference",
            "CarePlan",
            "Procedure",
            "Encounter",
            "Immunization",
            "DiagnosticReport",
            "Patient",
            "ServiceRequest",
            "SoapNote",
            "Vital",
            # Newer supported types:
            "Appointment",
            "Referral",
            "Surgery",
        }
        for alias, resolved in _TYPE_ALIASES.items():
            assert resolved in known_types, f"Alias '{alias}' -> '{resolved}' unknown"


class TestConfidenceAndDeps:
    def test_conf_and_deps_parsed(self):
        xml = (
            '<add type="Condition" src="Encounter/1" conf="medium"'
            ' deps="item-1,item-2" id="item-3">'
            "Some description"
            "</add>"
        )
        items = parse_manifest_dsl(xml)
        item = items[0]
        assert item.confidence == "medium"
        assert item.depends_on == ["item-1", "item-2"]
        assert item.item_id == "item-3"

    def test_deps_whitespace_handling(self):
        xml = (
            '<add type="Condition" src="Encounter/1" deps="a , b , c">'
            "Desc"
            "</add>"
        )
        items = parse_manifest_dsl(xml)
        assert items[0].depends_on == ["a", "b", "c"]


class TestDefaultValues:
    def test_defaults_when_omitted(self):
        xml = '<add type="Condition" src="Encounter/1">Desc</add>'
        items = parse_manifest_dsl(xml)
        item = items[0]
        assert item.confidence == "high"
        assert item.depends_on == []
        assert item.item_id  # auto-generated, non-empty
        assert len(item.item_id) > 0


class TestBareAmpersandSanitization:
    def test_bare_ampersand_in_text(self):
        xml = '<add type="Condition" src="Encounter/1">Valid &amp; Active</add>'
        items = parse_manifest_dsl(xml)
        assert items[0].description == "Valid & Active"

    def test_bare_ampersand_not_escaped(self):
        xml = '<add type="Condition" src="Encounter/1">Valid & Active</add>'
        items = parse_manifest_dsl(xml)
        assert items[0].description == "Valid & Active"

    def test_existing_entities_preserved(self):
        xml = '<add type="Condition" src="Encounter/1">A &lt; B &amp; C</add>'
        items = parse_manifest_dsl(xml)
        assert items[0].description == "A < B & C"


class TestEmptyInput:
    def test_empty_string(self):
        assert parse_manifest_dsl("") == []

    def test_whitespace_only(self):
        assert parse_manifest_dsl("   \n\t  ") == []


class TestInvalidXml:
    def test_malformed_xml_raises(self):
        with pytest.raises(ValueError, match="Invalid manifest DSL"):
            parse_manifest_dsl("<add type='Condition'>unclosed")

    def test_completely_broken_xml(self):
        with pytest.raises(ValueError, match="Invalid manifest DSL"):
            parse_manifest_dsl("<<<not xml at all>>>")


class TestUnknownElement:
    def test_unknown_tag_raises(self):
        with pytest.raises(ValueError, match="Unknown DSL element"):
            parse_manifest_dsl('<unknown src="Encounter/1">text</unknown>')

    def test_mixed_valid_and_unknown_raises(self):
        xml = (
            '<add type="Condition" src="Encounter/1">Ok</add>'
            '<bogus src="Encounter/1">Bad</bogus>'
        )
        with pytest.raises(ValueError, match="Unknown DSL element"):
            parse_manifest_dsl(xml)


class TestMissingRequiredAttrs:
    def test_add_without_type_raises(self):
        with pytest.raises(ValueError, match="missing required 'type'"):
            parse_manifest_dsl('<add src="Encounter/1">No type</add>')

    def test_edit_without_ref_raises(self):
        with pytest.raises(ValueError, match="missing required 'ref'"):
            parse_manifest_dsl('<edit src="Encounter/1">No ref</edit>')

    def test_remove_without_ref_raises(self):
        with pytest.raises(ValueError, match="missing required 'ref'"):
            parse_manifest_dsl('<remove src="Encounter/1">No ref</remove>')


class TestMultilineContent:
    def test_multiline_description(self):
        xml = (
            '<add type="Condition" code="E11.9" src="Encounter/5">\n'
            "    Add Type 2 diabetes diagnosis\n"
            "    based on HbA1c results from lab report\n"
            "</add>"
        )
        items = parse_manifest_dsl(xml)
        assert "Add Type 2 diabetes diagnosis" in items[0].description
        assert "based on HbA1c results" in items[0].description


class TestRefResourceTypeResolution:
    def test_edit_ref_resolves_alias(self):
        xml = '<edit ref="Cond/456" dose="10mg" src="Encounter/1">Fix</edit>'
        items = parse_manifest_dsl(xml)
        assert items[0].resource_type == "Condition"
        assert items[0].ref == "Cond/456"

    def test_remove_ref_resolves_alias(self):
        xml = '<remove ref="MedReq/789" src="Encounter/1">Remove</remove>'
        items = parse_manifest_dsl(xml)
        assert items[0].resource_type == "MedicationRequest"
        assert items[0].ref == "MedReq/789"


class TestDslItemDataclass:
    def test_dataclass_fields(self):
        item = DslItem(
            action="add",
            resource_type="Condition",
            description="Test",
            source_reference="Encounter/1",
            item_id="id-1",
        )
        assert item.confidence == "high"
        assert item.depends_on == []
        assert item.ref is None
        assert item.attrs == {}


# ---------------------------------------------------------------------------
# _resolve_type — direct tests
# ---------------------------------------------------------------------------


class TestResolveType:
    def test_known_alias_lowercase(self):
        assert _resolve_type("condition") == "Condition"

    def test_known_alias_uppercase_input(self):
        """_resolve_type lowercases the input before lookup."""
        assert _resolve_type("CONDITION") == "Condition"

    def test_soapnote_alias(self):
        assert _resolve_type("soap") == "SoapNote"
        assert _resolve_type("note") == "SoapNote"
        assert _resolve_type("soapnote") == "SoapNote"

    def test_surgery_aliases(self):
        assert _resolve_type("surgery") == "Surgery"
        assert _resolve_type("surg") == "Surgery"

    def test_referral_aliases(self):
        assert _resolve_type("referral") == "Referral"
        assert _resolve_type("transaction") == "Referral"

    def test_vital_aliases(self):
        assert _resolve_type("vital") == "Vital"
        assert _resolve_type("vitals") == "Vital"
        assert _resolve_type("vitalsigns") == "Vital"

    def test_unknown_type_returned_unchanged(self):
        """Unknown type string is returned as-is (no lowercasing)."""
        assert _resolve_type("UnknownType") == "UnknownType"

    def test_appointment_aliases(self):
        assert _resolve_type("appt") == "Appointment"
        assert _resolve_type("scheduling") == "Appointment"

    def test_immunization_aliases(self):
        assert _resolve_type("imm") == "Immunization"
        assert _resolve_type("immunization") == "Immunization"


# ---------------------------------------------------------------------------
# _sanitize_xml — direct tests
# ---------------------------------------------------------------------------


class TestSanitizeXml:
    def test_bare_ampersand_in_text(self):
        raw = '<add src="X">salt & pepper</add>'
        result = _sanitize_xml(raw)
        assert "&amp;" in result
        assert " & " not in result

    def test_already_escaped_amp_not_double_escaped(self):
        """&amp; in input must NOT become &amp;amp;."""
        raw = '<add src="X">salt &amp; pepper</add>'
        result = _sanitize_xml(raw)
        assert "&amp;amp;" not in result

    def test_lt_in_attribute_value_escaped(self):
        """< in an attribute value like assessment="A1c < 7%" is escaped."""
        raw = '<add type="Condition" note="A1c < 7%" src="X">Desc</add>'
        result = _sanitize_xml(raw)
        # The note attribute should now have &lt; not bare <
        import re
        # Find the note= attribute value
        m = re.search(r'note="([^"]*)"', result)
        assert m is not None
        assert "&lt;" in m.group(1)

    def test_gt_in_attribute_value_escaped(self):
        """< in an attribute value like threshold="A1c > 8%" is escaped."""
        raw = '<add type="Condition" note="A1c > 8%" src="X">Desc</add>'
        result = _sanitize_xml(raw)
        import re
        m = re.search(r'note="([^"]*)"', result)
        assert m is not None
        assert "&gt;" in m.group(1)

    def test_bare_lt_in_text_content_escaped(self):
        """< in text content (not inside a tag) is escaped."""
        raw = '<add src="X">Goal: A1c < 7%</add>'
        result = _sanitize_xml(raw)
        # The text "Goal: A1c < 7%" should have < escaped
        assert "A1c &lt; 7%" in result

    def test_empty_string_returns_empty(self):
        assert _sanitize_xml("") == ""

    def test_valid_xml_unchanged(self):
        """Proper XML with no bare special chars passes through cleanly."""
        raw = '<add type="Condition" src="Encounter/1">All good</add>'
        result = _sanitize_xml(raw)
        # No escaping needed; structure unchanged
        assert 'type="Condition"' in result
        assert ">All good<" in result

    def test_unicode_content_passes_through(self):
        """Unicode characters in text/attrs are not mangled."""
        raw = '<add src="X">Café résumé naïve</add>'
        result = _sanitize_xml(raw)
        assert "Café résumé naïve" in result

    def test_existing_entities_preserved(self):
        """&lt; and &gt; entities in input are not double-escaped."""
        raw = '<add src="X">A &lt; B</add>'
        result = _sanitize_xml(raw)
        # Should stay as &lt;, not become &amp;lt;
        assert "&amp;lt;" not in result
        assert "&lt;" in result

    def test_quot_entity_not_double_escaped(self):
        """&quot; is a valid XML entity and must NOT become &amp;quot;."""
        raw = '<add src="X">She said &quot;hello&quot;</add>'
        result = _sanitize_xml(raw)
        assert "&amp;quot;" not in result
        assert "&quot;" in result

    def test_apos_entity_not_double_escaped(self):
        """&apos; is a valid XML entity and must NOT become &amp;apos;."""
        raw = "<add src=\"X\">it&apos;s fine</add>"
        result = _sanitize_xml(raw)
        assert "&amp;apos;" not in result
        assert "&apos;" in result

    def test_numeric_decimal_entity_not_double_escaped(self):
        """&#123; is a valid numeric entity and must NOT become &amp;#123;."""
        raw = '<add src="X">char &#123;</add>'
        result = _sanitize_xml(raw)
        assert "&amp;#123;" not in result
        assert "&#123;" in result

    def test_numeric_hex_entity_not_double_escaped(self):
        """&#xAB; is a valid hex entity and must NOT become &amp;#xAB;."""
        raw = '<add src="X">char &#xAB;</add>'
        result = _sanitize_xml(raw)
        assert "&amp;#xAB;" not in result
        assert "&#xAB;" in result

    def test_lt_while_already_in_tag_is_escaped(self):
        """A bare < that appears while we're already parsing a tag gets escaped."""
        # Input: <add< src="X">text</add>
        # After first pass (& escaping) — unchanged.
        # Third pass: first < → in_tag=True; second < while in_tag → &lt;
        raw = '<add< src="X">text</add>'
        result = _sanitize_xml(raw)
        # The result should have &lt; somewhere from the malformed second <
        assert "&lt;" in result
        # The tag still starts with <add
        assert result.startswith("<add")

    def test_bare_lt_followed_by_digit_is_escaped(self):
        """< followed by a digit in text content is escaped (not treated as a tag)."""
        raw = '<add src="X">value < 100 mg/dL</add>'
        result = _sanitize_xml(raw)
        assert "&lt;" in result
        # The original text structure is preserved (modulo escaping)
        assert "100 mg/dL" in result
