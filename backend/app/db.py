"""Database engine + session setup (SQLite via SQLModel)."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings

_engine: Engine | None = None


def get_engine() -> Engine:
    """Lazy-construct the SQLite engine so settings can be overridden in tests."""
    global _engine
    if _engine is None:
        settings = get_settings()
        # SQLite needs `check_same_thread=False` when used from FastAPI's threadpool.
        _engine = create_engine(
            settings.db_url,
            echo=False,
            connect_args={"check_same_thread": False},
        )
    return _engine


def reset_engine() -> None:
    """Drop the cached engine — used by tests after monkeypatching settings."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def init_db() -> None:
    """Create all tables. Call once on startup."""
    # Import models so SQLModel sees them before create_all.
    from app.models import asset, audit, workspace  # noqa: F401

    SQLModel.metadata.create_all(get_engine())


def get_session() -> Iterator[Session]:
    """FastAPI dependency that yields a session per request."""
    with Session(get_engine()) as session:
        yield session
