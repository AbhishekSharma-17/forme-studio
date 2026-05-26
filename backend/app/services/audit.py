"""Audit trail writer.

Writes to the database (canonical) AND mirrors a JSONL line into the
workspace folder so the audit trail travels with the project files.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlmodel import Session

from app.models.audit import AuditEvent
from app.services.filesystem import workspace_root


def record(
    session: Session,
    *,
    event: str,
    workspace_id: int | None = None,
    workspace_slug: str | None = None,
    actor: str = "system",
    payload: dict[str, Any] | None = None,
) -> AuditEvent:
    """Persist one audit row + mirror to disk.

    Args:
        session: open SQLModel session (caller commits).
        event: dotted event name, e.g. ``workspace.created``.
        workspace_id: numeric FK if known.
        workspace_slug: slug used to locate the workspace folder for the
            JSONL mirror. If omitted, only the DB row is written.
        actor: who/what triggered the event. Defaults to ``"system"``.
        payload: event-specific JSON data.

    .. note::
        In slice 1 the DB row is flushed (not committed) and the JSONL
        mirror is written *before* the caller commits. In slice 1 the
        only realistic commit failure is the SQLite UNIQUE on
        ``workspaces.slug``, which is pre-checked upstream — so this is
        theoretical. Once slice 2 adds image-gen events with network
        calls, move the mirror write to *after* commit (or wrap in
        try/unlink) so a rollback can't leave an orphan JSONL line.
    """
    row = AuditEvent(
        event=event,
        workspace_id=workspace_id,
        actor=actor,
        payload=payload or {},
    )
    session.add(row)
    session.flush()  # populate `id` and `created_at` for the mirror line

    if workspace_slug:
        _mirror_to_disk(workspace_slug, row)

    return row


def _mirror_to_disk(slug: str, row: AuditEvent) -> None:
    """Append a JSONL line to ``<workspace>/audit.log.jsonl``.

    Best-effort — if the directory isn't there yet (very first event during
    workspace creation), we silently skip the mirror; the DB row is still
    canonical.
    """
    path: Path = workspace_root(slug) / "audit.log.jsonl"
    if not path.parent.is_dir():
        return
    line = {
        "id": row.id,
        "event": row.event,
        "workspace_id": row.workspace_id,
        "actor": row.actor,
        "payload": row.payload,
        "created_at": (row.created_at or datetime.now(UTC)).isoformat(),
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
