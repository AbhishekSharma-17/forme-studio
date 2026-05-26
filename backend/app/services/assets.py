"""Persist a generated image as a workspace asset.

Single responsibility: turn ``(workspace, image_bytes, prompt, usage)``
into a row in ``assets`` + a file in ``workspaces/<slug>/generations/`` +
an ``asset.generated`` audit event. Anything that calls
``client.images.generate`` should funnel its output through here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from sqlmodel import Session

from app.config import get_settings
from app.models.asset import Asset
from app.models.workspace import Workspace
from app.services import audit
from app.services.filesystem import workspace_root
from app.services.image_normalize import NormalizedImage
from app.services.pricing import apply_markup, cost_from_usage

log = structlog.get_logger(__name__)


def save_generation(
    session: Session,
    workspace: Workspace,
    *,
    image_bytes: bytes,
    prompt: str,
    model: str,
    size: str,
    quality: str,
    variant_index: int,
    usage: dict[str, int],
    reference_ids: list[int] | None = None,
    commit: bool = True,
) -> Asset:
    """Persist a single generated variant.

    Writes the PNG to ``<workspace>/generations/<timestamp>_v<index>.png``,
    inserts an :class:`Asset` row, records ``asset.generated`` in the
    audit trail, and (optionally) commits.

    Args:
        session: open SQLModel session.
        workspace: the workspace we're generating into. ``workspace.id``
            must already be populated.
        image_bytes: the raw PNG bytes returned by OpenAI.
        prompt: the prompt that produced this image (for audit + later UX).
        model, size, quality: gpt-image-2 call parameters.
        variant_index: which variant within the batch (0 for n=1).
        usage: flattened usage dict, ``{"input_tokens": …, …}``.
        commit: commit the session after writing. Set ``False`` when the
            caller is batching multiple saves inside a single transaction.

    Returns:
        The persisted :class:`Asset` (with ``id`` populated).
    """
    if workspace.id is None:
        msg = "Workspace must be persisted before generations can be attached."
        raise ValueError(msg)

    settings = get_settings()
    provider_cost = cost_from_usage(usage)
    user_cost = apply_markup(provider_cost, settings.pricing_markup_percent)

    # Filename + path on disk
    # Microsecond precision so back-to-back saves (or two variants in the
    # same second) never collide on disk.
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    filename = f"{ts}_v{variant_index}.png"
    rel = f"generations/{filename}"
    abs_path: Path = workspace_root(workspace.slug) / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(image_bytes)

    asset = Asset(
        workspace_id=workspace.id,
        kind="generation",
        filename=filename,
        relative_path=rel,
        mime_type="image/png",
        size_bytes=len(image_bytes),
        prompt=prompt,
        model=model,
        image_size=size,
        quality=quality,
        variant_index=variant_index,
        provider_cost_usd=provider_cost,
        user_cost_usd=user_cost,
        usage=usage,
    )
    session.add(asset)
    session.flush()

    audit.record(
        session,
        event="asset.generated",
        workspace_id=workspace.id,
        workspace_slug=workspace.slug,
        payload={
            "asset_id": asset.id,
            "relative_path": rel,
            "prompt": prompt,
            "model": model,
            "size": size,
            "quality": quality,
            "variant_index": variant_index,
            "provider_cost_usd": provider_cost,
            "user_cost_usd": user_cost,
            "usage": usage,
            "references": reference_ids or [],
        },
    )

    if commit:
        session.commit()
        session.refresh(asset)

    log.info(
        "asset_generated",
        workspace=workspace.slug,
        asset_id=asset.id,
        bytes=len(image_bytes),
        provider_cost_usd=provider_cost,
    )
    return asset


def save_reference(
    session: Session,
    workspace: Workspace,
    *,
    normalized: NormalizedImage,
    original_filename: str,
    commit: bool = True,
) -> Asset:
    """Persist a normalized reference image into the workspace.

    Writes the PNG to ``<workspace>/references/<timestamp>_<stem>.png``,
    inserts an :class:`Asset` row with ``kind='reference'`` and records a
    ``reference.uploaded`` audit event.
    """
    if workspace.id is None:
        msg = "Workspace must be persisted before references can be attached."
        raise ValueError(msg)

    # Microsecond precision so back-to-back saves (or two variants in the
    # same second) never collide on disk.
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    safe_stem = Path(normalized.filename).stem
    filename = f"{ts}_{safe_stem}.png"
    rel = f"references/{filename}"
    abs_path: Path = workspace_root(workspace.slug) / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(normalized.data)

    asset = Asset(
        workspace_id=workspace.id,
        kind="reference",
        filename=filename,
        relative_path=rel,
        mime_type=normalized.mime_type,
        size_bytes=len(normalized.data),
        prompt=None,
        model=None,
        image_size=f"{normalized.width}x{normalized.height}",
        quality=None,
        variant_index=0,
    )
    session.add(asset)
    session.flush()

    audit.record(
        session,
        event="reference.uploaded",
        workspace_id=workspace.id,
        workspace_slug=workspace.slug,
        payload={
            "asset_id": asset.id,
            "relative_path": rel,
            "original_filename": original_filename,
            "width": normalized.width,
            "height": normalized.height,
            "size_bytes": len(normalized.data),
        },
    )

    if commit:
        session.commit()
        session.refresh(asset)

    log.info(
        "reference_uploaded",
        workspace=workspace.slug,
        asset_id=asset.id,
        bytes=len(normalized.data),
        size=f"{normalized.width}x{normalized.height}",
    )
    return asset


def save_export(
    session: Session,
    workspace: Workspace,
    *,
    source_asset_id: int,
    out_path: Path,
    mime_type: str,
    payload_extra: dict[str, Any] | None = None,
    audit_event: str = "export.created",
    commit: bool = True,
) -> Asset:
    """Register a file that already exists on disk as an export Asset.

    Unlike :func:`save_generation`, the file is written by the caller
    (export pipeline); this function only persists the DB row + audit
    event. The path must be inside the workspace tree.
    """
    if workspace.id is None:
        msg = "Workspace must be persisted before exports can be attached."
        raise ValueError(msg)
    root = workspace_root(workspace.slug)
    try:
        rel = out_path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        msg = f"Export path {out_path} is not inside workspace {workspace.slug}."
        raise ValueError(msg) from exc

    rel_str = str(rel)
    asset = Asset(
        workspace_id=workspace.id,
        kind="export",
        filename=out_path.name,
        relative_path=rel_str,
        mime_type=mime_type,
        size_bytes=out_path.stat().st_size,
        prompt=None,
        model=None,
        image_size=None,
        quality=None,
        variant_index=0,
    )
    session.add(asset)
    session.flush()

    audit.record(
        session,
        event=audit_event,
        workspace_id=workspace.id,
        workspace_slug=workspace.slug,
        payload={
            "asset_id": asset.id,
            "source_asset_id": source_asset_id,
            "relative_path": rel_str,
            "mime_type": mime_type,
            "size_bytes": asset.size_bytes,
            **(payload_extra or {}),
        },
    )

    if commit:
        session.commit()
        session.refresh(asset)

    log.info(
        "export_saved",
        workspace=workspace.slug,
        asset_id=asset.id,
        kind=audit_event,
        bytes=asset.size_bytes,
    )
    return asset


def absolute_path(workspace: Workspace, asset: Asset) -> Path:
    """Resolve an asset's on-disk location for serving."""
    return workspace_root(workspace.slug) / asset.relative_path
