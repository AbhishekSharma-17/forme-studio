"""Tests for the CDR export endpoint + service.

Both providers are mocked so the tests are hermetic:
* ``cloudconvert`` — ``httpx.AsyncClient`` is monkeypatched. The full
  job → upload → poll → download chain is replayed via a stateful stub.
* ``uniconvertor`` — ``asyncio.create_subprocess_exec`` writes a stub
  ``.cdr`` to the expected output path and returns rc=0.

Also tests the upstream vector step is mocked separately — the CDR
endpoint orchestrates PNG → SVG → CDR, so both stages must be
intercepted.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.services.vector import VectorResult
from tests.stubs import StubOpenAIClient

SAMPLE_SVG = (
    b'<?xml version="1.0"?>'
    b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
    b'<rect width="100" height="100" fill="black"/></svg>'
)
SAMPLE_CDR = b"\x52\x49\x46\x46" + b"\x00\x00" + b"CDR8" * 32  # "RIFF" + dummy CDR body


# ----------------------------------------------------------------- helpers


def _create_workspace(client: TestClient) -> dict[str, object]:
    res = client.post(
        "/api/packaging/workspaces",
        json={"name": "CDR Test", "product_type": "lotion_bottle_label"},
    )
    assert res.status_code == 201, res.text
    return res.json()


def _generate(client: TestClient, slug: str) -> int:
    res = client.post(
        f"/api/packaging/workspaces/{slug}/generate",
        json={"prompt": "CDR source variant", "n": 1, "quality": "high"},
    )
    assert res.status_code == 200
    return int(res.json()["assets"][0]["id"])


async def _fake_vectorize(_png: bytes, *, provider: str | None = None) -> VectorResult:
    """Bypass the real vector dispatcher — the CDR tests aren't about that."""
    return VectorResult(
        svg_bytes=SAMPLE_SVG,
        provider=(provider or "vectorizer_ai"),  # type: ignore[arg-type]
        mode="test" if provider != "inkscape_potrace" else None,
        size_bytes=len(SAMPLE_SVG),
    )


def _patch_vectorize(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the route's vectorize import with our fake."""
    monkeypatch.setattr(
        "app.modules.packaging.routes.run_vectorize", _fake_vectorize
    )


def _enable_cdr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flip the master CDR toggle on (off by default in production).

    Almost every CDR test needs this — call it after _patch_vectorize so
    the route can be exercised. Forgetting it produces a 503 with the
    "CDR exports are disabled" message.
    """
    monkeypatch.setenv("FORME_CDR_ENABLED", "true")
    import app.config as config_module
    config_module._settings = None


# --------------------------------------------------------- UniConvertor path


class _StubProcess:
    def __init__(self, returncode: int = 0, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", self._stderr


def _patch_uniconvertor_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    *,
    returncode: int = 0,
    stderr: bytes = b"",
    write_cdr: bool = True,
    cdr_payload: bytes = SAMPLE_CDR,
) -> list[list[str]]:
    """Mock create_subprocess_exec; capture the argv list."""
    calls: list[list[str]] = []

    async def _fake_subprocess(
        *cmd: str, stdout: Any = None, stderr_arg: Any = None, **_: Any
    ) -> _StubProcess:
        calls.append(list(cmd))
        if write_cdr:
            # The service shells out with: [binary, input.svg, output.cdr].
            # Write a stub CDR to the third arg (the output path).
            out = Path(cmd[2])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(cdr_payload)
        return _StubProcess(returncode=returncode, stderr=stderr)

    monkeypatch.setattr(
        "app.services.export_cdr.asyncio.create_subprocess_exec",
        _fake_subprocess,
    )
    # Pretend the UniConvertor binary exists.
    monkeypatch.setattr(
        "app.services.export_cdr.Path.is_file",
        lambda _self: True,
    )
    return calls


def test_cdr_export_via_uniconvertor_writes_cdr_asset(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_cdr(monkeypatch)
    _patch_vectorize(monkeypatch)
    calls = _patch_uniconvertor_subprocess(monkeypatch)

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/cdr",
        json={"source_asset_id": src_id, "cdr_provider": "uniconvertor"},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["cdr_provider"] == "uniconvertor"
    assert body["vector_provider"] == "vectorizer_ai"
    assert body["asset"]["mime_type"] == "application/x-cdr"
    assert body["asset"]["kind"] == "export"
    assert body["cdr_size_bytes"] == len(SAMPLE_CDR)
    assert body["svg_size_bytes"] == len(SAMPLE_SVG)

    cdr_path = (
        isolated_paths / "workspaces" / ws["slug"] / body["asset"]["relative_path"]
    )
    assert cdr_path.is_file()
    assert cdr_path.read_bytes() == SAMPLE_CDR

    # Shell command shape: [binary, <input>, <output>]
    assert len(calls) == 1
    assert calls[0][1].endswith(".svg")
    assert calls[0][2].endswith(".cdr")

    audit = (isolated_paths / "workspaces" / ws["slug"] / "audit.log.jsonl").read_text()
    events = [json.loads(line)["event"] for line in audit.strip().splitlines()]
    assert "export.cdr.created" in events


def test_cdr_export_502_when_uniconvertor_fails(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_cdr(monkeypatch)
    _patch_vectorize(monkeypatch)
    _patch_uniconvertor_subprocess(
        monkeypatch,
        returncode=1,
        stderr=b"libcdr import failure",
        write_cdr=False,
    )

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/cdr",
        json={"source_asset_id": src_id, "cdr_provider": "uniconvertor"},
    )
    assert res.status_code == 502
    detail = res.json()["detail"]
    assert "UniConvertor exited 1" in detail
    assert "libcdr import failure" in detail


def test_cdr_export_503_when_uniconvertor_binary_missing(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No is_file patch → the default conftest 'no-inkscape' path applies and
    the binary check fails before we even reach the subprocess call."""
    _enable_cdr(monkeypatch)
    _patch_vectorize(monkeypatch)
    # Default conftest sets FORME_INKSCAPE_PATH but uniconvertor_path uses
    # the production default which won't exist in tmp. So just leave it.

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/cdr",
        json={"source_asset_id": src_id, "cdr_provider": "uniconvertor"},
    )
    assert res.status_code == 503
    assert "UniConvertor CLI not found" in res.json()["detail"]


# --------------------------------------------------------- CloudConvert path


class _StubCCResponse:
    def __init__(
        self, status_code: int = 200, json_body: Any = None, content: bytes = b""
    ) -> None:
        self.status_code = status_code
        self._json = json_body or {}
        self.content = content
        self.text = json.dumps(self._json) if self._json else ""

    def json(self) -> Any:
        return self._json


class _StubCCClient:
    """Stateful CloudConvert mock — replays job → upload → poll → download."""

    def __init__(self, scenario: str = "happy") -> None:
        self.scenario = scenario
        self.poll_count = 0
        # Track which calls happened for assertions.
        self.calls: list[tuple[str, str]] = []

    async def __aenter__(self) -> _StubCCClient:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> _StubCCResponse:
        self.calls.append(("POST", url))
        if url.endswith("/v2/jobs"):
            # Create-job response.
            if self.scenario == "create-job-error":
                return _StubCCResponse(
                    status_code=401,
                    json_body={"message": "Unauthenticated"},
                )
            return _StubCCResponse(
                status_code=200,
                json_body={
                    "data": {
                        "id": "job-abc-123",
                        "tasks": [
                            {
                                "name": "import-svg",
                                "result": {
                                    "form": {
                                        "url": "https://storage.cloudconvert.test/upload-here",
                                        "parameters": {"signature": "stub"},
                                    }
                                },
                            },
                            {"name": "convert-cdr"},
                            {"name": "export-cdr"},
                        ],
                    }
                },
            )
        if "upload-here" in url:
            return _StubCCResponse(status_code=200)
        raise AssertionError(f"unexpected POST {url}")

    async def get(self, url: str, **kwargs: Any) -> _StubCCResponse:
        self.calls.append(("GET", url))
        if "/v2/jobs/" in url:
            # Poll-job response.
            self.poll_count += 1
            if self.scenario == "task-error":
                return _StubCCResponse(
                    status_code=200,
                    json_body={
                        "data": {
                            "id": "job-abc-123",
                            "tasks": [
                                {
                                    "name": "convert-cdr",
                                    "status": "error",
                                    "message": "Invalid SVG payload",
                                }
                            ],
                        }
                    },
                )
            if self.poll_count < 2:
                # First poll = still running.
                return _StubCCResponse(
                    status_code=200,
                    json_body={
                        "data": {
                            "id": "job-abc-123",
                            "tasks": [
                                {"name": "export-cdr", "status": "processing"}
                            ],
                        }
                    },
                )
            # Second poll = finished.
            return _StubCCResponse(
                status_code=200,
                json_body={
                    "data": {
                        "id": "job-abc-123",
                        "tasks": [
                            {
                                "name": "export-cdr",
                                "status": "finished",
                                "result": {
                                    "files": [
                                        {
                                            "filename": "output.cdr",
                                            "url": "https://storage.cloudconvert.test/dl/output.cdr",
                                        }
                                    ]
                                },
                            }
                        ],
                    }
                },
            )
        if "/dl/output.cdr" in url:
            return _StubCCResponse(status_code=200, content=SAMPLE_CDR)
        raise AssertionError(f"unexpected GET {url}")


def _patch_cloudconvert(monkeypatch: pytest.MonkeyPatch, scenario: str = "happy") -> None:
    monkeypatch.setattr(
        "app.services.export_cdr.httpx.AsyncClient",
        lambda *a, **kw: _StubCCClient(scenario=scenario),
    )
    # Speed up the poll-loop in tests so we don't actually wait 1.5s.
    monkeypatch.setattr("app.services.export_cdr.asyncio.sleep", _no_sleep)


async def _no_sleep(_seconds: float) -> None:
    return None


def test_cdr_export_via_cloudconvert_writes_cdr_asset(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLOUDCONVERT_API_KEY", "stub-cc-token")
    _enable_cdr(monkeypatch)

    _patch_vectorize(monkeypatch)
    _patch_cloudconvert(monkeypatch, scenario="happy")

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/cdr",
        json={"source_asset_id": src_id, "cdr_provider": "cloudconvert"},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["cdr_provider"] == "cloudconvert"
    assert body["vector_provider"] == "vectorizer_ai"

    cdr_path = (
        isolated_paths / "workspaces" / ws["slug"] / body["asset"]["relative_path"]
    )
    assert cdr_path.read_bytes() == SAMPLE_CDR


def test_cdr_export_503_when_cloudconvert_key_missing(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The error message must reference whichever key (live/sandbox) is
    missing for the current toggle — sandbox vs live are separate slots."""
    _enable_cdr(monkeypatch)
    _patch_vectorize(monkeypatch)
    # Default: sandbox toggle off → expects live key, which conftest clears.

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/cdr",
        json={"source_asset_id": src_id, "cdr_provider": "cloudconvert"},
    )
    assert res.status_code == 503
    detail = res.json()["detail"]
    assert "live API key" in detail
    assert "CLOUDCONVERT_API_KEY" in detail
    assert "uniconvertor" in detail

    # Now flip the sandbox toggle on, leave both keys empty → the error
    # should now name the sandbox key.
    monkeypatch.setenv("FORME_CLOUDCONVERT_SANDBOX", "true")
    import app.config as config_module
    config_module._settings = None

    res2 = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/cdr",
        json={"source_asset_id": src_id, "cdr_provider": "cloudconvert"},
    )
    assert res2.status_code == 503
    detail2 = res2.json()["detail"]
    assert "sandbox API key" in detail2
    assert "CLOUDCONVERT_SANDBOX_API_KEY" in detail2


def test_cdr_export_502_when_cloudconvert_task_errors(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `status=error` task during polling surfaces as 502 with the message."""
    monkeypatch.setenv("CLOUDCONVERT_API_KEY", "stub-cc-token")
    _enable_cdr(monkeypatch)

    _patch_vectorize(monkeypatch)
    _patch_cloudconvert(monkeypatch, scenario="task-error")

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/cdr",
        json={"source_asset_id": src_id, "cdr_provider": "cloudconvert"},
    )
    assert res.status_code == 502
    detail = res.json()["detail"]
    assert "convert-cdr" in detail
    assert "Invalid SVG payload" in detail


def test_cdr_export_502_when_cloudconvert_create_job_fails(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An auth failure on create-job surfaces with the upstream message."""
    monkeypatch.setenv("CLOUDCONVERT_API_KEY", "bad-token")
    _enable_cdr(monkeypatch)

    _patch_vectorize(monkeypatch)
    _patch_cloudconvert(monkeypatch, scenario="create-job-error")

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/cdr",
        json={"source_asset_id": src_id, "cdr_provider": "cloudconvert"},
    )
    assert res.status_code == 502
    detail = res.json()["detail"]
    assert "401" in detail
    assert "Unauthenticated" in detail


# ----------------------------------------------------- validation paths


def test_cdr_export_rejects_unknown_source(
    client: TestClient, fake_openai: StubOpenAIClient
) -> None:
    ws = _create_workspace(client)
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/cdr",
        json={"source_asset_id": 99999},
    )
    assert res.status_code == 422
    assert "does not belong" in res.json()["detail"]


def test_cdr_export_rejects_reference_kind(
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
        f"/api/packaging/workspaces/{ws['slug']}/exports/cdr",
        json={"source_asset_id": ref["id"]},
    )
    assert res.status_code == 422
    assert "Can only export 'generation'" in res.json()["detail"]


def test_cdr_export_503_when_master_toggle_off(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default FORME_CDR_ENABLED=false → 503 before any provider work.

    Even with a working CloudConvert key and the source asset valid, the
    master toggle gates the entire feature. The conftest leaves it off
    so this test only needs to NOT call _enable_cdr.
    """
    monkeypatch.setenv("CLOUDCONVERT_API_KEY", "stub-cc-token")
    import app.config as config_module
    config_module._settings = None

    _patch_vectorize(monkeypatch)
    # No CDR enable, no provider patch — should short-circuit at the toggle.

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/cdr",
        json={"source_asset_id": src_id},
    )
    assert res.status_code == 503
    detail = res.json()["detail"]
    assert "CDR exports are disabled" in detail
    assert "FORME_CDR_ENABLED" in detail


def test_cdr_export_uses_sandbox_host_when_toggled(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FORME_CLOUDCONVERT_SANDBOX=true → requests hit the sandbox host
    AND authenticate with the sandbox key (not the live one)."""
    # Set BOTH keys with distinct values, then flip to sandbox — the
    # service must reach for the sandbox-named one.
    monkeypatch.setenv("CLOUDCONVERT_API_KEY", "live-token-should-not-be-used")
    monkeypatch.setenv("CLOUDCONVERT_SANDBOX_API_KEY", "stub-sandbox-token")
    monkeypatch.setenv("FORME_CLOUDCONVERT_SANDBOX", "true")
    _enable_cdr(monkeypatch)

    _patch_vectorize(monkeypatch)

    captured: list[tuple[str, str]] = []

    class _RecordingClient(_StubCCClient):
        async def post(self, url: str, **kwargs: Any) -> _StubCCResponse:
            captured.append(("POST", url))
            return await super().post(url, **kwargs)

        async def get(self, url: str, **kwargs: Any) -> _StubCCResponse:
            captured.append(("GET", url))
            return await super().get(url, **kwargs)

    monkeypatch.setattr(
        "app.services.export_cdr.httpx.AsyncClient",
        lambda *a, **kw: _RecordingClient(scenario="happy"),
    )
    monkeypatch.setattr("app.services.export_cdr.asyncio.sleep", _no_sleep)

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/cdr",
        json={"source_asset_id": src_id, "cdr_provider": "cloudconvert"},
    )
    assert res.status_code == 201, res.text

    # At least one call must have gone to the sandbox host; none should
    # have hit the production host.
    sandbox_hits = [c for c in captured if "sandbox.cloudconvert.com" in c[1]]
    prod_hits = [
        c for c in captured if "api.cloudconvert.com" in c[1] and "sandbox" not in c[1]
    ]
    assert sandbox_hits, captured
    assert not prod_hits, captured


def test_cloudconvert_active_key_property_picks_correctly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Settings.cloudconvert_active_key property tracks the toggle.

    This is a unit test on config — no FastAPI client needed. Exists to
    catch the easy mistake of reading the wrong key field directly.
    """
    monkeypatch.setenv("FORME_WORKSPACES_DIR", "/tmp")
    monkeypatch.setenv("FORME_DB_PATH", "/tmp/forme.db")
    monkeypatch.setenv("CLOUDCONVERT_API_KEY", "live-XYZ")
    monkeypatch.setenv("CLOUDCONVERT_SANDBOX_API_KEY", "sandbox-ABC")

    import app.config as config_module

    # Sandbox off → expects the live key
    monkeypatch.setenv("FORME_CLOUDCONVERT_SANDBOX", "false")
    config_module._settings = None
    assert config_module.get_settings().cloudconvert_active_key == "live-XYZ"

    # Flip sandbox on → expects the sandbox key
    monkeypatch.setenv("FORME_CLOUDCONVERT_SANDBOX", "true")
    config_module._settings = None
    assert config_module.get_settings().cloudconvert_active_key == "sandbox-ABC"

    # Sandbox on but no sandbox key set → None (does NOT fall back to live)
    monkeypatch.setenv("CLOUDCONVERT_SANDBOX_API_KEY", "")
    config_module._settings = None
    assert config_module.get_settings().cloudconvert_active_key is None


def test_cdr_export_400_on_unknown_provider(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_cdr(monkeypatch)
    _patch_vectorize(monkeypatch)
    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/cdr",
        json={"source_asset_id": src_id, "cdr_provider": "magic_unicorn"},
    )
    assert res.status_code == 400
    assert "Unknown CDR provider" in res.json()["detail"]


def test_cdr_export_user_picks_fallback_explicitly(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-auto fallback: uniconvertor 502 → user retries with cloudconvert."""
    _enable_cdr(monkeypatch)
    _patch_vectorize(monkeypatch)

    # 1. First call: uniconvertor fails.
    _patch_uniconvertor_subprocess(
        monkeypatch, returncode=1, stderr=b"crashed", write_cdr=False
    )
    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])
    first = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/cdr",
        json={"source_asset_id": src_id, "cdr_provider": "uniconvertor"},
    )
    assert first.status_code == 502

    # 2. Second call: user picks the fallback.
    monkeypatch.setenv("CLOUDCONVERT_API_KEY", "stub-cc-token")
    import app.config as config_module
    config_module._settings = None
    _patch_cloudconvert(monkeypatch, scenario="happy")

    second = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/cdr",
        json={"source_asset_id": src_id, "cdr_provider": "cloudconvert"},
    )
    assert second.status_code == 201, second.text
    assert second.json()["cdr_provider"] == "cloudconvert"
