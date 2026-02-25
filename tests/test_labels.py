"""Tests for UUID → 3-word label mapping.

Ported from CollabBoard's src/ai/labels.test.js.
Verifies identical behavior to the JS implementation.
"""

from __future__ import annotations

import uuid

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.agent.labels import (
    LabelRegistry,
    is_label,
    is_uuid,
    uuid_to_label,
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

st_uuid = st.from_type(uuid.UUID).map(str)


# ---------------------------------------------------------------------------
# uuid_to_label properties (matching CollabBoard's PBT suite)
# ---------------------------------------------------------------------------


class TestUuidToLabelProperties:
    @given(st_uuid)
    def test_always_produces_exactly_3_words(self, u: str) -> None:
        parts = uuid_to_label(u).split(" ")
        assert len(parts) == 3

    @given(st_uuid)
    def test_every_word_is_lowercase_alphabetic(self, u: str) -> None:
        for word in uuid_to_label(u).split(" "):
            assert word.isalpha()
            assert word == word.lower()

    @given(st_uuid)
    def test_is_deterministic(self, u: str) -> None:
        assert uuid_to_label(u) == uuid_to_label(u)

    @given(st_uuid)
    def test_dashes_are_irrelevant(self, u: str) -> None:
        no_dashes = u.replace("-", "")
        assert uuid_to_label(u) == uuid_to_label(no_dashes)

    @given(st_uuid)
    def test_output_is_shorter_than_uuid(self, u: str) -> None:
        assert len(uuid_to_label(u)) < len(u)


class TestUuidToLabelKnownValues:
    def test_known_collision_pair_produces_same_label(self) -> None:
        a = "bbb13f7a-966e-4c7c-aea5-4bac3ce98505"
        b = "ef4f8cd0-25b9-4029-9316-0f2f3b069b34"
        assert uuid_to_label(a) == uuid_to_label(b)
        assert uuid_to_label(a) == "tango golf potato"

    def test_identical_uuids_produce_identical_labels(self) -> None:
        u = "deadbeef-dead-beef-dead-beefdeadbeef"
        assert uuid_to_label(u) == uuid_to_label(u)


# ---------------------------------------------------------------------------
# is_label / is_uuid
# ---------------------------------------------------------------------------


class TestPredicates:
    def test_is_label_true(self) -> None:
        assert is_label("tango golf potato")
        assert is_label("alpha bravo charlie")

    def test_is_label_false(self) -> None:
        assert not is_label("bbb13f7a-966e-4c7c-aea5-4bac3ce98505")
        assert not is_label("hello")
        assert not is_label("one two")
        assert not is_label("one two three four")
        assert not is_label("one 2 three")

    def test_is_uuid_true(self) -> None:
        assert is_uuid("bbb13f7a-966e-4c7c-aea5-4bac3ce98505")
        assert is_uuid("bbb13f7a966e4c7caea54bac3ce98505")

    def test_is_uuid_false(self) -> None:
        assert not is_uuid("tango golf potato")
        assert not is_uuid("not-a-uuid")


# ---------------------------------------------------------------------------
# LabelRegistry
# ---------------------------------------------------------------------------


class TestLabelRegistry:
    def test_register_and_resolve(self) -> None:
        reg = LabelRegistry()
        uid = "bbb13f7a-966e-4c7c-aea5-4bac3ce98505"
        label = reg.register(uid)
        assert label == "tango golf potato"

        result = reg.resolve("tango golf potato")
        assert result == {"ok": True, "uuid": uid}

    def test_resolve_raw_uuid(self) -> None:
        reg = LabelRegistry()
        uid = "bbb13f7a-966e-4c7c-aea5-4bac3ce98505"
        reg.register(uid)

        result = reg.resolve(uid)
        assert result["ok"] is True
        assert result["uuid"] == uid

    def test_collision_returns_error_with_matches(self) -> None:
        reg = LabelRegistry()
        a = "bbb13f7a-966e-4c7c-aea5-4bac3ce98505"
        b = "ef4f8cd0-25b9-4029-9316-0f2f3b069b34"
        reg.register(a)
        reg.register(b)

        result = reg.resolve("tango golf potato")
        assert result["ok"] is False
        assert "Multiple" in result["error"]
        assert set(result["matches"]) == {a, b}

    def test_collision_uuid_fallback_still_works(self) -> None:
        reg = LabelRegistry()
        a = "bbb13f7a-966e-4c7c-aea5-4bac3ce98505"
        b = "ef4f8cd0-25b9-4029-9316-0f2f3b069b34"
        reg.register(a)
        reg.register(b)

        result_a = reg.resolve(a)
        assert result_a == {"ok": True, "uuid": a}
        result_b = reg.resolve(b)
        assert result_b == {"ok": True, "uuid": b}

    def test_resolve_reference_with_label(self) -> None:
        reg = LabelRegistry()
        uid = "bbb13f7a-966e-4c7c-aea5-4bac3ce98505"
        reg.register(uid)

        result = reg.resolve_reference("Encounter/tango golf potato")
        assert result == {"ok": True, "reference": f"Encounter/{uid}"}

    def test_resolve_reference_with_uuid(self) -> None:
        reg = LabelRegistry()
        uid = "bbb13f7a-966e-4c7c-aea5-4bac3ce98505"
        reg.register(uid)

        result = reg.resolve_reference(f"Condition/{uid}")
        assert result == {"ok": True, "reference": f"Condition/{uid}"}

    def test_resolve_not_found(self) -> None:
        reg = LabelRegistry()
        result = reg.resolve("nonexistent label here")
        assert result["ok"] is False
        assert "not found" in result["error"]

    def test_register_bundle(self) -> None:
        reg = LabelRegistry()
        bundle = {
            "resourceType": "Bundle",
            "total": 2,
            "entry": [
                {
                    "resource": {
                        "resourceType": "Condition",
                        "id": "bbb13f7a-966e-4c7c-aea5-4bac3ce98505",
                    }
                },
                {
                    "resource": {
                        "resourceType": "Encounter",
                        "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                    }
                },
            ],
        }
        reg.register_bundle(bundle)
        assert len(reg) == 2
        assert reg.get_label("bbb13f7a-966e-4c7c-aea5-4bac3ce98505") == "tango golf potato"

    def test_register_idempotent(self) -> None:
        reg = LabelRegistry()
        uid = "bbb13f7a-966e-4c7c-aea5-4bac3ce98505"
        label1 = reg.register(uid)
        label2 = reg.register(uid)
        assert label1 == label2
        assert len(reg) == 1

    def test_format_context_table(self) -> None:
        reg = LabelRegistry()
        reg.register("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        table = reg.format_context_table()
        assert "Resource Labels" in table
        assert "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" in table

    def test_format_context_table_collision(self) -> None:
        reg = LabelRegistry()
        a = "bbb13f7a-966e-4c7c-aea5-4bac3ce98505"
        b = "ef4f8cd0-25b9-4029-9316-0f2f3b069b34"
        reg.register(a)
        reg.register(b)
        table = reg.format_context_table()
        assert "COLLISION" in table
        assert a in table
        assert b in table

    @given(st.lists(st_uuid, min_size=1, max_size=20))
    @settings(max_examples=50)
    def test_every_registered_uuid_has_valid_label(self, uuids: list[str]) -> None:
        reg = LabelRegistry()
        for u in uuids:
            label = reg.register(u)
            assert len(label.split(" ")) == 3

    @given(st_uuid)
    def test_round_trip_no_collision(self, u: str) -> None:
        reg = LabelRegistry()
        label = reg.register(u)
        result = reg.resolve(label)
        assert result["ok"] is True
        assert result["uuid"] == u
