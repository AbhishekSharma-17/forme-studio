"""Vector export — PNG → SVG.

Two interchangeable providers, picked by ``FORME_VECTORIZER_PROVIDER``
(or overridden per call):

* ``vectorizer_ai``     — hosted https://vectorizer.ai/api/v1/vectorize
  (paid, highest quality, returns a clean multi-colour SVG).
* ``inkscape_potrace``  — local Inkscape CLI driving the bundled
  potrace tracer (free, runs on the same box, monochrome output).

The dispatcher (:func:`vectorize`) **never** falls back automatically.
Failures bubble up as 502/503 so the UI can offer a "Try with fallback?"
button — the user explicitly decides which provider to retry with.
"""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx
import structlog
from fastapi import HTTPException, status

from app.config import Settings, get_settings

log = structlog.get_logger(__name__)

ProviderName = Literal["vectorizer_ai", "inkscape_potrace"]
_VALID_PROVIDERS: frozenset[str] = frozenset({"vectorizer_ai", "inkscape_potrace"})
_VALID_MODES: frozenset[str] = frozenset({"production", "test", "preview"})


@dataclass(frozen=True)
class VectorResult:
    """One SVG produced by either provider."""

    svg_bytes: bytes
    provider: ProviderName
    mode: str | None         # vectorizer.ai mode, or None for Inkscape
    size_bytes: int


# --------------------------------------------------------------- dispatcher


async def vectorize(
    png_bytes: bytes,
    *,
    provider: str | None = None,
) -> VectorResult:
    """Trace ``png_bytes`` into an SVG using the configured provider.

    Args:
        png_bytes: input raster.
        provider: optional override of ``FORME_VECTORIZER_PROVIDER`` —
            the UI passes this when the user picks "Try with fallback?"
            after a failure. ``None`` uses the env-configured primary.

    Raises:
        HTTPException 400 if the provider name is unknown.
        HTTPException 503 if the chosen provider has no credentials /
            tooling.
        HTTPException 502 / 504 if the provider call itself fails.
    """
    settings = get_settings()
    chosen = provider or settings.vectorizer_provider

    if chosen not in _VALID_PROVIDERS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unknown vector provider '{chosen}'. "
                f"Choose one of: {', '.join(sorted(_VALID_PROVIDERS))}."
            ),
        )

    if chosen == "vectorizer_ai":
        if not (settings.vectorizer_ai_api_id and settings.vectorizer_ai_api_key):
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Vectorizer.AI credentials are missing. Set "
                    "VECTORIZER_AI_API_ID + VECTORIZER_AI_API_KEY in .env, "
                    "or pick the 'inkscape_potrace' provider."
                ),
            )
        return await _vectorize_via_vectorizer_ai(png_bytes, settings)

    # inkscape_potrace
    if not Path(settings.inkscape_path).is_file():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"Inkscape CLI not found at {settings.inkscape_path}. "
                "Install Inkscape (`brew install --cask inkscape`) "
                "or update FORME_INKSCAPE_PATH."
            ),
        )
    return await _vectorize_via_inkscape(png_bytes, settings)


# --------------------------------------------------------- Vectorizer.AI


_VECTORIZER_AI_URL = "https://vectorizer.ai/api/v1/vectorize"


async def _vectorize_via_vectorizer_ai(
    png_bytes: bytes, settings: Settings
) -> VectorResult:
    """POST to vectorizer.ai with HTTP-Basic auth, return SVG bytes."""
    mode = settings.vectorizer_ai_mode
    if mode not in _VALID_MODES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid FORME_VECTORIZER_AI_MODE '{mode}'. "
                f"Choose one of: {', '.join(sorted(_VALID_MODES))}."
            ),
        )

    api_id = settings.vectorizer_ai_api_id
    api_key = settings.vectorizer_ai_api_key
    # Guarded above; assert for the type checker.
    assert api_id is not None
    assert api_key is not None
    auth = (api_id, api_key)

    log.info(
        "vectorizer_ai_call",
        mode=mode,
        bytes=len(png_bytes),
    )

    try:
        async with httpx.AsyncClient(timeout=settings.vectorizer_timeout_s) as http:
            resp = await http.post(
                _VECTORIZER_AI_URL,
                auth=auth,
                files={"image": ("input.png", png_bytes, "image/png")},
                data={"mode": mode, "output.file_format": "svg"},
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Vectorizer.AI timed out after {settings.vectorizer_timeout_s}s.",
        ) from exc
    except Exception as exc:
        log.exception("vectorizer_ai_request_failed")
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"Vectorizer.AI request failed: {exc}",
        ) from exc

    if resp.status_code != 200:
        # Vectorizer returns JSON error bodies — keep them short for the UI.
        detail = (resp.text or "").strip()[:400] or f"HTTP {resp.status_code}"
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"Vectorizer.AI returned {resp.status_code}: {detail}",
        )

    svg = resp.content
    if not svg or b"<svg" not in svg[:512]:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail="Vectorizer.AI returned an empty / non-SVG response.",
        )

    log.info(
        "vectorizer_ai_done",
        mode=mode,
        bytes=len(svg),
    )
    return VectorResult(
        svg_bytes=svg,
        provider="vectorizer_ai",
        mode=mode,
        size_bytes=len(svg),
    )


# ----------------------------------------------------- Inkscape + potrace


async def _vectorize_via_inkscape(
    png_bytes: bytes, settings: Settings
) -> VectorResult:
    """Shell out to ``inkscape`` to trace ``png_bytes`` with its potrace engine.

    Uses Inkscape's actions chain (introduced in 1.2): we open the PNG as
    a document, select-all, trace-bitmap, and export the result to SVG.

    The trace defaults to a black-and-white brightness threshold tracer —
    fine for a quick local preview, but designers wanting full colour
    should configure ``vectorizer_ai`` and rerun.
    """
    inkscape = settings.inkscape_path

    with tempfile.TemporaryDirectory(prefix="forme_vector_") as tmp:
        tmp_path = Path(tmp)
        input_path = tmp_path / "input.png"
        output_path = tmp_path / "output.svg"
        input_path.write_bytes(png_bytes)

        actions = (
            "select-all;"
            "trace-bitmap;"
            f"export-filename:{output_path};"
            "export-do;"
            "FileQuit"
        )
        cmd = [inkscape, "--actions", actions, str(input_path)]

        log.info("inkscape_potrace_call", cmd=cmd[0], bytes=len(png_bytes))

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=settings.vectorizer_timeout_s,
            )
        except TimeoutError as exc:
            raise HTTPException(
                status.HTTP_504_GATEWAY_TIMEOUT,
                detail=(
                    f"Inkscape Potrace timed out after "
                    f"{settings.vectorizer_timeout_s}s."
                ),
            ) from exc
        except FileNotFoundError as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Inkscape CLI not executable: {exc}",
            ) from exc
        except Exception as exc:
            log.exception("inkscape_potrace_failed")
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Inkscape Potrace call failed: {exc}",
            ) from exc

        if proc.returncode != 0 or not output_path.is_file():
            err_tail = (stderr or b"").decode("utf-8", errors="replace")[:400]
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                detail=(
                    f"Inkscape Potrace exited {proc.returncode}: "
                    f"{err_tail or 'no SVG produced'}"
                ),
            )

        svg = output_path.read_bytes()

    log.info("inkscape_potrace_done", bytes=len(svg))
    return VectorResult(
        svg_bytes=svg,
        provider="inkscape_potrace",
        mode=None,
        size_bytes=len(svg),
    )


# ----------------------------------------------------------------- naming


def derive_export_filename(asset_id: int, ext: str = "svg") -> str:
    """Build a unique-per-run filename: ``asset<id>_<utc-stamp>.svg``."""
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    return f"asset{asset_id}_{ts}.{ext}"
