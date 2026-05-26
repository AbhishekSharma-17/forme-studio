"""Tests for the Design Round flatten endpoint (slice 10e).

The endpoint converts an approved bottle-mockup generation into a clean
flat-label generation by calling gpt-image-2's edit API with a fixed
"strip the bottle context" prompt. We stub the OpenAI client so the
test runs hermetic.

Covers:
* happy path: design_mode=True workspace → flatten produces a new
  Asset(kind="generation") linked to its source via audit lineage
* design_mode=False workspace returns 409
* non-generation source asset returns 422
* OpenAI edit failure returns 502
* the flatten prompt actually carries the workspace trim dims
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image

from tests.stubs import StubOpenAIClient


def _transparent_png_b64() -> str:
    """A tiny RGBA PNG to stand in for gpt-image-2 output."""
    import base64

    img = Image.new("RGBA", (64, 64), (255, 255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _create_design_workspace(client: TestClient) -> dict[str, Any]:
    """Workspace with design_mode=True (brainstorm-on-product flow)."""
    res = client.post(
        "/api/packaging/workspaces",
        json={
            "name": "Brainstorm WS",
            "product_type": "lotion_bottle_label",
            "design_mode": True,
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


def _make_source_generation(client: TestClient, slug: str) -> int:
    """Generate one variant (uses stub openai) → returns asset_id."""
    res = client.post(
        f"/api/packaging/workspaces/{slug}/generate",
        json={"prompt": "Bottle mockup with label", "n": 1, "quality": "high"},
    )
    assert res.status_code == 200, res.text
    return int(res.json()["assets"][0]["id"])


def _patch_edit(
    fake_openai: StubOpenAIClient, captured: dict[str, Any]
) -> None:
    """Stub the openai client's images.edit to return a fixed image."""

    async def _edit(**kwargs: Any) -> Any:
        captured.update(kwargs)

        class _D:
            def __init__(self) -> None:
                self.b64_json = _transparent_png_b64()

        class _U:
            def model_dump(self) -> dict[str, Any]:
                return {"input_tokens": 80, "output_tokens": 80}

        class _R:
            def __init__(self) -> None:
                self.data = [_D()]
                self.usage = _U()

        return _R()

    fake_openai.images.edit = _edit  # type: ignore[method-assign]


# ──────────────────────────────────────────────────────────────────────
#  happy path
# ──────────────────────────────────────────────────────────────────────


def test_design_flatten_creates_new_generation_with_lineage(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
) -> None:
    """Approved mockup → flatten endpoint → new Asset(generation)."""
    captured: dict[str, Any] = {}
    _patch_edit(fake_openai, captured)

    ws = _create_design_workspace(client)
    mockup_id = _make_source_generation(client, ws["slug"])

    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/design/flatten",
        json={"source_asset_id": mockup_id},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["flattened_from"] == mockup_id
    assert body["asset"]["id"] != mockup_id
    assert body["asset"]["kind"] == "generation"
    assert body["provider_cost_usd"] > 0

    # The audit JSONL carries the lineage.
    audit_path = isolated_paths / "workspaces" / ws["slug"] / "audit.log.jsonl"
    events = [json.loads(line) for line in audit_path.read_text().splitlines()]
    flattened_events = [e for e in events if e["event"] == "asset.flattened"]
    assert len(flattened_events) == 1
    payload = flattened_events[0]["payload"]
    assert payload["flattened_from"] == mockup_id
    assert payload["new_asset_id"] == body["asset"]["id"]


def test_design_flatten_prompt_carries_workspace_trim_dims(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
) -> None:
    """The prompt sent to images.edit must include the trim mm.

    This is what tells gpt-image-2 to render the flat label at the right
    aspect; otherwise it might output a square thumbnail.
    """
    captured: dict[str, Any] = {}
    _patch_edit(fake_openai, captured)

    ws = _create_design_workspace(client)
    mockup_id = _make_source_generation(client, ws["slug"])

    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/design/flatten",
        json={"source_asset_id": mockup_id},
    )
    assert res.status_code == 201

    # Lotion preset is 70 × 100 mm — both dims must appear in the prompt.
    prompt = captured.get("prompt", "")
    assert "70" in prompt and "100" in prompt
    # And it must instruct the model to remove the bottle context.
    assert "flat" in prompt.lower()
    assert "bottle" in prompt.lower() or "product" in prompt.lower()


# ──────────────────────────────────────────────────────────────────────
#  error paths
# ──────────────────────────────────────────────────────────────────────


def test_design_flatten_rejects_non_design_mode_workspace(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
) -> None:
    """If design_mode=False, flatten makes no sense — return 409."""
    captured: dict[str, Any] = {}
    _patch_edit(fake_openai, captured)

    # Plain workspace (design_mode defaults False).
    res = client.post(
        "/api/packaging/workspaces",
        json={"name": "Plain WS", "product_type": "lotion_bottle_label"},
    )
    assert res.status_code == 201
    ws = res.json()
    mockup_id = _make_source_generation(client, ws["slug"])

    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/design/flatten",
        json={"source_asset_id": mockup_id},
    )
    assert res.status_code == 409
    assert "design_mode" in res.json()["detail"]


def test_design_flatten_rejects_reference_kind(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
) -> None:
    """Source asset must be a generation; references can't be flattened."""
    ws = _create_design_workspace(client)
    # Upload a reference image
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (10, 10, 10)).save(buf, format="PNG")
    ref = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/references",
        files=[("files", ("ref.png", buf.getvalue(), "image/png"))],
    ).json()["references"][0]

    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/design/flatten",
        json={"source_asset_id": ref["id"]},
    )
    assert res.status_code == 422
    assert "'generation'" in res.json()["detail"]


def test_design_flatten_surfaces_openai_failure_as_502(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
) -> None:
    """OpenAI edit raises → endpoint returns 502 with the upstream message."""

    async def _boom(**_kwargs: Any) -> Any:
        raise RuntimeError("upstream image-edit 500")

    fake_openai.images.edit = _boom  # type: ignore[method-assign]

    ws = _create_design_workspace(client)
    mockup_id = _make_source_generation(client, ws["slug"])

    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/design/flatten",
        json={"source_asset_id": mockup_id},
    )
    assert res.status_code == 502
    assert "Flatten edit failed" in res.json()["detail"]
