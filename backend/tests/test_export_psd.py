"""Tests for the Tier A flat PSD export endpoint."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from psd_tools import PSDImage

from tests.stubs import StubOpenAIClient


def _create_workspace(client: TestClient) -> dict[str, object]:
    res = client.post(
        "/api/packaging/workspaces",
        json={"name": "PSD Test", "product_type": "lotion_bottle_label"},
    )
    assert res.status_code == 201, res.text
    return res.json()


def _generate(client: TestClient, slug: str) -> int:
    res = client.post(
        f"/api/packaging/workspaces/{slug}/generate",
        json={"prompt": "Test design for PSD", "n": 1, "quality": "high"},
    )
    assert res.status_code == 200
    return int(res.json()["assets"][0]["id"])


def test_export_psd_creates_cmyk_asset_and_audit(
    client: TestClient, isolated_paths: Path, fake_openai: StubOpenAIClient
) -> None:
    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])

    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/psd",
        json={"source_asset_id": src_id, "color_space": "CMYK", "dpi": 300},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["color_space"] == "CMYK"
    assert body["dpi"] == 300

    # File landed in exports/
    asset = body["asset"]
    assert asset["kind"] == "export"
    assert asset["mime_type"] == "image/vnd.adobe.photoshop"
    psd_path = isolated_paths / "workspaces" / ws["slug"] / asset["relative_path"]
    assert psd_path.is_file()
    assert psd_path.suffix == ".psd"

    # The PSD is actually valid CMYK
    psd = PSDImage.open(str(psd_path))
    assert int(psd.color_mode) == 4  # 4 == CMYK in PSD spec

    # Audit row written
    audit = (isolated_paths / "workspaces" / ws["slug"] / "audit.log.jsonl").read_text()
    lines = [json.loads(line) for line in audit.strip().splitlines()]
    psd_events = [line for line in lines if line["event"] == "export.psd.tier_a.created"]
    assert len(psd_events) == 1
    assert psd_events[0]["payload"]["source_asset_id"] == src_id
    assert psd_events[0]["payload"]["color_space"] == "CMYK"
    assert psd_events[0]["payload"]["tier"] == "A"


def test_export_psd_rgb_mode_produces_rgb_file(
    client: TestClient, isolated_paths: Path, fake_openai: StubOpenAIClient
) -> None:
    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])

    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/psd",
        json={"source_asset_id": src_id, "color_space": "RGB", "dpi": 300},
    )
    assert res.status_code == 201
    psd_path = (
        isolated_paths / "workspaces" / ws["slug"] / res.json()["asset"]["relative_path"]
    )
    psd = PSDImage.open(str(psd_path))
    assert int(psd.color_mode) == 3  # 3 == RGB


def test_export_psd_rejects_unknown_source(
    client: TestClient, fake_openai: StubOpenAIClient
) -> None:
    ws = _create_workspace(client)
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/psd",
        json={"source_asset_id": 9999, "color_space": "CMYK", "dpi": 300},
    )
    assert res.status_code == 422
    assert "does not belong" in res.json()["detail"]


def test_export_psd_rejects_reference_kind(
    client: TestClient, isolated_paths: Path, fake_openai: StubOpenAIClient
) -> None:
    """Only generation assets can be exported as PSD (slice 4.5 extends this)."""
    import io

    from PIL import Image

    ws = _create_workspace(client)
    buf = io.BytesIO()
    Image.new("RGB", (200, 200), (0, 0, 0)).save(buf, format="PNG")
    ref = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/references",
        files=[("files", ("logo.png", buf.getvalue(), "image/png"))],
    ).json()["references"][0]

    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/psd",
        json={"source_asset_id": ref["id"], "color_space": "CMYK", "dpi": 300},
    )
    assert res.status_code == 422
    assert "Can only export 'generation'" in res.json()["detail"]


def test_export_psd_invalid_color_space_422(
    client: TestClient, fake_openai: StubOpenAIClient
) -> None:
    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/psd",
        json={"source_asset_id": src_id, "color_space": "LAB", "dpi": 300},
    )
    assert res.status_code == 422
    assert "color_space must be" in res.json()["detail"]
