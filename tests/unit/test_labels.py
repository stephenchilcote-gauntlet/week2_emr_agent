"""Unit tests for src/agent/labels.py — UUID ↔ word-id encoding."""

from __future__ import annotations

import uuid

import pytest

from src.agent.labels import (
    is_uuid,
    is_word_id,
    replace_uuids_with_words,
    replace_words_with_uuids,
    resolve_identifier,
    resolve_reference,
    uuid_to_words,
    words_to_uuid,
)

# A known UUID and its word encoding for deterministic assertions
_KNOWN_UUID = "bbb13f7a-966e-4c7c-aea5-4bac3ce98505"


# ---------------------------------------------------------------------------
# is_uuid
# ---------------------------------------------------------------------------


class TestIsUuid:
    def test_valid_lowercase_uuid(self) -> None:
        assert is_uuid("bbb13f7a-966e-4c7c-aea5-4bac3ce98505")

    def test_valid_uppercase_uuid(self) -> None:
        assert is_uuid("BBB13F7A-966E-4C7C-AEA5-4BAC3CE98505")

    def test_valid_no_dashes(self) -> None:
        assert is_uuid("bbb13f7a966e4c7caea54bac3ce98505")

    def test_random_uuid(self) -> None:
        assert is_uuid(str(uuid.uuid4()))

    def test_plain_integer(self) -> None:
        assert not is_uuid("42")

    def test_empty_string(self) -> None:
        assert not is_uuid("")

    def test_partial_uuid(self) -> None:
        assert not is_uuid("bbb13f7a-966e-4c7c")

    def test_word_id_is_not_uuid(self) -> None:
        words = uuid_to_words(_KNOWN_UUID)
        assert not is_uuid(words)

    def test_strips_whitespace(self) -> None:
        assert is_uuid(f"  {_KNOWN_UUID}  ")


# ---------------------------------------------------------------------------
# is_word_id
# ---------------------------------------------------------------------------


class TestIsWordId:
    def test_valid_word_id(self) -> None:
        words = uuid_to_words(_KNOWN_UUID)
        assert is_word_id(words)

    def test_nine_words_not_word_id(self) -> None:
        words = uuid_to_words(_KNOWN_UUID).split()[:9]
        assert not is_word_id(" ".join(words))

    def test_eleven_words_not_word_id(self) -> None:
        words = uuid_to_words(_KNOWN_UUID) + " extra"
        assert not is_word_id(words)

    def test_uuid_is_not_word_id(self) -> None:
        assert not is_word_id(_KNOWN_UUID)

    def test_empty_string(self) -> None:
        assert not is_word_id("")

    def test_ten_invalid_words_not_word_id(self) -> None:
        # 10 words not from the wordlist
        assert not is_word_id("xxx yyy zzz aaa bbb ccc ddd eee fff ggg")

    def test_strips_leading_whitespace(self) -> None:
        words = "  " + uuid_to_words(_KNOWN_UUID) + "  "
        assert is_word_id(words)


# ---------------------------------------------------------------------------
# uuid_to_words / words_to_uuid round-trip
# ---------------------------------------------------------------------------


class TestUuidWordsRoundTrip:
    def test_encode_decode_known_uuid(self) -> None:
        words = uuid_to_words(_KNOWN_UUID)
        assert words_to_uuid(words) == _KNOWN_UUID

    def test_random_uuid_round_trips(self) -> None:
        u = str(uuid.uuid4())
        assert words_to_uuid(uuid_to_words(u)) == u

    def test_nil_uuid_round_trips(self) -> None:
        nil = "00000000-0000-0000-0000-000000000000"
        assert words_to_uuid(uuid_to_words(nil)) == nil

    def test_max_uuid_round_trips(self) -> None:
        max_uuid = "ffffffff-ffff-ffff-ffff-ffffffffffff"
        assert words_to_uuid(uuid_to_words(max_uuid)) == max_uuid

    def test_produces_exactly_10_words(self) -> None:
        words = uuid_to_words(_KNOWN_UUID)
        assert len(words.split()) == 10

    def test_different_uuids_produce_different_words(self) -> None:
        u1 = str(uuid.uuid4())
        u2 = str(uuid.uuid4())
        assert uuid_to_words(u1) != uuid_to_words(u2)


# ---------------------------------------------------------------------------
# replace_uuids_with_words
# ---------------------------------------------------------------------------


class TestReplaceUuidsWithWords:
    def test_replaces_single_uuid_in_text(self) -> None:
        text = f"Patient ID is {_KNOWN_UUID} and is active"
        result = replace_uuids_with_words(text)
        assert _KNOWN_UUID not in result
        assert "Patient ID is" in result
        assert "and is active" in result

    def test_text_with_no_uuid_unchanged(self) -> None:
        text = "No identifiers here"
        assert replace_uuids_with_words(text) == text

    def test_replaces_multiple_uuids(self) -> None:
        u1 = str(uuid.uuid4())
        u2 = str(uuid.uuid4())
        text = f"{u1} and {u2}"
        result = replace_uuids_with_words(text)
        assert u1 not in result
        assert u2 not in result
        # Result should have 20 words for the two UUIDs plus "and"
        assert len(result.split()) == 21

    def test_uuid_in_fhir_reference(self) -> None:
        text = f"Condition/{_KNOWN_UUID}"
        result = replace_uuids_with_words(text)
        assert _KNOWN_UUID not in result
        # Resource type prefix is preserved
        assert "Condition/" in result


# ---------------------------------------------------------------------------
# replace_words_with_uuids
# ---------------------------------------------------------------------------


class TestReplaceWordsWithUuids:
    def test_replaces_word_id_back_to_uuid(self) -> None:
        words = uuid_to_words(_KNOWN_UUID)
        result = replace_words_with_uuids(words)
        assert result == _KNOWN_UUID

    def test_text_with_no_word_id_unchanged(self) -> None:
        text = "No word IDs here at all"
        assert replace_words_with_uuids(text) == text

    def test_round_trip_through_both_replacements(self) -> None:
        original = f"Resource {_KNOWN_UUID} found"
        encoded = replace_uuids_with_words(original)
        decoded = replace_words_with_uuids(encoded)
        assert decoded == original


# ---------------------------------------------------------------------------
# resolve_identifier
# ---------------------------------------------------------------------------


class TestResolveIdentifier:
    def test_passthrough_raw_uuid(self) -> None:
        assert resolve_identifier(_KNOWN_UUID) == _KNOWN_UUID

    def test_decodes_word_id_to_uuid(self) -> None:
        words = uuid_to_words(_KNOWN_UUID)
        assert resolve_identifier(words) == _KNOWN_UUID

    def test_passthrough_plain_string(self) -> None:
        assert resolve_identifier("42") == "42"

    def test_strips_whitespace(self) -> None:
        assert resolve_identifier(f"  {_KNOWN_UUID}  ") == _KNOWN_UUID

    def test_empty_string_passthrough(self) -> None:
        assert resolve_identifier("") == ""


# ---------------------------------------------------------------------------
# resolve_reference
# ---------------------------------------------------------------------------


class TestResolveReference:
    def test_resolves_fhir_style_word_id(self) -> None:
        words = uuid_to_words(_KNOWN_UUID)
        ref = f"Condition/{words}"
        result = resolve_reference(ref)
        assert result == f"Condition/{_KNOWN_UUID}"

    def test_passthrough_fhir_style_uuid(self) -> None:
        ref = f"Patient/{_KNOWN_UUID}"
        assert resolve_reference(ref) == ref

    def test_no_slash_delegates_to_resolve_identifier(self) -> None:
        assert resolve_reference(_KNOWN_UUID) == _KNOWN_UUID

    def test_preserves_resource_type(self) -> None:
        ref = f"MedicationRequest/{_KNOWN_UUID}"
        result = resolve_reference(ref)
        assert result.startswith("MedicationRequest/")

    def test_plain_string_passthrough(self) -> None:
        assert resolve_reference("plain-reference") == "plain-reference"
