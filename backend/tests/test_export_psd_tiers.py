"""Tier B / Tier C PSD export tests.

Stubs SAM-2 segmentation and Tesseract OCR so the route can be
exercised end-to-end without network or system binaries.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import app.services.segmentation as seg_module
from app.services.ocr import OcrResult, TextRegion
from app.services.segmentation import Mask, SegmentationResult
from tests.stubs import StubOpenAIClient


def _create_workspace(client: TestClient) -> dict[str, Any]:
    res = client.post(
        "/api/packaging/workspaces",
        json={"name": "Tier Test", "product_type": "lotion_bottle_label"},
    )
    assert res.status_code == 201
    return res.json()


def _generate(client: TestClient, slug: str) -> int:
    res = client.post(
        f"/api/packaging/workspaces/{slug}/generate",
        json={"prompt": "Test", "n": 1, "quality": "high"},
    )
    assert res.status_code == 200, res.text
    return int(res.json()["assets"][0]["id"])


def _mask_png(size: tuple[int, int], bbox: tuple[int, int, int, int]) -> bytes:
    """Build a 1-channel mask PNG: white inside bbox, black outside."""
    canvas = Image.new("L", size, 0)
    # Paint the rectangle as 255.
    inside = Image.new("L", (bbox[2] - bbox[0], bbox[3] - bbox[1]), 255)
    canvas.paste(inside, (bbox[0], bbox[1]))
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def stub_sam2(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Patch the segmentation dispatcher with a deterministic 2-mask result."""
    calls: list[dict[str, Any]] = []

    async def fake_segment(image_bytes: bytes) -> SegmentationResult:
        calls.append({"size_bytes": len(image_bytes)})
        # The stub PNG is 1x1; build two masks anyway just for layer count.
        return SegmentationResult(
            masks=[
                Mask(
                    name="sam2_layer_01",
                    png_bytes=_mask_png((4, 4), (0, 0, 4, 4)),
                    bbox=(0, 0, 1, 1),
                    area_px=1,
                ),
                Mask(
                    name="sam2_layer_02",
                    png_bytes=_mask_png((4, 4), (0, 0, 4, 4)),
                    bbox=(0, 0, 1, 1),
                    area_px=1,
                ),
            ],
            width=1,
            height=1,
            provider="replicate",
            model="meta/sam-2",
        )

    monkeypatch.setattr(seg_module, "segment", fake_segment)
    # Route module imports the function by name; patch there too.
    import app.modules.packaging.routes as routes_module

    monkeypatch.setattr(routes_module, "run_segmentation", fake_segment)
    return calls


@pytest.fixture()
def stub_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch ocr_extract with three deterministic text regions."""

    def fake_extract(image_bytes: bytes, min_confidence: float = 60.0) -> OcrResult:
        return OcrResult(
            regions=[
                TextRegion("Glow", (0, 0, 1, 1), 95.0, 100),
                TextRegion("Serenity", (0, 1, 1, 2), 92.0, 100),
                TextRegion("Lotion", (0, 2, 1, 3), 89.0, 101),
            ],
            lang="eng",
        )

    import app.modules.packaging.routes as routes_module

    monkeypatch.setattr(routes_module, "ocr_extract", fake_extract)


# ---------------------- Tier B ----------------------


def test_export_tier_b_writes_layered_psd(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    stub_sam2: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FORME_SEGMENTATION_PROVIDER", "replicate")
    monkeypatch.setenv("REPLICATE_API_TOKEN", "stub")
    import app.config as config_module
    config_module._settings = None

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])

    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/psd",
        json={"source_asset_id": src_id, "tier": "B", "color_space": "CMYK", "dpi": 300},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["tier"] == "B"
    assert body["segmentation_provider"] == "replicate"
    assert body["layer_count"] == 1 + 2  # base + 2 masks

    # PSD file exists
    psd_path = (
        isolated_paths / "workspaces" / ws["slug"] / body["asset"]["relative_path"]
    )
    assert psd_path.is_file()

    # SAM-2 was called once
    assert len(stub_sam2) == 1

    # Audit row uses the tier-B event name
    audit_text = (
        isolated_paths / "workspaces" / ws["slug"] / "audit.log.jsonl"
    ).read_text()
    events = [json.loads(line)["event"] for line in audit_text.strip().splitlines()]
    assert "export.psd.tier_b.created" in events


def test_export_tier_b_blocked_when_segmentation_off(
    client: TestClient,
    fake_openai: StubOpenAIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FORME_SEGMENTATION_PROVIDER", "none")
    import app.config as config_module
    config_module._settings = None

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/psd",
        json={"source_asset_id": src_id, "tier": "B"},
    )
    assert res.status_code == 503
    assert "Segmentation is disabled" in res.json()["detail"]


# ---------------------- Tier C ----------------------


def test_export_tier_c_writes_text_layers_and_sidecar(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    stub_sam2: list[dict[str, Any]],
    stub_ocr: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FORME_TIER_C_ENABLED", "true")
    monkeypatch.setenv("FORME_SEGMENTATION_PROVIDER", "replicate")
    monkeypatch.setenv("REPLICATE_API_TOKEN", "stub")
    import app.config as config_module
    config_module._settings = None

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])

    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/psd",
        json={"source_asset_id": src_id, "tier": "C"},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["tier"] == "C"
    assert body["text_layer_count"] == 3
    # PSD = base + 2 sam2 layers + 3 text layers
    assert body["layer_count"] == 1 + 2 + 3
    assert body["sidecar_url"] is not None

    # Sidecar JSON contains every region
    sidecar_path = (
        isolated_paths / "workspaces" / ws["slug"] / "exports"
    ).glob("*.ocr.json")
    sidecars = list(sidecar_path)
    assert len(sidecars) == 1
    sidecar_data = json.loads(sidecars[0].read_text())
    assert len(sidecar_data["regions"]) == 3
    assert sidecar_data["regions"][0]["text"] == "Glow"

    # Two export audit rows: the PSD itself + the sidecar JSON
    audit_text = (
        isolated_paths / "workspaces" / ws["slug"] / "audit.log.jsonl"
    ).read_text()
    events = [json.loads(line)["event"] for line in audit_text.strip().splitlines()]
    assert "export.psd.tier_c.created" in events
    assert "export.psd.tier_c.sidecar_saved" in events


def test_export_tier_c_blocked_when_disabled(
    client: TestClient,
    fake_openai: StubOpenAIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tier C must respect FORME_TIER_C_ENABLED even if SAM-2 is configured."""
    monkeypatch.setenv("FORME_TIER_C_ENABLED", "false")
    monkeypatch.setenv("FORME_SEGMENTATION_PROVIDER", "replicate")
    monkeypatch.setenv("REPLICATE_API_TOKEN", "stub")
    import app.config as config_module
    config_module._settings = None

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])

    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/psd",
        json={"source_asset_id": src_id, "tier": "C"},
    )
    assert res.status_code == 503
    assert "Tier C requires OCR which is disabled" in res.json()["detail"]


def test_export_invalid_tier_returns_422(
    client: TestClient, fake_openai: StubOpenAIClient
) -> None:
    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/psd",
        json={"source_asset_id": src_id, "tier": "Z"},
    )
    assert res.status_code == 422
    assert "tier must be" in res.json()["detail"]


# ---------------------- Tier A + OCR (no SAM-2 dependency) ----------------------


def test_export_tier_a_ocr_writes_text_layers_and_sidecar_without_segmentation(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    stub_ocr: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tier 'A+OCR' = flat PSD + Tesseract text overlays, NO SAM-2 call.

    Pin both behaviours: (a) text layers appear named with the detected
    string + position, (b) the segmentation service is never invoked
    (so a broken SAM-2 model can't break this tier).
    """
    monkeypatch.setenv("FORME_TIER_C_ENABLED", "true")
    import app.config as config_module
    config_module._settings = None

    # Sentinel: if the route ever calls segmentation for A+OCR, this fires.
    import app.modules.packaging.routes as routes_module

    async def _no_segmentation_allowed(_bytes: bytes) -> SegmentationResult:
        raise AssertionError(
            "Tier A+OCR must NOT call segmentation"
        )

    monkeypatch.setattr(routes_module, "run_segmentation", _no_segmentation_allowed)

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])

    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/psd",
        json={"source_asset_id": src_id, "tier": "A+OCR"},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["tier"] == "A+OCR"
    # 1 base layer + 3 stub OCR text layers = 4 total
    assert body["layer_count"] == 4
    assert body["sidecar_url"] is not None
    # text_layer_count is only populated for Tier C/A+OCR
    assert body["text_layer_count"] == 3

    # Sidecar lives next to the PSD and lists each region
    psd_path = isolated_paths / "workspaces" / ws["slug"] / body["asset"]["relative_path"]
    sidecar = psd_path.with_suffix(".ocr.json")
    assert sidecar.is_file()
    sidecar_data = json.loads(sidecar.read_text())
    assert sidecar_data["tier"] == "A+OCR"
    assert {r["text"] for r in sidecar_data["regions"]} == {"Glow", "Serenity", "Lotion"}
    # And critically — NO segmentation block in the A+OCR sidecar
    assert "segmentation" not in sidecar_data


def test_export_tier_a_ocr_accepts_alias_just_ocr(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    stub_ocr: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`tier='OCR'` is a friendlier alias for the same A+OCR pipeline."""
    monkeypatch.setenv("FORME_TIER_C_ENABLED", "true")
    import app.config as config_module
    config_module._settings = None

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])

    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/psd",
        json={"source_asset_id": src_id, "tier": "OCR"},
    )
    assert res.status_code == 201, res.text
    assert res.json()["tier"] == "A+OCR"


def test_export_tier_a_ocr_blocked_when_ocr_disabled(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
) -> None:
    """A+OCR shares the FORME_TIER_C_ENABLED toggle since it uses OCR."""
    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])

    # The conftest leaves FORME_TIER_C_ENABLED unset → defaults to false
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/psd",
        json={"source_asset_id": src_id, "tier": "A+OCR"},
    )
    assert res.status_code == 503
    detail = res.json()["detail"]
    assert "A+OCR" in detail
    assert "FORME_TIER_C_ENABLED" in detail
