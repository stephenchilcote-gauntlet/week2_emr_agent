"""Unit tests for src/observability/cost.py — LLM cost calculation."""

from __future__ import annotations

import pytest

from src.observability.cost import calculate_cost, get_pricing


# ---------------------------------------------------------------------------
# get_pricing — known model IDs
# ---------------------------------------------------------------------------


class TestGetPricing:
    def test_sonnet_4_6(self) -> None:
        input_price, output_price = get_pricing("claude-sonnet-4-6")
        assert input_price == 3.0
        assert output_price == 15.0

    def test_opus_4_6(self) -> None:
        input_price, output_price = get_pricing("claude-opus-4-6")
        assert input_price == 5.0
        assert output_price == 25.0

    def test_haiku_4_5(self) -> None:
        input_price, output_price = get_pricing("claude-haiku-4-5")
        assert input_price == 1.0
        assert output_price == 5.0

    def test_haiku_4_5_with_date_suffix(self) -> None:
        """Dated model ID with 8-digit suffix is recognized."""
        input_price, output_price = get_pricing("claude-haiku-4-5-20251001")
        assert input_price == 1.0
        assert output_price == 5.0

    def test_sonnet_4_with_8_digit_date(self) -> None:
        """claude-sonnet-4-20250514 is matched by the dated-suffix logic."""
        input_price, output_price = get_pricing("claude-sonnet-4-20250514")
        assert input_price == 3.0
        assert output_price == 15.0

    def test_unknown_model_falls_back_to_default(self) -> None:
        """Unknown model ID returns default Sonnet-class pricing."""
        input_price, output_price = get_pricing("claude-future-model-99")
        # Default is Sonnet-class pricing
        assert input_price == 3.0
        assert output_price == 15.0

    def test_empty_string_falls_back_to_default(self) -> None:
        input_price, output_price = get_pricing("")
        assert isinstance(input_price, float)
        assert isinstance(output_price, float)

    def test_returns_tuple_of_floats(self) -> None:
        result = get_pricing("claude-haiku-3-5")
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert all(isinstance(x, float) for x in result)

    def test_dated_suffix_non_8_digits_not_stripped(self) -> None:
        """A 7-digit date suffix is NOT stripped — falls back to default."""
        input_price, output_price = get_pricing("claude-sonnet-4-2025051")
        # Not an 8-digit suffix, no stripping, falls back to default
        assert input_price == 3.0
        assert output_price == 15.0

    def test_dated_suffix_8_digits_unknown_base_falls_to_default(self) -> None:
        """8-digit suffix stripped but resulting base is not in map → default pricing."""
        # "claude-novelmodel" is not in _MODEL_PRICING even without the date suffix
        input_price, output_price = get_pricing("claude-novelmodel-20251001")
        assert input_price == 3.0
        assert output_price == 15.0

    def test_no_dash_in_model_name_falls_to_default(self) -> None:
        """A model name with no dash produces one part → dated-suffix logic skipped."""
        input_price, output_price = get_pricing("unknownmodel")
        assert input_price == 3.0
        assert output_price == 15.0

    def test_opus_4_1_more_expensive_than_sonnet(self) -> None:
        opus_price = get_pricing("claude-opus-4-1")
        sonnet_price = get_pricing("claude-sonnet-4-6")
        assert opus_price[0] > sonnet_price[0], "Opus input should cost more than Sonnet"
        assert opus_price[1] > sonnet_price[1], "Opus output should cost more than Sonnet"

    def test_haiku_cheaper_than_sonnet(self) -> None:
        haiku = get_pricing("claude-haiku-4-5")
        sonnet = get_pricing("claude-sonnet-4-6")
        assert haiku[0] < sonnet[0], "Haiku input should cost less than Sonnet"


# ---------------------------------------------------------------------------
# calculate_cost
# ---------------------------------------------------------------------------


class TestCalculateCost:
    def test_zero_tokens_returns_zero(self) -> None:
        assert calculate_cost("claude-sonnet-4-6", 0, 0) == 0.0

    def test_positive_tokens_returns_positive_cost(self) -> None:
        cost = calculate_cost("claude-sonnet-4-6", 1000, 500)
        assert cost > 0.0

    def test_cost_is_in_dollars_not_cents(self) -> None:
        """1000 input tokens at $3/M = $0.003 (less than a penny)."""
        cost = calculate_cost("claude-sonnet-4-6", 1000, 0)
        assert cost == pytest.approx(0.003)

    def test_output_tokens_more_expensive_than_input(self) -> None:
        """With Sonnet pricing: output is 5x more expensive per token."""
        input_only = calculate_cost("claude-sonnet-4-6", 1_000_000, 0)
        output_only = calculate_cost("claude-sonnet-4-6", 0, 1_000_000)
        assert output_only == pytest.approx(15.0)
        assert input_only == pytest.approx(3.0)

    def test_haiku_cheaper_than_sonnet_for_same_tokens(self) -> None:
        haiku_cost = calculate_cost("claude-haiku-4-5", 1000, 1000)
        sonnet_cost = calculate_cost("claude-sonnet-4-6", 1000, 1000)
        assert haiku_cost < sonnet_cost

    def test_one_million_input_tokens_matches_price_per_million(self) -> None:
        """1M input tokens at $3.0/M = $3.0."""
        cost = calculate_cost("claude-sonnet-4-6", 1_000_000, 0)
        assert cost == pytest.approx(3.0)

    def test_one_million_output_tokens_matches_price_per_million(self) -> None:
        """1M output tokens at $15.0/M = $15.0."""
        cost = calculate_cost("claude-sonnet-4-6", 0, 1_000_000)
        assert cost == pytest.approx(15.0)

    def test_unknown_model_uses_default_pricing(self) -> None:
        """Unknown model falls back to default pricing without raising."""
        cost = calculate_cost("unknown-model", 1000, 500)
        assert isinstance(cost, float)
        assert cost > 0.0

    def test_large_token_counts(self) -> None:
        """Very large token counts don't raise exceptions."""
        cost = calculate_cost("claude-sonnet-4-6", 10_000_000, 5_000_000)
        assert cost > 0.0
        assert cost == pytest.approx(10_000_000 * 3.0 / 1e6 + 5_000_000 * 15.0 / 1e6)
