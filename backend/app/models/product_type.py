"""ProductType — a configurable preset for a packaging product.

Was a hardcoded ``PRESETS`` dict in ``app.modules.packaging.presets`` until
the user asked for a configurable surface. The five original presets now
live in this table as ``is_builtin=True`` rows seeded on first startup
(see ``app.services.product_types.seed_builtins``).

Custom rows (``is_builtin=False``) can be created, edited, and deleted
freely via the API. Built-in rows can be **listed and used** but the API
refuses edits/deletes — they're the floor of what the studio ships with.

Workspaces freeze their print specs into ``Workspace.specs`` at creation
time, so editing a ProductType later does NOT mutate any existing
workspace — only future workspace creations pick up the new values.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ProductType(SQLModel, table=True):
    """One configurable preset (built-in or user-created)."""

    __tablename__ = "product_types"

    id: int | None = Field(default=None, primary_key=True)
    # Unique, URL/folder-safe key — also what Workspace.product_type stores.
    key: str = Field(index=True, unique=True, min_length=2, max_length=80)
    label: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)

    # Trim size in millimetres.
    trim_w_mm: float = Field(gt=0)
    trim_h_mm: float = Field(gt=0)
    bleed_mm: float = Field(ge=0, default=3.0)
    dpi: int = Field(default=300, ge=72, le=1200)
    color_space: str = Field(default="CMYK", max_length=10)  # CMYK | RGB
    # gpt-image-2 native size we'll request at generation time.
    generation_size: str = Field(default="1024x1536", max_length=20)
    notes: str = Field(default="", max_length=1000)

    # `is_builtin=True` rows are seeded from PRESETS on first startup and
    # cannot be edited or deleted via the API. They're protected because
    # they're the documented baseline the README + USER_GUIDE reference.
    is_builtin: bool = Field(default=False, index=True)
    # Module ownership — packaging today, apparel tomorrow.
    module: str = Field(default="packaging", index=True, max_length=40)

    created_at: datetime = Field(default_factory=_utc_now, index=True)
    updated_at: datetime = Field(default_factory=_utc_now)
