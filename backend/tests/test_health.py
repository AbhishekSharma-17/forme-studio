"""Health endpoint exposes capabilities + model id."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_returns_ok_with_capabilities(client: TestClient) -> None:
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert body["image_model"]

    # In CI no keys are set + Inkscape is pointed at a fake path — every
    # capability is false. Tesseract may or may not be on PATH in CI;
    # accept either but check the key is present.
    caps = body["capabilities"]
    assert caps["openai_image"] is False
    assert caps["vectorizer_ai"] is False
    assert caps["inkscape"] is False
    assert caps["segmentation_replicate"] is False
    assert caps["segmentation_self_hosted"] is False
    assert caps["segmentation_sam3"] is False
    assert "tesseract" in caps
    # CDR caps — master toggle off + no provider creds in CI = all false.
    assert caps["cdr_enabled"] is False
    assert caps["cdr_cloudconvert"] is False
    assert caps["cdr_uniconvertor"] is False

    # Providers reflect what's configured (not what's reachable).
    # Defaults: cloudconvert primary, uniconvertor fallback (slice 7 polish).
    assert body["providers"] == {
        "vectorizer_primary": "vectorizer_ai",
        "vectorizer_fallback": "inkscape_potrace",
        "segmentation": "none",
        "cdr_primary": "cloudconvert",
        "cdr_fallback": "uniconvertor",
    }

    # Tier availability — A always true; A+OCR depends on FORME_TIER_C_ENABLED
    # (off in CI conftest) so it's False; B+C also False (no seg provider).
    assert body["tiers"] == {
        "tier_a": True,
        "tier_a_ocr": False,
        "tier_b": False,
        "tier_c": False,
    }
