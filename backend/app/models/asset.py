"""Asset = one file produced inside a workspace.

For slice 2 the only kind is ``generation`` (output of gpt-image-2).
Slice 3 will add ``export`` (PSD/PDF/SVG) and slice-on-reference uploads.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlmodel import JSON, Column, Field, SQLModel


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Asset(SQLModel, table=True):
    """One file on disk that belongs to a workspace.

    The ``path`` is stored *relative to the workspace root* so workspaces
    can be moved between machines without rewriting DB rows.
    """

    __tablename__ = "assets"

    id: int | None = Field(default=None, primary_key=True)
    workspace_id: int = Field(index=True, foreign_key="workspaces.id")

    kind: str = Field(index=True, max_length=24)  # "generation" | "export" | "reference"
    filename: str = Field(max_length=200)
    relative_path: str = Field(max_length=400)
    mime_type: str = Field(default="image/png", max_length=80)
    size_bytes: int = Field(default=0, ge=0)

    # gpt-image-2 specifics; empty dict for non-generation kinds.
    prompt: str | None = Field(default=None)
    model: str | None = Field(default=None, max_length=80)
    image_size: str | None = Field(default=None, max_length=24)
    quality: str | None = Field(default=None, max_length=24)
    variant_index: int = Field(default=0, ge=0)

    # Exact cost from OpenAI usage tokens.
    provider_cost_usd: float = Field(default=0.0, ge=0.0)
    user_cost_usd: float = Field(default=0.0, ge=0.0)
    usage: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    created_at: datetime = Field(default_factory=_utc_now, index=True)
