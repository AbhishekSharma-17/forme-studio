"""PSD export — Tier A (flat) and Tier A+OCR (flat + Tesseract overlays).

* **Tier A** — single flat layer (RGB or CMYK), no OCR. Cheap, always
  works, the baseline deliverable.
* **Tier A+OCR** — Tier A plus OCR-driven layers that mark every
  detected text region; each layer name encodes the detected text so a
  designer can convert them to native Photoshop type layers. A sidecar
  JSON carries the full OCR data for programmatic use.

For multi-layered editable output, see ``app.services.compose`` — the
Composable PSD pipeline regenerates every visual element on a
transparent canvas and assembles them into a properly-named layer stack.

DPI is stamped into the PSD's Resolution Info image resource for all
tiers; psd-tools 1.17 has a buggy ``ResoulutionInfo.write`` so we pack
the 16-byte block ourselves.
"""

from __future__ import annotations

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

log = structlog.get_logger(__name__)

ColorSpace = Literal["RGB", "CMYK"]
Tier = Literal["A", "A+OCR"]


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
    sidecar_path: Path | None = None  # JSON sidecar (Tier A+OCR OCR data)


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


# ============================ TIER A+OCR ============================


def export_to_psd_a_ocr(
    *,
    source_png_path: Path,
    out_path: Path,
    ocr: OcrResult,
    color_space: ColorSpace = "CMYK",
    dpi: int = 300,
) -> PsdExportResult:
    """Tier A flat PSD + Tesseract OCR text-region overlays.

    Built for the "I just want to fix text" workflow — every detected
    text region becomes a layer named ``text: "<detected>" @<left>,<top>``
    sitting on top of the flat CMYK base, plus a sidecar JSON.

    Designer opens the PSD, sees the layers panel filled with every
    detected text region by its actual content, and can:

    * Spot OCR errors by reading the layer names
    * Replace any layer with a real Photoshop type layer (encoded text +
      position is the spec — designer copy-paste-types and deletes the
      pixel overlay)
    * Reposition text without re-rendering the entire sticker
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rgb, _ = _load_rgb(source_png_path)
    base_image = rgb.convert("CMYK") if color_space == "CMYK" else rgb

    psd = PSDImage.frompil(base_image)
    _stamp_resolution(psd, dpi)
    width, height = rgb.size

    # OCR text overlay layers (one per region, named with the text)
    text_layer_count = 0
    for region in ocr.regions:
        left, top, right, bottom = region.bbox
        w, h = right - left, bottom - top
        if w <= 0 or h <= 0:
            continue
        crop = rgb.crop((left, top, right, bottom)).convert("RGBA")
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
                "tier": "A+OCR",
                "regions": [
                    {
                        "text": r.text,
                        "bbox": list(r.bbox),
                        "confidence": r.confidence,
                        "line_id": r.line_id,
                    }
                    for r in ocr.regions
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    log.info(
        "psd_tier_a_ocr_saved",
        path=str(out_path),
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
        tier="A+OCR",
        layer_count=1 + text_layer_count,
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
