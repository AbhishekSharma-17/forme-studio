"""Print PDF/X-4 export.

Generates a press-ready PDF for a workspace generation:

* CMYK image (converted via the configured ICC profile if present;
  baseline ``PIL.Image.convert("CMYK")`` otherwise).
* MediaBox = trim + bleed, **BleedBox** = same, **TrimBox** = trim only —
  the three boxes a press needs to align dies and bleed safely.
* Optional **trim marks** at the four corners (5 mm long, 100% K).
* Optional **registration marks** mid-edge so the press operator can
  align all four colour plates.
* ICC profile embedded as an OutputIntent so the file is colour-managed
  (PDF/X-4-compatible — not strictly PDF/X conformant without further
  metadata, but accepted by every print shop we've tested with).

The PDF is built with ReportLab. We never raster-flatten transparency —
the X-4 spec preserves live transparency for downstream colour-managed
rendering.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from PIL import Image, ImageCms, ImageOps
from reportlab.lib.colors import CMYKColor
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfdoc
from reportlab.pdfgen import canvas
from reportlab.pdfgen.canvas import Canvas

from app.config import get_settings

# ReportLab's PDFCatalog only emits attributes listed in __NoDefault__; the
# default list doesn't include OutputIntents (it's a PDF/X-specific entry),
# so we extend it at import time. Idempotent across reloads.
if "OutputIntents" not in pdfdoc.PDFCatalog.__NoDefault__:
    pdfdoc.PDFCatalog.__NoDefault__ = (
        *pdfdoc.PDFCatalog.__NoDefault__,
        "OutputIntents",
    )

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class PdfExportResult:
    path: Path
    size_bytes: int
    trim_mm: tuple[float, float]
    bleed_mm: float
    dpi: int
    icc_profile: str          # human label
    icc_embedded: bool        # True if a valid ICC was attached
    trim_marks: bool
    registration_marks: bool


PROCESS_BLACK = CMYKColor(0, 0, 0, 1)


# --------------------------------------------------------------------- entry


def export_to_pdf(
    *,
    source_png_path: Path,
    out_path: Path,
    trim_mm: tuple[float, float],
    bleed_mm: float,
    dpi: int = 300,
    trim_marks: bool = True,
    registration_marks: bool = True,
) -> PdfExportResult:
    """Build a print-ready PDF.

    Args:
        source_png_path: the generation PNG to place at trim size.
        out_path: where to write the .pdf.
        trim_mm: ``(width_mm, height_mm)`` of the final cut size.
        bleed_mm: bleed extending past the trim on all four sides.
        dpi: target pixels-per-inch (informational; the image is placed
            at trim+bleed regardless).
        trim_marks: draw 5 mm corner crop marks just outside the trim.
        registration_marks: draw bullseye targets at the mid-points of
            each side.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    settings = get_settings()

    # 1. Build the CMYK image at trim aspect.
    cmyk_image, icc_label, icc_used = _to_cmyk(source_png_path, settings.print_icc_path)

    # 2. Extend by mirrored bleed strips so the **media-aspect** image we
    #    embed never distorts the trim region. The model produced a
    #    trim-aspect PNG (workspace.generation_size); the bleed area is
    #    filled by mirroring the outer pixels in from each edge.
    trim_w_mm, trim_h_mm = trim_mm
    bleed_px_x = round(cmyk_image.width * bleed_mm / trim_w_mm) if trim_w_mm > 0 else 0
    bleed_px_y = round(cmyk_image.height * bleed_mm / trim_h_mm) if trim_h_mm > 0 else 0
    cmyk_with_bleed = _extend_with_mirror_bleed(cmyk_image, bleed_px_x, bleed_px_y)

    # 3. Encode for ReportLab (JPEG keeps file size sensible; CMYK
    #    preserved). DPI is informational metadata.
    cmyk_buf = io.BytesIO()
    cmyk_with_bleed.save(cmyk_buf, format="JPEG", quality=92, dpi=(dpi, dpi))
    cmyk_buf.seek(0)

    # 4. Compute pagebox dimensions in points (1 mm = 2.834645 pt).
    media_w_pt = (trim_w_mm + 2 * bleed_mm) * mm
    media_h_pt = (trim_h_mm + 2 * bleed_mm) * mm
    trim_w_pt = trim_w_mm * mm
    trim_h_pt = trim_h_mm * mm
    bleed_pt = bleed_mm * mm

    # 5. Create the canvas
    pdf_kwargs: dict[str, Any] = {
        "pageCompression": 1,
        "pagesize": (media_w_pt, media_h_pt),
        "pdfVersion": (1, 6),
    }
    c = canvas.Canvas(str(out_path), **pdf_kwargs)
    c.setTitle(f"{source_png_path.stem} — Forme Studio")
    c.setCreator("Forme Studio")
    c.setSubject("Print-ready CMYK PDF")

    # MediaBox is automatic from pagesize. TrimBox + BleedBox via the
    # canvas helpers (ReportLab 3.5+).
    c.setTrimBox(
        (bleed_pt, bleed_pt, bleed_pt + trim_w_pt, bleed_pt + trim_h_pt)
    )
    c.setBleedBox((0, 0, media_w_pt, media_h_pt))

    # 6. Place the (already bleed-extended) image at full media size.
    #    Its aspect now matches the media — no horizontal/vertical stretch
    #    of the design within the trim box. The mirrored strips only
    #    occupy the bleed ring.
    c.drawImage(
        ImageReader(cmyk_buf),
        x=0,
        y=0,
        width=media_w_pt,
        height=media_h_pt,
        preserveAspectRatio=False,
        mask=None,
    )

    # 7. Marks
    if trim_marks:
        _draw_trim_marks(c, bleed_pt, trim_w_pt, trim_h_pt)
    if registration_marks:
        _draw_registration_marks(c, bleed_pt, trim_w_pt, trim_h_pt)

    # 8. Output intent (ICC profile)
    icc_embedded = False
    if icc_used:
        icc_embedded = _attach_output_intent(
            c, settings.print_icc_path, settings.print_icc_name
        )

    c.showPage()
    c.save()

    size_bytes = out_path.stat().st_size
    log.info(
        "pdf_exported",
        path=str(out_path),
        icc_label=icc_label,
        icc_embedded=icc_embedded,
        size_bytes=size_bytes,
        trim_mm=trim_mm,
        bleed_mm=bleed_mm,
    )
    return PdfExportResult(
        path=out_path,
        size_bytes=size_bytes,
        trim_mm=trim_mm,
        bleed_mm=bleed_mm,
        dpi=dpi,
        icc_profile=icc_label,
        icc_embedded=icc_embedded,
        trim_marks=trim_marks,
        registration_marks=registration_marks,
    )


# ----------------------------------------------------------------- helpers


def _extend_with_mirror_bleed(
    image: Image.Image, bleed_px_x: int, bleed_px_y: int
) -> Image.Image:
    """Add bleed strips around ``image`` by mirroring the outer pixels.

    Standard press-industry approach: take the outermost ``bleed_px``
    pixels of each edge, flip them, paste outside the trim. Solid
    backgrounds extend cleanly; designs near the edge get a soft mirror
    that 99% of the time looks intentional once the sheet is trimmed.

    Returns a new image of size ``(w + 2*bleed_px_x, h + 2*bleed_px_y)``.
    Pass-through when both bleed dimensions are zero.
    """
    if bleed_px_x <= 0 and bleed_px_y <= 0:
        return image

    w, h = image.size
    bx = max(0, bleed_px_x)
    by = max(0, bleed_px_y)
    new = Image.new(image.mode, (w + 2 * bx, h + 2 * by))
    new.paste(image, (bx, by))

    if bx > 0:
        # Left strip: flip the left bx columns left↔right and place them
        # outside the trim.
        left = image.crop((0, 0, bx, h))
        new.paste(ImageOps.mirror(left), (0, by))
        right = image.crop((w - bx, 0, w, h))
        new.paste(ImageOps.mirror(right), (bx + w, by))

    if by > 0:
        # Top strip: same with the top by rows flipped top↔bottom.
        top = new.crop((0, by, w + 2 * bx, by + by))
        new.paste(ImageOps.flip(top), (0, 0))
        bottom = new.crop((0, by + h - by, w + 2 * bx, by + h))
        new.paste(ImageOps.flip(bottom), (0, by + h))

    return new


def _to_cmyk(
    source_png_path: Path, icc_path_str: str
) -> tuple[Image.Image, str, bool]:
    """Return (cmyk_pil_image, human_label, used_icc).

    Falls back to ``Image.convert("CMYK")`` if the ICC file is missing or
    fails to load. Designers see the label in ``/api/health`` so they
    know which path was taken.
    """
    with Image.open(source_png_path) as src:
        src.load()
        rgb = src.convert("RGB")

    icc_path = Path(icc_path_str)
    if icc_path.is_file():
        try:
            src_profile = ImageCms.createProfile("sRGB")
            dst_profile = ImageCms.getOpenProfile(str(icc_path))
            transform = ImageCms.buildTransformFromOpenProfiles(
                src_profile,
                dst_profile,
                "RGB",
                "CMYK",
                renderingIntent=ImageCms.Intent.PERCEPTUAL,
            )
            cmyk = ImageCms.applyTransform(rgb, transform)
            if cmyk is not None:
                return cmyk, icc_path.name, True
        except Exception as exc:
            log.warning("icc_apply_failed", icc=str(icc_path), error=str(exc))

    # Baseline conversion — fine for proofing, not press-grade.
    return rgb.convert("CMYK"), "Pillow baseline (no ICC)", False


def _draw_trim_marks(c: Canvas, bleed_pt: float, tw: float, th: float) -> None:
    """5 mm corner trim marks, 0.25 pt wide, in 100% K."""
    mark_len = 5 * mm
    offset = 1 * mm  # gap between mark and trim edge

    c.saveState()
    c.setStrokeColor(PROCESS_BLACK)
    c.setLineWidth(0.25)

    x0, y0 = bleed_pt, bleed_pt
    x1, y1 = bleed_pt + tw, bleed_pt + th

    # Each corner has one vertical + one horizontal mark sitting outside
    # the trim with a small gap so they don't print *over* the trim.
    for x, y, dx, dy in [
        # Bottom-left
        (x0, y0 - offset, 0, -mark_len),
        (x0 - offset, y0, -mark_len, 0),
        # Bottom-right
        (x1, y0 - offset, 0, -mark_len),
        (x1 + offset, y0, mark_len, 0),
        # Top-left
        (x0, y1 + offset, 0, mark_len),
        (x0 - offset, y1, -mark_len, 0),
        # Top-right
        (x1, y1 + offset, 0, mark_len),
        (x1 + offset, y1, mark_len, 0),
    ]:
        c.line(x, y, x + dx, y + dy)

    c.restoreState()


def _draw_registration_marks(c: Canvas, bleed_pt: float, tw: float, th: float) -> None:
    """Bullseye at the centre of each side, in CMYK process-black."""
    radius = 2.5 * mm
    inner = 1 * mm

    c.saveState()
    c.setStrokeColor(PROCESS_BLACK)
    c.setLineWidth(0.4)

    centres = [
        (bleed_pt + tw / 2, bleed_pt / 2),                   # bottom edge
        (bleed_pt + tw / 2, bleed_pt + th + bleed_pt / 2),   # top edge
        (bleed_pt / 2, bleed_pt + th / 2),                   # left edge
        (bleed_pt + tw + bleed_pt / 2, bleed_pt + th / 2),   # right edge
    ]
    for cx, cy in centres:
        # outer circle
        c.circle(cx, cy, radius, stroke=1, fill=0)
        # inner crosshair
        c.line(cx - radius, cy, cx - inner, cy)
        c.line(cx + inner, cy, cx + radius, cy)
        c.line(cx, cy - radius, cx, cy - inner)
        c.line(cx, cy + inner, cx, cy + radius)

    c.restoreState()


def _attach_output_intent(c: Canvas, icc_path: str, label: str) -> bool:
    """Embed an ICC profile as a PDF/X output intent.

    Implemented against ReportLab's low-level ``PDFICCProfile`` /
    ``PDFOutputIntent`` helpers; both have stable signatures in 3.x+.
    Returns False if anything goes wrong — we still produce a valid PDF,
    it just won't carry an OutputIntent.
    """
    try:
        # Lazy import — ReportLab's pdfdoc surface area is large and we
        # only need a handful of constructors.
        from reportlab.pdfbase import pdfdoc
    except Exception:  # pragma: no cover
        return False

    try:
        icc_bytes = Path(icc_path).read_bytes()

        # Embed the ICC as a PDFStream.
        icc_stream = pdfdoc.PDFStream(
            dictionary=pdfdoc.PDFDictionary({"N": 4}),
            content=icc_bytes,
            filters=[pdfdoc.PDFStreamFilterZCompress()],
        )
        icc_ref = c._doc.Reference(icc_stream)

        # Build the OutputIntent dict (GTS_PDFX style).
        output_intent = pdfdoc.PDFDictionary(
            {
                "Type": pdfdoc.PDFName("OutputIntent"),
                "S": pdfdoc.PDFName("GTS_PDFX"),
                "OutputConditionIdentifier": pdfdoc.PDFString(label),
                "OutputCondition": pdfdoc.PDFString(label),
                "RegistryName": pdfdoc.PDFString("http://www.color.org"),
                "Info": pdfdoc.PDFString(f"Forme Studio · {label}"),
                "DestOutputProfile": icc_ref,
            }
        )

        # Catalog-level OutputIntents array
        c._doc.Catalog.OutputIntents = pdfdoc.PDFArray([output_intent])
        return True
    except Exception as exc:
        log.warning("output_intent_failed", error=str(exc))
        return False


def derive_export_filename(asset_id: int) -> str:
    """Filename rule for a PDF export."""
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    return f"asset{asset_id}_{ts}.pdf"
