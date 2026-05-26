"""Audit trail — every state-changing action gets one row.

Compliance for the pilot, and the source of truth for "what happened to
this asset" when the customer asks.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlmodel import JSON, Column, Field, SQLModel


def _utc_now() -> datetime:
    return datetime.now(UTC)


class AuditEvent(SQLModel, table=True):
    """One immutable record of something that happened.

    `event` uses dotted names: `workspace.created`, `asset.generated`,
    `asset.exported.psd`, etc. The `payload` carries event-specific data.
    """

    __tablename__ = "audit_events"

    id: int | None = Field(default=None, primary_key=True)
    event: str = Field(index=True, max_length=80)
    workspace_id: int | None = Field(default=None, index=True, foreign_key="workspaces.id")
    actor: str = Field(default="system", max_length=80)
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utc_now, index=True)
