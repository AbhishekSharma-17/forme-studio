"""Unit tests for the unified analyzer (slice 10a).

Exercises ``app.services.analyze.analyze`` end-to-end with stubs for:
* OpenAI vision (chat.completions returns a fixed graphic manifest)
* Tesseract OCR (a fake ``ocr_extract`` returns a known TextRegion list)

Verifies:
* graphics + text both end up in the unified manifest
* OCR words are collapsed into block-level entries (one per paragraph)
* the merged list is sorted top-to-bottom by ``position_mm``
* text element bboxes are correctly converted from pixels → mm
* ``ocr_available=False`` when OCR raises ``OcrUnavailableError``
* multi-line paragraphs merge into one block when their bboxes touch
* horizontally-separated lines stay as distinct blocks
"""

from __future__ import annotations

import io
from typing import Any

import pytest
from PIL import Image

from app.services import ocr as ocr_module
from app.services.analyze import analyze
from app.services.ocr import OcrResult, OcrUnavailableError, TextRegion

# ──────────────────────────────────────────────────────────────────────
#  helpers
# ──────────────────────────────────────────────────────────────────────


def _png_bytes(w: int = 1000, h: int = 1500) -> bytes:
    """Build a blank PNG used as the analyze input image."""
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (255, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


_GRAPHIC_MANIFEST = """
{
  "elements": [
    {
      "name": "imara_wordmark",
      "label": "IMARA wordmark",
      "prompt": "IMARA wordmark in gold. Transparent. Isolated.",
      "position_mm": [16, 8, 43, 18],
      "size_px": "1024x1024",
      "kind": "wordmark",
      "vectorizable": true
    },
    {
      "name": "sandalwood_botanical",
      "label": "Sandalwood botanical",
      "prompt": "Sandalwood leaves in gold line-art. Transparent. Isolated.",
      "position_mm": [12, 35, 51, 51],
      "size_px": "1024x1024",
      "kind": "graphic",
      "vectorizable": false
    }
  ]
}
"""


class _StubChatResponse:
    def __init__(self, content: str) -> None:
        class _Msg:
            def __init__(self, c: str) -> None:
                self.content = c

        class _Choice:
            def __init__(self, c: str) -> None:
                self.message = _Msg(c)

        self.choices = [_Choice(content)]


class _StubOpenAI:
    """Minimal OpenAI client stub for the vision pass.

    `analyze.analyze` accepts an ``AsyncOpenAI`` typed argument but only
    touches ``client.chat.completions.create``.
    """

    def __init__(self, manifest_json: str) -> None:
        manifest = manifest_json

        class _Completions:
            async def create(self, **_kwargs: Any) -> _StubChatResponse:
                return _StubChatResponse(manifest)

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


@pytest.fixture()
def patch_ocr(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch ``ocr.extract`` so we control text-discovery output."""
    holder: dict[str, Any] = {"impl": None, "calls": 0}

    def _fake_extract(image_bytes: bytes, min_confidence: float = 60.0) -> OcrResult:
        holder["calls"] += 1
        impl = holder["impl"]
        if impl is None:
            return OcrResult(regions=[], lang="eng")
        return impl(image_bytes)

    # Patch BOTH the source module AND the re-export inside analyze, since
    # `analyze.py` does `from app.services.ocr import extract as ocr_extract`
    # which captures the function reference at import time.
    monkeypatch.setattr(ocr_module, "extract", _fake_extract)
    import app.services.analyze as analyze_module

    monkeypatch.setattr(analyze_module, "ocr_extract", _fake_extract)
    return holder


# ──────────────────────────────────────────────────────────────────────
#  tests
# ──────────────────────────────────────────────────────────────────────


async def test_analyze_merges_graphics_and_text_sorted_top_to_bottom(
    patch_ocr: dict[str, Any],
) -> None:
    """Graphics from vision + text from OCR end up in one sorted list."""
    # OCR returns 3 words on 2 lines, both at the bottom of the design.
    # Image is 1000 × 1500 px, trim is 70 × 100 mm → 1 mm = ~14.3 px.
    # Words at y ≈ 1300 px → ~87 mm (below the botanical at y=35 mm).
    def _ocr(image_bytes: bytes) -> OcrResult:
        return OcrResult(
            regions=[
                TextRegion("INGREDIENTS", (50, 1300, 350, 1340), 95.0, 1001),
                TextRegion("Water,", (50, 1350, 200, 1390), 92.0, 1002),
                TextRegion("Cocamide", (210, 1350, 380, 1390), 90.0, 1002),
            ],
            lang="eng",
        )

    patch_ocr["impl"] = _ocr

    client = _StubOpenAI(_GRAPHIC_MANIFEST)
    result = await analyze(
        client,  # type: ignore[arg-type]
        _png_bytes(1000, 1500),
        trim_mm=(70.0, 100.0),
    )

    assert result.ocr_available is True
    assert result.ocr_lang == "eng"
    assert result.image_px == (1000, 1500)
    assert result.trim_mm == (70.0, 100.0)

    # 2 graphics + 1 text block — the INGREDIENTS header + the two
    # ingredient words below it have a small gap and horizontally
    # overlap, so the merge heuristic correctly collapses them into a
    # single paragraph block the user can then edit / split if needed.
    assert len(result.elements) == 3

    # Sorted by y_mm ascending. Wordmark (y=8) first, botanical (y=35)
    # second, then the OCR text block at the bottom.
    y_order = [e.position_mm[1] for e in result.elements]
    assert y_order == sorted(y_order)

    # Names check
    names = [e.name for e in result.elements]
    assert names[0] == "imara_wordmark"
    assert names[1] == "sandalwood_botanical"

    text_elements = [e for e in result.elements if e.kind == "text"]
    assert len(text_elements) == 1
    assert text_elements[0].name == "text_01"

    # The merged block contains both the header and the words below it.
    block = text_elements[0]
    assert "INGREDIENTS" in block.text
    assert "Water" in block.text
    assert block.confidence is not None and 80 < block.confidence < 100


async def test_analyze_collapses_adjacent_lines_into_block(
    patch_ocr: dict[str, Any],
) -> None:
    """Two vertically-touching lines become ONE paragraph block."""

    def _ocr(image_bytes: bytes) -> OcrResult:
        # Line A: y=100-140. Line B: y=145-185 (gap of 5 px, well under
        # the ~32 px line-height → merge). Both horizontally overlap.
        return OcrResult(
            regions=[
                TextRegion("Forme", (10, 100, 100, 140), 95.0, 1),
                TextRegion("Studio", (110, 100, 200, 140), 93.0, 1),
                TextRegion("packaging", (10, 145, 200, 185), 91.0, 2),
                TextRegion("studio", (210, 145, 300, 185), 90.0, 2),
            ],
            lang="eng",
        )

    patch_ocr["impl"] = _ocr

    client = _StubOpenAI('{"elements": []}')
    # Empty graphic manifest is invalid for vision; we test the OCR path
    # alone via run_ocr-only logic. But the vision discoverer needs a
    # non-empty list, so we feed one minimal graphic.
    client = _StubOpenAI(
        '{"elements": [{"name":"x","label":"X","prompt":"X","position_mm":[0,0,1,1],'
        '"size_px":"1024x1024","kind":"graphic","vectorizable":false}]}'
    )

    result = await analyze(
        client,  # type: ignore[arg-type]
        _png_bytes(1000, 1500),
        trim_mm=(70.0, 100.0),
    )

    text_elements = [e for e in result.elements if e.kind == "text"]
    # Both lines merge into a single block — that's the expected behaviour
    # because they're horizontally overlapping and vertically adjacent.
    assert len(text_elements) == 1
    block = text_elements[0]
    # Block text contains all four words, with a newline between lines.
    assert "Forme" in block.text
    assert "Studio" in block.text
    assert "packaging" in block.text
    assert "\n" in block.text


async def test_analyze_keeps_horizontally_separated_lines_distinct(
    patch_ocr: dict[str, Any],
) -> None:
    """Two columns of text (left/right) must NOT merge into one block."""

    def _ocr(image_bytes: bytes) -> OcrResult:
        # Left column at x=10-100, right column at x=500-700, same y range.
        return OcrResult(
            regions=[
                TextRegion("LeftA", (10, 100, 100, 140), 95.0, 1),
                TextRegion("LeftB", (10, 145, 100, 185), 93.0, 2),
                TextRegion("RightA", (500, 100, 700, 140), 92.0, 3),
                TextRegion("RightB", (500, 145, 700, 185), 91.0, 4),
            ],
            lang="eng",
        )

    patch_ocr["impl"] = _ocr
    client = _StubOpenAI(
        '{"elements": [{"name":"x","label":"X","prompt":"X","position_mm":[0,0,1,1],'
        '"size_px":"1024x1024","kind":"graphic","vectorizable":false}]}'
    )

    result = await analyze(
        client,  # type: ignore[arg-type]
        _png_bytes(1000, 1500),
        trim_mm=(70.0, 100.0),
    )

    text_elements = [e for e in result.elements if e.kind == "text"]
    # Left column lines merge (vertically adjacent, horizontally overlap)
    # → 1 left block; right column same → 1 right block. Total 2.
    assert len(text_elements) == 2


async def test_analyze_ocr_unavailable_falls_back_gracefully(
    patch_ocr: dict[str, Any],
) -> None:
    """If Tesseract isn't installed, analyze still returns graphics."""

    def _ocr(image_bytes: bytes) -> OcrResult:
        raise OcrUnavailableError("tesseract not found")

    patch_ocr["impl"] = _ocr

    client = _StubOpenAI(_GRAPHIC_MANIFEST)
    result = await analyze(
        client,  # type: ignore[arg-type]
        _png_bytes(1000, 1500),
        trim_mm=(70.0, 100.0),
    )

    assert result.ocr_available is False
    assert result.ocr_lang is None
    # Only graphic elements
    assert all(e.kind != "text" for e in result.elements)
    assert len(result.elements) == 2


async def test_analyze_skips_ocr_when_run_ocr_false(
    patch_ocr: dict[str, Any],
) -> None:
    """run_ocr=False bypasses the Tesseract call entirely."""

    def _ocr(image_bytes: bytes) -> OcrResult:
        raise AssertionError("OCR should NOT be called when run_ocr=False")

    patch_ocr["impl"] = _ocr

    client = _StubOpenAI(_GRAPHIC_MANIFEST)
    result = await analyze(
        client,  # type: ignore[arg-type]
        _png_bytes(1000, 1500),
        trim_mm=(70.0, 100.0),
        run_ocr=False,
    )

    assert result.ocr_available is False
    assert patch_ocr["calls"] == 0


async def test_analyze_text_bbox_pixel_to_mm_conversion(
    patch_ocr: dict[str, Any],
) -> None:
    """OCR pixel bboxes get correctly scaled to mm using image/trim ratio.

    Image: 1000 × 1500 px, trim: 70 × 100 mm → 1 mm = 10/0.7 px horizontally,
    10/0.7 ≈ 14.286 px/mm vertically.  A word at px (140, 150) → ~9.8 mm, ~10 mm.
    """

    def _ocr(image_bytes: bytes) -> OcrResult:
        return OcrResult(
            regions=[
                # Single word: bbox (140 px, 150 px, 280 px, 300 px)
                TextRegion("Imara", (140, 150, 280, 300), 95.0, 1),
            ],
            lang="eng",
        )

    patch_ocr["impl"] = _ocr
    client = _StubOpenAI(
        '{"elements": [{"name":"x","label":"X","prompt":"X","position_mm":[50,50,1,1],'
        '"size_px":"1024x1024","kind":"graphic","vectorizable":false}]}'
    )

    result = await analyze(
        client,  # type: ignore[arg-type]
        _png_bytes(1000, 1500),
        trim_mm=(70.0, 100.0),
    )

    text_elements = [e for e in result.elements if e.kind == "text"]
    assert len(text_elements) == 1
    block = text_elements[0]

    # px-to-mm: 1000 px = 70 mm → 1 px = 0.07 mm. 140 px = 9.8 mm.
    x_mm, y_mm, w_mm, h_mm = block.position_mm
    assert x_mm == pytest.approx(140 * 70 / 1000, abs=0.01)
    assert y_mm == pytest.approx(150 * 100 / 1500, abs=0.01)
    assert w_mm == pytest.approx((280 - 140) * 70 / 1000, abs=0.01)
    assert h_mm == pytest.approx((300 - 150) * 100 / 1500, abs=0.01)


async def test_analyze_text_size_px_matches_aspect(
    patch_ocr: dict[str, Any],
) -> None:
    """Wide text blocks get a landscape size_px; tall ones portrait."""

    def _ocr(image_bytes: bytes) -> OcrResult:
        return OcrResult(
            regions=[
                # Wide block: 300x40 → aspect 7.5 → landscape
                TextRegion("WIDE HEADLINE", (100, 100, 400, 140), 95.0, 1),
            ],
            lang="eng",
        )

    patch_ocr["impl"] = _ocr
    client = _StubOpenAI(
        '{"elements": [{"name":"x","label":"X","prompt":"X","position_mm":[0,0,1,1],'
        '"size_px":"1024x1024","kind":"graphic","vectorizable":false}]}'
    )

    result = await analyze(
        client,  # type: ignore[arg-type]
        _png_bytes(1000, 1500),
        trim_mm=(70.0, 100.0),
    )

    text_elements = [e for e in result.elements if e.kind == "text"]
    assert text_elements[0].size_px == "1536x1024"


async def test_analyze_filters_body_copy_from_vision_output(
    patch_ocr: dict[str, Any],
) -> None:
    """If vision sneaks body_copy through, the analyzer drops it.

    OCR is the source of truth for text — body_copy from vision would
    duplicate what OCR finds.
    """
    patch_ocr["impl"] = lambda _b: OcrResult(regions=[], lang="eng")
    manifest_with_body_copy = """
    {
      "elements": [
        {
          "name": "real_graphic",
          "label": "Graphic",
          "prompt": "A graphic. Transparent. Isolated.",
          "position_mm": [0, 0, 10, 10],
          "size_px": "1024x1024",
          "kind": "graphic",
          "vectorizable": false
        },
        {
          "name": "stray_body_copy",
          "label": "Body",
          "prompt": "Should be filtered",
          "position_mm": [0, 50, 10, 10],
          "size_px": "1024x1024",
          "kind": "body_copy",
          "vectorizable": false
        }
      ]
    }
    """
    client = _StubOpenAI(manifest_with_body_copy)

    result = await analyze(
        client,  # type: ignore[arg-type]
        _png_bytes(1000, 1500),
        trim_mm=(70.0, 100.0),
    )

    names = {e.name for e in result.elements}
    assert "real_graphic" in names
    assert "stray_body_copy" not in names
