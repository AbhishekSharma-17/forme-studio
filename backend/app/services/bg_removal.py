"""Server-side background removal — rembg / u2net (free, local, ONNX).

gpt-image-2 doesn't honour OpenAI's ``background="transparent"`` parameter
on the snapshot we pin, and an in-prompt directive only nudges it toward
a clean *visual* backdrop — the returned PNG is still 100% opaque. To get
a real RGBA matte we run every generated element through rembg's u2net
ONNX model after generation.

Why rembg:

* **Free** — no per-call API cost, no subscription. MIT licensed.
* **Local** — runs in-process on CPU. One-time ~176 MB model download
  cached under ``~/.u2net/``, then ~1.5 s per call on a modern Mac.
* **Quality** — for our use case (logos, wordmarks, ornaments, seals on
  clean gpt-image-2 backdrops) the alpha matte is essentially perfect:
  >90 % transparent on the backdrop, soft anti-aliased edges around the
  subject.

The compose pipeline (``app.services.compose.generate_element``) calls
this *after* gpt-image-2 returns the RGB PNG. Failure isn't fatal — if
rembg isn't available or errors out we fall back to a luminance-keying
heuristic, then to the input unchanged.

The session is built lazily on first call so module import stays cheap
and the FastAPI startup time isn't blocked on ONNX initialisation.
"""

from __future__ import annotations

import asyncio
import io
from typing import Any

import structlog
from PIL import Image

log = structlog.get_logger(__name__)


# Cached u2net session. Built on the first call and reused for the lifetime
# of the worker. ``None`` once we've tried + failed (we fall back instead).
_rembg_session: Any = None
_rembg_unavailable: bool = False


def _build_session() -> Any:
    """Construct (or return cached) rembg session. None if rembg isn't installed."""
    global _rembg_session, _rembg_unavailable
    if _rembg_session is not None:
        return _rembg_session
    if _rembg_unavailable:
        return None
    try:
        from rembg import new_session  # type: ignore[import-untyped]
    except ImportError as exc:
        log.warning("rembg_import_failed_falling_back", error=str(exc))
        _rembg_unavailable = True
        return None
    try:
        _rembg_session = new_session("u2net")
        return _rembg_session
    except Exception as exc:
        log.warning("rembg_session_build_failed_falling_back", error=str(exc))
        _rembg_unavailable = True
        return None


def _rembg_remove(png_bytes: bytes) -> bytes:
    """Synchronous rembg call. Returns input unchanged on failure."""
    session = _build_session()
    if session is None:
        return _pillow_luminance_key(png_bytes)
    try:
        from rembg import remove

        return bytes(remove(png_bytes, session=session))
    except Exception as exc:
        log.warning("rembg_remove_failed_falling_back", error=str(exc))
        return _pillow_luminance_key(png_bytes)


def _pillow_luminance_key(png_bytes: bytes, *, threshold: int = 240) -> bytes:
    """Fallback: key out near-white pixels to alpha=0.

    Works well for clean light backgrounds (which is exactly what
    gpt-image-2 produces under the transparent-bg prompt directive).
    Less reliable for arbitrary subjects — that's why rembg is primary.

    Args:
        png_bytes: input PNG bytes (RGB or RGBA).
        threshold: luminance cutoff [0–255]. Pixels above → alpha 0.

    Returns:
        RGBA PNG bytes with the backdrop keyed to transparent + a soft
        edge feather over ``threshold-30 ≤ luma ≤ threshold``.
    """
    try:
        # NumPy is already a hard dep via Pillow's image pipeline.
        import numpy as np

        with Image.open(io.BytesIO(png_bytes)) as img:
            rgba = img.convert("RGBA")
        arr = np.array(rgba)
        rgb = arr[..., :3].astype(np.int16)
        luma = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
        # Hard kill above threshold.
        hard_mask = luma > threshold
        arr[hard_mask, 3] = 0
        # Soft feather over a 30-step window to avoid jagged halos.
        soft_low = threshold - 30
        soft_mask = (luma > soft_low) & (luma <= threshold) & ~hard_mask
        soft_alpha = ((threshold - luma[soft_mask]) / 30.0 * 255).clip(0, 255)
        arr[soft_mask, 3] = soft_alpha.astype(np.uint8)
        out = Image.fromarray(arr, "RGBA")
        buf = io.BytesIO()
        out.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as exc:
        log.warning("pillow_luminance_key_failed", error=str(exc))
        return png_bytes


async def remove_background(png_bytes: bytes) -> bytes:
    """Async wrapper — runs the (CPU-bound) rembg call on a worker thread.

    Idempotent: passing an already-transparent PNG through is safe; rembg
    will simply re-detect the subject and produce an essentially-identical
    matte.

    Args:
        png_bytes: input PNG bytes (RGB or RGBA, any size).

    Returns:
        RGBA PNG bytes with the background keyed to alpha=0. Falls back to
        Pillow luminance keying, then to the input unchanged, if rembg is
        unavailable or fails.
    """
    return await asyncio.to_thread(_rembg_remove, png_bytes)
