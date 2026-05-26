"""Segmentation service — turns one PNG into a stack of masks.

Used by Tier B / Tier C PSD exports. Three interchangeable providers:

* ``replicate``    — hosted SAM-2 on Replicate (default; pay-per-call).
* ``self_hosted``  — generic SAM (v2/v3) deployment using the original
                     Forme wire contract; documented in
                     :func:`_segment_self_hosted`.
* ``sam3``         — SAM 3.1 self-hosted (image-only). Same shape but
                     accepts optional ``score`` + ``label`` per mask so
                     text-prompted segmentation can name Tier B layers
                     semantically ("logo", "wordmark"). Wire contract
                     lives in :func:`_segment_sam3` and ``docs/SAM_UPGRADE.md``.

The dispatcher (:func:`segment`) reads ``FORME_SEGMENTATION_PROVIDER`` —
fallbacks are never auto-invoked. The caller surfaces errors to the user
who explicitly picks a different provider.
"""

from __future__ import annotations

import asyncio
import base64
import io
from dataclasses import dataclass
from typing import Any

import httpx
import structlog
from fastapi import HTTPException, status
from PIL import Image

from app.config import Settings, get_settings

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class Mask:
    """One segmentation mask.

    ``png_bytes`` is a single-channel (L) PNG where 255 = inside the mask,
    0 = outside. ``bbox`` is ``(left, top, right, bottom)`` in pixel
    coordinates of the source image (PIL-style).

    ``label`` and ``score`` are populated by SAM 3.x providers. ``label``
    carries the concept the model was text-prompted with ("logo",
    "bottle") so downstream code can use it for layer naming; ``score``
    is the model's per-mask confidence. Both are ``None`` for SAM-2 and
    for AMG (automatic mask generation) runs.
    """

    name: str
    png_bytes: bytes
    bbox: tuple[int, int, int, int]
    area_px: int
    label: str | None = None
    score: float | None = None


@dataclass(frozen=True)
class SegmentationResult:
    masks: list[Mask]
    width: int
    height: int
    provider: str
    model: str


# ---------- public dispatcher --------------------------------------------


async def segment(image_bytes: bytes) -> SegmentationResult:
    """Run segmentation against the configured provider.

    Raises 503 if the configured provider has no credentials, or if
    ``FORME_SEGMENTATION_PROVIDER`` is ``"none"``.
    """
    settings = get_settings()
    provider = settings.segmentation_provider

    if provider == "none":
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Segmentation is disabled. Set FORME_SEGMENTATION_PROVIDER "
                "to 'replicate' or 'self_hosted'."
            ),
        )

    if provider == "replicate":
        if not settings.replicate_api_token:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="REPLICATE_API_TOKEN is not configured.",
            )
        return await _segment_replicate(image_bytes, settings)

    if provider == "self_hosted":
        if not settings.segmentation_self_hosted_url:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="FORME_SEGMENTATION_SELF_HOSTED_URL is not configured.",
            )
        return await _segment_self_hosted(image_bytes, settings)

    if provider == "sam3":
        if not settings.sam3_endpoint_url:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "FORME_SAM3_ENDPOINT_URL is not configured. "
                    "Deploy SAM 3.1 image inference and set the URL in "
                    "Settings, or switch the segmentation provider to "
                    "'replicate' or 'self_hosted'."
                ),
            )
        return await _segment_sam3(image_bytes, settings)

    raise HTTPException(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"Unknown segmentation provider '{provider}'.",
    )


# ---------- Replicate (primary) ------------------------------------------


async def _segment_replicate(
    image_bytes: bytes, settings: Settings
) -> SegmentationResult:
    """Call Replicate's SAM-2 automatic-mask endpoint.

    The model identifier is configurable via ``FORME_REPLICATE_SAM2_MODEL``
    (defaults to ``meta/sam-2``). The output is expected to expose either
    ``individual_masks`` (list of PNG URLs) or ``masks`` — we accept both
    shapes so swapping community ports is a config change, not a code one.
    """
    # Lazy import — the dependency exists but we only pay the import cost
    # when SAM-2 is actually invoked.
    from replicate.client import Client as ReplicateClient

    width, height = _png_dimensions(image_bytes)

    client = ReplicateClient(api_token=settings.replicate_api_token)
    data_uri = "data:image/png;base64," + base64.b64encode(image_bytes).decode()

    log.info(
        "segmentation_replicate_call",
        model=settings.replicate_sam2_model,
        width=width,
        height=height,
    )

    try:
        output = await asyncio.wait_for(
            client.async_run(
                settings.replicate_sam2_model,
                input={
                    "image": data_uri,
                    "use_m2m": True,
                    "points_per_side": 32,
                    "pred_iou_thresh": 0.88,
                    "stability_score_thresh": 0.95,
                },
            ),
            timeout=settings.segmentation_timeout_s,
        )
    except TimeoutError as exc:
        raise HTTPException(
            status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Replicate SAM-2 timed out after {settings.segmentation_timeout_s}s.",
        ) from exc
    except Exception as exc:
        log.exception("segmentation_replicate_failed")
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"Replicate SAM-2 call failed: {exc}",
        ) from exc

    mask_urls = _extract_mask_urls(output)
    if not mask_urls:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=(
                "Replicate SAM-2 returned no masks. "
                f"Raw output keys: {list(output) if isinstance(output, dict) else type(output).__name__}"
            ),
        )

    # Download all masks in parallel.
    async with httpx.AsyncClient(timeout=60) as http:
        tasks = [http.get(url) for url in mask_urls]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

    masks: list[Mask] = []
    for i, resp in enumerate(responses):
        if isinstance(resp, BaseException):
            log.warning("mask_download_failed", index=i, error=str(resp))
            continue
        mask_png = resp.content
        bbox, area = _bbox_and_area_from_png(mask_png)
        if area == 0:
            continue
        masks.append(
            Mask(
                name=f"sam2_layer_{i + 1:02d}",
                png_bytes=mask_png,
                bbox=bbox,
                area_px=area,
            )
        )

    # Order largest-first so designers see the dominant regions on top.
    masks.sort(key=lambda m: m.area_px, reverse=True)

    log.info("segmentation_done", masks=len(masks), provider="replicate")
    return SegmentationResult(
        masks=masks,
        width=width,
        height=height,
        provider="replicate",
        model=settings.replicate_sam2_model,
    )


def _extract_mask_urls(output: Any) -> list[str]:
    """Pull mask URLs out of a Replicate output, tolerating shape variants."""
    if not output:
        return []
    # Common shapes we accept:
    #   {"individual_masks": [url, url, ...]}
    #   {"masks": [url, url, ...]}
    #   [url, url, ...]
    if isinstance(output, list):
        return [str(u) for u in output if u]
    if isinstance(output, dict):
        for key in ("individual_masks", "masks", "output"):
            if key in output:
                value = output[key]
                if isinstance(value, list):
                    return [str(u) for u in value if u]
    return []


# ---------- Self-hosted (your DGX Spark) ---------------------------------


async def _segment_self_hosted(
    image_bytes: bytes, settings: Settings
) -> SegmentationResult:
    """Call a self-hosted SAM-2 endpoint.

    **Contract** — your service must accept ``POST <URL>``, body
    ``multipart/form-data`` with a single ``image`` field carrying the PNG
    bytes, and respond JSON of shape::

        {
          "width":  1024,
          "height": 1536,
          "masks": [
            {"png_b64": "...", "bbox": [x1, y1, x2, y2], "area_px": 1234},
            ...
          ]
        }

    The bearer token (if any) is sent in the ``Authorization`` header.
    """
    width, height = _png_dimensions(image_bytes)
    url = settings.segmentation_self_hosted_url
    assert url is not None  # guarded by the dispatcher

    headers: dict[str, str] = {}
    if settings.segmentation_self_hosted_token:
        headers["Authorization"] = f"Bearer {settings.segmentation_self_hosted_token}"

    log.info("segmentation_self_hosted_call", url=url)

    try:
        async with httpx.AsyncClient(timeout=settings.segmentation_timeout_s) as http:
            resp = await http.post(
                url,
                files={"image": ("input.png", image_bytes, "image/png")},
                headers=headers,
            )
    except Exception as exc:
        log.exception("segmentation_self_hosted_failed")
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"Self-hosted SAM-2 call failed: {exc}",
        ) from exc

    if resp.status_code != 200:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"Self-hosted SAM-2 returned {resp.status_code}: {resp.text[:300]}",
        )

    body = resp.json()
    raw_masks = body.get("masks", [])
    masks: list[Mask] = []
    for i, m in enumerate(raw_masks):
        png_bytes = base64.b64decode(m["png_b64"])
        bbox_list = m.get("bbox", [0, 0, width, height])
        masks.append(
            Mask(
                name=m.get("name") or f"sam2_layer_{i + 1:02d}",
                png_bytes=png_bytes,
                bbox=(
                    int(bbox_list[0]),
                    int(bbox_list[1]),
                    int(bbox_list[2]),
                    int(bbox_list[3]),
                ),
                area_px=int(m.get("area_px", 0)),
            )
        )

    masks.sort(key=lambda m: m.area_px, reverse=True)
    return SegmentationResult(
        masks=masks,
        width=int(body.get("width", width)),
        height=int(body.get("height", height)),
        provider="self_hosted",
        model="self_hosted",
    )


# ---------- SAM 3.1 self-hosted (image only) -----------------------------


async def _segment_sam3(
    image_bytes: bytes, settings: Settings
) -> SegmentationResult:
    """Call a self-hosted SAM 3.1 image inference endpoint.

    **Why a separate branch from ``self_hosted``** — SAM 3 (and 3.1) is
    *concept-promptable*: feed it a comma-separated list of phrases
    ("logo, wordmark, bottle") and it returns named, scored masks for
    each instance. SAM-2 has no such surface. We isolate the SAM 3
    contract here so users can keep a SAM-2 deployment running in
    parallel during cutover, and so Tier B PSDs can promote the semantic
    ``label`` to layer names when present.

    **Contract** — your endpoint must accept::

        POST <FORME_SAM3_ENDPOINT_URL>
        multipart/form-data:
            image      = <PNG bytes>          # required
            mode       = "auto" | "text"      # optional, defaults to "auto"
            text_prompt = "logo, wordmark"    # optional, comma-separated
                                              # concepts. Required when mode="text".

    and respond JSON::

        {
          "width":  1024,
          "height": 1536,
          "model":  "sam3.1-image",            # informational
          "masks": [
            {
              "png_b64": "...",                # single-channel mask PNG
              "bbox":    [x1, y1, x2, y2],     # PIL pixel coords
              "area_px": 12345,
              "score":   0.95,                 # optional, SAM 3 confidence
              "label":   "logo"                # optional, text-prompted only
            },
            ...
          ]
        }

    ``score`` and ``label`` are optional — when present, downstream code
    (Tier B PSD writer) names layers using ``label`` instead of the
    generic ``sam3_layer_NN`` fallback.

    The bearer token (if any) is sent as ``Authorization: Bearer <token>``.
    """
    width, height = _png_dimensions(image_bytes)
    url = settings.sam3_endpoint_url
    assert url is not None  # guarded by the dispatcher

    headers: dict[str, str] = {}
    if settings.sam3_endpoint_token:
        headers["Authorization"] = f"Bearer {settings.sam3_endpoint_token}"

    # Build the form payload. If a text prompt is configured, use
    # "text" mode and pass the prompt; otherwise stay in "auto" (AMG).
    form: dict[str, str] = {}
    if settings.sam3_text_prompt:
        form["mode"] = "text"
        form["text_prompt"] = settings.sam3_text_prompt
    else:
        form["mode"] = "auto"

    log.info(
        "segmentation_sam3_call",
        url=url,
        mode=form["mode"],
        prompt=settings.sam3_text_prompt,
    )

    try:
        async with httpx.AsyncClient(timeout=settings.segmentation_timeout_s) as http:
            resp = await http.post(
                url,
                files={"image": ("input.png", image_bytes, "image/png")},
                data=form,
                headers=headers,
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status.HTTP_504_GATEWAY_TIMEOUT,
            detail=(
                f"SAM 3.1 endpoint timed out after "
                f"{settings.segmentation_timeout_s}s."
            ),
        ) from exc
    except Exception as exc:
        log.exception("segmentation_sam3_failed")
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"SAM 3.1 call failed: {exc}",
        ) from exc

    if resp.status_code != 200:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"SAM 3.1 returned {resp.status_code}: {resp.text[:300]}",
        )

    body = resp.json()
    raw_masks = body.get("masks", [])
    masks: list[Mask] = []
    for i, m in enumerate(raw_masks):
        try:
            png_bytes = base64.b64decode(m["png_b64"])
        except (KeyError, ValueError) as exc:
            log.warning("sam3_mask_decode_failed", index=i, error=str(exc))
            continue
        bbox_list = m.get("bbox", [0, 0, width, height])
        label = m.get("label")
        label_str: str | None = str(label).strip() if label else None
        score_raw = m.get("score")
        score: float | None
        try:
            score = float(score_raw) if score_raw is not None else None
        except (TypeError, ValueError):
            score = None

        # Layer name preference: caller-supplied "name" → semantic
        # "label" with optional disambiguator → anonymous fallback.
        if m.get("name"):
            layer_name = str(m["name"])
        elif label_str:
            # SAM 3 may return multiple instances of the same concept
            # ("logo" appears twice). Disambiguate by appending the
            # 1-indexed position; keep the human-readable root.
            layer_name = label_str
        else:
            layer_name = f"sam3_layer_{i + 1:02d}"

        masks.append(
            Mask(
                name=layer_name,
                png_bytes=png_bytes,
                bbox=(
                    int(bbox_list[0]),
                    int(bbox_list[1]),
                    int(bbox_list[2]),
                    int(bbox_list[3]),
                ),
                area_px=int(m.get("area_px", 0)),
                label=label_str,
                score=score,
            )
        )

    # Disambiguate same-label masks by appending 1-based counters
    # ("logo" + "logo" → "logo_1" + "logo_2"). Keep singletons untouched.
    _disambiguate_layer_names(masks)

    # Largest first so the dominant region paints on top of the stack.
    masks.sort(key=lambda mk: mk.area_px, reverse=True)

    log.info(
        "segmentation_done",
        masks=len(masks),
        provider="sam3",
        labelled=sum(1 for m in masks if m.label),
    )
    return SegmentationResult(
        masks=masks,
        width=int(body.get("width", width)),
        height=int(body.get("height", height)),
        provider="sam3",
        model=str(body.get("model", "sam3.1-image")),
    )


def _disambiguate_layer_names(masks: list[Mask]) -> None:
    """Mutate ``masks`` to make every ``name`` unique within the list.

    For example, two masks both named ``"logo"`` become ``"logo_1"`` and
    ``"logo_2"``. Singletons keep their original name. Because :class:`Mask`
    is frozen, we replace entries in the list with new instances.
    """
    counts: dict[str, int] = {}
    for m in masks:
        counts[m.name] = counts.get(m.name, 0) + 1
    if not any(c > 1 for c in counts.values()):
        return

    seen: dict[str, int] = {}
    for i, m in enumerate(masks):
        if counts[m.name] == 1:
            continue
        seen[m.name] = seen.get(m.name, 0) + 1
        new_name = f"{m.name}_{seen[m.name]}"
        masks[i] = Mask(
            name=new_name,
            png_bytes=m.png_bytes,
            bbox=m.bbox,
            area_px=m.area_px,
            label=m.label,
            score=m.score,
        )


# ---------- helpers ------------------------------------------------------


def _png_dimensions(image_bytes: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(image_bytes)) as im:
        return im.size


def _bbox_and_area_from_png(png_bytes: bytes) -> tuple[tuple[int, int, int, int], int]:
    """Read a 1-channel mask PNG and return (bbox, area in pixels).

    ``Image.getbbox()`` returns the bbox of non-zero pixels (or ``None``
    if the mask is empty). For ordering we want the total non-zero area;
    ``histogram()`` gives us that in one C call rather than iterating in
    Python.
    """
    with Image.open(io.BytesIO(png_bytes)) as im:
        gray = im.convert("L")
        bbox = gray.getbbox()
        if bbox is None:
            return (0, 0, 0, 0), 0
        # histogram() returns 256 bins: zero-intensity at index 0, all
        # non-zero values from 1..255.
        hist = gray.histogram()
        area = sum(hist[1:])
        return bbox, area
