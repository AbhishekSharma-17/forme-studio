"""Idempotent migration tests for ``app.db._ensure_column``.

The Forme dev DB started life as v0.1 — no ``workspaces.design_mode``
column. When slice 10d added the Design Mode toggle we couldn't ask
developers to wipe ``forme.db``; ``init_db()`` has to upgrade in place.

These tests prove:

1. A fresh DB picks up the new column via ``create_all`` (the happy path).
2. A *legacy* DB — created without the column — gets it back-filled when
   ``init_db()`` runs, and the default value lands on existing rows.
3. Calling ``init_db()`` twice in a row is a no-op (idempotent).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

import app.config as config_module
import app.db as db_module


def _columns(engine: object, table: str) -> set[str]:
    """Return the column names of ``table`` via ``PRAGMA table_info``."""
    with engine.connect() as conn:  # type: ignore[attr-defined]
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {r[1] for r in rows}


def _build_legacy_workspaces_db(db_path: Path) -> None:
    """Create a SQLite DB with the v0.1 workspaces schema (no design_mode).

    Mirrors the columns that existed before slice 10d so we can verify
    ``init_db()`` brings it forward without losing rows.
    """
    engine = create_engine(
        f"sqlite:///{db_path}",
        echo=False,
        connect_args={"check_same_thread": False},
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE workspaces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    slug VARCHAR NOT NULL UNIQUE,
                    name VARCHAR NOT NULL,
                    description VARCHAR,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                "INSERT INTO workspaces (slug, name, description, created_at) "
                "VALUES ('legacy-workspace', 'Legacy Bottle', 'pre-slice-10d', "
                "'2026-01-01 00:00:00')"
            )
        )
    engine.dispose()


@pytest.fixture()
def legacy_db_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Same isolation as ``conftest.isolated_paths`` but with a pre-seeded DB."""
    workspaces = tmp_path / "workspaces"
    db = tmp_path / "forme.db"
    workspaces.mkdir()

    _build_legacy_workspaces_db(db)

    monkeypatch.setenv("FORME_WORKSPACES_DIR", str(workspaces))
    monkeypatch.setenv("FORME_DB_PATH", str(db))
    for k in (
        "OPENAI_API_KEY",
        "VECTORIZER_AI_API_ID",
        "VECTORIZER_AI_API_KEY",
        "CLOUDCONVERT_API_KEY",
        "CLOUDCONVERT_SANDBOX_API_KEY",
    ):
        monkeypatch.setenv(k, "")

    config_module._settings = None
    db_module.reset_engine()
    yield tmp_path
    config_module._settings = None
    db_module.reset_engine()


def test_init_db_backfills_design_mode_on_legacy_workspaces(legacy_db_paths: Path) -> None:
    """Legacy DB (no design_mode column) gets the column added in place."""
    # Sanity: the legacy DB really is missing the column before we run init_db.
    pre = create_engine(
        f"sqlite:///{legacy_db_paths / 'forme.db'}",
        connect_args={"check_same_thread": False},
    )
    assert "design_mode" not in _columns(pre, "workspaces")
    pre.dispose()

    db_module.init_db()

    engine = db_module.get_engine()
    cols = _columns(engine, "workspaces")
    assert "design_mode" in cols, (
        "init_db() must add the design_mode column to legacy workspaces tables"
    )

    # Existing row picked up the SQLite default — 0/False.
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT slug, design_mode FROM workspaces WHERE slug = 'legacy-workspace'")
        ).fetchone()
    assert row is not None
    assert row[0] == "legacy-workspace"
    # SQLite stores BOOLEAN as 0/1 — both forms accepted.
    assert row[1] in (0, False)


def test_init_db_is_idempotent_on_legacy_workspaces(legacy_db_paths: Path) -> None:
    """Calling init_db() twice in a row does not raise / duplicate columns."""
    db_module.init_db()
    db_module.init_db()  # would raise "duplicate column name" if not idempotent

    engine = db_module.get_engine()
    # Single instance of design_mode, no shadow copies.
    cols_list = [
        r[1]
        for r in engine.connect()
        .execute(text("PRAGMA table_info(workspaces)"))
        .fetchall()
    ]
    assert cols_list.count("design_mode") == 1


def test_init_db_fresh_db_has_design_mode_via_create_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A brand-new DB picks up design_mode through SQLModel.create_all directly."""
    db = tmp_path / "fresh.db"
    workspaces = tmp_path / "workspaces"
    workspaces.mkdir()
    monkeypatch.setenv("FORME_WORKSPACES_DIR", str(workspaces))
    monkeypatch.setenv("FORME_DB_PATH", str(db))
    for k in (
        "OPENAI_API_KEY",
        "VECTORIZER_AI_API_ID",
        "VECTORIZER_AI_API_KEY",
        "CLOUDCONVERT_API_KEY",
        "CLOUDCONVERT_SANDBOX_API_KEY",
    ):
        monkeypatch.setenv(k, "")
    config_module._settings = None
    db_module.reset_engine()
    try:
        db_module.init_db()
        engine = db_module.get_engine()
        assert "design_mode" in _columns(engine, "workspaces")
    finally:
        config_module._settings = None
        db_module.reset_engine()
