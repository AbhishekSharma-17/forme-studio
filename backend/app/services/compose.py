"""Composable PSD — generate each visual element fresh on a transparent
canvas via gpt-image-2, then assemble into a properly-layered PSD.

This is the user-facing answer to "I want designer-grade output, not a
flat baked image." Architecture:

  1. ``app.services.vision.discover_elements`` → manifest (list[ElementSpec])
  2. ``generate_element`` → transparent PNG bytes per element
  3. ``assemble_composable_psd`` → CMYK 300 DPI PSD with one layer per
     element, positioned in millimetres relative to the trim.

No SAM/segmentation needed — we never slice an existing image. Each
element is reborn cleanly with native gpt-image-2 alpha.

See ``docs/COMPOSABLE_PSD.md`` for the end-to-end workflow.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from openai import AsyncOpenAI
from PIL import Image
from psd_tools import PSDImage
from psd_tools.api.layers import PixelLayer

from app.services.openai_image import generate as openai_generate
from app.services.pricing import cost_from_usage
from app.services.vision import ElementSpec

log = structlog.get_logger(__name__)


# 1 mm in pixels at the given DPI. Used to convert position_mm → pixels
# when placing each element layer onto the PSD canvas.
def _mm_to_px(mm: float, dpi: int) -> int:
    return round(mm * dpi / 25.4)


@dataclass(frozen=True)
class GeneratedElement:
    """One element's transparent PNG + the spec that produced it."""

    spec: ElementSpec
    png_bytes: bytes               # RGBA PNG with alpha channel
    width_px: int
    height_px: int
    cost_usd: float                # per-element gpt-image-2 cost
    asset_id: int | None = None    # set when persisted via assets.save_*


@dataclass(frozen=True)
class CompositeResult:
    """Outcome of an assemble_composable_psd call."""

    path: Path
    size_bytes: int
    width_px: int
    height_px: int
    dpi: int
    layer_count: int               # 1 base + N elements (+ M optional OCR layers)
    element_count: int
    total_generation_cost_usd: float


# ─────────────────────────────────────────────────────────────────────────
#  PER-ELEMENT GENERATION
# ─────────────────────────────────────────────────────────────────────────


async def generate_element(
    client: AsyncOpenAI,
    spec: ElementSpec,
    *,
    model: str,
    quality: str = "high",
) -> GeneratedElement:
    """Render one element on a transparent canvas via gpt-image-2.

    Each element gets the model's full attention — no surrounding
    composition to fight — so quality per element is usually noticeably
    higher than what you'd extract via SAM from a whole-sticker render.

    Raises any OpenAI SDK exception unchanged so the caller can decide
    whether to retry or abort the whole composition.
    """
    log.info(
        "compose_element_generate",
        name=spec.name,
        size_px=spec.size_px,
        quality=quality,
    )
    result = await openai_generate(
        client,
        model=model,
        prompt=spec.prompt,
        size=spec.size_px,
        quality=quality,
        n=1,
        transparent_background=True,
    )
    if not result["images_b64"]:
        raise RuntimeError(
            f"gpt-image-2 returned no image for element '{spec.name}'."
        )
    png_bytes = base64.b64decode(result["images_b64"][0])
    with Image.open(io.BytesIO(png_bytes)) as img:
        # Force RGBA in case the model returned RGB (rare but defensive).
        rgba = img.convert("RGBA")
        w, h = rgba.size
        buf = io.BytesIO()
        rgba.save(buf, format="PNG")
        png_bytes = buf.getvalue()

    cost = cost_from_usage(result["usage"])
    log.info(
        "compose_element_done",
        name=spec.name,
        bytes=len(png_bytes),
        width=w,
        height=h,
        cost_usd=cost,
    )
    return GeneratedElement(
        spec=spec,
        png_bytes=png_bytes,
        width_px=w,
        height_px=h,
        cost_usd=cost,
    )


# ─────────────────────────────────────────────────────────────────────────
#  PSD ASSEMBLY
# ─────────────────────────────────────────────────────────────────────────


def assemble_composable_psd(
    *,
    elements: list[GeneratedElement],
    trim_mm: tuple[float, float],
    bleed_mm: float,
    dpi: int,
    out_path: Path,
    background_rgb: tuple[int, int, int] = (255, 255, 255),
    color_space: str = "CMYK",
) -> CompositeResult:
    """Stack each element on a base canvas at its prescribed ``position_mm``.

    The base canvas is built at **trim + 2×bleed** dimensions so the PSD
    is ready for press without further padding. Each element layer is
    resampled (high-quality Lanczos) from its native render size to the
    target ``position_mm`` size, then pasted at its top-left offset.

    Args:
        elements: list of generated transparent-PNG elements + specs.
        trim_mm: (w, h) trim in millimetres.
        bleed_mm: bleed on every side, in millimetres.
        dpi: target DPI (300 for press).
        out_path: where to write the .psd.
        background_rgb: base canvas fill colour (default white).
        color_space: 'CMYK' or 'RGB'. CMYK is press-ready.

    Returns:
        :class:`CompositeResult` with paths + layer breakdown.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    trim_w_mm, trim_h_mm = trim_mm
    canvas_w_mm = trim_w_mm + 2 * bleed_mm
    canvas_h_mm = trim_h_mm + 2 * bleed_mm
    canvas_w_px = _mm_to_px(canvas_w_mm, dpi)
    canvas_h_px = _mm_to_px(canvas_h_mm, dpi)
    bleed_px = _mm_to_px(bleed_mm, dpi)

    log.info(
        "compose_assemble_start",
        canvas_px=(canvas_w_px, canvas_h_px),
        elements=len(elements),
        dpi=dpi,
        color_space=color_space,
    )

    # 1. Build the base background layer at full canvas size in RGB,
    #    then convert if CMYK was requested.
    base_rgb = Image.new("RGB", (canvas_w_px, canvas_h_px), background_rgb)
    base = base_rgb.convert("CMYK") if color_space == "CMYK" else base_rgb

    psd = PSDImage.frompil(base)
    _stamp_resolution(psd, dpi)

    # 2. Element layers (preserve manifest order so designers can rely on
    #    z-index = order they appeared in the discovery JSON).
    for elem in elements:
        x_mm, y_mm, w_mm, h_mm = elem.spec.position_mm

        # Coordinates are relative to TRIM top-left, but the canvas
        # includes the bleed ring — so offset by bleed_px.
        x_px = bleed_px + _mm_to_px(x_mm, dpi)
        y_px = bleed_px + _mm_to_px(y_mm, dpi)
        target_w_px = max(1, _mm_to_px(w_mm, dpi))
        target_h_px = max(1, _mm_to_px(h_mm, dpi))

        with Image.open(io.BytesIO(elem.png_bytes)) as src:
            src.load()
            rgba = src.convert("RGBA")
            # High-quality resample to the target physical size.
            resampled = rgba.resize(
                (target_w_px, target_h_px),
                Image.Resampling.LANCZOS,
            )

        layer = PixelLayer.frompil(
            resampled,
            psd,
            name=elem.spec.name,
            top=y_px,
            left=x_px,
        )
        psd.append(layer)

    psd.save(str(out_path))

    total_cost = sum(e.cost_usd for e in elements)
    log.info(
        "compose_assemble_done",
        path=str(out_path),
        elements=len(elements),
        total_cost_usd=total_cost,
        size_bytes=out_path.stat().st_size,
    )
    return CompositeResult(
        path=out_path,
        size_bytes=out_path.stat().st_size,
        width_px=canvas_w_px,
        height_px=canvas_h_px,
        dpi=dpi,
        layer_count=1 + len(elements),
        element_count=len(elements),
        total_generation_cost_usd=total_cost,
    )


# ─────────────────────────────────────────────────────────────────────────
#  helpers
# ─────────────────────────────────────────────────────────────────────────


def _stamp_resolution(psd: PSDImage, dpi: int) -> None:
    """Bake DPI into the PSD resolution-info image resource.

    Mirrors the helper in export_psd.py — Photoshop reads this on open
    and shows the correct pixels-per-inch instead of the default 72.
    """
    from psd_tools.constants import Resource
    from psd_tools.psd.image_resources import ImageResource, ImageResources

    res_data = (
        int(dpi * 65536).to_bytes(4, "big")
        + (0).to_bytes(2, "big")
        + (1).to_bytes(2, "big")
        + (0).to_bytes(2, "big")
        + int(dpi * 65536).to_bytes(4, "big")
        + (0).to_bytes(2, "big")
        + (1).to_bytes(2, "big")
        + (0).to_bytes(2, "big")
    )
    block = ImageResource(
        signature=b"8BIM",
        key=Resource.RESOLUTION_INFO,
        name="",
        data=res_data,
    )
    if psd.image_resources is None:
        psd.image_resources = ImageResources()
    psd.image_resources[Resource.RESOLUTION_INFO] = block


def derive_composable_filename(asset_id: int) -> str:
    """Filename rule: ``assetX_composable_<utc-stamp>.psd``."""
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    return f"asset{asset_id}_composable_{ts}.psd"


def manifest_to_json(elements: list[ElementSpec]) -> list[dict[str, Any]]:
    """Serialise a manifest for the API response."""
    return [e.to_dict() for e in elements]
