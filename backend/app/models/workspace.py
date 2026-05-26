"""Workspace = one product/SKU the user is designing for.

Each workspace pins which module owns it (`packaging`, `apparel`, etc.) and
which product type within that module (`lotion_bottle_label`, `cream_box`, ...).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlmodel import JSON, Column, Field, SQLModel


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Workspace(SQLModel, table=True):
    """One product workspace.

    `slug` doubles as the on-disk folder name under `FORME_WORKSPACES_DIR`,
    and as the URL segment in the frontend.
    """

    __tablename__ = "workspaces"

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(index=True, unique=True, min_length=2, max_length=80)
    name: str = Field(min_length=1, max_length=200)

    # Module + product type — packaging is module #1; more modules will follow.
    module: str = Field(default="packaging", index=True, max_length=40)
    product_type: str = Field(max_length=80)

    # Print/design specs frozen at creation time (mm, dpi, bleed, etc.).
    # JSON because the shape varies per product_type and we don't want a
    # migration for every new field a preset adds.
    specs: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    description: str | None = Field(default=None, max_length=2000)

    # Design mode (slice 10d):
    # * False (default) — "Analyze-existing" flow: user uploads a finished
    #   label PNG, system analyzes + assembles + exports.
    # * True — "Brainstorm-on-product" flow: user uploads a plain product
    #   photo + style references + brief, system designs the label visible
    #   on the product, iterates, approves, auto-flattens, then feeds into
    #   the analyze + assemble pipeline.
    design_mode: bool = Field(default=False)

    created_at: datetime = Field(default_factory=_utc_now, index=True)
    updated_at: datetime = Field(default_factory=_utc_now)
