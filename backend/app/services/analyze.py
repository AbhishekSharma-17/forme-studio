"""Unified design analyzer — graphic discovery + text discovery in one pass.

Phase 1 of the unified Composable pipeline. Given a finished sticker /
label PNG, this:

  1. Runs ``vision.discover_elements()`` for graphic elements (logos,
     wordmarks, illustrations, ornaments). Output: list[ElementSpec] with
     kind != "text".
  2. Runs ``ocr.extract()`` for text regions. Output: list[TextRegion].
  3. Collapses OCR words into logical *blocks* (by Tesseract's line_id,
     merged into multi-line paragraphs when contiguous) so the review UI
     doesn't drown in 50+ single-word entries.
  4. Maps each text block to an ``ElementSpec(kind="text")`` with the
     OCR'd string in ``text`` and the average word confidence in
     ``confidence``.
  5. Merges + sorts top-to-bottom, left-to-right by ``position_mm`` so
     the review UI feels natural.

The review UI then shows one unified list (graphics + text) where the
user can edit any element. Phase 3 assembly branches on ``kind``:
``text`` → Pillow rasterizer, everything else → gpt-image-2.

If OCR is unavailable (binary missing or disabled), we still return the
graphic manifest — text is best-effort, not load-bearing.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from openai import AsyncOpenAI
from PIL import Image

from app.services.ocr import OcrResult, OcrUnavailableError, TextRegion
from app.services.ocr import extract as ocr_extract
from app.services.vision import ElementSpec, discover_elements

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class AnalyzeResult:
    """The output of one unified analyze pass."""

    elements: list[ElementSpec]
    image_px: tuple[int, int]    # source image dimensions
    trim_mm: tuple[float, float]
    ocr_available: bool           # False when Tesseract isn't installed
    ocr_lang: str | None          # the lang Tesseract ran with, or None


async def analyze(
    client: AsyncOpenAI,
    image_bytes: bytes,
    *,
    trim_mm: tuple[float, float],
    extra_hint: str | None = None,
    run_ocr: bool = True,
) -> AnalyzeResult:
    """Discover graphic + text elements and return the unified manifest.

    Args:
        client: authenticated OpenAI client for the vision pass.
        image_bytes: PNG/JPEG bytes of the design.
        trim_mm: ``(width, height)`` trim in millimetres. Drives both the
            vision-prompt coords AND the px→mm conversion for OCR bboxes.
        extra_hint: optional designer hint passed through to vision.
        run_ocr: set False to skip the OCR pass entirely (useful when
            you know there's no text, or want to save the Tesseract call).
    """
    # Source image dimensions — needed to convert OCR pixel bboxes to mm.
    img_w_px, img_h_px = _image_dimensions(image_bytes)

    log.info(
        "analyze_start",
        bytes=len(image_bytes),
        image_px=(img_w_px, img_h_px),
        trim_mm=trim_mm,
        run_ocr=run_ocr,
    )

    graphics = await discover_elements(
        client, image_bytes, trim_mm=trim_mm, extra_hint=extra_hint
    )
    # Vision can sometimes still emit text-bearing kinds even though the
    # updated prompt asks it not to. Filter them out — OCR is the source
    # of truth for text.
    graphics = [g for g in graphics if g.kind not in {"body_copy"}]

    text_elements: list[ElementSpec] = []
    ocr_available = True
    ocr_lang: str | None = None

    if run_ocr:
        try:
            ocr_result = ocr_extract(image_bytes)
            ocr_lang = ocr_result.lang
            text_elements = _ocr_to_elements(
                ocr_result,
                image_px=(img_w_px, img_h_px),
                trim_mm=trim_mm,
            )
        except OcrUnavailableError as exc:
            log.warning("analyze_ocr_unavailable", error=str(exc))
            ocr_available = False
    else:
        ocr_available = False

    merged = _merge_and_sort(graphics, text_elements)

    log.info(
        "analyze_done",
        graphics=len(graphics),
        text=len(text_elements),
        total=len(merged),
        ocr_available=ocr_available,
    )

    return AnalyzeResult(
        elements=merged,
        image_px=(img_w_px, img_h_px),
        trim_mm=trim_mm,
        ocr_available=ocr_available,
        ocr_lang=ocr_lang,
    )


# ─────────────────────────────────────────────────────────────────────────
#  internals
# ─────────────────────────────────────────────────────────────────────────


def _image_dimensions(image_bytes: bytes) -> tuple[int, int]:
    """Peek at a PNG/JPEG and return ``(width_px, height_px)``."""
    import io

    with Image.open(io.BytesIO(image_bytes)) as im:
        return im.size


def _ocr_to_elements(
    ocr: OcrResult,
    *,
    image_px: tuple[int, int],
    trim_mm: tuple[float, float],
) -> list[ElementSpec]:
    """Collapse OCR words into block-level ElementSpecs.

    Tesseract returns one record per word, which would flood the review
    UI. We group by ``line_id`` to get one entry per detected text line,
    then merge adjacent lines whose bboxes touch vertically into a single
    "block" element — that's typically how a designer thinks about copy
    ("the headline", "the ingredients paragraph"), not word-by-word.
    """
    if not ocr.regions:
        return []

    img_w_px, img_h_px = image_px
    trim_w_mm, trim_h_mm = trim_mm
    mm_per_px_w = trim_w_mm / img_w_px if img_w_px else 0.0
    mm_per_px_h = trim_h_mm / img_h_px if img_h_px else 0.0

    # 1. Group words by line_id.
    lines: dict[int, list[TextRegion]] = {}
    for r in ocr.regions:
        lines.setdefault(r.line_id, []).append(r)

    # 2. Build per-line records with merged bbox + concatenated text.
    line_records: list[_LineRecord] = []
    for line_id, words in lines.items():
        words_sorted = sorted(words, key=lambda w: w.bbox[0])
        text = " ".join(w.text for w in words_sorted)
        left = min(w.bbox[0] for w in words_sorted)
        top = min(w.bbox[1] for w in words_sorted)
        right = max(w.bbox[2] for w in words_sorted)
        bottom = max(w.bbox[3] for w in words_sorted)
        avg_conf = sum(w.confidence for w in words_sorted) / len(words_sorted)
        line_records.append(
            _LineRecord(
                line_id=line_id,
                text=text,
                bbox=(left, top, right, bottom),
                confidence=avg_conf,
            )
        )

    # 3. Sort lines top-to-bottom, then merge vertically-adjacent ones
    #    (gap < median line-height) into paragraph blocks.
    line_records.sort(key=lambda r: (r.bbox[1], r.bbox[0]))
    blocks = _merge_lines_into_blocks(line_records)

    # 4. Convert each block to an ElementSpec(kind="text").
    elements: list[ElementSpec] = []
    for idx, block in enumerate(blocks):
        left_px, top_px, right_px, bottom_px = block.bbox
        x_mm = left_px * mm_per_px_w
        y_mm = top_px * mm_per_px_h
        w_mm = (right_px - left_px) * mm_per_px_w
        h_mm = (bottom_px - top_px) * mm_per_px_h
        # Pick the gpt-image-2 native aspect closest to the block — used
        # by the Pillow text renderer to choose the working canvas in
        # Phase 3 assembly. Aspect-based selection keeps font size sane.
        aspect = (right_px - left_px) / max(1, bottom_px - top_px)
        if aspect > 1.3:
            size_px = "1536x1024"
        elif aspect < 0.77:
            size_px = "1024x1536"
        else:
            size_px = "1024x1024"

        # Slug for the layer name.
        slug = f"text_{idx + 1:02d}"
        # Short label preview for the UI.
        preview = block.text if len(block.text) <= 40 else block.text[:37] + "…"

        elements.append(
            ElementSpec(
                name=slug,
                label=preview,
                prompt=block.text,  # informational; assembly doesn't gpt-image-2 text
                position_mm=(x_mm, y_mm, w_mm, h_mm),
                size_px=size_px,
                kind="text",
                text=block.text,
                confidence=block.confidence,
                vectorizable=False,
            )
        )

    return elements


@dataclass(frozen=True)
class _LineRecord:
    line_id: int
    text: str
    bbox: tuple[int, int, int, int]
    confidence: float


def _merge_lines_into_blocks(
    lines: list[_LineRecord],
) -> list[_LineRecord]:
    """Merge vertically-adjacent lines into paragraph blocks.

    For each line (processed in top-to-bottom order), find any *existing
    block* that this line is a continuation of:

    * vertical gap to the block's bottom is in ``[0, ~80% median height]``
      (next line strictly below, close enough to be the same paragraph)
    * horizontal bboxes overlap (so a left-column line can't merge with
      a right-column block)

    Lookback (rather than purely pairwise next-comparison) is essential
    in multi-column layouts where rows of left + right column words are
    interleaved in the sort order.
    """
    if not lines:
        return []

    heights = [r.bbox[3] - r.bbox[1] for r in lines]
    median_h = sorted(heights)[len(heights) // 2]
    gap_threshold = max(1, int(0.8 * median_h))

    # Track each block's source-line count so we can weight-average the
    # confidence as lines accrue.
    blocks: list[_LineRecord] = []
    block_line_counts: list[int] = []

    for line in lines:
        merged_into: int | None = None
        for i, block in enumerate(blocks):
            gap = line.bbox[1] - block.bbox[3]
            # gap < 0 means same row (different column) — reject.
            # gap > threshold means too far (different paragraph) — reject.
            if gap < 0 or gap > gap_threshold:
                continue
            h_overlap = (
                min(block.bbox[2], line.bbox[2])
                - max(block.bbox[0], line.bbox[0])
            )
            if h_overlap <= 0:
                continue
            # Eligible — merge into this block.
            merged_bbox = (
                min(block.bbox[0], line.bbox[0]),
                min(block.bbox[1], line.bbox[1]),
                max(block.bbox[2], line.bbox[2]),
                max(block.bbox[3], line.bbox[3]),
            )
            n = block_line_counts[i]
            blended_conf = (block.confidence * n + line.confidence) / (n + 1)
            blocks[i] = _LineRecord(
                line_id=block.line_id,
                text=f"{block.text}\n{line.text}",
                bbox=merged_bbox,
                confidence=blended_conf,
            )
            block_line_counts[i] = n + 1
            merged_into = i
            break
        if merged_into is None:
            blocks.append(line)
            block_line_counts.append(1)
    return blocks


def _merge_and_sort(
    graphics: list[ElementSpec],
    text: list[ElementSpec],
) -> list[ElementSpec]:
    """Combine the two streams and sort top-to-bottom, left-to-right.

    Sort key is the bounding-box top-left in mm. Tie-breaker on `name`
    so the ordering is stable across re-runs.
    """
    combined = [*graphics, *text]
    combined.sort(key=lambda e: (e.position_mm[1], e.position_mm[0], e.name))
    return combined
