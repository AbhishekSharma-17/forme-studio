"""Shared FastAPI dependencies.

Single source of truth for the OpenAI client + workspace lookup so the
routes can stay focused on shape, not plumbing.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from openai import AsyncOpenAI
from sqlmodel import Session, select

from app.config import get_settings
from app.db import get_session
from app.models.workspace import Workspace

_async_client: AsyncOpenAI | None = None


def get_openai_client() -> AsyncOpenAI:
    """Return a cached ``AsyncOpenAI`` client.

    Raises 503 if ``OPENAI_API_KEY`` isn't configured — the frontend reads
    capability flags from ``/api/health`` and should grey the action out
    before the user ever hits this code path.
    """
    settings = get_settings()
    if not settings.openai_api_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OPENAI_API_KEY is not configured on the backend.",
        )
    global _async_client
    if _async_client is None:
        _async_client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _async_client


def reset_openai_client() -> None:
    """Drop the cached OpenAI client. Used by tests after monkeypatching env."""
    global _async_client
    _async_client = None


def resolve_workspace(slug: str, session: Session = Depends(get_session)) -> Workspace:
    """FastAPI dependency: load a workspace by slug or 404."""
    ws = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    if ws is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"No workspace '{slug}'."
        )
    return ws
