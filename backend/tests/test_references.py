"""Tests for the reference-image upload endpoint.

Single-image, multi-image, validation errors. Files are real PNGs built
on the fly so the normalize() path actually exercises Pillow.
"""

from __future__ import annotations

import io
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image


def _png_bytes(size: tuple[int, int], color: tuple[int, int, int] = (200, 80, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes(size: tuple[int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (30, 120, 60)).save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _create_workspace(client: TestClient) -> dict[str, object]:
    res = client.post(
        "/api/packaging/workspaces",
        json={"name": "Ref Test", "product_type": "lotion_bottle_label"},
    )
    assert res.status_code == 201
    return res.json()


def test_upload_single_reference(client: TestClient, isolated_paths: Path) -> None:
    ws = _create_workspace(client)
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/references",
        files=[("files", ("logo.png", _png_bytes((512, 256)), "image/png"))],
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["total"] == 1
    asset = body["references"][0]
    assert asset["kind"] == "reference"
    assert asset["mime_type"] == "image/png"
    assert asset["image_size"] == "512x256"

    # File landed under references/
    refs_dir = isolated_paths / "workspaces" / ws["slug"] / "references"
    pngs = list(refs_dir.glob("*.png"))
    assert len(pngs) == 1

    # Audit JSONL has reference.uploaded
    audit = (isolated_paths / "workspaces" / ws["slug"] / "audit.log.jsonl").read_text()
    assert "reference.uploaded" in audit


def test_upload_multiple_references_with_mixed_formats(
    client: TestClient, isolated_paths: Path
) -> None:
    ws = _create_workspace(client)
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/references",
        files=[
            ("files", ("logo.png", _png_bytes((400, 400)), "image/png")),
            ("files", ("mood.jpg", _jpeg_bytes((600, 400)), "image/jpeg")),
        ],
    )
    assert res.status_code == 201
    body = res.json()
    assert body["total"] == 2

    # Both stored as PNG after normalization
    refs_dir = isolated_paths / "workspaces" / ws["slug"] / "references"
    assert len(list(refs_dir.glob("*.png"))) == 2


def test_upload_rejects_non_image(client: TestClient) -> None:
    ws = _create_workspace(client)
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/references",
        files=[("files", ("notes.txt", b"this is not an image", "text/plain"))],
    )
    assert res.status_code == 400
    assert "Could not read image" in res.json()["detail"]


def test_upload_rejects_empty_file(client: TestClient) -> None:
    ws = _create_workspace(client)
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/references",
        files=[("files", ("empty.png", b"", "image/png"))],
    )
    assert res.status_code == 422
    assert "Empty upload" in res.json()["detail"]


def test_upload_resizes_oversize_image(
    client: TestClient, isolated_paths: Path
) -> None:
    ws = _create_workspace(client)
    # 5000x3000 → should be capped at 3840px long edge.
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/references",
        files=[("files", ("huge.png", _png_bytes((5000, 3000)), "image/png"))],
    )
    assert res.status_code == 201
    asset = res.json()["references"][0]
    w_str, h_str = asset["image_size"].split("x")
    w, h = int(w_str), int(h_str)
    assert max(w, h) == 3840


def test_listing_kind_filter_returns_only_references(
    client: TestClient,
) -> None:
    ws = _create_workspace(client)
    client.post(
        f"/api/packaging/workspaces/{ws['slug']}/references",
        files=[("files", ("logo.png", _png_bytes((128, 128)), "image/png"))],
    )
    res = client.get(
        f"/api/packaging/workspaces/{ws['slug']}/assets?kind=reference"
    )
    assert res.status_code == 200
    items = res.json()
    assert len(items) == 1
    assert items[0]["kind"] == "reference"


def test_serve_reference_file(client: TestClient) -> None:
    ws = _create_workspace(client)
    created = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/references",
        files=[("files", ("logo.png", _png_bytes((100, 100)), "image/png"))],
    ).json()["references"][0]
    res = client.get(
        f"/api/packaging/workspaces/{ws['slug']}/assets/{created['id']}/file"
    )
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/png"
    assert res.content[:8] == b"\x89PNG\r\n\x1a\n"
