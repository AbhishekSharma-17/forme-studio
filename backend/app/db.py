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
    """Create all tables + seed built-in product types. Call once on startup."""
    # Import models so SQLModel sees them before create_all.
    from app.models import asset, audit, product_type, workspace  # noqa: F401

    SQLModel.metadata.create_all(get_engine())

    # In-place upgrade for SQLite — SQLModel's create_all won't add missing
    # columns to existing tables. List every "new since v0.1" column we
    # need to backfill here and run idempotent ALTER TABLEs. The
    # default-value clause means existing rows automatically pick up the
    # default; new code reading the model just sees the field.
    _ensure_column("workspaces", "design_mode", "BOOLEAN NOT NULL DEFAULT 0")

    # Seed the five built-in product-type presets on first run. Idempotent —
    # the service only inserts rows whose `key` doesn't already exist.
    from app.services.product_types import seed_builtins

    with Session(get_engine()) as session:
        seed_builtins(session)
        session.commit()


def _ensure_column(table: str, column: str, column_def: str) -> None:
    """Add ``column`` to ``table`` if it doesn't exist (SQLite-only).

    Uses ``PRAGMA table_info`` to check for the column rather than relying
    on ``IF NOT EXISTS`` (which SQLite supports for tables but not columns
    in older versions). Safe to call repeatedly.
    """
    from sqlalchemy import text

    with get_engine().connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
        existing = {r[1] for r in rows}
        if column in existing:
            return
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {column_def}"))
        conn.commit()


def get_session() -> Iterator[Session]:
    """FastAPI dependency that yields a session per request."""
    with Session(get_engine()) as session:
        yield session
