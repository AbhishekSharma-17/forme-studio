"""Composable PSD — generate each visual element fresh on a transparent
canvas via gpt-image-2 (graphics) or Pillow (text), then assemble into
a properly-layered PSD.

This is the user-facing answer to "I want designer-grade output, not a
flat baked image." Architecture:

  1. ``app.services.analyze.analyze`` → unified manifest (graphics + OCR text)
  2. ``generate_element`` → transparent PNG bytes per element
     • ``kind="text"`` → Pillow renders the confirmed string
     • everything else → gpt-image-2 with transparent_background=True
  3. ``assemble_composable_psd`` → CMYK 300 DPI PSD with one layer per
     element, positioned in millimetres relative to the trim.

Each element is reborn cleanly with native alpha — no slicing of an
existing image, no segmentation step.
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
from PIL import Image, ImageDraw, ImageFont
from psd_tools import PSDImage
from psd_tools.api.layers import PixelLayer

from app.services.openai_image import generate as openai_generate
from app.services.pricing import cost_from_usage
from app.services.vector import vectorize as run_vectorize
from app.services.vision import ElementSpec

log = structlog.get_logger(__name__)


# Font discovery: tried in order; first hit wins. Bundle a TTF in
# ``backend/assets/fonts/DejaVuSans.ttf`` to override the system defaults.
# The chain covers macOS dev + Linux CI/prod without requiring a binary
# committed to the repo.
_FONT_CANDIDATES: tuple[str, ...] = (
    str(Path(__file__).resolve().parent.parent.parent / "assets/fonts/DejaVuSans.ttf"),
    "/System/Library/Fonts/Helvetica.ttc",          # macOS
    "/System/Library/Fonts/HelveticaNeue.ttc",      # macOS
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # Debian/Ubuntu
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",       # Fedora/RHEL
    "/Library/Fonts/Arial.ttf",                     # macOS user fonts
)


# 1 mm in pixels at the given DPI. Used to convert position_mm → pixels
# when placing each element layer onto the PSD canvas.
def _mm_to_px(mm: float, dpi: int) -> int:
    return round(mm * dpi / 25.4)


@dataclass(frozen=True)
class GeneratedElement:
    """One element's transparent PNG + the spec that produced it.

    ``svg_bytes`` is populated when the element passed through the
    selective auto-vectorizer in slice 10c (line-art kinds get
    vectorized, photo illustrations stay raster). The SVG is used by
    :func:`assemble_composable_svg` to build a vector composite for the
    SVG/CDR export cascade.
    """

    spec: ElementSpec
    png_bytes: bytes               # RGBA PNG with alpha channel
    width_px: int
    height_px: int
    cost_usd: float                # gpt-image-2 cost only; vector cost separate
    asset_id: int | None = None    # set when persisted via assets.save_*
    svg_bytes: bytes | None = None  # optional per-element SVG (slice 10c)
    vector_cost_usd: float = 0.0    # Vectorizer.AI credit cost if applicable


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


@dataclass(frozen=True)
class CompositeSvgResult:
    """Outcome of an assemble_composable_svg call."""

    path: Path
    size_bytes: int
    width_mm: float
    height_mm: float
    element_count: int
    vector_count: int              # elements composited as <g>...</g> paths
    raster_count: int              # elements embedded as <image href="data:...">


# Kinds that should auto-vectorize by default (line art, flat colour).
# Photo-realistic illustrations stay raster — vectorizing them would
# produce thousands of messy paths. ``text`` is handled separately
# (already rendered crisply by Pillow → embedded as <text> if vector).
_AUTO_VECTORIZE_KINDS: frozenset[str] = frozenset({"wordmark", "ornament", "seal"})


def _should_vectorize(spec: ElementSpec) -> bool:
    """True if this element should be auto-vectorized during assembly.

    Decision rule:
    * ``kind in {wordmark, ornament, seal}`` → always (logos & flat-colour shapes)
    * ``vectorizable=True`` hint from vision → respect it
    * ``kind="text"`` → never (we render text as raster + SVG <text> separately)
    * everything else → no (photo illustrations etc.)

    Note on headlines / decorative type rendered by gpt-image-2: those come
    back as ``kind="headline"`` (or similar graphic kinds) — *not* ``"text"``
    — because the user wanted them generated as styled visuals, not flat
    typography. We intentionally do NOT auto-vectorize them. gpt-image-2
    output has soft anti-aliased edges and subtle gradients; Vectorizer.AI
    turns those into hundreds of overlapping paths that print poorly.
    Vision can still set ``vectorizable=true`` per-element to opt in when
    it sees a clean flat-colour rendering.
    """
    if spec.kind == "text":
        return False
    if spec.kind in _AUTO_VECTORIZE_KINDS:
        return True
    return bool(spec.vectorizable)


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
    """Render one element on a transparent canvas.

    Routes by ``spec.kind``:

    * ``"text"`` → ``_render_text_element()`` rasterises ``spec.text``
      into a clean PNG using Pillow + a system / bundled font. Zero
      OpenAI cost.
    * everything else → gpt-image-2 with ``background="transparent"``.

    Each non-text element gets the model's full attention — no surrounding
    composition to fight — so quality per element is usually higher than
    what you'd extract via segmentation from a whole-sticker render.

    Raises any OpenAI SDK exception unchanged so the caller can decide
    whether to retry or abort the whole composition.
    """
    if spec.kind == "text":
        return _render_text_element(spec)

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
#  TEXT-ELEMENT RENDERING (Pillow)
# ─────────────────────────────────────────────────────────────────────────


def _render_text_element(spec: ElementSpec) -> GeneratedElement:
    """Rasterise a text element onto a transparent canvas via Pillow.

    Why Pillow, not gpt-image-2: image models garble small print and
    multi-line dense text. We have the exact string (from OCR or user
    edit) and the bounding box — typesetting it directly with a clean
    font gives crisp, predictable output.

    The element ends up as a normal PSD pixel layer in Phase 3, named
    ``<spec.name> [type:"<text>"]`` so a future Photoshop-script pass
    can find the layer + the original string content to auto-convert
    into a real Photoshop type layer with editable text. The designer
    can also just swap the rendered pixel for their own type layer in
    Photoshop directly.
    """
    if spec.text is None or not spec.text.strip():
        raise RuntimeError(
            f"Text element '{spec.name}' is missing text content."
        )

    width_px, height_px = _parse_size_px(spec.size_px)

    # Build the transparent canvas + draw context.
    canvas = Image.new("RGBA", (width_px, height_px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    # Decide font size from the canvas height + number of text lines so
    # the rendered text actually fills the bounding box the designer
    # expects. We start with a generous estimate and binary-search down
    # if the text overflows horizontally.
    text = spec.text.strip()
    lines = text.split("\n")
    line_count = max(1, len(lines))
    # Target ~85% of available vertical space, divided across the lines.
    target_line_h = int((height_px * 0.85) / line_count)
    font, font_size = _resolve_font_for_width(
        text=text,
        target_line_height_px=target_line_h,
        max_width_px=int(width_px * 0.95),
    )

    # Measure total text block size to centre it.
    bbox = draw.multiline_textbbox(
        (0, 0), text, font=font, spacing=int(font_size * 0.15)
    )
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    offset_x = max(0, (width_px - text_w) // 2)
    offset_y = max(0, (height_px - text_h) // 2)

    draw.multiline_text(
        (offset_x, offset_y),
        text,
        font=font,
        fill=(0, 0, 0, 255),
        spacing=int(font_size * 0.15),
        align="center",
    )

    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    png_bytes = buf.getvalue()

    log.info(
        "compose_text_element_rendered",
        name=spec.name,
        font_size=font_size,
        width=width_px,
        height=height_px,
        text_chars=len(text),
    )

    return GeneratedElement(
        spec=spec,
        png_bytes=png_bytes,
        width_px=width_px,
        height_px=height_px,
        cost_usd=0.0,
    )


def _parse_size_px(size_px: str) -> tuple[int, int]:
    """Parse 'WIDTHxHEIGHT' or fall back to a square canvas."""
    try:
        w_s, h_s = size_px.lower().split("x")
        return int(w_s), int(h_s)
    except (ValueError, AttributeError):
        log.warning("compose_size_px_unparseable", got=size_px)
        return 1024, 1024


def _load_font(size_px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Walk the font-candidate chain and return the first that loads.

    Falls back to Pillow's bitmap default if no TrueType is available
    (rare on macOS / standard Linux distros).
    """
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size_px)
        except OSError:
            continue
    log.warning(
        "compose_font_fallback_to_default",
        tried=len(_FONT_CANDIDATES),
        size_px=size_px,
    )
    # Pillow's default is a 10px raster — visibly worse but ensures the
    # pipeline doesn't crash on machines with no fonts at all.
    return ImageFont.load_default()


def _resolve_font_for_width(
    *,
    text: str,
    target_line_height_px: int,
    max_width_px: int,
) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, int]:
    """Pick a font size that fits the longest line within ``max_width_px``.

    Starts at ``target_line_height_px`` and shrinks down only if the
    widest line would overflow. Returns the loaded font + its final size.
    """
    size = max(8, int(target_line_height_px))
    longest = max(text.split("\n"), key=len)
    # Temporary draw context to measure (Pillow needs one).
    measure_img = Image.new("RGBA", (max_width_px, target_line_height_px))
    draw = ImageDraw.Draw(measure_img)

    while size > 8:
        font = _load_font(size)
        bbox = draw.textbbox((0, 0), longest, font=font)
        text_w = bbox[2] - bbox[0]
        if text_w <= max_width_px:
            return font, size
        size = max(8, int(size * 0.9))

    # Smallest acceptable size; let the caller render at this.
    return _load_font(size), size


# ─────────────────────────────────────────────────────────────────────────
#  PER-ELEMENT VECTORIZATION (slice 10c)
# ─────────────────────────────────────────────────────────────────────────


async def maybe_vectorize_element(
    elem: GeneratedElement,
    *,
    vector_provider: str | None = None,
    enabled: bool = True,
) -> GeneratedElement:
    """Optionally vectorize a generated element's PNG → SVG.

    Auto-vectorizes when:
      * ``enabled=True`` AND
      * :func:`_should_vectorize(elem.spec)` returns True.

    Otherwise returns ``elem`` unchanged. The decision happens here so
    the route doesn't need to know about the policy.

    Vectorizer failures DO NOT abort the assemble — we log and continue
    with the raster element. The PSD still gets a clean layer; the SVG
    composite just embeds an ``<image>`` for that layer instead of a
    vector ``<g>``. This keeps quality > availability.
    """
    if not enabled or not _should_vectorize(elem.spec):
        return elem

    try:
        result = await run_vectorize(elem.png_bytes, provider=vector_provider)
    except Exception as exc:
        log.warning(
            "compose_element_vectorize_failed",
            name=elem.spec.name,
            error=str(exc),
        )
        return elem

    # Vectorizer.AI cost: production = 1 credit (~$0.20), test = 0.1, preview = 0.
    # We approximate the dollar cost here (Vectorizer.AI doesn't return it
    # in the response). Adjust _VECTORIZER_AI_COST_USD to match your plan.
    cost = 0.0
    if result.provider == "vectorizer_ai":
        cost = {
            "production": 0.20,
            "test": 0.02,
            "preview": 0.0,
        }.get(result.mode or "production", 0.20)

    log.info(
        "compose_element_vectorized",
        name=elem.spec.name,
        provider=result.provider,
        mode=result.mode,
        svg_bytes=result.size_bytes,
        cost_usd=cost,
    )
    return GeneratedElement(
        spec=elem.spec,
        png_bytes=elem.png_bytes,
        width_px=elem.width_px,
        height_px=elem.height_px,
        cost_usd=elem.cost_usd,
        asset_id=elem.asset_id,
        svg_bytes=result.svg_bytes,
        vector_cost_usd=cost,
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

        layer_name = _layer_name_for(elem.spec)
        layer = PixelLayer.frompil(
            resampled,
            psd,
            name=layer_name,
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


# ─────────────────────────────────────────────────────────────────────────
#  SVG COMPOSITION (slice 10c)
# ─────────────────────────────────────────────────────────────────────────


def assemble_composable_svg(
    *,
    elements: list[GeneratedElement],
    trim_mm: tuple[float, float],
    bleed_mm: float,
    out_path: Path,
) -> CompositeSvgResult:
    """Compose all elements into a single SVG document.

    Each element becomes either:

    * a ``<g transform="translate(...) scale(...)">`` wrapping the
      per-element SVG content (when ``elem.svg_bytes`` is set), OR
    * an ``<image href="data:image/png;base64,...">`` placed at the
      element's mm coordinates (raster fallback for photos / failed
      vectorizations).

    Coordinates are in mm with the SVG viewBox set to the trim+bleed
    canvas, so the output is print-ready and scales infinitely.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    trim_w_mm, trim_h_mm = trim_mm
    canvas_w_mm = trim_w_mm + 2 * bleed_mm
    canvas_h_mm = trim_h_mm + 2 * bleed_mm

    vector_count = 0
    raster_count = 0
    body_parts: list[str] = []

    for elem in elements:
        x_mm, y_mm, w_mm, h_mm = elem.spec.position_mm
        # Coordinates are relative to trim; SVG canvas includes bleed.
        gx = bleed_mm + x_mm
        gy = bleed_mm + y_mm

        if elem.svg_bytes:
            # Vector path — inline the per-element SVG, stripping its
            # own <?xml ...?> + outer <svg ...> wrapper so we can nest
            # the inner content inside a <g> with our transform.
            inner = _strip_svg_wrapper(elem.svg_bytes)
            # Per-element SVGs come back at gpt-image-2's native pixel
            # resolution. We need to scale so its width × height fills
            # the element's mm bounding box. The element's intrinsic
            # viewport size is in inner_w_px × inner_h_px (parsed from
            # the SVG root); we approximate by using elem.width_px/height_px.
            scale_x = w_mm / elem.width_px if elem.width_px else 1.0
            scale_y = h_mm / elem.height_px if elem.height_px else 1.0
            body_parts.append(
                f'  <g id="{_xml_escape(elem.spec.name)}" '
                f'transform="translate({gx:.4f},{gy:.4f}) '
                f'scale({scale_x:.6f},{scale_y:.6f})">\n'
                f"{inner}\n"
                f"  </g>"
            )
            vector_count += 1
        else:
            # Raster path — embed the PNG as a data URI <image>.
            b64 = base64.b64encode(elem.png_bytes).decode("ascii")
            body_parts.append(
                f'  <image id="{_xml_escape(elem.spec.name)}" '
                f'x="{gx:.4f}" y="{gy:.4f}" '
                f'width="{w_mm:.4f}" height="{h_mm:.4f}" '
                f'href="data:image/png;base64,{b64}" />'
            )
            raster_count += 1

    svg_doc = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{canvas_w_mm}mm" height="{canvas_h_mm}mm" '
        f'viewBox="0 0 {canvas_w_mm} {canvas_h_mm}">\n'
        + "\n".join(body_parts)
        + "\n</svg>\n"
    )
    out_path.write_text(svg_doc, encoding="utf-8")

    log.info(
        "compose_svg_assembled",
        path=str(out_path),
        vector_count=vector_count,
        raster_count=raster_count,
        size_bytes=out_path.stat().st_size,
    )
    return CompositeSvgResult(
        path=out_path,
        size_bytes=out_path.stat().st_size,
        width_mm=canvas_w_mm,
        height_mm=canvas_h_mm,
        element_count=len(elements),
        vector_count=vector_count,
        raster_count=raster_count,
    )


def _strip_svg_wrapper(svg_bytes: bytes) -> str:
    """Strip the XML declaration + outer <svg> wrapper so the inner content
    can be nested inside our own <g> with a transform.

    Robust against both Vectorizer.AI (single-line packed SVG) and
    Inkscape Potrace (pretty-printed). Falls back to the raw text if
    the structure is unrecognisable — the SVG might be malformed, but
    embedding it in a comment is worse than embedding it as-is.
    """
    text = svg_bytes.decode("utf-8", errors="replace")
    # Drop XML decl.
    if text.lstrip().startswith("<?xml"):
        text = text[text.find("?>") + 2:]
    # Find the outer <svg ...> opening tag.
    start = text.find("<svg")
    if start < 0:
        return text
    open_end = text.find(">", start)
    if open_end < 0:
        return text
    inner_start = open_end + 1
    # Find the matching closing </svg>.
    close = text.rfind("</svg>")
    if close < inner_start:
        return text[inner_start:]
    return text[inner_start:close].strip()


def _xml_escape(s: str) -> str:
    """Minimal XML escape for element IDs / attribute values."""
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def derive_composable_svg_filename(asset_id: int) -> str:
    """Filename rule for the composable SVG: ``assetX_composable_<utc>.svg``."""
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    return f"asset{asset_id}_composable_{ts}.svg"


# ─────────────────────────────────────────────────────────────────────────
#  helpers (shared)
# ─────────────────────────────────────────────────────────────────────────


def _layer_name_for(spec: ElementSpec) -> str:
    """Build the PSD layer name for an element.

    Graphics → just ``spec.name`` (snake_case slug).
    Text → ``"<name> [type:\"<truncated text>\"]"`` so a future Photoshop
    script can find the layer + extract the original text content for
    auto-conversion to a real type layer. Text is escape-encoded + capped
    at 60 chars to keep the layer panel readable.
    """
    if spec.kind == "text" and spec.text:
        safe = (
            spec.text.replace("\n", " ").replace("\t", " ").replace('"', "'").strip()
        )
        if len(safe) > 60:
            safe = safe[:57] + "…"
        return f'{spec.name} [type:"{safe}"]'
    return spec.name


def derive_composable_filename(asset_id: int) -> str:
    """Filename rule: ``assetX_composable_<utc-stamp>.psd``."""
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    return f"asset{asset_id}_composable_{ts}.psd"


def manifest_to_json(elements: list[ElementSpec]) -> list[dict[str, Any]]:
    """Serialise a manifest for the API response."""
    return [e.to_dict() for e in elements]
