"""Pydantic request/response schemas for the packaging module."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class PackagingPresetOut(BaseModel):
    id: str                  # the `key` field — kept as `id` for API back-compat
    label: str
    description: str
    trim_mm: dict[str, float]
    bleed_mm: float
    dpi: int
    color_space: str
    generation_size: str
    notes: str
    is_builtin: bool = False


class ProductTypeCreate(BaseModel):
    """Body for ``POST /api/packaging/product-types``."""

    key: str = Field(..., min_length=2, max_length=80, pattern=r"^[a-z0-9_]+$")
    label: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    trim_w_mm: float = Field(..., gt=0, le=2000)
    trim_h_mm: float = Field(..., gt=0, le=2000)
    bleed_mm: float = Field(default=3.0, ge=0, le=30)
    dpi: int = Field(default=300, ge=72, le=1200)
    color_space: str = Field(default="CMYK", max_length=10)
    generation_size: str = Field(default="1024x1536", max_length=20)
    notes: str = Field(default="", max_length=1000)


class ProductTypeUpdate(BaseModel):
    """Partial body for ``PATCH /api/packaging/product-types/{key}``.

    All fields optional. `key` and `is_builtin` are intentionally absent
    — keys are immutable, and the seeded baseline is uneditable.
    """

    label: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    trim_w_mm: float | None = Field(default=None, gt=0, le=2000)
    trim_h_mm: float | None = Field(default=None, gt=0, le=2000)
    bleed_mm: float | None = Field(default=None, ge=0, le=30)
    dpi: int | None = Field(default=None, ge=72, le=1200)
    color_space: str | None = Field(default=None, max_length=10)
    generation_size: str | None = Field(default=None, max_length=20)
    notes: str | None = Field(default=None, max_length=1000)


class WorkspaceCreate(BaseModel):
    """Body for ``POST /api/packaging/workspaces``."""

    name: str = Field(..., min_length=1, max_length=200, description="Display name.")
    product_type: str = Field(..., description="Preset ID, e.g. 'lotion_bottle_label'.")
    description: str | None = Field(default=None, max_length=2000)
    slug: str | None = Field(
        default=None,
        description="Optional override; otherwise derived from `name`.",
        max_length=80,
    )


class WorkspaceOut(BaseModel):
    """Workspace returned from the API."""

    id: int
    slug: str
    name: str
    module: str
    product_type: str
    description: str | None
    specs: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    folder_path: str


class GenerateRequest(BaseModel):
    """Body for ``POST /api/packaging/workspaces/{slug}/generate(/stream)``.

    If ``reference_asset_ids`` is provided, generation is routed through
    ``images.edit`` so gpt-image-2 can use the references as visual input
    (brand colours, logo, mood). Up to 16 references — the model's hard cap.
    """

    prompt: str = Field(..., min_length=4, max_length=4000)
    n: int = Field(1, ge=1, le=4, description="Number of variants to produce.")
    quality: str = Field("high", description="low | medium | high | auto")
    reference_asset_ids: list[int] = Field(
        default_factory=list,
        max_length=16,
        description="Reference assets (logos, photos, mood boards) to inform generation.",
    )


class AssetOut(BaseModel):
    """One persisted asset in a workspace."""

    id: int
    workspace_id: int
    kind: str
    filename: str
    relative_path: str
    url: str  # ready-to-fetch URL for the file
    mime_type: str
    size_bytes: int
    prompt: str | None
    model: str | None
    image_size: str | None
    quality: str | None
    variant_index: int
    provider_cost_usd: float
    user_cost_usd: float
    usage: dict[str, int]
    created_at: datetime


class GenerateResponse(BaseModel):
    """Response body for the non-streaming generate endpoint."""

    assets: list[AssetOut]
    provider_cost_usd: float
    user_cost_usd: float
    markup_percent: float


class ReferenceUploadResponse(BaseModel):
    """Response body for ``POST /workspaces/{slug}/references``."""

    references: list[AssetOut]
    total: int


class PsdExportRequest(BaseModel):
    """Body for ``POST /workspaces/{slug}/exports/psd``.

    Tier selects the export pipeline:

      * ``A`` — flat single-layer PSD (no segmentation). Always available.
      * ``B`` — layered PSD via SAM-2. Requires ``segmentation_provider``
        to be reachable.
      * ``C`` — Tier B + OCR text-region overlays + JSON sidecar.
        Requires segmentation **and** Tesseract **and**
        ``FORME_TIER_C_ENABLED=true``.
    """

    source_asset_id: int = Field(
        ..., description="Generation asset to wrap as PSD."
    )
    tier: str = Field(
        "A", description="'A' (flat), 'B' (layered), or 'C' (layered + OCR)."
    )
    color_space: str = Field(
        "CMYK",
        description="'CMYK' (default, press-ready) or 'RGB' (preserve original).",
    )
    dpi: int = Field(
        300, ge=72, le=1200, description="Resolution baked into PSD metadata."
    )


class PsdExportResponse(BaseModel):
    """Response body for the PSD export endpoint."""

    asset: AssetOut
    source_asset_id: int
    tier: str
    color_space: str
    dpi: int
    width: int
    height: int
    layer_count: int
    sidecar_url: str | None = None
    segmentation_provider: str | None = None
    text_layer_count: int | None = None


class PdfExportRequest(BaseModel):
    """Body for ``POST /workspaces/{slug}/exports/pdf``.

    Trim + bleed come from the workspace's frozen specs — the caller
    just picks DPI and whether to draw printer marks.
    """

    source_asset_id: int = Field(
        ..., description="Generation asset to wrap as PDF/X-4."
    )
    dpi: int = Field(
        300, ge=72, le=1200, description="Image resolution baked into the PDF."
    )
    trim_marks: bool = Field(
        True, description="Draw 5 mm corner trim marks just outside the trim box."
    )
    registration_marks: bool = Field(
        True, description="Draw bullseye registration marks mid-edge."
    )


class PdfExportResponse(BaseModel):
    """Response body for the PDF export endpoint."""

    asset: AssetOut
    source_asset_id: int
    trim_mm: dict[str, float]
    bleed_mm: float
    dpi: int
    icc_profile: str
    icc_embedded: bool
    trim_marks: bool
    registration_marks: bool


class VectorExportRequest(BaseModel):
    """Body for ``POST /workspaces/{slug}/exports/vector``.

    ``provider`` overrides the env-configured ``FORME_VECTORIZER_PROVIDER``
    for this one call only — the UI passes the fallback name here after
    the user clicks "Try with fallback?" on a failure. Leave it ``None``
    to use the primary.
    """

    source_asset_id: int = Field(
        ..., description="Generation asset to vectorize to SVG."
    )
    provider: str | None = Field(
        default=None,
        description=(
            "Override the env-configured primary for this call only — "
            "'vectorizer_ai' or 'inkscape_potrace'."
        ),
    )


class VectorExportResponse(BaseModel):
    """Response body for the vector export endpoint."""

    asset: AssetOut
    source_asset_id: int
    provider: str
    mode: str | None
    size_bytes: int


class WorkspaceDeleteRequest(BaseModel):
    """Body for ``DELETE /workspaces/{slug}``.

    By default we delete only the DB rows (Workspace + cascaded Assets +
    AuditEvents) and leave the on-disk folder so audit trails and
    generated artwork are recoverable. Set ``delete_files=true`` to also
    rmtree ``<workspaces_root>/<slug>``.
    """

    delete_files: bool = Field(
        False,
        description=(
            "When true, also remove the workspace's on-disk folder "
            "(generations, references, exports, audit.log.jsonl)."
        ),
    )


class WorkspaceDeleteResponse(BaseModel):
    """Response body for the delete endpoint — what we removed."""

    slug: str
    deleted_assets: int
    deleted_audit_events: int
    files_deleted: bool


class CdrExportRequest(BaseModel):
    """Body for ``POST /workspaces/{slug}/exports/cdr``.

    Two providers, both overridable per call:

    * ``vector_provider`` — which engine generates the intermediate SVG
      (slice 6). Defaults to ``FORME_VECTORIZER_PROVIDER``.
    * ``cdr_provider``    — which engine converts SVG → CDR (this slice).
      Defaults to ``FORME_CDR_PROVIDER``.

    The UI passes either override when the user clicks "Try with
    fallback?" after a failure — non-auto fallback applies to both
    stages independently.
    """

    source_asset_id: int = Field(
        ..., description="Generation asset to vectorize then convert to CDR."
    )
    vector_provider: str | None = Field(
        default=None,
        description=(
            "Override the vectoriser primary for this call only — "
            "'vectorizer_ai' or 'inkscape_potrace'."
        ),
    )
    cdr_provider: str | None = Field(
        default=None,
        description=(
            "Override the CDR provider for this call only — "
            "'cloudconvert' or 'uniconvertor'."
        ),
    )


class CdrExportResponse(BaseModel):
    """Response body for the CDR export endpoint."""

    asset: AssetOut
    source_asset_id: int
    vector_provider: str
    cdr_provider: str
    svg_size_bytes: int
    cdr_size_bytes: int


class EditRequest(BaseModel):
    """Body for ``POST /workspaces/{slug}/edit(/stream)``.

    The ``base_asset_id`` is the variant or reference we're iterating on;
    extra ``reference_asset_ids`` are stacked as additional inputs so the
    model can see e.g. a generated label + the bottle photo + the logo.
    """

    prompt: str = Field(..., min_length=4, max_length=4000)
    base_asset_id: int = Field(..., description="Asset to iterate from.")
    reference_asset_ids: list[int] = Field(
        default_factory=list,
        max_length=16,
        description="Optional additional reference assets to bundle with the base.",
    )
    n: int = Field(1, ge=1, le=4)
    quality: str = Field("high", description="low | medium | high | auto")
