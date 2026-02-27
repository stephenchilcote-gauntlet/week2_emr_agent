"""Cost calculation for Anthropic Claude API usage."""
from __future__ import annotations


# Pricing per million tokens (input, output) in USD.
# Source: https://platform.claude.com/docs/en/about-claude/pricing
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-6": (5.0, 25.0),
    "claude-opus-4-5": (5.0, 25.0),
    "claude-opus-4-1": (15.0, 75.0),
    "claude-opus-4": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-sonnet-4-20250514": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-haiku-3-5": (0.8, 4.0),
}

# Default pricing when model is unknown.
_DEFAULT_PRICING: tuple[float, float] = (3.0, 15.0)


def get_pricing(model: str) -> tuple[float, float]:
    """Return (input_price, output_price) per million tokens for *model*.

    Falls back to Sonnet-class pricing if the model ID is not recognized.
    Model IDs with dated suffixes (e.g. ``-20250514``) are matched against
    the full ID first, then the base name without the date.
    """
    if model in _MODEL_PRICING:
        return _MODEL_PRICING[model]
    # Strip dated suffix (e.g. "claude-sonnet-4-20250514" -> try shorter)
    parts = model.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) == 8:
        base = parts[0]
        if base in _MODEL_PRICING:
            return _MODEL_PRICING[base]
    return _DEFAULT_PRICING


def calculate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """Calculate USD cost for a single LLM call."""
    input_price, output_price = get_pricing(model)
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000
