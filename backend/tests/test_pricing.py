"""Unit tests for pricing.usage_to_dict + cost_from_usage + apply_markup.

The cost calculator bills users — getting the math wrong has real
financial consequences. These tests pin the rates and the structural
behaviour (flattening nested usage dicts, treating cached tokens
separately, falling back gracefully on missing keys).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.pricing import apply_markup, cost_from_usage, usage_to_dict

# ----------------------------------------------------------------- helpers


class _UsagePydanticShape:
    """Stand-in for the real SDK usage object — exposes .model_dump()."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def model_dump(self) -> dict[str, Any]:
        return self._payload


# --------------------------------------------------------- usage_to_dict


def test_usage_to_dict_none_returns_empty() -> None:
    assert usage_to_dict(None) == {}


def test_usage_to_dict_flattens_one_level_of_nesting() -> None:
    """Nested dicts use dotted keys; scalars carry through."""
    raw = {
        "input_tokens": 1200,
        "output_tokens": 4096,
        "input_tokens_details": {
            "cached_tokens": 200,
            "image_tokens": 400,
        },
        "total_tokens": 5296,
    }
    flat = usage_to_dict(raw)
    assert flat == {
        "input_tokens": 1200,
        "output_tokens": 4096,
        "input_tokens_details.cached_tokens": 200,
        "input_tokens_details.image_tokens": 400,
        "total_tokens": 5296,
    }


def test_usage_to_dict_accepts_pydantic_shape() -> None:
    """A pydantic-like object with .model_dump() works the same as a dict."""
    obj = _UsagePydanticShape({"input_tokens": 10, "output_tokens": 20})
    assert usage_to_dict(obj) == {"input_tokens": 10, "output_tokens": 20}


def test_usage_to_dict_drops_non_numeric_values() -> None:
    """Strings / lists / nested-nested structures shouldn't crash, just skip."""
    raw = {
        "input_tokens": 100,
        "model": "gpt-image-2",          # string — dropped
        "rates": ["a", "b"],             # list — dropped
        "input_tokens_details": {
            "cached_tokens": 50,
            "weird": ["nope"],           # nested non-numeric — dropped
        },
    }
    flat = usage_to_dict(raw)
    assert flat == {
        "input_tokens": 100,
        "input_tokens_details.cached_tokens": 50,
    }


def test_usage_to_dict_coerces_floats_to_ints() -> None:
    """OpenAI occasionally returns float counts; ensure we cast cleanly."""
    flat = usage_to_dict({"input_tokens": 1200.0, "output_tokens": 4096.7})
    # 4096.7 → int(4096.7) → 4096 (Python's int() truncates toward zero)
    assert flat == {"input_tokens": 1200, "output_tokens": 4096}


# ------------------------------------------------------ cost_from_usage


def test_cost_from_usage_empty_dict_zero() -> None:
    assert cost_from_usage({}) == 0.0


def test_cost_from_usage_pure_text_input() -> None:
    """1,000,000 text-in tokens = $5.00."""
    flat = {"input_tokens": 1_000_000, "output_tokens": 0}
    assert cost_from_usage(flat) == 5.0


def test_cost_from_usage_pure_image_output() -> None:
    """1,000,000 output tokens = $30.00."""
    flat = {"input_tokens": 0, "output_tokens": 1_000_000}
    assert cost_from_usage(flat) == 30.0


def test_cost_from_usage_splits_text_from_image_input() -> None:
    """Image-input tokens are subtracted from text-input + billed separately.

    The base ``input_tokens`` is the *total* input; the image portion lives
    under ``input_tokens_details.image_tokens``. We must not double-count.
    """
    flat = {
        # 1k input total: 400 are image tokens, 600 are text → check pricing.
        "input_tokens": 1_000,
        "input_tokens_details.image_tokens": 400,
        "output_tokens": 0,
    }
    # 600 text * $5/M  +  400 image * $8/M  =  0.003 + 0.0032 = 0.0062
    assert cost_from_usage(flat) == pytest.approx(0.0062, abs=1e-7)


def test_cost_from_usage_cached_tokens_get_discount() -> None:
    """Cached input tokens bill at the $2/M rate, not $5/M."""
    flat = {
        "input_tokens": 1_000,
        "input_tokens_details.cached_tokens": 1_000,  # all cached
        "output_tokens": 0,
    }
    # 0 text * 5 + 0 image * 8 + 1000 cached * $2/M + 0 out = $0.002
    assert cost_from_usage(flat) == pytest.approx(0.002, abs=1e-7)


def test_cost_from_usage_realistic_high_quality_one_variant() -> None:
    """A typical high-quality single-variant gpt-image-2 call.

    Numbers picked to match the stub in tests/stubs.py so the assertion
    here doubles as a regression test on the shared fixture math.
    """
    # input_tokens=1200, output_tokens=4096, cached=200, image=400
    flat = {
        "input_tokens": 1200,
        "input_tokens_details.cached_tokens": 200,
        "input_tokens_details.image_tokens": 400,
        "output_tokens": 4096,
    }
    # text-only = 1200 - 200 - 400 = 600 → 600 * 5 / 1M    = 0.003
    # image-in  = 400 * 8 / 1M                              = 0.0032
    # cached    = 200 * 2 / 1M                              = 0.0004
    # image-out = 4096 * 30 / 1M                            = 0.12288
    # total                                                  = 0.12948
    assert cost_from_usage(flat) == pytest.approx(0.12948, abs=1e-7)


def test_cost_from_usage_image_input_clamp_negative() -> None:
    """If image_tokens exceeds input_tokens (sanity guard), text_only stays >= 0."""
    flat = {
        "input_tokens": 100,
        "input_tokens_details.image_tokens": 5_000,  # implausible but
        "output_tokens": 0,
    }
    # text_only_in = max(0, 100 - 0 - 5000) = 0
    # image_in = 5000 * 8 / 1M = 0.04
    assert cost_from_usage(flat) == pytest.approx(0.04, abs=1e-7)


# ------------------------------------------------------------ apply_markup


def test_apply_markup_zero_is_passthrough() -> None:
    assert apply_markup(0.12948, 0.0) == 0.12948


def test_apply_markup_negative_is_treated_as_zero() -> None:
    """Negative markup is nonsense — service should NOT discount."""
    assert apply_markup(0.10, -50.0) == 0.10


def test_apply_markup_10_percent() -> None:
    assert apply_markup(0.10, 10.0) == pytest.approx(0.11, abs=1e-7)


def test_apply_markup_rounds_to_six_decimals() -> None:
    # 0.123456789 * 1.07 = 0.13209876423 → round(6) = 0.132099
    assert apply_markup(0.123456789, 7.0) == pytest.approx(0.132099, abs=1e-7)
