"""Normalize uploaded images to a format gpt-image-2 will reliably accept.

The OpenAI ``client.images.edit`` endpoint occasionally rejects iPhone JPEGs
(EXIF orientation), progressive JPEGs, palette/CMYK PNGs, and oversized
files with ``Invalid image file or mode for image N``. Re-encoding every
upload as a baseline RGBA PNG with rotation baked in and metadata
stripped sidesteps all of these.

Same playbook we proved in opneai-image2, ported into a Forme-shaped
service that emits ``NormalizedImage`` records (bytes + dimensions +
sniffed mime).
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

# gpt-image-2 accepts reference inputs up to 3840 px on the long edge.
MAX_EDGE_PX = 3840


class NormalizeError(ValueError):
    """Raised when the caller's bytes aren't a usable image."""


@dataclass(frozen=True)
class NormalizedImage:
    filename: str          # cleaned, .png extension
    data: bytes            # the PNG bytes
    mime_type: str         # always "image/png"
    width: int
    height: int


def normalize(filename: str, raw: bytes) -> NormalizedImage:
    """Return a clean RGBA PNG ready for storage or upload.

    Args:
        filename: original filename (used to derive the stem of the new name).
        raw: raw uploaded bytes.

    Raises:
        NormalizeError: bytes don't decode as an image.
    """
    try:
        with Image.open(io.BytesIO(raw)) as src:
            src.load()
            rotated = ImageOps.exif_transpose(src) or src
            rgba = rotated.convert("RGBA")
            w, h = rgba.size
            longest = max(w, h)
            if longest > MAX_EDGE_PX:
                scale = MAX_EDGE_PX / longest
                rgba = rgba.resize(
                    (max(1, int(w * scale)), max(1, int(h * scale))),
                    Image.Resampling.LANCZOS,
                )
                w, h = rgba.size
            buf = io.BytesIO()
            rgba.save(buf, format="PNG", optimize=False)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise NormalizeError(f"Could not read image '{filename}': {exc}") from exc

    stem = Path(filename).stem or "image"
    return NormalizedImage(
        filename=f"{stem}.png",
        data=buf.getvalue(),
        mime_type="image/png",
        width=w,
        height=h,
    )
