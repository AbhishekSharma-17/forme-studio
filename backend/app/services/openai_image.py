"""Thin async wrapper around OpenAI's ``client.images.generate``.

Exposes two entry points used by the packaging routes:

* :func:`generate` — non-streaming. Returns final bytes + usage tokens.
  Used by tests, the non-stream fallback route, and future batch jobs.
* :func:`generate_stream` — async generator that yields partial-image
  frames as they arrive, plus a final ``completed`` event with the full
  bytes + usage. Used to drive the SSE endpoint.

Both honour the workspace's *frozen* generation size — the caller passes
the size; the service never picks it itself.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from typing import Any, Literal, TypedDict

import structlog
from openai import AsyncOpenAI

from app.services.pricing import usage_to_dict

log = structlog.get_logger(__name__)


class GenerationEvent(TypedDict, total=False):
    """One event in the streaming generate flow.

    Discriminated by ``type``:
      * ``partial``    — partial image frame; carries ``variant_index`` and ``image_b64``.
      * ``completed``  — final image; carries ``variant_index``, ``image_b64``, ``usage``.
      * ``error``      — fatal error; carries ``message``.
    """

    type: Literal["partial", "completed", "error"]
    variant_index: int
    image_b64: str
    usage: dict[str, int]
    message: str


class GenerationResult(TypedDict):
    """Return shape of :func:`generate`."""

    images_b64: list[str]
    usage: dict[str, int]


# ---------- non-streaming ------------------------------------------------


# Prompt prefix used when ``transparent_background=True``. The currently
# pinned gpt-image-2 snapshot rejects the API-level ``background="transparent"``
# kwarg, so we ask the model directly in the prompt — gpt-image-2 honours
# the instruction reliably and returns an RGBA PNG with the subject isolated
# on a transparent canvas. Worded firmly so the model can't silently fall
# back to a white/checker fill.
_TRANSPARENT_BG_PROMPT_PREFIX = (
    "Render this on a fully transparent background. Isolated subject only — "
    "no backdrop, no surroundings, no colour fill, no shadow plate. Output a "
    "clean PNG with an alpha channel showing just the subject and nothing else. "
)


async def generate(
    client: AsyncOpenAI,
    *,
    model: str,
    prompt: str,
    size: str,
    quality: str = "high",
    n: int = 1,
    timeout: float | None = None,
    transparent_background: bool = False,
) -> GenerationResult:
    """Generate ``n`` variants in one round-trip. No partial frames.

    ``size`` must be a value gpt-image-2 accepts directly
    (1024x1024, 1024x1536, 1536x1024, 2048x2048, …).

    When ``transparent_background=True`` we prepend
    :data:`_TRANSPARENT_BG_PROMPT_PREFIX` to the prompt instead of passing
    OpenAI's ``background="transparent"`` parameter — the pinned gpt-image-2
    snapshot rejects that kwarg, but it honours an in-prompt instruction
    natively and returns an RGBA PNG. Used by the composable-PSD workflow
    (slice 8/10) so each element layer drops cleanly onto the assembled
    canvas without SAM-2 masking artefacts.
    """
    effective_prompt = (
        _TRANSPARENT_BG_PROMPT_PREFIX + prompt if transparent_background else prompt
    )
    kwargs: dict[str, object] = {
        "model": model,
        "prompt": effective_prompt,
        "size": size,
        "quality": quality,
        "n": n,
    }
    if timeout is not None:
        kwargs["timeout"] = timeout
    res = await client.images.generate(**kwargs)  # type: ignore[call-overload]
    images = [d.b64_json for d in (res.data or []) if d.b64_json]
    return GenerationResult(
        images_b64=images,
        usage=usage_to_dict(res.usage),
    )


# ---------- streaming ----------------------------------------------------


async def generate_stream(
    client: AsyncOpenAI,
    *,
    model: str,
    prompt: str,
    size: str,
    quality: str = "high",
    n: int = 1,
    partial_images: int = 2,
    timeout: float | None = None,
) -> AsyncIterator[GenerationEvent]:
    """Stream events as gpt-image-2 produces partials + final images.

    Yields one or more ``partial`` events per variant followed by one
    ``completed`` event per variant. A single ``error`` event terminates
    the stream on failure.
    """
    try:
        stream = await client.images.generate(  # type: ignore[call-overload]
            model=model,
            prompt=prompt,
            size=size,
            quality=quality,
            n=n,
            stream=True,
            partial_images=partial_images,
            timeout=timeout,
        )
    except Exception as exc:  # network/auth/etc. before stream opens
        log.warning("openai_generate_stream_open_failed", error=str(exc))
        yield GenerationEvent(type="error", message=_friendly_error(exc))
        return

    async for ev in _consume_stream(stream):
        yield ev


# ---------- edit (use base + optional references as input) ---------------

# An OpenAI-compatible file tuple: (filename, bytes, mime_type).
FileTuple = tuple[str, bytes, str]


async def edit(
    client: AsyncOpenAI,
    *,
    model: str,
    prompt: str,
    images: list[FileTuple],
    size: str,
    quality: str = "high",
    n: int = 1,
    timeout: float | None = None,
) -> GenerationResult:
    """Non-streaming variant of :func:`edit_stream`. Used by tests + tools.

    ``images`` are stacked as the visual context — the model sees them all
    and produces ``n`` variants that respect the prompt.
    """
    if not images:
        msg = "At least one input image is required."
        raise ValueError(msg)
    res = await client.images.edit(  # type: ignore[call-overload]
        model=model,
        image=images if len(images) > 1 else images[0],
        prompt=prompt,
        n=n,
        size=size,
        quality=quality,
        timeout=timeout,
    )
    images_b64 = [d.b64_json for d in (res.data or []) if d.b64_json]
    return GenerationResult(
        images_b64=images_b64,
        usage=usage_to_dict(res.usage),
    )


async def edit_stream(
    client: AsyncOpenAI,
    *,
    model: str,
    prompt: str,
    images: list[FileTuple],
    size: str,
    quality: str = "high",
    n: int = 1,
    partial_images: int = 2,
    timeout: float | None = None,
) -> AsyncIterator[GenerationEvent]:
    """Stream events from ``client.images.edit`` (iterate on a base image).

    The OpenAI SDK accepts either a single file or a list — we always
    send a list so callers can stack a base + extra references in one
    call.
    """
    if not images:
        yield GenerationEvent(type="error", message="At least one input image is required.")
        return
    try:
        stream = await client.images.edit(  # type: ignore[call-overload]
            model=model,
            image=images if len(images) > 1 else images[0],
            prompt=prompt,
            n=n,
            size=size,
            quality=quality,
            stream=True,
            partial_images=partial_images,
            timeout=timeout,
        )
    except Exception as exc:
        log.warning("openai_edit_stream_open_failed", error=str(exc))
        yield GenerationEvent(type="error", message=_friendly_error(exc))
        return

    async for ev in _consume_stream(stream):
        yield ev


async def _consume_stream(stream: Any) -> AsyncIterator[GenerationEvent]:
    """Shared event-loop for both generate and edit streams."""
    try:
        async for event in stream:
            event_type = getattr(event, "type", None)
            variant_index = int(getattr(event, "image_generation_index", 0) or 0)
            b64 = getattr(event, "b64_json", None)

            if event_type == "image_generation.partial_image" and b64:
                yield GenerationEvent(
                    type="partial",
                    variant_index=variant_index,
                    image_b64=b64,
                )
            elif event_type == "image_generation.completed" and b64:
                usage = usage_to_dict(getattr(event, "usage", None))
                yield GenerationEvent(
                    type="completed",
                    variant_index=variant_index,
                    image_b64=b64,
                    usage=usage,
                )
    except Exception as exc:
        log.warning("openai_stream_mid_failed", error=str(exc))
        yield GenerationEvent(type="error", message=_friendly_error(exc))


def _friendly_error(exc: Any) -> str:
    msg = str(exc)
    # Strip provider's verbose request-id prefix when present.
    if "Error code:" in msg:
        return msg.split("Error code:", 1)[-1].strip()
    return msg or "Image generation failed."


def b64_to_bytes(b64: str) -> bytes:
    return base64.b64decode(b64)
