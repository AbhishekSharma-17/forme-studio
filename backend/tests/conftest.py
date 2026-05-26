"""Shared pytest fixtures.

Each test gets an isolated SQLite file in a per-test temp directory plus a
fresh workspaces root, so tests can run in parallel and never collide on
the real ``./forme.db``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.config as config_module
import app.db as db_module
import app.deps as deps_module
from app.deps import get_openai_client
from tests.stubs import StubOpenAIClient


@pytest.fixture()
def isolated_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point Forme at a fresh DB + workspaces root for the duration of a test.

    Also blanks out every provider credential so tests stay hermetic — even
    if the developer's real ``.env`` sits next to ``pyproject.toml``,
    explicit env vars win over the file in pydantic-settings.
    """
    workspaces = tmp_path / "workspaces"
    db = tmp_path / "forme.db"
    workspaces.mkdir()

    monkeypatch.setenv("FORME_WORKSPACES_DIR", str(workspaces))
    monkeypatch.setenv("FORME_DB_PATH", str(db))

    # Scrub provider credentials so tests can't leak to live APIs.
    for k in (
        "OPENAI_API_KEY",
        "VECTORIZER_AI_API_ID",
        "VECTORIZER_AI_API_KEY",
        "CLOUDCONVERT_API_KEY",
        "CLOUDCONVERT_SANDBOX_API_KEY",
    ):
        monkeypatch.setenv(k, "")
    # Default providers in tests: vectorizer paid, fallback potrace.
    monkeypatch.setenv("FORME_VECTORIZER_PROVIDER", "vectorizer_ai")
    monkeypatch.setenv("FORME_VECTORIZER_FALLBACK", "inkscape_potrace")
    # CDR exports are off-by-default in production; individual tests that
    # exercise the CDR endpoint flip this on explicitly. The health-route
    # test asserts the default-off state, hence the conftest leaves it off.
    monkeypatch.setenv("FORME_CDR_ENABLED", "false")
    monkeypatch.setenv("FORME_CLOUDCONVERT_SANDBOX", "false")
    # Tier A+OCR (OCR-augmented PSD) is gated by this toggle. The real .env
    # may have it ON (the user flipped it via the dashboard once), but tests
    # must see the off-by-default state unless they opt in explicitly.
    monkeypatch.setenv("FORME_TIER_C_ENABLED", "false")
    # Reset markup so cost assertions don't have to chase a moving target.
    monkeypatch.setenv("FORME_PRICING_MARKUP_PERCENT", "0")
    # Point Inkscape at a path that doesn't exist so the capability flag is
    # deterministically false in CI.
    monkeypatch.setenv("FORME_INKSCAPE_PATH", str(tmp_path / "no-inkscape"))

    # Force fresh settings + engine — Settings is cached at module level.
    config_module._settings = None
    db_module.reset_engine()
    return tmp_path


@pytest.fixture()
def client(isolated_paths: Path) -> Iterator[TestClient]:
    """Boot the FastAPI app against the isolated paths and yield a test client."""
    # Import inside the fixture so the lifespan picks up the patched env.
    from app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c

    # Tidy up the module-level caches so the next test starts clean.
    config_module._settings = None
    db_module.reset_engine()
    if "OPENAI_API_KEY" in os.environ:  # belt-and-braces for capability probe tests
        pass


@pytest.fixture()
def fake_openai(client: TestClient) -> Iterator[StubOpenAIClient]:
    """Replace the OpenAI client dependency with a stub for hermetic tests.

    Available to any test that wants to talk to the generate / edit
    routes without burning real API credits.
    """
    stub = StubOpenAIClient()
    deps_module._async_client = stub  # type: ignore[assignment]
    client.app.dependency_overrides[get_openai_client] = lambda: stub
    yield stub
    client.app.dependency_overrides.pop(get_openai_client, None)
    deps_module.reset_openai_client()
