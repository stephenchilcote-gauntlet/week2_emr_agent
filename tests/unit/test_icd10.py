"""Unit tests for src/verification/icd10.py — ICD-10 and CPT code validation."""

from __future__ import annotations

import pytest

from src.verification.icd10 import (
    COMMON_ICD10_CODES,
    ICD10_PREFIXES,
    validate_cpt_format,
    validate_icd10_format,
)


# ---------------------------------------------------------------------------
# validate_icd10_format — valid codes
# ---------------------------------------------------------------------------


class TestValidateIcd10FormatValid:
    def test_three_char_code_no_decimal(self) -> None:
        """I10 (hypertension) has no decimal portion."""
        assert validate_icd10_format("I10") is True

    def test_code_with_one_decimal_digit(self) -> None:
        assert validate_icd10_format("A00.1") is True

    def test_code_with_three_decimal_digits(self) -> None:
        assert validate_icd10_format("J45.909") is True

    def test_code_with_four_decimal_digits(self) -> None:
        assert validate_icd10_format("S06.1234") is True

    def test_e11_9_diabetes(self) -> None:
        assert validate_icd10_format("E11.9") is True

    def test_k21_0_gerd(self) -> None:
        assert validate_icd10_format("K21.0") is True

    def test_lowercase_accepted(self) -> None:
        """Lowercase input is uppercased internally before matching."""
        assert validate_icd10_format("e11.9") is True

    def test_leading_whitespace_stripped(self) -> None:
        assert validate_icd10_format("  I10") is True

    def test_trailing_whitespace_stripped(self) -> None:
        assert validate_icd10_format("I10  ") is True

    def test_all_letters_in_prefix_accepted(self) -> None:
        """All 26 capital letters are valid ICD-10 prefixes."""
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            code = f"{letter}00"
            assert validate_icd10_format(code) is True, f"Expected {code} to be valid"


# ---------------------------------------------------------------------------
# validate_icd10_format — invalid codes
# ---------------------------------------------------------------------------


class TestValidateIcd10FormatInvalid:
    def test_empty_string(self) -> None:
        assert validate_icd10_format("") is False

    def test_only_letter(self) -> None:
        assert validate_icd10_format("E") is False

    def test_two_digits_no_letter(self) -> None:
        assert validate_icd10_format("11.9") is False

    def test_five_decimal_digits_too_many(self) -> None:
        assert validate_icd10_format("E11.12345") is False

    def test_trailing_dot_no_decimal_digits(self) -> None:
        """E11. has a dot but no digits after it — invalid."""
        assert validate_icd10_format("E11.") is False

    def test_numeric_prefix(self) -> None:
        assert validate_icd10_format("1E1.9") is False

    def test_special_characters(self) -> None:
        assert validate_icd10_format("E11!9") is False

    def test_space_in_code(self) -> None:
        assert validate_icd10_format("E 11.9") is False

    def test_two_dots(self) -> None:
        assert validate_icd10_format("E11.9.1") is False


# ---------------------------------------------------------------------------
# validate_cpt_format — valid codes
# ---------------------------------------------------------------------------


class TestValidateCptFormatValid:
    def test_five_zeros(self) -> None:
        assert validate_cpt_format("00000") is True

    def test_five_nines(self) -> None:
        assert validate_cpt_format("99999") is True

    def test_random_five_digits(self) -> None:
        assert validate_cpt_format("12345") is True
        assert validate_cpt_format("99213") is True  # common office visit code

    def test_leading_zeros_valid(self) -> None:
        assert validate_cpt_format("01234") is True


# ---------------------------------------------------------------------------
# validate_cpt_format — invalid codes
# ---------------------------------------------------------------------------


class TestValidateCptFormatInvalid:
    def test_empty_string(self) -> None:
        assert validate_cpt_format("") is False

    def test_four_digits(self) -> None:
        assert validate_cpt_format("1234") is False

    def test_six_digits(self) -> None:
        assert validate_cpt_format("123456") is False

    def test_letters_not_accepted(self) -> None:
        assert validate_cpt_format("ABCDE") is False

    def test_mixed_letters_and_digits(self) -> None:
        assert validate_cpt_format("1234A") is False

    def test_whitespace_stripped_then_validated(self) -> None:
        """CPT validation strips whitespace then matches — leading space still valid."""
        assert validate_cpt_format(" 12345") is True

    def test_dot_separator_invalid(self) -> None:
        assert validate_cpt_format("123.4") is False


# ---------------------------------------------------------------------------
# COMMON_ICD10_CODES constant
# ---------------------------------------------------------------------------


class TestCommonIcd10Codes:
    def test_is_nonempty_dict(self) -> None:
        assert isinstance(COMMON_ICD10_CODES, dict)
        assert len(COMMON_ICD10_CODES) > 0

    def test_all_keys_are_valid_icd10(self) -> None:
        """Every key in COMMON_ICD10_CODES passes validate_icd10_format."""
        for code in COMMON_ICD10_CODES:
            assert validate_icd10_format(code), f"COMMON_ICD10_CODES key {code!r} is not valid"

    def test_all_values_are_strings(self) -> None:
        for code, description in COMMON_ICD10_CODES.items():
            assert isinstance(description, str), f"Description for {code!r} is not a string"
            assert len(description) > 0, f"Description for {code!r} is empty"

    def test_diabetes_present(self) -> None:
        assert "E11.9" in COMMON_ICD10_CODES

    def test_hypertension_present(self) -> None:
        assert "I10" in COMMON_ICD10_CODES


# ---------------------------------------------------------------------------
# ICD10_PREFIXES constant
# ---------------------------------------------------------------------------


class TestIcd10Prefixes:
    def test_is_set(self) -> None:
        assert isinstance(ICD10_PREFIXES, set)

    def test_has_26_entries(self) -> None:
        """ICD-10 uses all 26 letters A-Z as valid prefixes."""
        assert len(ICD10_PREFIXES) == 26

    def test_all_entries_are_uppercase_letters(self) -> None:
        for prefix in ICD10_PREFIXES:
            assert isinstance(prefix, str)
            assert len(prefix) == 1
            assert prefix.isupper()
            assert prefix.isalpha()

    def test_contains_common_prefixes(self) -> None:
        for letter in ("A", "E", "I", "J", "K", "M", "N", "Z"):
            assert letter in ICD10_PREFIXES
