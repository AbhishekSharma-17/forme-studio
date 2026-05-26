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
    design_mode: bool = Field(
        default=False,
        description=(
            "Workflow entry point. False (default) = 'analyze-existing' — "
            "user uploads a finished label and we recreate it component-by-"
            "component. True = 'design-on-product' — user uploads a plain "
            "product photo + style references + brief, the studio runs a "
            "brainstorm round designing the label on the product first."
        ),
    )


class WorkspaceUpdate(BaseModel):
    """Partial update body for ``PATCH /api/packaging/workspaces/{slug}``.

    Only safe, non-frozen fields are mutable. Trim/bleed/DPI live in
    ``specs`` and are intentionally immutable after creation.
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    design_mode: bool | None = None


class WorkspaceOut(BaseModel):
    """Workspace returned from the API."""

    id: int
    slug: str
    name: str
    module: str
    product_type: str
    description: str | None
    specs: dict[str, Any]
    design_mode: bool = False
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


class ElementSpecOut(BaseModel):
    """One detected/specified element in a composable PSD manifest."""

    name: str
    label: str
    prompt: str
    position_mm: list[float]            # [x, y, w, h]
    size_px: str
    kind: str
    # New in slice 10a — text + confidence are only set for kind="text"
    # (OCR-discovered); vectorizable is a hint from the analyzer to drive
    # selective auto-vectorization in Phase 3 (slice 10c).
    text: str | None = None
    confidence: float | None = None
    vectorizable: bool = False


class ComposeDiscoverRequest(BaseModel):
    """Body for ``POST /workspaces/{slug}/compose/discover``."""

    source_asset_id: int = Field(
        ..., description="The approved whole-sticker generation to decompose."
    )
    extra_hint: str | None = Field(
        default=None,
        max_length=500,
        description=(
            "Optional designer hint to bias the decomposition, e.g. "
            "'isolate the brand mark and hero illustration'."
        ),
    )


class ComposeDiscoverResponse(BaseModel):
    """Response body for the discovery call."""

    source_asset_id: int
    trim_mm: dict[str, float]
    elements: list[ElementSpecOut]
    discovery_cost_usd: float
    # New in slice 10a — surfaces whether OCR ran. The UI uses this to
    # decide whether to nudge the user about missing text elements.
    ocr_available: bool = True
    ocr_lang: str | None = None


class ComposeAssembleRequest(BaseModel):
    """Body for ``POST /workspaces/{slug}/exports/psd-composable``."""

    source_asset_id: int
    elements: list[ElementSpecOut] = Field(
        ...,
        min_length=1,
        max_length=30,
        description="Final manifest after the user has reviewed + edited.",
    )
    quality: str = Field(
        "high",
        description="gpt-image-2 quality for per-element generation.",
    )
    dpi: int = Field(300, ge=72, le=1200)
    color_space: str = Field("CMYK", description="'CMYK' (press) or 'RGB'.")
    vectorize: bool = Field(
        True,
        description=(
            "Auto-vectorize line-art elements (logos, wordmarks, ornaments) "
            "during assembly so the composable SVG is print-quality. "
            "Photo illustrations stay raster regardless. Set False to skip "
            "the Vectorizer.AI calls entirely (~$0.80-1.20 saving per "
            "sticker; the SVG export still works but every element is "
            "embedded as a raster <image>)."
        ),
    )


class ComposeElementOut(BaseModel):
    """One generated element + its asset reference."""

    name: str
    label: str
    asset_id: int
    width_px: int
    height_px: int
    cost_usd: float


class ComposeAssembleResponse(BaseModel):
    """Response body for the assemble call."""

    asset: AssetOut
    source_asset_id: int
    element_count: int
    layer_count: int
    elements: list[ComposeElementOut]
    total_cost_usd: float
    dpi: int
    color_space: str
    width_px: int
    height_px: int
    # New in slice 10c: the assembled SVG is produced as a sibling export
    # alongside the PSD. URL serves the file from the workspace's
    # exports/ folder; counts let the UI explain "X vector / Y raster".
    svg_asset_id: int | None = None
    svg_url: str | None = None
    svg_vector_count: int = 0
    svg_raster_count: int = 0
    vector_cost_usd: float = 0.0


class DesignFlattenRequest(BaseModel):
    """Body for ``POST /workspaces/{slug}/design/flatten`` (slice 10e).

    Takes the user's approved bottle-mockup variant from the Design
    Round and re-renders it as a clean flat label on white, ready to
    feed into the analyze + assemble pipeline.
    """

    source_asset_id: int = Field(
        ...,
        description=(
            "The approved generation from the design round — typically "
            "shows the label on the product. We'll edit-flatten it to a "
            "clean rectangular label PNG at the workspace's trim dims."
        ),
    )
    quality: str = Field(
        "high",
        description="gpt-image-2 quality for the flatten pass.",
    )


class DesignFlattenResponse(BaseModel):
    """Response body for the flatten call."""

    asset: AssetOut
    source_asset_id: int
    flattened_from: int
    provider_cost_usd: float
    user_cost_usd: float


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
