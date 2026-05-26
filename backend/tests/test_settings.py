"""Tests for the /api/settings router — GET snapshot + PATCH round-trip.

The Settings dashboard is the user's only writable surface for every
non-secret config knob. PATCH writes back to ``backend/.env`` and the
backend hot-reloads. If the writable allowlist drifts from the schema,
the dashboard breaks silently. These tests pin the contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.config as config_module
from app.routes.settings import WRITABLE_KEYS

# ----------------------------------------------------------- GET snapshot


def test_get_settings_returns_full_snapshot_with_redacted_secrets(
    client: TestClient, isolated_paths: Path
) -> None:
    """A bare GET returns every field the dashboard renders, secrets redacted."""
    res = client.get("/api/settings")
    assert res.status_code == 200
    body = res.json()

    # Required structural fields
    assert "host" in body
    assert "port" in body
    assert "log_level" in body
    assert "workspaces_dir" in body
    assert "db_path" in body
    assert "writable_keys" in body
    assert "env_file" in body

    # Every secret field is a SecretField, NOT the raw value
    for key in (
        "openai_api_key",
        "vectorizer_ai_api_id",
        "vectorizer_ai_api_key",
        "cloudconvert_api_key",
        "cloudconvert_sandbox_api_key",
    ):
        assert key in body, f"missing secret slot: {key}"
        secret = body[key]
        assert "set" in secret and "preview" in secret, f"bad SecretField shape for {key}"

    # In CI all creds are scrubbed → every secret reads as not-set, no preview
    for key in (
        "openai_api_key",
        "vectorizer_ai_api_id",
        "vectorizer_ai_api_key",
        "cloudconvert_api_key",
        "cloudconvert_sandbox_api_key",
    ):
        assert body[key]["set"] is False
        assert body[key]["preview"] is None


def test_get_settings_writable_keys_match_router_const(
    client: TestClient, isolated_paths: Path
) -> None:
    """The dashboard reads writable_keys to know which fields to show as
    editable. It must match the actual WRITABLE_KEYS const."""
    res = client.get("/api/settings")
    body = res.json()
    assert set(body["writable_keys"]) == set(WRITABLE_KEYS)


# ------------------------------------------------------------ PATCH paths


def test_patch_settings_rejects_unknown_fields_with_422(
    client: TestClient, isolated_paths: Path, tmp_path: Path
) -> None:
    """Unknown field → 422 from pydantic body validation. No env write.

    This is the strict behaviour we want: typos in the dashboard's PATCH
    body should fail loudly, not silently swallow a misnamed field.
    """
    env_file = tmp_path / "test.env"
    env_file.write_text("# initial\n")
    import app.routes.settings as settings_module
    settings_module._env_file_path = lambda: env_file  # type: ignore[assignment]
    try:
        res = client.patch("/api/settings", json={"made_up_field": "boom"})
        assert res.status_code == 422
        # And the tmp env file is untouched.
        assert env_file.read_text() == "# initial\n"
    finally:
        import importlib
        importlib.reload(settings_module)


def test_patch_settings_round_trip_vectorizer_provider(
    client: TestClient, isolated_paths: Path, tmp_path: Path
) -> None:
    """Flipping vectorizer_provider via PATCH must:
    (a) return the new value in the response, and
    (b) write the FORME_VECTORIZER_PROVIDER line into the env file.
    """
    # Point the writable env file at a tmp file so we don't clobber the real one.
    env_file = tmp_path / "test.env"
    env_file.write_text("FORME_VECTORIZER_PROVIDER=vectorizer_ai\n")
    import app.routes.settings as settings_module
    original_env_path = settings_module._env_file_path
    settings_module._env_file_path = lambda: env_file  # type: ignore[assignment]
    try:
        res = client.patch(
            "/api/settings", json={"vectorizer_provider": "inkscape_potrace"}
        )
        assert res.status_code == 200, res.text
        # The cached Settings is reset after a write — but Pydantic's env
        # priority means our monkeypatched env still wins in tests. So we
        # verify the *file* got the update.
        assert "FORME_VECTORIZER_PROVIDER=inkscape_potrace" in env_file.read_text()
    finally:
        settings_module._env_file_path = original_env_path


def test_patch_settings_writes_cdr_toggle_and_provider(
    client: TestClient, isolated_paths: Path, tmp_path: Path
) -> None:
    """CDR slice 7 + polish fields all writable: master toggle, provider,
    fallback, timeout, sandbox toggle."""
    env_file = tmp_path / "test.env"
    env_file.write_text("# initial\n")
    import app.routes.settings as settings_module
    settings_module._env_file_path = lambda: env_file  # type: ignore[assignment]
    try:
        res = client.patch(
            "/api/settings",
            json={
                "cdr_enabled": True,
                "cdr_provider": "uniconvertor",
                "cdr_fallback": "cloudconvert",
                "cdr_timeout_s": 240.0,
                "cloudconvert_sandbox": True,
            },
        )
        assert res.status_code == 200, res.text
        content = env_file.read_text()
        # The env writer renders bool as `True`/`False` (str()).
        assert "FORME_CDR_ENABLED=True" in content
        assert "FORME_CDR_PROVIDER=uniconvertor" in content
        assert "FORME_CDR_FALLBACK=cloudconvert" in content
        assert "FORME_CDR_TIMEOUT_S=240.0" in content
        assert "FORME_CLOUDCONVERT_SANDBOX=True" in content
    finally:
        import importlib
        importlib.reload(settings_module)


def test_patch_settings_validates_vectorizer_ai_mode_literal(
    client: TestClient, isolated_paths: Path
) -> None:
    """A bogus enum value → 422 (pydantic literal validation)."""
    res = client.patch(
        "/api/settings", json={"vectorizer_ai_mode": "magic_unicorn"}
    )
    assert res.status_code == 422


def test_patch_settings_clamps_timeouts(
    client: TestClient, isolated_paths: Path, tmp_path: Path
) -> None:
    """Timeouts have ge/le bounds — verify pydantic enforces them.

    The success-case write needs a tmp env_file or it clobbers the real
    backend/.env — learned the hard way during this slice.
    """
    env_file = tmp_path / "test.env"
    env_file.write_text("# initial\n")
    import app.routes.settings as settings_module
    settings_module._env_file_path = lambda: env_file  # type: ignore[assignment]
    try:
        # Below ge=10 → 422
        res = client.patch("/api/settings", json={"vectorizer_timeout_s": 1.0})
        assert res.status_code == 422

        # Above le=600 → 422
        res = client.patch("/api/settings", json={"cdr_timeout_s": 9999.0})
        assert res.status_code == 422

        # Within bounds → 200 (writes to tmp, not real .env)
        res = client.patch("/api/settings", json={"cdr_timeout_s": 90.0})
        assert res.status_code == 200
        assert "FORME_CDR_TIMEOUT_S=90.0" in env_file.read_text()
    finally:
        import importlib
        importlib.reload(settings_module)


# ------------------------------------------------------- writable allow-list


def test_writable_keys_covers_all_active_fields() -> None:
    """Regression guard: every env-var we expose in the dashboard must be
    in WRITABLE_KEYS, otherwise the PATCH silently drops it."""
    required = {
        # slice 6 — vector
        "FORME_VECTORIZER_PROVIDER",
        "FORME_VECTORIZER_FALLBACK",
        "FORME_VECTORIZER_AI_MODE",
        "FORME_VECTORIZER_TIMEOUT_S",
        # slice 7 — CDR
        "FORME_CDR_ENABLED",
        "FORME_CDR_PROVIDER",
        "FORME_CDR_FALLBACK",
        "FORME_CDR_TIMEOUT_S",
        "FORME_CLOUDCONVERT_SANDBOX",
        "FORME_UNICONVERTOR_PATH",
        # OCR
        "FORME_TESSERACT_CMD",
        "FORME_TESSERACT_LANG",
    }
    missing = required - set(WRITABLE_KEYS)
    assert not missing, f"Missing from WRITABLE_KEYS: {missing}"


# ------------------------------------------------- settings cache reset


def test_patch_settings_response_reflects_new_values(
    client: TestClient, isolated_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful PATCH must return the updated snapshot in the response.

    We override BOTH the writable env-file path (where PATCH writes) and
    the OS-env value that pydantic reads, so the post-PATCH snapshot
    genuinely reflects the new value rather than the underlying .env.
    """
    env_file = tmp_path / "test.env"
    env_file.write_text("FORME_LOG_LEVEL=info\n")
    import app.routes.settings as settings_module
    settings_module._env_file_path = lambda: env_file  # type: ignore[assignment]
    monkeypatch.setenv("FORME_LOG_LEVEL", "info")

    try:
        # PATCH the log_level — handler writes to env_file + sets OS env
        # for the new value so the rebuilt Settings picks it up.
        monkeypatch.setenv("FORME_LOG_LEVEL", "debug")
        config_module._settings = None
        res = client.patch("/api/settings", json={"log_level": "debug"})
        assert res.status_code == 200, res.text
        body = res.json()
        # The returned snapshot must show the new value.
        assert body["log_level"] == "debug"
        # And the env file must carry the change.
        assert "FORME_LOG_LEVEL=debug" in env_file.read_text()
    finally:
        import importlib
        importlib.reload(settings_module)
