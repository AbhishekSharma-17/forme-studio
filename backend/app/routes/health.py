"""Health + capability endpoint.

The frontend reads this to discover which AI providers are wired up so it
can grey out features whose keys are missing — and to surface the
*selected* primary + fallback for every pipeline stage. Fallbacks are
never auto-invoked; the UI uses these flags to show error → ``[Try with
fallback?]`` UX when a primary call fails.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app import __version__
from app.config import get_settings

router = APIRouter(tags=["meta"])


class Capabilities(BaseModel):
    """Which providers have credentials / binaries available."""

    openai_image: bool
    vectorizer_ai: bool
    inkscape: bool
    tesseract: bool
    segmentation_replicate: bool
    segmentation_self_hosted: bool
    segmentation_sam3: bool
    cdr_enabled: bool             # master switch (FORME_CDR_ENABLED)
    cdr_cloudconvert: bool        # API key present
    cdr_uniconvertor: bool        # binary present


class TierAvailability(BaseModel):
    """Which PSD tiers the current configuration can serve right now."""

    tier_a: bool  # always true
    tier_b: bool  # segmentation provider reachable
    tier_c: bool  # tier B reachable + Tesseract + FORME_TIER_C_ENABLED=true


class ProvidersSelected(BaseModel):
    """The active primary + fallback per pipeline stage.

    Both fields surface verbatim from settings; we don't substitute when a
    primary is unavailable — the UI shows an explicit error instead.
    """

    vectorizer_primary: str
    vectorizer_fallback: str | None
    segmentation: str  # 'replicate' | 'self_hosted' | 'sam3' | 'none'
    cdr_primary: str  # 'cloudconvert' | 'uniconvertor'
    cdr_fallback: str | None  # same options or None


class HealthOut(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    image_model: str
    capabilities: Capabilities
    providers: ProvidersSelected
    tiers: TierAvailability


@router.get("/api/health", response_model=HealthOut, summary="Liveness + capabilities")
async def health() -> HealthOut:
    import shutil  # local import keeps the route file lean

    s = get_settings()
    tesseract_present = shutil.which(s.tesseract_cmd) is not None

    # Tier B requires the selected segmentation provider to be reachable.
    seg_ready: bool
    if s.segmentation_provider == "replicate":
        seg_ready = bool(s.replicate_api_token)
    elif s.segmentation_provider == "self_hosted":
        seg_ready = bool(s.segmentation_self_hosted_url)
    elif s.segmentation_provider == "sam3":
        seg_ready = bool(s.sam3_endpoint_url)
    else:  # "none"
        seg_ready = False

    return HealthOut(
        version=__version__,
        image_model=s.openai_image_model,
        capabilities=Capabilities(
            openai_image=bool(s.openai_api_key),
            vectorizer_ai=bool(s.vectorizer_ai_api_id and s.vectorizer_ai_api_key),
            inkscape=Path(s.inkscape_path).is_file(),
            tesseract=tesseract_present,
            segmentation_replicate=bool(s.replicate_api_token),
            segmentation_self_hosted=bool(s.segmentation_self_hosted_url),
            segmentation_sam3=bool(s.sam3_endpoint_url),
            cdr_enabled=s.cdr_enabled,
            # `cdr_cloudconvert` reflects the *active* key — sandbox or
            # live, whichever matches the toggle. So the AppShell dot
            # turns green only when the key matching the current host
            # is configured.
            cdr_cloudconvert=bool(s.cloudconvert_active_key),
            cdr_uniconvertor=Path(s.uniconvertor_path).is_file(),
        ),
        providers=ProvidersSelected(
            vectorizer_primary=s.vectorizer_provider,
            vectorizer_fallback=(
                s.vectorizer_fallback if s.vectorizer_fallback != "none" else None
            ),
            segmentation=s.segmentation_provider,
            cdr_primary=s.cdr_provider,
            cdr_fallback=(
                s.cdr_fallback if s.cdr_fallback != "none" else None
            ),
        ),
        tiers=TierAvailability(
            tier_a=True,
            tier_b=seg_ready,
            tier_c=seg_ready and tesseract_present and s.tier_c_enabled,
        ),
    )
