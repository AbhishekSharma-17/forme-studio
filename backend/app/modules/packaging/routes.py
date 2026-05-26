"""Packaging module routes — workspaces + presets + generate + assets."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from openai import AsyncOpenAI
from sqlmodel import Session, col, select

from app.config import get_settings
from app.db import get_session
from app.deps import get_openai_client, resolve_workspace
from app.models.asset import Asset
from app.models.audit import AuditEvent
from app.models.workspace import Workspace
from app.modules.packaging.schemas import (
    AssetOut,
    CdrExportRequest,
    CdrExportResponse,
    ComposeAssembleRequest,
    ComposeAssembleResponse,
    ComposeDiscoverRequest,
    ComposeDiscoverResponse,
    ComposeElementOut,
    EditRequest,
    ElementSpecOut,
    GenerateRequest,
    GenerateResponse,
    PackagingPresetOut,
    PdfExportRequest,
    PdfExportResponse,
    ProductTypeCreate,
    ProductTypeUpdate,
    PsdExportRequest,
    PsdExportResponse,
    ReferenceUploadResponse,
    VectorExportRequest,
    VectorExportResponse,
    WorkspaceCreate,
    WorkspaceDeleteRequest,
    WorkspaceDeleteResponse,
    WorkspaceOut,
)
from app.services import audit
from app.services import product_types as product_types_service
from app.services.assets import (
    absolute_path,
    save_export,
    save_generation,
    save_reference,
)
from app.services.compose import (
    GeneratedElement,
    assemble_composable_psd,
    derive_composable_filename,
    generate_element,
)
from app.services.export_cdr import (
    convert_svg_to_cdr,
)
from app.services.export_cdr import (
    derive_export_filename as derive_cdr_filename,
)
from app.services.export_pdf import (
    derive_export_filename as derive_pdf_filename,
)
from app.services.export_pdf import (
    export_to_pdf,
)
from app.services.export_psd import (
    derive_export_filename,
    export_to_psd,
    export_to_psd_a_ocr,
    export_to_psd_tier_b,
    export_to_psd_tier_c,
)
from app.services.filesystem import ensure_workspace_dir, slugify, workspace_root
from app.services.image_normalize import NormalizeError, normalize
from app.services.ocr import OcrUnavailableError
from app.services.ocr import extract as ocr_extract
from app.services.openai_image import (
    FileTuple,
    b64_to_bytes,
    edit,
    edit_stream,
    generate,
    generate_stream,
)
from app.services.pricing import apply_markup, cost_from_usage
from app.services.segmentation import segment as run_segmentation
from app.services.vector import (
    derive_export_filename as derive_vector_filename,
)
from app.services.vector import (
    vectorize as run_vectorize,
)
from app.services.vision import ElementSpec, discover_elements

# Reference-upload guardrails. The model's hard cap is 16 references per
# call, so we accept up to 16 per *upload* batch too.
MAX_REFERENCE_FILES = 16
MAX_REFERENCE_BYTES_PER_FILE = 25 * 1024 * 1024  # 25 MB

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/packaging", tags=["packaging"])


# --------------------------------------------------------------------- presets
def _product_type_to_out(pt: Any) -> PackagingPresetOut:
    """Project a ``ProductType`` row to the public preset shape."""
    return PackagingPresetOut(
        id=pt.key,
        label=pt.label,
        description=pt.description,
        trim_mm={"w": pt.trim_w_mm, "h": pt.trim_h_mm},
        bleed_mm=pt.bleed_mm,
        dpi=pt.dpi,
        color_space=pt.color_space,
        generation_size=pt.generation_size,
        notes=pt.notes,
        is_builtin=pt.is_builtin,
    )


@router.get(
    "/presets",
    response_model=list[PackagingPresetOut],
    summary="List packaging product-type presets (built-in + custom)",
)
async def list_presets(
    session: Session = Depends(get_session),
) -> list[PackagingPresetOut]:
    """Lists every product type configured in the database.

    Kept under ``/presets`` for back-compat with the existing frontend;
    the canonical name is now ``product-types`` (see the CRUD endpoints
    below). Both routes return the same data.
    """
    rows = product_types_service.list_all(session)
    return [_product_type_to_out(r) for r in rows]


# --------------------------------------------------------- product types (CRUD)
@router.get(
    "/product-types",
    response_model=list[PackagingPresetOut],
    summary="List product types (alias for /presets)",
)
async def list_product_types(
    session: Session = Depends(get_session),
) -> list[PackagingPresetOut]:
    rows = product_types_service.list_all(session)
    return [_product_type_to_out(r) for r in rows]


@router.post(
    "/product-types",
    response_model=PackagingPresetOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a custom product type",
)
async def create_product_type(
    body: ProductTypeCreate,
    session: Session = Depends(get_session),
) -> PackagingPresetOut:
    """Create a user-defined product type.

    ``key`` must be unique across the table — collision with a built-in or
    an existing custom row returns 409.
    """
    payload = body.model_dump()
    row = product_types_service.create(session, payload)
    return _product_type_to_out(row)


@router.patch(
    "/product-types/{key}",
    response_model=PackagingPresetOut,
    summary="Update a custom product type (built-ins are immutable)",
)
async def update_product_type(
    key: str,
    body: ProductTypeUpdate,
    session: Session = Depends(get_session),
) -> PackagingPresetOut:
    """Edit a custom product type.

    Built-in rows refuse mutation with 409. Existing workspaces using the
    key are *not* affected — their ``specs`` were frozen at creation time.
    """
    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    row = product_types_service.update(session, key, payload)
    return _product_type_to_out(row)


@router.delete(
    "/product-types/{key}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a custom product type",
)
async def delete_product_type(
    key: str,
    session: Session = Depends(get_session),
) -> None:
    """Refuses on built-ins or when any workspace still references the key."""
    product_types_service.delete(session, key)


# ------------------------------------------------------------------ workspaces
@router.post(
    "/workspaces",
    response_model=WorkspaceOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a packaging workspace",
)
async def create_workspace(
    body: WorkspaceCreate,
    session: Session = Depends(get_session),
) -> WorkspaceOut:
    # Reads from the product_types table — built-in + user-created rows
    # are both valid. Missing key → 422 with the same error users saw
    # before this slice (back-compat).
    pt = product_types_service.get_by_key(session, body.product_type)
    if pt is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown product_type '{body.product_type}'.",
        )

    slug = slugify(body.slug or body.name)

    if session.exec(select(Workspace).where(Workspace.slug == slug)).first() is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"A workspace with slug '{slug}' already exists.",
        )

    ws = Workspace(
        slug=slug,
        name=body.name,
        module="packaging",
        product_type=body.product_type,
        # Specs frozen at creation time — later edits to the product type
        # never mutate this row.
        specs=product_types_service.to_specs(pt),
        description=body.description,
    )
    session.add(ws)
    session.flush()

    folder = ensure_workspace_dir(slug)

    audit.record(
        session,
        event="workspace.created",
        workspace_id=ws.id,
        workspace_slug=slug,
        payload={
            "name": body.name,
            "product_type": body.product_type,
            "specs": ws.specs,
            "folder": str(folder),
        },
    )

    session.commit()
    session.refresh(ws)

    log.info("workspace_created", slug=slug, product_type=body.product_type)
    return _workspace_to_out(ws)


@router.get(
    "/workspaces",
    response_model=list[WorkspaceOut],
    summary="List all packaging workspaces",
)
async def list_workspaces(
    session: Session = Depends(get_session),
) -> list[WorkspaceOut]:
    rows = session.exec(
        select(Workspace)
        .where(Workspace.module == "packaging")
        .order_by(col(Workspace.created_at).desc())
    ).all()
    return [_workspace_to_out(w) for w in rows]


@router.get(
    "/workspaces/{slug}",
    response_model=WorkspaceOut,
    summary="Fetch one packaging workspace by slug",
)
async def get_workspace(
    workspace: Workspace = Depends(resolve_workspace),
) -> WorkspaceOut:
    return _workspace_to_out(workspace)


@router.delete(
    "/workspaces/{slug}",
    response_model=WorkspaceDeleteResponse,
    summary="Delete a workspace (and optionally its on-disk folder)",
)
async def delete_workspace(
    body: WorkspaceDeleteRequest | None = None,
    workspace: Workspace = Depends(resolve_workspace),
    session: Session = Depends(get_session),
) -> WorkspaceDeleteResponse:
    """Remove a workspace from the database.

    The Asset + AuditEvent rows for this workspace are explicitly deleted
    (no DB-level CASCADE configured; we want the behaviour to be the
    same on SQLite + Postgres). Optionally also rmtree the on-disk
    folder when ``delete_files=true`` — by default we keep it so the
    audit JSONL + generated artwork remain recoverable.

    A final ``workspace.deleted`` audit row is written **before** the
    DB row goes, so the deletion itself shows up in any global audit
    aggregation (the per-workspace JSONL is gone with the folder).
    """
    import shutil

    body = body or WorkspaceDeleteRequest()
    slug = workspace.slug
    ws_id = workspace.id

    # Snapshot counts BEFORE the tombstone so the payload reflects what
    # accumulated during the workspace's lifetime, not the deletion event
    # itself.
    assets_stmt = select(Asset).where(Asset.workspace_id == ws_id)
    asset_rows = list(session.exec(assets_stmt))
    pre_audits_stmt = select(AuditEvent).where(AuditEvent.workspace_id == ws_id)
    pre_audit_rows = list(session.exec(pre_audits_stmt))
    n_assets = len(asset_rows)
    n_pre_audits = len(pre_audit_rows)

    # Write the tombstone audit FIRST (still linked to the workspace).
    audit.record(
        session,
        event="workspace.deleted",
        workspace_id=ws_id,
        workspace_slug=slug,
        payload={
            "name": workspace.name,
            "module": workspace.module,
            "product_type": workspace.product_type,
            "deleted_assets": n_assets,
            "deleted_audit_events": n_pre_audits,
            "delete_files": body.delete_files,
        },
    )

    # Re-query so we delete the snapshot + the tombstone we just inserted.
    # The response counter reports the *actual* number of audit rows that
    # were deleted (snapshot + tombstone).
    final_audits = list(session.exec(pre_audits_stmt))
    n_total_audits_deleted = len(final_audits)

    # Cascade-delete in DB (children first, then the workspace itself).
    for a in asset_rows:
        session.delete(a)
    for ev in final_audits:
        session.delete(ev)
    session.delete(workspace)
    session.commit()

    # On-disk cleanup is best-effort; failures here are logged but don't
    # surface as 5xx because the DB state is already consistent.
    files_deleted = False
    if body.delete_files:
        ws_path = workspace_root(slug)
        if ws_path.is_dir():
            try:
                shutil.rmtree(ws_path)
                files_deleted = True
            except OSError as exc:
                log.warning(
                    "workspace_files_rmtree_failed", slug=slug, error=str(exc)
                )

    log.info(
        "workspace_deleted",
        slug=slug,
        deleted_assets=n_assets,
        deleted_audit_events=n_total_audits_deleted,
        files_deleted=files_deleted,
    )
    return WorkspaceDeleteResponse(
        slug=slug,
        deleted_assets=n_assets,
        deleted_audit_events=n_total_audits_deleted,
        files_deleted=files_deleted,
    )


# ----------------------------------------------------------------- generations
@router.post(
    "/workspaces/{slug}/generate",
    response_model=GenerateResponse,
    summary="Generate one or more variants (non-streaming)",
)
async def generate_in_workspace(
    body: GenerateRequest,
    workspace: Workspace = Depends(resolve_workspace),
    session: Session = Depends(get_session),
    client: AsyncOpenAI = Depends(get_openai_client),
) -> GenerateResponse:
    """Generate ``n`` variants in one round-trip and persist them.

    Size is **locked** to ``workspace.specs.generation_size`` — the user
    can never produce off-spec artwork inside a workspace.

    If ``reference_asset_ids`` is non-empty the request is routed through
    ``images.edit`` so the model can see those references; otherwise it
    runs through plain ``images.generate``.
    """
    settings = get_settings()
    size = _generation_size(workspace)

    refs = [
        _resolve_asset(session, workspace, aid, "reference_asset_ids")
        for aid in body.reference_asset_ids
    ]
    ref_images: list[FileTuple] = [_asset_to_filetuple(workspace, a) for a in refs]

    try:
        if ref_images:
            result = await edit(
                client,
                model=settings.openai_image_model,
                prompt=body.prompt,
                images=ref_images,
                size=size,
                quality=body.quality,
                n=body.n,
                timeout=settings.image_generation_timeout_s,
            )
        else:
            result = await generate(
                client,
                model=settings.openai_image_model,
                prompt=body.prompt,
                size=size,
                quality=body.quality,
                n=body.n,
                timeout=settings.image_generation_timeout_s,
            )
    except Exception as exc:
        log.exception("openai_generate_failed", workspace=workspace.slug)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"OpenAI image generation failed: {exc}",
        ) from exc

    saved: list[Asset] = []
    ref_ids = [a.id for a in refs if a.id is not None]
    for idx, b64 in enumerate(result["images_b64"]):
        saved.append(
            save_generation(
                session,
                workspace,
                image_bytes=b64_to_bytes(b64),
                prompt=body.prompt,
                model=settings.openai_image_model,
                size=size,
                quality=body.quality,
                variant_index=idx,
                # Usage is for the whole batch; attribute it to the first variant
                # so we don't double-count.
                usage=result["usage"] if idx == 0 else {},
                reference_ids=ref_ids,
                commit=False,
            )
        )
    session.commit()
    for a in saved:
        session.refresh(a)

    provider_cost = cost_from_usage(result["usage"])
    user_cost = apply_markup(provider_cost, settings.pricing_markup_percent)
    return GenerateResponse(
        assets=[_asset_to_out(a, workspace) for a in saved],
        provider_cost_usd=provider_cost,
        user_cost_usd=user_cost,
        markup_percent=settings.pricing_markup_percent,
    )


@router.post(
    "/workspaces/{slug}/generate/stream",
    summary="Generate variants with partial-image SSE streaming",
    response_class=StreamingResponse,
)
async def generate_in_workspace_stream(
    body: GenerateRequest,
    workspace: Workspace = Depends(resolve_workspace),
    session: Session = Depends(get_session),
    client: AsyncOpenAI = Depends(get_openai_client),
) -> StreamingResponse:
    """SSE endpoint: yields ``partial`` events as gpt-image-2 produces
    intermediate frames, then ``asset`` events with the persisted Asset
    metadata, then a final ``cost`` event with totals.

    Event types:
      * ``partial``  — ``{variant_index, image_b64}``
      * ``asset``    — ``{variant_index, asset}`` (full Asset shape)
      * ``cost``     — ``{provider_cost_usd, user_cost_usd, markup_percent}``
      * ``error``    — ``{message}``
      * ``done``     — terminal sentinel
    """
    settings = get_settings()
    size = _generation_size(workspace)

    # Pre-resolve references so a bad ID errors with 422 before the stream opens.
    refs = [
        _resolve_asset(session, workspace, aid, "reference_asset_ids")
        for aid in body.reference_asset_ids
    ]
    ref_images: list[FileTuple] = [_asset_to_filetuple(workspace, a) for a in refs]

    async def event_source() -> AsyncIterator[bytes]:
        agg_usage: dict[str, int] = {}
        saved: list[Asset] = []
        stream_iter = (
            edit_stream(
                client,
                model=settings.openai_image_model,
                prompt=body.prompt,
                images=ref_images,
                size=size,
                quality=body.quality,
                n=body.n,
                partial_images=2,
                timeout=settings.image_generation_timeout_s,
            )
            if ref_images
            else generate_stream(
                client,
                model=settings.openai_image_model,
                prompt=body.prompt,
                size=size,
                quality=body.quality,
                n=body.n,
                partial_images=2,
                timeout=settings.image_generation_timeout_s,
            )
        )
        try:
            async for ev in stream_iter:
                etype = ev.get("type")
                if etype == "partial":
                    yield _sse(
                        "partial",
                        {
                            "variant_index": ev["variant_index"],
                            "image_b64": ev["image_b64"],
                        },
                    )
                elif etype == "completed":
                    # First completed event carries the usage block; later
                    # variants may carry incremental usage. Accumulate so
                    # the final cost is exact.
                    if ev.get("usage"):
                        for k, v in ev["usage"].items():
                            agg_usage[k] = agg_usage.get(k, 0) + v
                    asset = save_generation(
                        session,
                        workspace,
                        image_bytes=b64_to_bytes(ev["image_b64"]),
                        prompt=body.prompt,
                        model=settings.openai_image_model,
                        size=size,
                        quality=body.quality,
                        variant_index=ev["variant_index"],
                        usage=ev.get("usage", {}),
                        reference_ids=[a.id for a in refs if a.id is not None],
                        commit=False,
                    )
                    saved.append(asset)
                    yield _sse(
                        "asset",
                        {
                            "variant_index": ev["variant_index"],
                            "asset": _asset_to_out(asset, workspace).model_dump(
                                mode="json"
                            ),
                        },
                    )
                elif etype == "error":
                    yield _sse("error", {"message": ev.get("message", "Unknown error")})

            session.commit()
            for a in saved:
                session.refresh(a)

            provider_cost = cost_from_usage(agg_usage)
            user_cost = apply_markup(provider_cost, settings.pricing_markup_percent)
            yield _sse(
                "cost",
                {
                    "provider_cost_usd": provider_cost,
                    "user_cost_usd": user_cost,
                    "markup_percent": settings.pricing_markup_percent,
                    "usage": agg_usage,
                    "asset_ids": [a.id for a in saved],
                    "references": [a.id for a in refs if a.id is not None],
                },
            )
            yield _sse("done", {})
        except asyncio.CancelledError:
            session.rollback()
            raise
        except Exception as exc:
            session.rollback()
            log.exception("generate_stream_failed", workspace=workspace.slug)
            yield _sse("error", {"message": str(exc)})

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ----------------------------------------------------------------------- edit
@router.post(
    "/workspaces/{slug}/edit/stream",
    summary="Iterate on a base asset (+ optional references) — SSE stream",
    response_class=StreamingResponse,
)
async def edit_in_workspace_stream(
    body: EditRequest,
    workspace: Workspace = Depends(resolve_workspace),
    session: Session = Depends(get_session),
    client: AsyncOpenAI = Depends(get_openai_client),
) -> StreamingResponse:
    """SSE endpoint: edit a generated variant (or reference) into a new variant.

    Loads ``base_asset_id`` + any ``reference_asset_ids`` from disk, packs
    them as a multi-image input to ``client.images.edit``, streams partials
    + finals back to the caller, persists every final as a new
    ``Asset(kind='generation')`` linked to its origin via the audit
    payload's ``edit_of`` field.

    All assets must belong to this workspace.
    """
    settings = get_settings()
    size = _generation_size(workspace)

    # Resolve base + references, validate, pack as file tuples.
    base = _resolve_asset(session, workspace, body.base_asset_id, "base_asset_id")
    refs = [
        _resolve_asset(session, workspace, aid, "reference_asset_ids")
        for aid in body.reference_asset_ids
    ]
    images: list[FileTuple] = [_asset_to_filetuple(workspace, a) for a in [base, *refs]]
    edit_chain_ids = [a.id for a in [base, *refs] if a.id is not None]

    async def event_source() -> AsyncIterator[bytes]:
        agg_usage: dict[str, int] = {}
        saved: list[Asset] = []
        try:
            async for ev in edit_stream(
                client,
                model=settings.openai_image_model,
                prompt=body.prompt,
                images=images,
                size=size,
                quality=body.quality,
                n=body.n,
                partial_images=2,
                timeout=settings.image_generation_timeout_s,
            ):
                etype = ev.get("type")
                if etype == "partial":
                    yield _sse(
                        "partial",
                        {
                            "variant_index": ev["variant_index"],
                            "image_b64": ev["image_b64"],
                        },
                    )
                elif etype == "completed":
                    if ev.get("usage"):
                        for k, v in ev["usage"].items():
                            agg_usage[k] = agg_usage.get(k, 0) + v
                    asset = save_generation(
                        session,
                        workspace,
                        image_bytes=b64_to_bytes(ev["image_b64"]),
                        prompt=body.prompt,
                        model=settings.openai_image_model,
                        size=size,
                        quality=body.quality,
                        variant_index=ev["variant_index"],
                        usage=ev.get("usage", {}),
                        commit=False,
                    )
                    # Extra audit row that captures the edit chain.
                    audit.record(
                        session,
                        event="asset.edited",
                        workspace_id=workspace.id,
                        workspace_slug=workspace.slug,
                        payload={
                            "new_asset_id": asset.id,
                            "edit_of": base.id,
                            "references": [a.id for a in refs],
                            "prompt": body.prompt,
                        },
                    )
                    saved.append(asset)
                    yield _sse(
                        "asset",
                        {
                            "variant_index": ev["variant_index"],
                            "asset": _asset_to_out(asset, workspace).model_dump(
                                mode="json"
                            ),
                            "edit_of": base.id,
                        },
                    )
                elif etype == "error":
                    yield _sse("error", {"message": ev.get("message", "Unknown error")})

            session.commit()
            for a in saved:
                session.refresh(a)

            provider_cost = cost_from_usage(agg_usage)
            user_cost = apply_markup(provider_cost, settings.pricing_markup_percent)
            yield _sse(
                "cost",
                {
                    "provider_cost_usd": provider_cost,
                    "user_cost_usd": user_cost,
                    "markup_percent": settings.pricing_markup_percent,
                    "usage": agg_usage,
                    "asset_ids": [a.id for a in saved],
                    "edit_chain": edit_chain_ids,
                },
            )
            yield _sse("done", {})
        except asyncio.CancelledError:
            session.rollback()
            raise
        except Exception as exc:
            session.rollback()
            log.exception("edit_stream_failed", workspace=workspace.slug)
            yield _sse("error", {"message": str(exc)})

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ----------------------------------------------------------------- references
@router.post(
    "/workspaces/{slug}/references",
    response_model=ReferenceUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload reference images (logos, mood boards, product photos)",
)
async def upload_references(
    files: list[UploadFile] = File(..., description="One or more image files."),
    workspace: Workspace = Depends(resolve_workspace),
    session: Session = Depends(get_session),
) -> ReferenceUploadResponse:
    """Normalize each upload and save it as an ``Asset(kind='reference')``.

    Every upload is re-encoded as an RGBA PNG with EXIF rotation baked in
    so it'll never trip the gpt-image-2 edit endpoint when used as a
    reference later.
    """
    if not files:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="At least one file is required."
        )
    if len(files) > MAX_REFERENCE_FILES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Too many files (max {MAX_REFERENCE_FILES}).",
        )

    saved: list[Asset] = []
    for up in files:
        raw = await up.read()
        if not raw:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Empty upload: '{up.filename}'.",
            )
        if len(raw) > MAX_REFERENCE_BYTES_PER_FILE:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=(
                    f"File '{up.filename}' is "
                    f"{len(raw) / 1_048_576:.1f} MB; max is "
                    f"{MAX_REFERENCE_BYTES_PER_FILE // 1_048_576} MB."
                ),
            )
        try:
            norm = normalize(up.filename or "image", raw)
        except NormalizeError as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

        saved.append(
            save_reference(
                session,
                workspace,
                normalized=norm,
                original_filename=up.filename or "image",
                commit=False,
            )
        )

    session.commit()
    for a in saved:
        session.refresh(a)

    log.info(
        "references_uploaded",
        workspace=workspace.slug,
        count=len(saved),
    )
    return ReferenceUploadResponse(
        references=[_asset_to_out(a, workspace) for a in saved],
        total=len(saved),
    )


# --------------------------------------------------------------------- exports
@router.post(
    "/workspaces/{slug}/exports/psd",
    response_model=PsdExportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Export a generation as Tier A flat PSD (CMYK / RGB, 300 DPI)",
)
async def export_psd(
    body: PsdExportRequest,
    workspace: Workspace = Depends(resolve_workspace),
    session: Session = Depends(get_session),
) -> PsdExportResponse:
    """Wrap a generation PNG in a Photoshop file with workspace print specs.

    Tier A = single flat layer in the requested colour space. Tier B
    (layered via SAM-2) lands once the self-hosted segmentation endpoint
    is wired in slice 4.5.
    """
    if body.color_space.upper() not in {"CMYK", "RGB"}:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"color_space must be 'CMYK' or 'RGB', got '{body.color_space}'.",
        )
    color_space = body.color_space.upper()

    # Normalise tier — accept "A+OCR" / "A_OCR" / "OCR" all → "A+OCR"
    raw_tier = body.tier.upper().replace("_", "+").replace(" ", "")
    if raw_tier == "OCR":
        raw_tier = "A+OCR"
    tier = raw_tier
    if tier not in {"A", "B", "C", "A+OCR"}:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"tier must be 'A', 'A+OCR', 'B', or 'C', got '{body.tier}'."
            ),
        )

    settings = get_settings()
    # Tier C (SAM-2 + OCR) and A+OCR (OCR-only) both need the OCR pipeline,
    # which is gated by the same FORME_TIER_C_ENABLED toggle.
    if tier in {"C", "A+OCR"} and not settings.tier_c_enabled:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"Tier {tier} requires OCR which is disabled. Enable "
                "FORME_TIER_C_ENABLED=true in your .env (or toggle in "
                "Settings → Tier C)."
            ),
        )

    source = _resolve_asset(session, workspace, body.source_asset_id, "source_asset_id")
    if source.kind != "generation":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Can only export 'generation' assets to PSD; "
                f"asset {body.source_asset_id} is '{source.kind}'."
            ),
        )

    out_path = (
        workspace_root(workspace.slug)
        / "exports"
        / derive_export_filename(source.id or 0)
    )

    source_path = absolute_path(workspace, source)
    segmentation = None
    ocr_result = None

    try:
        if tier == "A":
            result = export_to_psd(
                source_png_path=source_path,
                out_path=out_path,
                color_space=color_space,  # type: ignore[arg-type]
                dpi=body.dpi,
            )
        elif tier == "A+OCR":
            # Flat PSD + OCR text overlays. NO segmentation dependency —
            # this exists specifically so users can fix garbled text
            # without needing SAM-2/SAM-3 to be available.
            try:
                ocr_result = ocr_extract(source_path.read_bytes())
            except OcrUnavailableError as exc:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
                ) from exc
            result = export_to_psd_a_ocr(
                source_png_path=source_path,
                out_path=out_path,
                ocr=ocr_result,
                color_space=color_space,  # type: ignore[arg-type]
                dpi=body.dpi,
            )
        elif tier == "B":
            segmentation = await run_segmentation(source_path.read_bytes())
            result = export_to_psd_tier_b(
                source_png_path=source_path,
                out_path=out_path,
                segmentation=segmentation,
                color_space=color_space,  # type: ignore[arg-type]
                dpi=body.dpi,
            )
        else:  # tier == "C"
            segmentation = await run_segmentation(source_path.read_bytes())
            try:
                ocr_result = ocr_extract(source_path.read_bytes())
            except OcrUnavailableError as exc:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
                ) from exc
            result = export_to_psd_tier_c(
                source_png_path=source_path,
                out_path=out_path,
                segmentation=segmentation,
                ocr=ocr_result,
                color_space=color_space,  # type: ignore[arg-type]
                dpi=body.dpi,
            )
    except HTTPException:
        raise
    except Exception as exc:
        log.exception(
            "psd_export_failed",
            workspace=workspace.slug,
            source=source.id,
            tier=tier,
        )
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Tier {tier} PSD export failed: {exc}",
        ) from exc

    payload_extra: dict[str, Any] = {
        "color_space": result.color_space,
        "dpi": result.dpi,
        "width": result.width,
        "height": result.height,
        "tier": result.tier,
        "layer_count": result.layer_count,
    }
    if segmentation is not None:
        payload_extra["segmentation_provider"] = segmentation.provider
        payload_extra["segmentation_model"] = segmentation.model
        payload_extra["mask_count"] = len(segmentation.masks)
        # When SAM 3.x runs text-prompted, surface the labels found so the
        # audit row records *what* the model identified, not just the count.
        labelled = [m.label for m in segmentation.masks if m.label]
        if labelled:
            payload_extra["mask_labels"] = labelled
    if ocr_result is not None:
        payload_extra["text_region_count"] = len(ocr_result.regions)
        payload_extra["ocr_lang"] = ocr_result.lang

    asset = save_export(
        session,
        workspace,
        source_asset_id=source.id or 0,
        out_path=result.path,
        mime_type="image/vnd.adobe.photoshop",
        payload_extra=payload_extra,
        audit_event=f"export.psd.tier_{result.tier.lower()}.created",
    )

    # Register the sidecar JSON (Tier C only) as a separate audit row so
    # downstream automations can find it via the audit trail.
    sidecar_url = None
    if result.sidecar_path is not None and result.sidecar_path.exists():
        try:
            sidecar_asset = save_export(
                session,
                workspace,
                source_asset_id=source.id or 0,
                out_path=result.sidecar_path,
                mime_type="application/json",
                payload_extra={"of_psd_asset_id": asset.id, "kind": "ocr_sidecar"},
                audit_event="export.psd.tier_c.sidecar_saved",
            )
            sidecar_url = f"/api/packaging/workspaces/{workspace.slug}/assets/{sidecar_asset.id}/file"
        except ValueError:
            log.warning("sidecar_save_failed", path=str(result.sidecar_path))

    log.info(
        "psd_exported",
        workspace=workspace.slug,
        source_asset_id=source.id,
        export_asset_id=asset.id,
        tier=result.tier,
        color_space=result.color_space,
        dpi=result.dpi,
        layer_count=result.layer_count,
    )
    return PsdExportResponse(
        asset=_asset_to_out(asset, workspace),
        source_asset_id=source.id or 0,
        tier=result.tier,
        color_space=result.color_space,
        dpi=result.dpi,
        width=result.width,
        height=result.height,
        layer_count=result.layer_count,
        sidecar_url=sidecar_url,
        segmentation_provider=segmentation.provider if segmentation else None,
        text_layer_count=(
            len(ocr_result.regions) if ocr_result is not None else None
        ),
    )


# --------------------------------------------------------------- PDF export
@router.post(
    "/workspaces/{slug}/exports/pdf",
    response_model=PdfExportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Export a generation as print-ready PDF/X-4 (CMYK, trim/bleed/marks)",
)
async def export_pdf(
    body: PdfExportRequest,
    workspace: Workspace = Depends(resolve_workspace),
    session: Session = Depends(get_session),
) -> PdfExportResponse:
    """Generate a CMYK print PDF using the workspace's frozen trim + bleed.

    Output is a PDF/X-4-compatible file:
      * ICC profile embedded as an OutputIntent
      * TrimBox + BleedBox + MediaBox set per the workspace preset
      * Optional 5 mm trim marks + bullseye registration marks
    """
    source = _resolve_asset(session, workspace, body.source_asset_id, "source_asset_id")
    if source.kind != "generation":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Can only export 'generation' assets to PDF; "
                f"asset {body.source_asset_id} is '{source.kind}'."
            ),
        )

    # Workspace's frozen print specs drive trim + bleed.
    specs = workspace.specs or {}
    trim = specs.get("trim_mm") or {}
    try:
        trim_w = float(trim["w"])
        trim_h = float(trim["h"])
        bleed = float(specs["bleed_mm"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"Workspace '{workspace.slug}' is missing frozen trim/bleed specs. "
                "Re-create the workspace to repair."
            ),
        ) from exc

    out_path = (
        workspace_root(workspace.slug)
        / "exports"
        / derive_pdf_filename(source.id or 0)
    )

    try:
        result = export_to_pdf(
            source_png_path=absolute_path(workspace, source),
            out_path=out_path,
            trim_mm=(trim_w, trim_h),
            bleed_mm=bleed,
            dpi=body.dpi,
            trim_marks=body.trim_marks,
            registration_marks=body.registration_marks,
        )
    except Exception as exc:
        log.exception(
            "pdf_export_failed",
            workspace=workspace.slug,
            source=source.id,
        )
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"PDF export failed: {exc}",
        ) from exc

    asset = save_export(
        session,
        workspace,
        source_asset_id=source.id or 0,
        out_path=result.path,
        mime_type="application/pdf",
        payload_extra={
            "trim_mm": {"w": trim_w, "h": trim_h},
            "bleed_mm": bleed,
            "dpi": result.dpi,
            "icc_profile": result.icc_profile,
            "icc_embedded": result.icc_embedded,
            "trim_marks": result.trim_marks,
            "registration_marks": result.registration_marks,
        },
        audit_event="export.pdf.created",
    )

    log.info(
        "pdf_exported_to_workspace",
        workspace=workspace.slug,
        source_asset_id=source.id,
        export_asset_id=asset.id,
        icc_profile=result.icc_profile,
        icc_embedded=result.icc_embedded,
    )
    return PdfExportResponse(
        asset=_asset_to_out(asset, workspace),
        source_asset_id=source.id or 0,
        trim_mm={"w": trim_w, "h": trim_h},
        bleed_mm=bleed,
        dpi=result.dpi,
        icc_profile=result.icc_profile,
        icc_embedded=result.icc_embedded,
        trim_marks=result.trim_marks,
        registration_marks=result.registration_marks,
    )


# ------------------------------------------------------------ Vector export
@router.post(
    "/workspaces/{slug}/exports/vector",
    response_model=VectorExportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Vectorize a generation into SVG (vectorizer_ai or inkscape_potrace)",
)
async def export_vector(
    body: VectorExportRequest,
    workspace: Workspace = Depends(resolve_workspace),
    session: Session = Depends(get_session),
) -> VectorExportResponse:
    """Convert a generation PNG into an SVG file.

    Routes to ``FORME_VECTORIZER_PROVIDER`` by default. The UI may pass
    ``provider`` explicitly when retrying with the configured fallback —
    failures **never** auto-fall-back; the caller decides.
    """
    source = _resolve_asset(session, workspace, body.source_asset_id, "source_asset_id")
    if source.kind != "generation":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Can only export 'generation' assets to SVG; "
                f"asset {body.source_asset_id} is '{source.kind}'."
            ),
        )

    png_bytes = absolute_path(workspace, source).read_bytes()

    result = await run_vectorize(png_bytes, provider=body.provider)

    out_path = (
        workspace_root(workspace.slug)
        / "exports"
        / derive_vector_filename(source.id or 0)
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(result.svg_bytes)

    asset = save_export(
        session,
        workspace,
        source_asset_id=source.id or 0,
        out_path=out_path,
        mime_type="image/svg+xml",
        payload_extra={
            "provider": result.provider,
            "mode": result.mode,
        },
        audit_event="export.vector.created",
    )

    log.info(
        "vector_exported_to_workspace",
        workspace=workspace.slug,
        source_asset_id=source.id,
        export_asset_id=asset.id,
        provider=result.provider,
        mode=result.mode,
        size_bytes=result.size_bytes,
    )
    return VectorExportResponse(
        asset=_asset_to_out(asset, workspace),
        source_asset_id=source.id or 0,
        provider=result.provider,
        mode=result.mode,
        size_bytes=result.size_bytes,
    )


# ---------------------------------------------------------------- CDR export
@router.post(
    "/workspaces/{slug}/exports/cdr",
    response_model=CdrExportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Vectorize a generation and convert the SVG to CorelDRAW (.cdr)",
)
async def export_cdr(
    body: CdrExportRequest,
    workspace: Workspace = Depends(resolve_workspace),
    session: Session = Depends(get_session),
) -> CdrExportResponse:
    """Two-stage export: PNG → SVG (slice 6) → CDR (slice 7).

    Each stage's provider is independently overridable via the request
    body — that's how the UI implements the "Try with <fallback>?"
    button. Stages never auto-fall-back.
    """
    source = _resolve_asset(session, workspace, body.source_asset_id, "source_asset_id")
    if source.kind != "generation":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Can only export 'generation' assets to CDR; "
                f"asset {body.source_asset_id} is '{source.kind}'."
            ),
        )

    # 1. Vectorize the PNG (slice 6).
    png_bytes = absolute_path(workspace, source).read_bytes()
    vector_result = await run_vectorize(png_bytes, provider=body.vector_provider)

    # 2. Convert the SVG to CDR (slice 7).
    cdr_result = await convert_svg_to_cdr(
        vector_result.svg_bytes, provider=body.cdr_provider
    )

    # 3. Persist the CDR on disk + as an Asset(kind="export").
    out_path = (
        workspace_root(workspace.slug)
        / "exports"
        / derive_cdr_filename(source.id or 0)
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(cdr_result.cdr_bytes)

    asset = save_export(
        session,
        workspace,
        source_asset_id=source.id or 0,
        out_path=out_path,
        mime_type="application/x-cdr",
        payload_extra={
            "vector_provider": vector_result.provider,
            "vector_mode": vector_result.mode,
            "svg_size_bytes": vector_result.size_bytes,
            "cdr_provider": cdr_result.provider,
            "cdr_size_bytes": cdr_result.size_bytes,
        },
        audit_event="export.cdr.created",
    )

    log.info(
        "cdr_exported_to_workspace",
        workspace=workspace.slug,
        source_asset_id=source.id,
        export_asset_id=asset.id,
        vector_provider=vector_result.provider,
        cdr_provider=cdr_result.provider,
        cdr_size_bytes=cdr_result.size_bytes,
    )
    return CdrExportResponse(
        asset=_asset_to_out(asset, workspace),
        source_asset_id=source.id or 0,
        vector_provider=vector_result.provider,
        cdr_provider=cdr_result.provider,
        svg_size_bytes=vector_result.size_bytes,
        cdr_size_bytes=cdr_result.size_bytes,
    )


# ─────────────────────────────────────────────────────────── Composable PSD
@router.post(
    "/workspaces/{slug}/compose/discover",
    response_model=ComposeDiscoverResponse,
    summary="Analyse a finished design → JSON manifest of visual elements",
)
async def compose_discover(
    body: ComposeDiscoverRequest,
    workspace: Workspace = Depends(resolve_workspace),
    session: Session = Depends(get_session),
    client: AsyncOpenAI = Depends(get_openai_client),
) -> ComposeDiscoverResponse:
    """Run GPT-4o-mini vision over the source design and return a JSON
    manifest of detected visual elements (logos, headlines, illustrations,
    ornaments, body-copy blocks) with positions in mm + suggested prompts.

    The frontend renders this manifest for review, lets the user edit
    each element's prompt, add missing elements, or remove unwanted ones,
    then calls the assemble endpoint with the final manifest.
    """
    source = _resolve_asset(session, workspace, body.source_asset_id, "source_asset_id")
    if source.kind != "generation":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Can only compose from 'generation' assets; "
                f"asset {body.source_asset_id} is '{source.kind}'."
            ),
        )

    # Pull frozen trim dims from the workspace specs.
    specs = workspace.specs or {}
    trim = specs.get("trim_mm") or {}
    try:
        trim_w = float(trim["w"])
        trim_h = float(trim["h"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"Workspace '{workspace.slug}' is missing frozen trim specs."
            ),
        ) from exc

    image_bytes = absolute_path(workspace, source).read_bytes()
    specs_list = await discover_elements(
        client,
        image_bytes,
        trim_mm=(trim_w, trim_h),
        extra_hint=body.extra_hint,
    )

    log.info(
        "compose_discovered",
        workspace=workspace.slug,
        source_asset_id=source.id,
        element_count=len(specs_list),
    )
    return ComposeDiscoverResponse(
        source_asset_id=source.id or 0,
        trim_mm={"w": trim_w, "h": trim_h},
        elements=[ElementSpecOut(**s.to_dict()) for s in specs_list],
        # GPT-4o-mini vision ~$0.001-0.005 per call; we don't surface
        # exact tokens (the chat-completions response doesn't carry the
        # same usage shape as images). Reported as 0 for now.
        discovery_cost_usd=0.0,
    )


@router.post(
    "/workspaces/{slug}/exports/psd-composable",
    response_model=ComposeAssembleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate every element + assemble into a layered PSD",
)
async def compose_assemble(
    body: ComposeAssembleRequest,
    workspace: Workspace = Depends(resolve_workspace),
    session: Session = Depends(get_session),
    client: AsyncOpenAI = Depends(get_openai_client),
) -> ComposeAssembleResponse:
    """Fire N parallel gpt-image-2 calls (one per element) with
    ``transparent_background=true``, then assemble the results into a
    proper layered PSD at CMYK 300 DPI.

    ``body.elements`` is the final manifest after the user reviewed +
    edited + added missing items. ``kind="body_copy"`` elements are
    skipped during per-element generation — the assumption is the user
    will handle dense regulatory copy via the Tier A+OCR pipeline
    instead (cheaper, no garbled small print risk).
    """
    settings = get_settings()
    source = _resolve_asset(session, workspace, body.source_asset_id, "source_asset_id")
    if source.kind != "generation":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Can only compose from 'generation' assets; "
                f"asset {body.source_asset_id} is '{source.kind}'."
            ),
        )

    specs_ws = workspace.specs or {}
    trim = specs_ws.get("trim_mm") or {}
    try:
        trim_w = float(trim["w"])
        trim_h = float(trim["h"])
        bleed = float(specs_ws["bleed_mm"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Workspace '{workspace.slug}' is missing trim/bleed specs.",
        ) from exc

    # Filter out body_copy elements — those route to OCR, not gpt-image-2.
    renderable = [e for e in body.elements if e.kind != "body_copy"]
    if not renderable:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "No renderable elements in the manifest (everything was "
                "'body_copy', which is handled by Tier A+OCR instead)."
            ),
        )

    # Generate each element sequentially. FastAPI's single-process worker
    # makes parallelism cheap to defer; sequential keeps OpenAI rate
    # limiting predictable.
    generated: list[GeneratedElement] = []
    try:
        for elem_spec in renderable:
            spec = ElementSpec.from_dict(elem_spec.model_dump())
            gen = await generate_element(
                client,
                spec,
                model=settings.openai_image_model,
                quality=body.quality,
            )
            generated.append(gen)
    except Exception as exc:
        log.exception(
            "compose_element_generation_failed",
            workspace=workspace.slug,
            source=source.id,
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"Per-element generation failed: {exc}",
        ) from exc

    # Persist each generated element as an Asset(kind="generation") so
    # the frontend can show them, allow per-element regeneration, etc.
    persisted_elements: list[ComposeElementOut] = []
    for g in generated:
        # Persist each element as a regular generation. The audit row
        # uses the default "asset.generated" event — element identity is
        # captured by the prompt + the assembled-PSD payload below.
        asset = save_generation(
            session,
            workspace,
            image_bytes=g.png_bytes,
            prompt=f"[composable:{g.spec.name}] {g.spec.prompt}",
            model=settings.openai_image_model,
            size=g.spec.size_px,
            quality=body.quality,
            variant_index=0,
            usage={},
            reference_ids=[source.id] if source.id else None,
        )
        persisted_elements.append(
            ComposeElementOut(
                name=g.spec.name,
                label=g.spec.label,
                asset_id=asset.id or 0,
                width_px=g.width_px,
                height_px=g.height_px,
                cost_usd=g.cost_usd,
            )
        )

    # Assemble the layered PSD.
    out_path = (
        workspace_root(workspace.slug)
        / "exports"
        / derive_composable_filename(source.id or 0)
    )
    try:
        result = assemble_composable_psd(
            elements=generated,
            trim_mm=(trim_w, trim_h),
            bleed_mm=bleed,
            dpi=body.dpi,
            out_path=out_path,
            color_space=body.color_space,
        )
    except Exception as exc:
        log.exception("compose_assembly_failed", workspace=workspace.slug)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"PSD assembly failed: {exc}",
        ) from exc

    # Register the assembled PSD as an export Asset.
    export_asset = save_export(
        session,
        workspace,
        source_asset_id=source.id or 0,
        out_path=result.path,
        mime_type="image/vnd.adobe.photoshop",
        payload_extra={
            "tier": "Composable",
            "element_count": result.element_count,
            "layer_count": result.layer_count,
            "dpi": result.dpi,
            "color_space": body.color_space,
            "width_px": result.width_px,
            "height_px": result.height_px,
            "elements": [e.model_dump() for e in persisted_elements],
            "total_generation_cost_usd": result.total_generation_cost_usd,
        },
        audit_event="export.psd.composable.created",
    )

    log.info(
        "composable_psd_exported",
        workspace=workspace.slug,
        source_asset_id=source.id,
        export_asset_id=export_asset.id,
        elements=result.element_count,
        cost_usd=result.total_generation_cost_usd,
    )
    return ComposeAssembleResponse(
        asset=_asset_to_out(export_asset, workspace),
        source_asset_id=source.id or 0,
        element_count=result.element_count,
        layer_count=result.layer_count,
        elements=persisted_elements,
        total_cost_usd=result.total_generation_cost_usd,
        dpi=result.dpi,
        color_space=body.color_space,
        width_px=result.width_px,
        height_px=result.height_px,
    )


# --------------------------------------------------------------------- assets
@router.get(
    "/workspaces/{slug}/assets",
    response_model=list[AssetOut],
    summary="List assets attached to a workspace",
)
async def list_assets(
    workspace: Workspace = Depends(resolve_workspace),
    session: Session = Depends(get_session),
    kind: str | None = None,
) -> list[AssetOut]:
    stmt = select(Asset).where(Asset.workspace_id == workspace.id)
    if kind:
        stmt = stmt.where(Asset.kind == kind)
    stmt = stmt.order_by(col(Asset.created_at).desc())
    rows = session.exec(stmt).all()
    return [_asset_to_out(a, workspace) for a in rows]


@router.get(
    "/workspaces/{slug}/assets/{asset_id}/file",
    summary="Serve the asset's binary file",
    response_class=FileResponse,
)
async def serve_asset_file(
    asset_id: int,
    workspace: Workspace = Depends(resolve_workspace),
    session: Session = Depends(get_session),
) -> FileResponse:
    asset = session.get(Asset, asset_id)
    if asset is None or asset.workspace_id != workspace.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Asset not found.")
    path = absolute_path(workspace, asset)
    if not path.is_file():
        raise HTTPException(
            status.HTTP_410_GONE,
            detail=f"Asset row exists but file is missing: {path}",
        )
    return FileResponse(path, media_type=asset.mime_type, filename=asset.filename)


# --------------------------------------------------------------------- helpers
def _resolve_asset(
    session: Session, workspace: Workspace, asset_id: int, field_name: str
) -> Asset:
    asset = session.get(Asset, asset_id)
    if asset is None or asset.workspace_id != workspace.id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{field_name} {asset_id} does not belong to workspace '{workspace.slug}'.",
        )
    if not absolute_path(workspace, asset).is_file():
        raise HTTPException(
            status.HTTP_410_GONE,
            detail=f"Asset {asset_id} is missing on disk.",
        )
    return asset


def _asset_to_filetuple(workspace: Workspace, asset: Asset) -> FileTuple:
    """Read an asset off disk and shape it for the OpenAI SDK."""
    data = absolute_path(workspace, asset).read_bytes()
    return (asset.filename, data, asset.mime_type or "image/png")


def _generation_size(workspace: Workspace) -> str:
    size = workspace.specs.get("generation_size") if workspace.specs else None
    if not isinstance(size, str):
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"Workspace '{workspace.slug}' is missing a frozen generation_size. "
                "Re-create the workspace to repair its specs."
            ),
        )
    return size


def _sse(event: str, data: dict[str, Any]) -> bytes:
    """Format one SSE frame. Always JSON-encoded data."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


def _workspace_to_out(ws: Workspace) -> WorkspaceOut:
    return WorkspaceOut(
        id=ws.id or 0,
        slug=ws.slug,
        name=ws.name,
        module=ws.module,
        product_type=ws.product_type,
        description=ws.description,
        specs=ws.specs,
        created_at=ws.created_at or datetime.now(UTC),
        updated_at=ws.updated_at or datetime.now(UTC),
        folder_path=str(workspace_root(ws.slug)),
    )


def _asset_to_out(asset: Asset, workspace: Workspace) -> AssetOut:
    return AssetOut(
        id=asset.id or 0,
        workspace_id=asset.workspace_id,
        kind=asset.kind,
        filename=asset.filename,
        relative_path=asset.relative_path,
        url=f"/api/packaging/workspaces/{workspace.slug}/assets/{asset.id}/file",
        mime_type=asset.mime_type,
        size_bytes=asset.size_bytes,
        prompt=asset.prompt,
        model=asset.model,
        image_size=asset.image_size,
        quality=asset.quality,
        variant_index=asset.variant_index,
        provider_cost_usd=asset.provider_cost_usd,
        user_cost_usd=asset.user_cost_usd,
        usage=asset.usage,
        created_at=asset.created_at or datetime.now(UTC),
    )
