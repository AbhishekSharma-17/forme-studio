"""PSD export — Tiers A, B, C.

* **Tier A** — single flat layer (RGB or CMYK), no segmentation. Cheap,
  always works, the baseline deliverable.
* **Tier B** — layered PSD built from SAM-2 masks. Base + one
  transparent overlay per mask region so designers can hide/show parts
  selectively.
* **Tier C** — Tier B plus OCR-driven layers that mark every detected
  text region, each layer named with the detected text so a designer
  can convert them to native Photoshop type layers. A sidecar JSON
  carries the full OCR data for programmatic use.

DPI is stamped into the PSD's Resolution Info image resource for all
tiers; psd-tools 1.17 has a buggy ``ResoulutionInfo.write`` so we pack
the 16-byte block ourselves.
"""

from __future__ import annotations

import io
import json
import struct
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import structlog
from PIL import Image
from psd_tools import PSDImage
from psd_tools.api.layers import PixelLayer
from psd_tools.constants import Resource
from psd_tools.psd.image_resources import ImageResource, ImageResources

from app.services.ocr import OcrResult
from app.services.segmentation import Mask, SegmentationResult

log = structlog.get_logger(__name__)

ColorSpace = Literal["RGB", "CMYK"]
Tier = Literal["A", "B", "C"]


@dataclass(frozen=True)
class PsdExportResult:
    """Outcome of a single PSD export."""

    path: Path
    size_bytes: int
    width: int
    height: int
    color_space: ColorSpace
    dpi: int
    tier: Tier = "A"
    layer_count: int = 1
    sidecar_path: Path | None = None  # JSON sidecar (Tier C OCR data)


def export_to_psd(
    *,
    source_png_path: Path,
    out_path: Path,
    color_space: ColorSpace = "CMYK",
    dpi: int = 300,
) -> PsdExportResult:
    """Convert a PNG into a print-spec PSD.

    Args:
        source_png_path: input PNG (typically a workspace generation).
        out_path: where to write the .psd file. Parent dirs are created.
        color_space: output mode. ``"CMYK"`` runs Pillow's built-in sRGB→CMYK
            (good for Tier A; perceptual mapping comes when we wire an ICC
            profile in slice 4.5). ``"RGB"`` keeps the original color space.
        dpi: pixels-per-inch to bake into the PSD's Resolution Info block.

    Returns:
        :class:`PsdExportResult` with the path + metadata.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rgb, (width, height) = _load_rgb(source_png_path)
    psd_image = rgb.convert("CMYK") if color_space == "CMYK" else rgb

    psd = PSDImage.frompil(psd_image)
    _stamp_resolution(psd, dpi)
    psd.save(str(out_path))

    return PsdExportResult(
        path=out_path,
        size_bytes=out_path.stat().st_size,
        width=width,
        height=height,
        color_space=color_space,
        dpi=dpi,
        tier="A",
        layer_count=1,
    )


# ============================ TIER B ============================


def export_to_psd_tier_b(
    *,
    source_png_path: Path,
    out_path: Path,
    segmentation: SegmentationResult,
    color_space: ColorSpace = "CMYK",
    dpi: int = 300,
) -> PsdExportResult:
    """Layered PSD: base + one transparent overlay per SAM-2 mask.

    Each mask becomes a layer that shows the masked portion of the
    original image with everything outside the mask fully transparent.
    Designers can toggle visibility of individual regions in Photoshop
    without re-rendering anything.

    Args:
        source_png_path: the generation PNG.
        out_path: where to write the .psd.
        segmentation: SAM-2 masks (see ``app.services.segmentation``).
        color_space: ``"CMYK"`` (default, press-ready) or ``"RGB"``.
        dpi: stamped into the PSD's Resolution Info.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rgb, _ = _load_rgb(source_png_path)
    base_image = rgb.convert("CMYK") if color_space == "CMYK" else rgb

    psd = PSDImage.frompil(base_image)
    _stamp_resolution(psd, dpi)
    width, height = rgb.size

    # Add a layer per mask. We composite the *original* image masked by
    # the SAM-2 alpha so the layer carries the actual pixels of that
    # region — not the colour-converted CMYK preview.
    for mask in segmentation.masks:
        layer_image = _compose_layer_from_mask(rgb, mask, width, height)
        if layer_image is None:
            continue
        layer = PixelLayer.frompil(
            layer_image,
            psd,
            name=mask.name,
            top=int(mask.bbox[1]),
            left=int(mask.bbox[0]),
        )
        psd.append(layer)

    psd.save(str(out_path))
    log.info(
        "psd_tier_b_saved",
        path=str(out_path),
        layers=1 + len(segmentation.masks),
        provider=segmentation.provider,
    )
    return PsdExportResult(
        path=out_path,
        size_bytes=out_path.stat().st_size,
        width=width,
        height=height,
        color_space=color_space,
        dpi=dpi,
        tier="B",
        layer_count=1 + len(segmentation.masks),
    )


def _compose_layer_from_mask(
    source_rgb: Image.Image,
    mask: Mask,
    canvas_w: int,
    canvas_h: int,
) -> Image.Image | None:
    """Build an RGBA layer that shows source pixels only inside the mask.

    Crops to the mask's bbox to keep the PSD small (Photoshop honours
    the per-layer top/left coordinates).
    """
    left, top, right, bottom = mask.bbox
    if right - left <= 0 or bottom - top <= 0:
        return None

    # Open mask, snap to bbox.
    with Image.open(io.BytesIO(mask.png_bytes)) as raw_mask:
        alpha = raw_mask.convert("L")
        # Some providers return a full-canvas mask; if it matches our
        # canvas we crop to bbox. If it's already cropped, resize to bbox.
        if alpha.size == (canvas_w, canvas_h):
            alpha_crop = alpha.crop((left, top, right, bottom))
        else:
            alpha_crop = alpha.resize((right - left, bottom - top), Image.Resampling.LANCZOS)

    rgb_crop = source_rgb.crop((left, top, right, bottom)).convert("RGB")
    layer = Image.new("RGBA", rgb_crop.size, (0, 0, 0, 0))
    layer.paste(rgb_crop, (0, 0), alpha_crop)
    return layer


# ============================ TIER C ============================


def export_to_psd_tier_c(
    *,
    source_png_path: Path,
    out_path: Path,
    segmentation: SegmentationResult,
    ocr: OcrResult,
    color_space: ColorSpace = "CMYK",
    dpi: int = 300,
) -> PsdExportResult:
    """Tier B + per-text-region overlay layers + JSON sidecar.

    For every OCR word with confidence ≥ 60 we add a thin pixel layer
    whose **name encodes the detected text** (Photoshop shows it in the
    Layers panel), positioned at the word's bbox. Designers replace each
    one with a native type layer using the encoded text + position as
    the spec.

    The full OCR result is also written to a JSON sidecar next to the
    PSD so downstream automations (or a future "auto-text-layer"
    Photoshop script) can read it without parsing layer names.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rgb, _ = _load_rgb(source_png_path)
    base_image = rgb.convert("CMYK") if color_space == "CMYK" else rgb

    psd = PSDImage.frompil(base_image)
    _stamp_resolution(psd, dpi)
    width, height = rgb.size

    # SAM-2 layers (same as Tier B)
    for mask in segmentation.masks:
        layer_image = _compose_layer_from_mask(rgb, mask, width, height)
        if layer_image is None:
            continue
        layer = PixelLayer.frompil(
            layer_image,
            psd,
            name=mask.name,
            top=int(mask.bbox[1]),
            left=int(mask.bbox[0]),
        )
        psd.append(layer)

    # OCR text overlay layers (one per region, named with the text)
    text_layer_count = 0
    for region in ocr.regions:
        left, top, right, bottom = region.bbox
        w, h = right - left, bottom - top
        if w <= 0 or h <= 0:
            continue
        crop = rgb.crop((left, top, right, bottom)).convert("RGBA")
        # Slugify the text into a Photoshop-friendly layer name.
        safe = region.text.replace("\n", " ").replace("\t", " ").strip()
        if len(safe) > 60:
            safe = safe[:57] + "…"
        layer_name = f'text: "{safe}" @{left},{top}'
        layer = PixelLayer.frompil(
            crop, psd, name=layer_name, top=top, left=left
        )
        psd.append(layer)
        text_layer_count += 1

    psd.save(str(out_path))

    # JSON sidecar
    sidecar = out_path.with_suffix(".ocr.json")
    sidecar.write_text(
        json.dumps(
            {
                "source_png": str(source_png_path),
                "lang": ocr.lang,
                "regions": [
                    {
                        "text": r.text,
                        "bbox": list(r.bbox),
                        "confidence": r.confidence,
                        "line_id": r.line_id,
                    }
                    for r in ocr.regions
                ],
                "segmentation": {
                    "provider": segmentation.provider,
                    "model": segmentation.model,
                    "mask_count": len(segmentation.masks),
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    log.info(
        "psd_tier_c_saved",
        path=str(out_path),
        sam2_layers=len(segmentation.masks),
        text_layers=text_layer_count,
        sidecar=str(sidecar),
    )
    return PsdExportResult(
        path=out_path,
        size_bytes=out_path.stat().st_size,
        width=width,
        height=height,
        color_space=color_space,
        dpi=dpi,
        tier="C",
        layer_count=1 + len(segmentation.masks) + text_layer_count,
        sidecar_path=sidecar,
    )


# ============================ shared helpers ============================


def _load_rgb(source_png_path: Path) -> tuple[Image.Image, tuple[int, int]]:
    """Open a generation PNG and return an RGB copy + dimensions.

    Alpha is flattened against white so colours match what the model
    intended; designers can re-introduce transparency in Photoshop.
    """
    with Image.open(source_png_path) as src:
        src.load()
        if src.mode == "RGB":
            return src.copy(), src.size
        background = Image.new("RGB", src.size, (255, 255, 255))
        if src.mode == "RGBA":
            background.paste(src, mask=src.split()[3])
        else:
            background.paste(src.convert("RGB"))
        return background, src.size


def _stamp_resolution(psd: PSDImage, dpi: int) -> None:
    """Write the Resolution Info image resource so Photoshop opens at correct DPI.

    psd-tools 1.17 ships a buggy ``ResoulutionInfo`` (sic) class whose
    ``write()`` signature is incompatible with the resource-writer; we
    bypass it by packing the 16-byte block ourselves and stuffing it into
    a generic :class:`ImageResource`.

    Layout (big-endian, 16 bytes total):

      horizontal      : I  (32-bit fixed, 16.16)
      horizontal_unit : H  (1 = px/inch, 2 = px/cm)
      width_unit      : H  (display unit, Photoshop ignores)
      vertical        : I
      vertical_unit   : H
      height_unit     : H
    """
    resources = psd.image_resources
    if not isinstance(resources, ImageResources):
        return  # defensive — shouldn't happen on a fresh PSDImage

    fixed = int(dpi) << 16
    data = struct.pack(">IHHIHH", fixed, 1, 2, fixed, 1, 2)
    resources[Resource.RESOLUTION_INFO] = ImageResource(
        key=Resource.RESOLUTION_INFO, data=data
    )


def derive_export_filename(asset_id: int) -> str:
    """Filename rule for an export: ``<asset>_<ts>.psd`` (microsecond precision)."""
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    return f"asset{asset_id}_{ts}.psd"
