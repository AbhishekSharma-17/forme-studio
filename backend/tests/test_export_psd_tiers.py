"""PSD tier tests — Tier A flat + Tier A+OCR.

Tier B (SAM-2 layered) and Tier C (segmentation + OCR) were removed in
slice 9; Tier A+OCR replaces the use-case ("designer wants to fix text")
without the SAM-2 dependency. See ``compose_*`` endpoints for the
multi-layered editable-element workflow.

Stubs Tesseract OCR so the route can be exercised end-to-end without
the system binary.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.services.ocr import OcrResult, TextRegion
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


# ---------------------- Tier validation ----------------------


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


def test_export_legacy_tier_b_rejected(
    client: TestClient, fake_openai: StubOpenAIClient
) -> None:
    """Tier B was removed in slice 9 — request must 422, not silently route."""
    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/psd",
        json={"source_asset_id": src_id, "tier": "B"},
    )
    assert res.status_code == 422


def test_export_legacy_tier_c_rejected(
    client: TestClient, fake_openai: StubOpenAIClient
) -> None:
    """Tier C was removed in slice 9 — request must 422, not silently route."""
    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/psd",
        json={"source_asset_id": src_id, "tier": "C"},
    )
    assert res.status_code == 422


# ---------------------- Tier A + OCR ----------------------


def test_export_tier_a_ocr_writes_text_layers_and_sidecar(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    stub_ocr: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tier 'A+OCR' = flat PSD + Tesseract text overlays, NO SAM-2 call.

    Pin: (a) text layers appear named with the detected string + position,
    (b) the OCR sidecar JSON is written with every region.
    """
    monkeypatch.setenv("FORME_TIER_C_ENABLED", "true")
    import app.config as config_module
    config_module._settings = None

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
    assert body["text_layer_count"] == 3

    # Sidecar lives next to the PSD and lists each region
    psd_path = isolated_paths / "workspaces" / ws["slug"] / body["asset"]["relative_path"]
    sidecar = psd_path.with_suffix(".ocr.json")
    assert sidecar.is_file()
    sidecar_data = json.loads(sidecar.read_text())
    assert sidecar_data["tier"] == "A+OCR"
    assert {r["text"] for r in sidecar_data["regions"]} == {"Glow", "Serenity", "Lotion"}
    # No segmentation block — A+OCR doesn't use any SAM provider.
    assert "segmentation" not in sidecar_data

    # Audit trail: tier_a_ocr.created + ocr_sidecar.saved
    audit_text = (
        isolated_paths / "workspaces" / ws["slug"] / "audit.log.jsonl"
    ).read_text()
    events = [json.loads(line)["event"] for line in audit_text.strip().splitlines()]
    assert "export.psd.tier_a_ocr.created" in events
    assert "export.psd.ocr_sidecar.saved" in events


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
    """A+OCR honours the FORME_TIER_C_ENABLED gate."""
    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])

    # The conftest pins FORME_TIER_C_ENABLED=false
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/psd",
        json={"source_asset_id": src_id, "tier": "A+OCR"},
    )
    assert res.status_code == 503
    detail = res.json()["detail"]
    assert "A+OCR" in detail
    assert "FORME_TIER_C_ENABLED" in detail
