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
        "cdr_primary": "cloudconvert",
        "cdr_fallback": "uniconvertor",
    }

    # Slice 10g — the legacy `tiers` block was removed. The Composable
    # pipeline is the only PSD producer; OCR availability is signalled
    # via the capabilities.tesseract bool above instead.
    assert "tiers" not in body
