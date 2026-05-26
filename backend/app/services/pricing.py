"""Exact-cost calculator for gpt-image-2 usage.

OpenAI returns a ``usage`` block with token counts on every successful
image response. We turn it into a per-image dollar amount using the
public pricing matrix below.

Rates (USD per 1M tokens, gpt-image-2 family, 2026-04-21 snapshot):

  * text input              $5
  * image input             $8
  * image input cached      $2
  * image output            $30
"""

from __future__ import annotations

from typing import Any

_TEXT_IN_PER_MTOK = 5.0
_IMAGE_IN_PER_MTOK = 8.0
_IMAGE_IN_CACHED_PER_MTOK = 2.0
_IMAGE_OUT_PER_MTOK = 30.0


def usage_to_dict(usage: Any) -> dict[str, int]:
    """Flatten an OpenAI ``usage`` object into a plain int dict.

    Accepts either a pydantic model with attribute access (live SDK calls)
    or a plain dict (replays from logs / tests).
    """
    if usage is None:
        return {}
    if isinstance(usage, dict):
        d = usage
    else:
        d = usage.model_dump() if hasattr(usage, "model_dump") else dict(usage.__dict__)

    flat: dict[str, int] = {}
    for k, v in d.items():
        if isinstance(v, dict):
            for sub_k, sub_v in v.items():
                if isinstance(sub_v, (int, float)):
                    flat[f"{k}.{sub_k}"] = int(sub_v)
        elif isinstance(v, (int, float)):
            flat[k] = int(v)
    return flat


def cost_from_usage(flat: dict[str, int]) -> float:
    """Compute provider cost in USD from a flattened usage dict."""
    text_in = flat.get("input_tokens", 0)
    text_in_cached = flat.get("input_tokens_details.cached_tokens", 0)
    # Some SDK versions break image vs text input across separate keys; we
    # also accept the explicit aliases for forward compatibility.
    image_in = flat.get("input_tokens_details.image_tokens", 0)
    text_only_in = max(0, text_in - text_in_cached - image_in)
    image_out = flat.get("output_tokens", 0)

    cost = (
        (text_only_in / 1_000_000.0) * _TEXT_IN_PER_MTOK
        + (image_in / 1_000_000.0) * _IMAGE_IN_PER_MTOK
        + (text_in_cached / 1_000_000.0) * _IMAGE_IN_CACHED_PER_MTOK
        + (image_out / 1_000_000.0) * _IMAGE_OUT_PER_MTOK
    )
    return round(cost, 6)


def apply_markup(provider_cost_usd: float, markup_percent: float) -> float:
    """Apply optional markup. ``markup_percent`` is a percentage (e.g. ``10`` = +10%)."""
    if markup_percent <= 0:
        return provider_cost_usd
    return round(provider_cost_usd * (1.0 + markup_percent / 100.0), 6)
