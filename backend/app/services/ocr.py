"""OCR service — Tesseract wrapper for Tier C editable-text layers.

Why Tesseract: it's local, free, installs in one ``brew install``,
handles English packaging copy adequately, and doesn't require us to
spin up another GPU box. If Tier C needs more accuracy, swap this
service for OpenAI Vision or PaddleOCR later — the contract is the same.
"""

from __future__ import annotations

import io
import shutil
from dataclasses import dataclass

import pytesseract
import structlog
from PIL import Image

from app.config import get_settings

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class TextRegion:
    """One detected word + its bbox + confidence."""

    text: str
    bbox: tuple[int, int, int, int]  # (left, top, right, bottom)
    confidence: float                # 0–100; 95+ is reliable
    line_id: int                     # words on the same line share an id


@dataclass(frozen=True)
class OcrResult:
    regions: list[TextRegion]
    lang: str


class OcrUnavailableError(RuntimeError):
    """Raised when Tesseract CLI isn't installed at the configured path."""


def is_available() -> bool:
    """True if the configured Tesseract CLI is resolvable."""
    settings = get_settings()
    return shutil.which(settings.tesseract_cmd) is not None


def extract(image_bytes: bytes, min_confidence: float = 60.0) -> OcrResult:
    """Run Tesseract over a PNG, return clean TextRegion records.

    Words below ``min_confidence`` are dropped — Tesseract returns lots
    of noise on coloured backgrounds, and packaging shots are messy.

    Raises:
        OcrUnavailableError: Tesseract binary isn't on the configured path.
    """
    settings = get_settings()
    binary = shutil.which(settings.tesseract_cmd)
    if binary is None:
        msg = (
            f"Tesseract binary '{settings.tesseract_cmd}' not found. "
            "Install with `brew install tesseract` or set FORME_TESSERACT_CMD."
        )
        raise OcrUnavailableError(msg)

    pytesseract.pytesseract.tesseract_cmd = binary

    with Image.open(io.BytesIO(image_bytes)) as im:
        rgb = im.convert("RGB")
        data = pytesseract.image_to_data(
            rgb,
            lang=settings.tesseract_lang,
            output_type=pytesseract.Output.DICT,
        )

    regions: list[TextRegion] = []
    n = len(data["text"])
    for i in range(n):
        raw = (data["text"][i] or "").strip()
        if not raw:
            continue
        conf = float(data["conf"][i])
        if conf < min_confidence:
            continue
        left = int(data["left"][i])
        top = int(data["top"][i])
        width = int(data["width"][i])
        height = int(data["height"][i])
        # Tesseract assigns line_num within each block; we collapse to a
        # globally unique line id by combining block+par+line.
        line_id = (
            int(data["block_num"][i]) * 10_000
            + int(data["par_num"][i]) * 100
            + int(data["line_num"][i])
        )
        regions.append(
            TextRegion(
                text=raw,
                bbox=(left, top, left + width, top + height),
                confidence=conf,
                line_id=line_id,
            )
        )

    log.info(
        "ocr_extracted",
        word_count=len(regions),
        lang=settings.tesseract_lang,
    )
    return OcrResult(regions=regions, lang=settings.tesseract_lang)
