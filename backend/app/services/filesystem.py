"""Workspace filesystem layout.

Each workspace has a predictable directory tree on disk. This module is the
single place that knows that layout — modules call into it rather than
joining paths themselves.

Layout::

    <workspaces_root>/<workspace_slug>/
        brief.md             # human-editable brief (created blank)
        references/          # uploaded reference images
        generations/         # AI-generated artwork (timestamped)
        exports/             # print-ready outputs (psd/pdf/svg/...)
        audit.log.jsonl      # local mirror of audit events (DB is canonical)
"""

from __future__ import annotations

import re
from pathlib import Path

from app.config import get_settings

_SLUG_RE = re.compile(r"[^a-z0-9-]+")

WORKSPACE_SUBDIRS: tuple[str, ...] = ("references", "generations", "exports")


def slugify(name: str) -> str:
    """Lowercase, hyphenated, filesystem-safe slug.

    Collapses runs of disallowed characters into a single hyphen, trims
    leading/trailing hyphens, and caps the length so it stays usable as both
    a folder name and a URL segment.
    """
    s = name.strip().lower()
    s = _SLUG_RE.sub("-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:80] or "workspace"


def workspace_root(slug: str) -> Path:
    """Return the absolute path to a workspace's directory (not created)."""
    return get_settings().workspaces_dir / slug


def ensure_workspace_dir(slug: str) -> Path:
    """Create the workspace directory tree if it doesn't exist.

    Idempotent — safe to call repeatedly. Returns the workspace root.
    """
    root = workspace_root(slug)
    root.mkdir(parents=True, exist_ok=True)
    for sub in WORKSPACE_SUBDIRS:
        (root / sub).mkdir(exist_ok=True)
    brief = root / "brief.md"
    if not brief.exists():
        brief.write_text(f"# {slug}\n\n_Write the design brief here._\n", encoding="utf-8")
    return root


def workspace_exists_on_disk(slug: str) -> bool:
    return workspace_root(slug).is_dir()
