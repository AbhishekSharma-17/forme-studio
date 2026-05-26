"""Tests for the vector (SVG) export endpoint + service.

Both providers are mocked so the tests are hermetic:
* ``vectorizer_ai`` — ``httpx.AsyncClient.post`` is monkeypatched to return
  a canned 200 with SVG bytes (or a non-200 to exercise the error path).
* ``inkscape_potrace`` — ``asyncio.create_subprocess_exec`` is patched
  to write a stub SVG to the expected output path and return a 0 exit code.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.stubs import StubOpenAIClient

# A real, parseable SVG body so the response validator accepts it.
SAMPLE_SVG = (
    b'<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
    b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
    b'<rect width="100" height="100" fill="red"/></svg>'
)


# ----------------------------------------------------------------- helpers


def _create_workspace(client: TestClient) -> dict[str, object]:
    res = client.post(
        "/api/packaging/workspaces",
        json={"name": "Vector Test", "product_type": "lotion_bottle_label"},
    )
    assert res.status_code == 201, res.text
    return res.json()


def _generate(client: TestClient, slug: str) -> int:
    res = client.post(
        f"/api/packaging/workspaces/{slug}/generate",
        json={"prompt": "Vector source variant", "n": 1, "quality": "high"},
    )
    assert res.status_code == 200, res.text
    return int(res.json()["assets"][0]["id"])


class _StubResponse:
    """Minimal duck-typed httpx.Response."""

    def __init__(
        self,
        status_code: int = 200,
        content: bytes = SAMPLE_SVG,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self.content = content
        self.text = text or (content.decode("utf-8", errors="replace") if content else "")


class _StubAsyncClient:
    """A minimal stand-in for ``httpx.AsyncClient`` used by the service."""

    def __init__(self, response_factory: Any = None, **_: Any) -> None:
        self._make_response = response_factory or (lambda **_: _StubResponse())
        self.post_calls: list[dict[str, Any]] = []

    async def __aenter__(self) -> _StubAsyncClient:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> _StubResponse:
        self.post_calls.append({"url": url, **kwargs})
        return self._make_response(url=url, **kwargs)


def _patch_httpx(
    monkeypatch: pytest.MonkeyPatch,
    response_factory: Any = None,
) -> list[dict[str, Any]]:
    """Install the stub ``httpx.AsyncClient`` and return its call log."""
    calls: list[dict[str, Any]] = []

    def _client_factory(*_a: Any, **kw: Any) -> _StubAsyncClient:
        stub = _StubAsyncClient(response_factory=response_factory, **kw)
        # Share the inner list so callers can inspect after the request.
        stub.post_calls = calls
        return stub

    monkeypatch.setattr("app.services.vector.httpx.AsyncClient", _client_factory)
    return calls


# ----------------------------------------------------- vectorizer.ai path


def test_vector_export_via_vectorizer_ai_writes_svg_asset(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: creds present → POST to vectorizer.ai → SVG persists."""
    monkeypatch.setenv("VECTORIZER_AI_API_ID", "stub-id")
    monkeypatch.setenv("VECTORIZER_AI_API_KEY", "stub-secret")
    monkeypatch.setenv("FORME_VECTORIZER_AI_MODE", "test")
    import app.config as config_module
    config_module._settings = None

    calls = _patch_httpx(monkeypatch)

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/vector",
        json={"source_asset_id": src_id},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["provider"] == "vectorizer_ai"
    assert body["mode"] == "test"
    assert body["asset"]["mime_type"] == "image/svg+xml"
    assert body["asset"]["kind"] == "export"

    # The SVG should have landed on disk inside the workspace exports dir.
    svg_path = (
        isolated_paths / "workspaces" / ws["slug"] / body["asset"]["relative_path"]
    )
    assert svg_path.is_file()
    assert svg_path.read_bytes() == SAMPLE_SVG
    assert body["size_bytes"] == len(SAMPLE_SVG)

    # The service must have called vectorizer.ai with HTTP-Basic auth + mode.
    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == "https://vectorizer.ai/api/v1/vectorize"
    assert call["data"]["mode"] == "test"
    assert call["data"]["output.file_format"] == "svg"
    assert call["auth"] == ("stub-id", "stub-secret")

    audit = (isolated_paths / "workspaces" / ws["slug"] / "audit.log.jsonl").read_text()
    events = [json.loads(line)["event"] for line in audit.strip().splitlines()]
    assert "export.vector.created" in events


def test_vector_export_returns_502_when_vectorizer_returns_error(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-200 from vectorizer.ai surfaces as 502 with the upstream detail."""
    monkeypatch.setenv("VECTORIZER_AI_API_ID", "stub-id")
    monkeypatch.setenv("VECTORIZER_AI_API_KEY", "stub-secret")
    import app.config as config_module
    config_module._settings = None

    def _err_factory(**_: Any) -> _StubResponse:
        return _StubResponse(
            status_code=429,
            content=b'{"error":"quota_exceeded"}',
            text='{"error":"quota_exceeded"}',
        )

    _patch_httpx(monkeypatch, response_factory=_err_factory)

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/vector",
        json={"source_asset_id": src_id},
    )
    assert res.status_code == 502, res.text
    detail = res.json()["detail"]
    assert "Vectorizer.AI" in detail
    assert "429" in detail
    assert "quota_exceeded" in detail


def test_vector_export_returns_502_when_response_is_not_svg(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """200 but garbage body must not produce a broken SVG export."""
    monkeypatch.setenv("VECTORIZER_AI_API_ID", "stub-id")
    monkeypatch.setenv("VECTORIZER_AI_API_KEY", "stub-secret")
    import app.config as config_module
    config_module._settings = None

    def _bogus_factory(**_: Any) -> _StubResponse:
        return _StubResponse(content=b"not an svg")

    _patch_httpx(monkeypatch, response_factory=_bogus_factory)

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/vector",
        json={"source_asset_id": src_id},
    )
    assert res.status_code == 502
    assert "empty / non-SVG" in res.json()["detail"]


def test_vector_export_returns_503_when_vectorizer_credentials_missing(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No API ID/key → 503 with a clear, actionable message."""
    monkeypatch.setenv("VECTORIZER_AI_API_ID", "")
    monkeypatch.setenv("VECTORIZER_AI_API_KEY", "")
    import app.config as config_module
    config_module._settings = None

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/vector",
        json={"source_asset_id": src_id},
    )
    assert res.status_code == 503
    detail = res.json()["detail"]
    assert "Vectorizer.AI credentials" in detail
    assert "inkscape_potrace" in detail  # caller should see the alternative


def test_vector_export_504_on_vectorizer_ai_timeout(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """httpx.TimeoutException is surfaced as 504, not 502."""
    monkeypatch.setenv("VECTORIZER_AI_API_ID", "stub-id")
    monkeypatch.setenv("VECTORIZER_AI_API_KEY", "stub-secret")
    import app.config as config_module
    config_module._settings = None

    class _TimingOutClient(_StubAsyncClient):
        async def post(self, *_a: Any, **_kw: Any) -> _StubResponse:
            raise httpx.TimeoutException("simulated timeout")

    monkeypatch.setattr(
        "app.services.vector.httpx.AsyncClient",
        lambda *a, **kw: _TimingOutClient(**kw),
    )

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/vector",
        json={"source_asset_id": src_id},
    )
    assert res.status_code == 504
    assert "timed out" in res.json()["detail"]


# ----------------------------------------------------- inkscape path


class _StubProcess:
    """Stand-in for ``asyncio.subprocess.Process``."""

    def __init__(self, returncode: int = 0, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", self._stderr


def _patch_inkscape_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    *,
    returncode: int = 0,
    stderr: bytes = b"",
    write_svg: bool = True,
    svg_payload: bytes = SAMPLE_SVG,
) -> list[list[str]]:
    """Patch the subprocess + inkscape binary check used by ``vector.py``.

    Returns a list that captures every argv the service shells out with.
    """
    calls: list[list[str]] = []

    async def _fake_subprocess(
        *cmd: str, stdout: Any = None, stderr_arg: Any = None, **_: Any
    ) -> _StubProcess:
        calls.append(list(cmd))
        if write_svg:
            # The service builds the output filename and embeds it inside the
            # --actions argument as 'export-filename:<path>;'. Pull it back.
            actions = cmd[cmd.index("--actions") + 1]
            for part in actions.split(";"):
                if part.startswith("export-filename:"):
                    out = Path(part.split(":", 1)[1])
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(svg_payload)
                    break
        return _StubProcess(returncode=returncode, stderr=stderr)

    monkeypatch.setattr(
        "app.services.vector.asyncio.create_subprocess_exec",
        _fake_subprocess,
    )
    # Make ``Path(inkscape_path).is_file()`` true without needing a real
    # binary on disk.
    monkeypatch.setattr(
        "app.services.vector.Path.is_file",
        lambda _self: True,
    )
    return calls


def test_vector_export_via_inkscape_potrace_writes_svg_asset(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit ``provider=inkscape_potrace`` shells out to inkscape and
    persists the resulting SVG."""
    calls = _patch_inkscape_subprocess(monkeypatch)

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/vector",
        json={"source_asset_id": src_id, "provider": "inkscape_potrace"},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["provider"] == "inkscape_potrace"
    assert body["mode"] is None
    assert body["asset"]["mime_type"] == "image/svg+xml"

    # Audit + on-disk SVG
    svg_path = (
        isolated_paths / "workspaces" / ws["slug"] / body["asset"]["relative_path"]
    )
    assert svg_path.read_bytes() == SAMPLE_SVG

    # Shelled out with the expected --actions chain.
    assert len(calls) == 1
    cmd = calls[0]
    assert "--actions" in cmd
    actions = cmd[cmd.index("--actions") + 1]
    assert "select-all" in actions
    assert "trace-bitmap" in actions
    assert "export-filename:" in actions
    assert "export-do" in actions


def test_vector_export_502_when_inkscape_exits_nonzero(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_inkscape_subprocess(
        monkeypatch,
        returncode=1,
        stderr=b"trace-bitmap action unknown",
        write_svg=False,
    )

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/vector",
        json={"source_asset_id": src_id, "provider": "inkscape_potrace"},
    )
    assert res.status_code == 502
    detail = res.json()["detail"]
    assert "Inkscape Potrace exited 1" in detail
    assert "trace-bitmap action unknown" in detail


def test_vector_export_503_when_inkscape_binary_missing(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If FORME_INKSCAPE_PATH doesn't exist, error before shelling out."""
    # Leave the default fixture's no-inkscape path in place; just ask for it.
    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/vector",
        json={"source_asset_id": src_id, "provider": "inkscape_potrace"},
    )
    assert res.status_code == 503
    detail = res.json()["detail"]
    assert "Inkscape CLI not found" in detail


def test_vector_export_504_when_inkscape_times_out(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """asyncio.TimeoutError → 504 with the configured timeout in the detail."""

    async def _hang(*_a: Any, **_kw: Any) -> _StubProcess:
        # Force the wait_for in the service to time out.
        raise TimeoutError("simulated timeout")

    monkeypatch.setattr(
        "app.services.vector.asyncio.create_subprocess_exec", _hang
    )
    monkeypatch.setattr(
        "app.services.vector.Path.is_file",
        lambda _self: True,
    )

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/vector",
        json={"source_asset_id": src_id, "provider": "inkscape_potrace"},
    )
    assert res.status_code == 504
    assert "Inkscape Potrace timed out" in res.json()["detail"]


# ----------------------------------------------------- validation paths


def test_vector_export_rejects_unknown_source(
    client: TestClient, fake_openai: StubOpenAIClient
) -> None:
    ws = _create_workspace(client)
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/vector",
        json={"source_asset_id": 99999},
    )
    assert res.status_code == 422
    assert "does not belong" in res.json()["detail"]


def test_vector_export_rejects_reference_kind(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
) -> None:
    import io

    from PIL import Image

    ws = _create_workspace(client)
    buf = io.BytesIO()
    Image.new("RGB", (256, 256), (0, 0, 0)).save(buf, format="PNG")
    ref = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/references",
        files=[("files", ("logo.png", buf.getvalue(), "image/png"))],
    ).json()["references"][0]

    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/vector",
        json={"source_asset_id": ref["id"]},
    )
    assert res.status_code == 422
    assert "Can only export 'generation'" in res.json()["detail"]


def test_vector_export_400_on_unknown_provider(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
) -> None:
    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/vector",
        json={"source_asset_id": src_id, "provider": "magic_unicorn"},
    )
    assert res.status_code == 400
    assert "Unknown vector provider" in res.json()["detail"]


def test_vector_export_user_picks_fallback_explicitly(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-auto fallback: user retries with ``provider=inkscape_potrace``
    after vectorizer_ai failed → second call succeeds and produces SVG."""
    monkeypatch.setenv("VECTORIZER_AI_API_ID", "stub-id")
    monkeypatch.setenv("VECTORIZER_AI_API_KEY", "stub-secret")
    import app.config as config_module
    config_module._settings = None

    # 1) First call: vectorizer.ai errors out.
    def _err(**_: Any) -> _StubResponse:
        return _StubResponse(status_code=500, content=b"upstream blew up")

    _patch_httpx(monkeypatch, response_factory=_err)

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])
    first = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/vector",
        json={"source_asset_id": src_id},
    )
    assert first.status_code == 502

    # 2) Second call: the UI/user explicitly chose the fallback provider.
    _patch_inkscape_subprocess(monkeypatch)
    second = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/vector",
        json={"source_asset_id": src_id, "provider": "inkscape_potrace"},
    )
    assert second.status_code == 201, second.text
    assert second.json()["provider"] == "inkscape_potrace"


# Pin the asyncio fixture to a function loop (matches the other test files).
@pytest.fixture(autouse=False)
def _silence_event_loop_warning() -> None:  # pragma: no cover
    # Hook for future event-loop scoping if needed; currently a no-op.
    asyncio.get_event_loop_policy()
