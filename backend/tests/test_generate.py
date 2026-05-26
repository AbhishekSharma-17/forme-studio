"""Tests for the workspace-scoped generate + assets routes.

We stub the OpenAI client (shared in ``tests/stubs.py``, wired by the
``fake_openai`` fixture in ``conftest.py``) so tests stay hermetic —
no network, no key required.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from tests.stubs import TINY_PNG_B64, StubOpenAIClient


def _create_workspace(client: TestClient, product_type: str = "lotion_bottle_label") -> dict[str, Any]:
    res = client.post(
        "/api/packaging/workspaces",
        json={"name": "Test SKU", "product_type": product_type},
    )
    assert res.status_code == 201, res.text
    return res.json()


def test_generate_requires_openai_key(client: TestClient) -> None:
    """Without an API key configured, hitting generate returns 503."""
    ws = _create_workspace(client)
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/generate",
        json={"prompt": "Test prompt", "n": 1, "quality": "high"},
    )
    assert res.status_code == 503
    assert "OPENAI_API_KEY" in res.json()["detail"]


def test_generate_saves_asset_file_and_audit(
    client: TestClient, isolated_paths: Path, fake_openai: StubOpenAIClient
) -> None:
    ws = _create_workspace(client)

    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/generate",
        json={"prompt": "A cosmetic lotion bottle label, minimal sage green", "n": 1, "quality": "high"},
    )
    assert res.status_code == 200, res.text
    body = res.json()

    # Response shape
    assert len(body["assets"]) == 1
    asset = body["assets"][0]
    assert asset["kind"] == "generation"
    assert asset["image_size"] == "1024x1536"  # lotion_bottle_label frozen size
    assert asset["variant_index"] == 0
    assert asset["url"].endswith(f"/assets/{asset['id']}/file")
    assert body["provider_cost_usd"] > 0
    assert body["markup_percent"] == 0.0

    # File landed in workspaces/<slug>/generations/
    gen_dir = isolated_paths / "workspaces" / ws["slug"] / "generations"
    pngs = list(gen_dir.glob("*.png"))
    assert len(pngs) == 1
    # And the bytes match what the stub returned
    assert pngs[0].read_bytes() == base64.b64decode(TINY_PNG_B64)

    # Audit row + JSONL mirror both got the event
    audit_jsonl = (
        isolated_paths / "workspaces" / ws["slug"] / "audit.log.jsonl"
    ).read_text()
    assert "asset.generated" in audit_jsonl


def test_generate_multi_variant(
    client: TestClient, isolated_paths: Path, fake_openai: StubOpenAIClient
) -> None:
    ws = _create_workspace(client)
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/generate",
        json={"prompt": "Test", "n": 3, "quality": "high"},
    )
    assert res.status_code == 200
    body = res.json()
    assert len(body["assets"]) == 3
    indices = sorted(a["variant_index"] for a in body["assets"])
    assert indices == [0, 1, 2]


def test_list_assets_returns_what_we_generated(
    client: TestClient, fake_openai: StubOpenAIClient
) -> None:
    ws = _create_workspace(client)
    client.post(
        f"/api/packaging/workspaces/{ws['slug']}/generate",
        json={"prompt": "Test", "n": 2, "quality": "high"},
    )
    res = client.get(f"/api/packaging/workspaces/{ws['slug']}/assets")
    assert res.status_code == 200
    assets = res.json()
    assert len(assets) == 2
    for a in assets:
        assert a["kind"] == "generation"


def test_serve_asset_file_returns_png(
    client: TestClient, fake_openai: StubOpenAIClient
) -> None:
    ws = _create_workspace(client)
    gen = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/generate",
        json={"prompt": "Test", "n": 1, "quality": "high"},
    ).json()
    asset_id = gen["assets"][0]["id"]
    res = client.get(f"/api/packaging/workspaces/{ws['slug']}/assets/{asset_id}/file")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/png"
    assert res.content == base64.b64decode(TINY_PNG_B64)


def test_serve_unknown_asset_404(client: TestClient, fake_openai: StubOpenAIClient) -> None:
    ws = _create_workspace(client)
    res = client.get(f"/api/packaging/workspaces/{ws['slug']}/assets/9999/file")
    assert res.status_code == 404


def test_generate_with_references_routes_through_edit(
    client: TestClient, isolated_paths: Path, fake_openai: StubOpenAIClient
) -> None:
    """When reference_asset_ids is non-empty, generation must call images.edit
    so gpt-image-2 can see the references — not plain images.generate."""
    import io

    from PIL import Image

    ws = client.post(
        "/api/packaging/workspaces",
        json={"name": "Refs Gen Test", "product_type": "lotion_bottle_label"},
    ).json()

    # Upload one reference
    buf = io.BytesIO()
    Image.new("RGB", (256, 256), (40, 90, 60)).save(buf, format="PNG")
    ref_id = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/references",
        files=[("files", ("logo.png", buf.getvalue(), "image/png"))],
    ).json()["references"][0]["id"]

    fake_openai.images.edit_calls.clear()
    fake_openai.images.generate_calls.clear()

    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/generate",
        json={
            "prompt": "Lotion bottle in this style",
            "n": 1,
            "quality": "high",
            "reference_asset_ids": [ref_id],
        },
    )
    assert res.status_code == 200, res.text

    # Routed through edit, not plain generate
    assert len(fake_openai.images.edit_calls) == 1
    assert len(fake_openai.images.generate_calls) == 0

    # SDK got the reference as the image input
    image_arg = fake_openai.images.edit_calls[0]["image"]
    # Single ref → passed as a tuple, not a list
    assert isinstance(image_arg, tuple)
    assert len(image_arg) == 3

    # Asset persisted; audit row carries the reference id
    audit = (isolated_paths / "workspaces" / ws["slug"] / "audit.log.jsonl").read_text()
    import json
    lines = [json.loads(line) for line in audit.strip().splitlines()]
    gen_lines = [line for line in lines if line["event"] == "asset.generated"]
    assert len(gen_lines) == 1
    assert gen_lines[0]["payload"]["references"] == [ref_id]


def test_cost_calculation_uses_real_pricing(
    client: TestClient, fake_openai: StubOpenAIClient
) -> None:
    """Sanity-check the price comes out roughly right for the stub usage."""
    ws = _create_workspace(client)
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/generate",
        json={"prompt": "Test", "n": 1, "quality": "high"},
    )
    body = res.json()
    # Stub: 600 text-only input @ $5/M = $0.003
    #       400 image input    @ $8/M = $0.0032
    #       200 cached         @ $2/M = $0.0004
    #     4096 image output    @ $30/M = $0.12288
    # Total ≈ $0.12948
    cost = body["provider_cost_usd"]
    assert 0.12 < cost < 0.14, f"unexpected cost: {cost}"
