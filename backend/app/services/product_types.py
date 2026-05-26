"""Product type service — DB-backed CRUD with built-in protection.

Replaces the old ``app.modules.packaging.presets`` hardcoded dict. The
five original presets are now seeded on first startup as ``is_builtin``
rows; users can also POST their own.

Built-in rows are immutable via the API:
* PATCH refuses with 409 Conflict (clear hint to clone-then-edit).
* DELETE refuses with 409 Conflict.

User-created rows can be edited or deleted freely *unless* an existing
Workspace references their ``key`` — in which case DELETE returns 409
with the count of dependent workspaces. (Editing is fine; we only
freeze specs at workspace creation time, so later edits never mutate
existing workspaces.)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import HTTPException, status
from sqlmodel import Session, col, select

from app.models.product_type import ProductType
from app.models.workspace import Workspace

log = structlog.get_logger(__name__)


# ----------------------------------------------------- built-in seed data


# Mirrors the original PRESETS dict — keep in sync with docs/USER_GUIDE.md.
_BUILTIN_PRESETS: list[dict[str, Any]] = [
    {
        "key": "lotion_bottle_label",
        "label": "Lotion bottle label (250 ml)",
        "description": "Wrap-around label for a 250 ml cosmetic lotion bottle.",
        "trim_w_mm": 70.0,
        "trim_h_mm": 100.0,
        "bleed_mm": 3.0,
        "dpi": 300,
        "color_space": "CMYK",
        "generation_size": "1024x1536",
        "notes": "Portrait label. Leave 4 mm safety margin around copy.",
    },
    {
        "key": "cream_jar_label",
        "label": "Cream jar top label (50 g)",
        "description": "Circular top label for a 50 g cream jar.",
        "trim_w_mm": 60.0,
        "trim_h_mm": 60.0,
        "bleed_mm": 3.0,
        "dpi": 300,
        "color_space": "CMYK",
        "generation_size": "1024x1024",
        "notes": "Square crop; vector ornaments work best around the rim.",
    },
    {
        "key": "cream_box_tuck_end",
        "label": "Cream box (tuck-end carton, 50 ml)",
        "description": "Folding carton for a 50 ml cream tube. 5-panel dieline.",
        "trim_w_mm": 140.0,
        "trim_h_mm": 50.0,
        "bleed_mm": 3.0,
        "dpi": 300,
        "color_space": "CMYK",
        "generation_size": "1536x1024",
        "notes": "Front panel only at generation time; dieline assembly later.",
    },
    {
        "key": "serum_dropper_label",
        "label": "Serum dropper bottle label (30 ml)",
        "description": "Narrow wrap label for a 30 ml dropper bottle.",
        "trim_w_mm": 50.0,
        "trim_h_mm": 80.0,
        "bleed_mm": 3.0,
        "dpi": 300,
        "color_space": "CMYK",
        "generation_size": "1024x1536",
        "notes": "Tall narrow ratio — copy must be short and centered.",
    },
    {
        "key": "shampoo_pouch",
        "label": "Shampoo sachet pouch (10 ml)",
        "description": "Single-use flat sachet for shampoo/conditioner.",
        "trim_w_mm": 90.0,
        "trim_h_mm": 120.0,
        "bleed_mm": 3.0,
        "dpi": 300,
        "color_space": "CMYK",
        "generation_size": "1024x1536",
        "notes": "Two-sided printing; this preset covers the front face.",
    },
]


def seed_builtins(session: Session) -> int:
    """Idempotently insert any missing built-in rows. Returns count inserted."""
    inserted = 0
    for row in _BUILTIN_PRESETS:
        existing = session.exec(
            select(ProductType).where(ProductType.key == row["key"])
        ).first()
        if existing is not None:
            continue
        session.add(ProductType(**row, is_builtin=True, module="packaging"))
        inserted += 1
    if inserted:
        log.info("product_types_seeded", inserted=inserted)
    return inserted


# ---------------------------------------------------------------- queries


def list_all(
    session: Session, module: str = "packaging"
) -> list[ProductType]:
    """Built-ins first (by label), then custom rows newest-first."""
    rows = session.exec(
        select(ProductType)
        .where(ProductType.module == module)
        .order_by(
            col(ProductType.is_builtin).desc(),
            col(ProductType.created_at).asc(),
        )
    ).all()
    return list(rows)


def get_by_key(session: Session, key: str) -> ProductType | None:
    return session.exec(
        select(ProductType).where(ProductType.key == key)
    ).first()


def get_by_id(session: Session, id_: int) -> ProductType | None:
    return session.get(ProductType, id_)


# ----------------------------------------------------- mutating operations


def create(session: Session, payload: dict[str, Any]) -> ProductType:
    """Create a custom (user-defined) product type.

    Raises 409 on key collision; 422 on validation. Always
    ``is_builtin=False`` — built-ins are seed-only.
    """
    key = payload.get("key", "").strip()
    if not key:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="`key` is required and must be non-empty.",
        )
    if get_by_key(session, key) is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Product type with key '{key}' already exists.",
        )

    row = ProductType(
        key=key,
        label=payload.get("label", key),
        description=payload.get("description", ""),
        trim_w_mm=float(payload["trim_w_mm"]),
        trim_h_mm=float(payload["trim_h_mm"]),
        bleed_mm=float(payload.get("bleed_mm", 3.0)),
        dpi=int(payload.get("dpi", 300)),
        color_space=str(payload.get("color_space", "CMYK")),
        generation_size=str(payload.get("generation_size", "1024x1536")),
        notes=str(payload.get("notes", "")),
        is_builtin=False,
        module=str(payload.get("module", "packaging")),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    log.info("product_type_created", key=row.key, label=row.label)
    return row


def update(
    session: Session, key: str, payload: dict[str, Any]
) -> ProductType:
    """Update an existing custom product type.

    Refuses to mutate built-in rows (409). Existing workspaces that
    already reference this key are NOT affected — they froze their specs
    at create time. Only future workspace creations pick up the changes.
    """
    row = get_by_key(session, key)
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Product type '{key}' does not exist.",
        )
    if row.is_builtin:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                f"Built-in product type '{key}' cannot be edited. "
                "Create a custom copy under a new key instead."
            ),
        )

    # Allowlist of mutable fields.
    mutable: tuple[str, ...] = (
        "label",
        "description",
        "trim_w_mm",
        "trim_h_mm",
        "bleed_mm",
        "dpi",
        "color_space",
        "generation_size",
        "notes",
    )
    changed = False
    for field in mutable:
        if field in payload and payload[field] is not None:
            new_val = payload[field]
            if field in ("trim_w_mm", "trim_h_mm", "bleed_mm"):
                new_val = float(new_val)
            elif field == "dpi":
                new_val = int(new_val)
            else:
                new_val = str(new_val) if not isinstance(new_val, str) else new_val
            if getattr(row, field) != new_val:
                setattr(row, field, new_val)
                changed = True

    if changed:
        row.updated_at = datetime.now(UTC)
        session.add(row)
        session.commit()
        session.refresh(row)
        log.info("product_type_updated", key=row.key)
    return row


def delete(session: Session, key: str) -> None:
    """Delete a custom product type.

    Refuses (409) if it's built-in OR any Workspace references the key.
    """
    row = get_by_key(session, key)
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Product type '{key}' does not exist.",
        )
    if row.is_builtin:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                f"Built-in product type '{key}' cannot be deleted. "
                "It's part of the studio's seeded baseline."
            ),
        )

    n_workspaces = len(
        list(
            session.exec(
                select(Workspace).where(Workspace.product_type == key)
            )
        )
    )
    if n_workspaces > 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                f"Product type '{key}' is in use by {n_workspaces} "
                "workspace(s). Delete or migrate those workspaces first."
            ),
        )

    session.delete(row)
    session.commit()
    log.info("product_type_deleted", key=key)


# ----------------------------------------------------------- compat shim


def to_specs(pt: ProductType) -> dict[str, Any]:
    """Project a ProductType into the workspace ``specs`` JSON blob.

    The shape matches what the old ``preset_to_specs()`` returned so the
    existing Workspace.specs schema stays stable and frontend code that
    reads e.g. ``ws.specs.trim_mm.w`` keeps working.
    """
    return {
        "preset_id": pt.key,
        "trim_mm": {"w": pt.trim_w_mm, "h": pt.trim_h_mm},
        "bleed_mm": pt.bleed_mm,
        "dpi": pt.dpi,
        "color_space": pt.color_space,
        "generation_size": pt.generation_size,
        "notes": pt.notes,
    }
