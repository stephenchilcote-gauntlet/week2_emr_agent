"""Tests for bijective UUID ↔ word-encoded identifier mapping."""

from __future__ import annotations

import uuid

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.agent.labels import (
    WORDLIST,
    is_uuid,
    is_word_id,
    replace_uuids_with_words,
    resolve_identifier,
    resolve_reference,
    uuid_to_words,
    words_to_uuid,
)

st_uuid = st.from_type(uuid.UUID).map(str)


class TestUuidToWords:
    @given(st_uuid)
    def test_always_produces_10_words(self, u: str) -> None:
        parts = uuid_to_words(u).split()
        assert len(parts) == 10

    @given(st_uuid)
    def test_every_word_is_in_wordlist(self, u: str) -> None:
        for word in uuid_to_words(u).split():
            assert word in WORDLIST

    @given(st_uuid)
    def test_is_deterministic(self, u: str) -> None:
        assert uuid_to_words(u) == uuid_to_words(u)

    @given(st_uuid)
    def test_dashes_are_irrelevant(self, u: str) -> None:
        no_dashes = u.replace("-", "")
        assert uuid_to_words(u) == uuid_to_words(no_dashes)

    @given(st_uuid)
    def test_round_trip(self, u: str) -> None:
        words = uuid_to_words(u)
        assert words_to_uuid(words) == u

    @given(st_uuid)
    def test_is_bijective_no_collisions(self, u: str) -> None:
        words = uuid_to_words(u)
        reconstructed = words_to_uuid(words)
        assert reconstructed == u


class TestWordsToUuid:
    def test_rejects_wrong_word_count(self) -> None:
        with pytest.raises(ValueError, match="Expected 10"):
            words_to_uuid("one two three")

    def test_rejects_unknown_words(self) -> None:
        with pytest.raises(ValueError, match="not in wordlist"):
            words_to_uuid("zzzznotaword " * 10)


class TestIsWordId:
    def test_valid_word_id(self) -> None:
        u = "bbb13f7a-966e-4c7c-aea5-4bac3ce98505"
        word_id = uuid_to_words(u)
        assert is_word_id(word_id)

    def test_uuid_is_not_word_id(self) -> None:
        assert not is_word_id("bbb13f7a-966e-4c7c-aea5-4bac3ce98505")

    def test_short_string_is_not_word_id(self) -> None:
        assert not is_word_id("hello world")

    def test_random_words_not_in_list(self) -> None:
        assert not is_word_id("supercalifragilistic " * 10)


class TestIsUuid:
    def test_dashed_uuid(self) -> None:
        assert is_uuid("bbb13f7a-966e-4c7c-aea5-4bac3ce98505")

    def test_undashed_uuid(self) -> None:
        assert is_uuid("bbb13f7a966e4c7caea54bac3ce98505")

    def test_word_id_is_not_uuid(self) -> None:
        assert not is_uuid("tango golf potato")


class TestReplaceUuidsWithWords:
    def test_replaces_uuid_in_json(self) -> None:
        u = "bbb13f7a-966e-4c7c-aea5-4bac3ce98505"
        text = f'{{"id": "{u}", "name": "test"}}'
        result = replace_uuids_with_words(text)
        assert u not in result
        # The word-encoded version should be in the result
        expected_words = uuid_to_words(u)
        assert expected_words in result

    def test_replaces_multiple_uuids(self) -> None:
        u1 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        u2 = "11111111-2222-3333-4444-555555555555"
        text = f'"ref1": "{u1}", "ref2": "{u2}"'
        result = replace_uuids_with_words(text)
        assert u1 not in result
        assert u2 not in result

    def test_preserves_non_uuid_text(self) -> None:
        text = "hello world, no uuids here"
        assert replace_uuids_with_words(text) == text


class TestResolveIdentifier:
    def test_uuid_passthrough(self) -> None:
        u = "bbb13f7a-966e-4c7c-aea5-4bac3ce98505"
        assert resolve_identifier(u) == u

    def test_word_id_to_uuid(self) -> None:
        u = "bbb13f7a-966e-4c7c-aea5-4bac3ce98505"
        words = uuid_to_words(u)
        assert resolve_identifier(words) == u

    def test_unknown_identifier_passthrough(self) -> None:
        assert resolve_identifier("patient-1") == "patient-1"


class TestResolveReference:
    def test_fhir_reference_with_uuid(self) -> None:
        u = "bbb13f7a-966e-4c7c-aea5-4bac3ce98505"
        assert resolve_reference(f"Condition/{u}") == f"Condition/{u}"

    def test_fhir_reference_with_word_id(self) -> None:
        u = "bbb13f7a-966e-4c7c-aea5-4bac3ce98505"
        words = uuid_to_words(u)
        result = resolve_reference(f"Encounter/{words}")
        assert result == f"Encounter/{u}"

    def test_bare_uuid(self) -> None:
        u = "bbb13f7a-966e-4c7c-aea5-4bac3ce98505"
        assert resolve_reference(u) == u

    def test_bare_word_id(self) -> None:
        u = "bbb13f7a-966e-4c7c-aea5-4bac3ce98505"
        words = uuid_to_words(u)
        assert resolve_reference(words) == u


class TestBijectiveProperties:
    """Verify the encoding is truly bijective — distinct UUIDs always
    produce distinct word-IDs."""

    @given(st.lists(st_uuid, min_size=2, max_size=50, unique=True))
    @settings(max_examples=50)
    def test_no_collisions_in_batch(self, uuids: list[str]) -> None:
        word_ids = [uuid_to_words(u) for u in uuids]
        assert len(set(word_ids)) == len(uuids)

    def test_known_collision_pair_no_longer_collides(self) -> None:
        """The old 3-word system had collisions. The new system must not."""
        a = "bbb13f7a-966e-4c7c-aea5-4bac3ce98505"
        b = "ef4f8cd0-25b9-4029-9316-0f2f3b069b34"
        assert uuid_to_words(a) != uuid_to_words(b)
        assert words_to_uuid(uuid_to_words(a)) == a
        assert words_to_uuid(uuid_to_words(b)) == b
