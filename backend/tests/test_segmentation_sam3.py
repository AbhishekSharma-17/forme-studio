"""Tests for the SAM 3.1 self-hosted segmentation adapter.

The HTTP layer is fully mocked — we never touch a real DGX. Tests cover:

* happy paths with the rich schema (label + score → semantic layer names)
* backward compat: a SAM 3 endpoint that omits label/score still works
* dispatcher errors: missing URL → 503, non-200 → 502, timeout → 504
* the disambiguator: two masks called "logo" become "logo_1" + "logo_2"
"""

from __future__ import annotations

import base64
import io
from typing import Any

import httpx
import pytest
from PIL import Image

from app.services.segmentation import (
    Mask,
    _disambiguate_layer_names,
    segment,
)

# --------------------------------------------------------------- fixtures


def _tiny_mask_png(value: int = 255) -> bytes:
    """Return a 32×32 single-channel PNG filled with ``value`` (0–255).

    Used as a stand-in for what the SAM 3 endpoint would emit per mask.
    """
    im = Image.new("L", (32, 32), value)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _tiny_input_png() -> bytes:
    """The 'source image' the dispatcher sees — small but valid PNG."""
    im = Image.new("RGB", (1024, 1536), (200, 80, 60))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


class _StubResponse:
    def __init__(
        self,
        status_code: int = 200,
        json_body: dict[str, Any] | None = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._json_body = json_body or {}
        self.text = text or (str(self._json_body) if self._json_body else "")

    def json(self) -> dict[str, Any]:
        return self._json_body


class _StubAsyncClient:
    def __init__(self, response: _StubResponse, **_: Any) -> None:
        self._resp = response
        self.last_call: dict[str, Any] | None = None

    async def __aenter__(self) -> _StubAsyncClient:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> _StubResponse:
        self.last_call = {"url": url, **kwargs}
        return self._resp


def _patch_sam3_httpx(
    monkeypatch: pytest.MonkeyPatch, response: _StubResponse
) -> dict[str, Any]:
    """Install a stub httpx.AsyncClient; return a dict the test can inspect."""
    captured: dict[str, Any] = {}

    def _factory(*_a: Any, **kw: Any) -> _StubAsyncClient:
        client = _StubAsyncClient(response, **kw)
        # Share the captured dict via closure on the same instance.
        original_post = client.post

        async def _post(url: str, **post_kw: Any) -> _StubResponse:
            captured["url"] = url
            captured.update(post_kw)
            return await original_post(url, **post_kw)

        client.post = _post  # type: ignore[method-assign]
        return client

    monkeypatch.setattr("app.services.segmentation.httpx.AsyncClient", _factory)
    return captured


def _reset_settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    """Set SAM 3 env vars and clear the cached Settings object."""
    monkeypatch.setenv("FORME_SEGMENTATION_PROVIDER", "sam3")
    for key, val in env.items():
        monkeypatch.setenv(key, val)
    import app.config as config_module
    config_module._settings = None


# ----------------------------------------------------------- happy paths


@pytest.mark.asyncio
async def test_sam3_text_prompt_returns_labelled_masks(
    isolated_paths: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Text-prompted SAM 3.1 → masks carry label, score, semantic name."""
    _reset_settings(
        monkeypatch,
        FORME_SAM3_ENDPOINT_URL="https://dgx.local/sam3/image",
        FORME_SAM3_ENDPOINT_TOKEN="tok-abc",
        FORME_SAM3_TEXT_PROMPT="logo, bottle",
    )
    body = {
        "width": 1024,
        "height": 1536,
        "model": "sam3.1-image-r1",
        "masks": [
            {
                "png_b64": base64.b64encode(_tiny_mask_png()).decode(),
                "bbox": [10, 20, 200, 300],
                "area_px": 50000,
                "label": "logo",
                "score": 0.92,
            },
            {
                "png_b64": base64.b64encode(_tiny_mask_png()).decode(),
                "bbox": [40, 50, 800, 1400],
                "area_px": 900000,
                "label": "bottle",
                "score": 0.88,
            },
        ],
    }
    captured = _patch_sam3_httpx(monkeypatch, _StubResponse(json_body=body))

    result = await segment(_tiny_input_png())

    # Provider + model carried through
    assert result.provider == "sam3"
    assert result.model == "sam3.1-image-r1"
    assert result.width == 1024
    assert result.height == 1536
    assert len(result.masks) == 2

    # Largest area first (bottle) — verifies the ordering rule
    assert result.masks[0].label == "bottle"
    assert result.masks[0].name == "bottle"
    assert result.masks[0].score == pytest.approx(0.88)
    assert result.masks[1].label == "logo"
    assert result.masks[1].name == "logo"
    assert result.masks[1].score == pytest.approx(0.92)

    # Wire format: bearer token + form data
    assert captured["url"] == "https://dgx.local/sam3/image"
    assert captured["headers"]["Authorization"] == "Bearer tok-abc"
    assert captured["data"]["mode"] == "text"
    assert captured["data"]["text_prompt"] == "logo, bottle"


@pytest.mark.asyncio
async def test_sam3_auto_mode_when_no_prompt(
    isolated_paths: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty FORME_SAM3_TEXT_PROMPT → mode=auto, masks named sam3_layer_NN."""
    _reset_settings(
        monkeypatch,
        FORME_SAM3_ENDPOINT_URL="https://dgx.local/sam3/image",
        FORME_SAM3_TEXT_PROMPT="",
    )
    body = {
        "width": 1024,
        "height": 1536,
        "masks": [
            {
                "png_b64": base64.b64encode(_tiny_mask_png()).decode(),
                "bbox": [0, 0, 100, 100],
                "area_px": 10000,
                # No label / no score — AMG returns anonymous masks.
            },
            {
                "png_b64": base64.b64encode(_tiny_mask_png()).decode(),
                "bbox": [0, 0, 200, 200],
                "area_px": 40000,
            },
        ],
    }
    captured = _patch_sam3_httpx(monkeypatch, _StubResponse(json_body=body))

    result = await segment(_tiny_input_png())

    assert result.provider == "sam3"
    assert captured["data"]["mode"] == "auto"
    # No text_prompt key in auto mode.
    assert "text_prompt" not in captured["data"]
    # Anonymous fallback naming.
    names = {m.name for m in result.masks}
    assert names == {"sam3_layer_01", "sam3_layer_02"}
    for m in result.masks:
        assert m.label is None
        assert m.score is None


@pytest.mark.asyncio
async def test_sam3_duplicate_labels_get_disambiguated(
    isolated_paths: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two masks both labelled 'logo' must produce unique layer names."""
    _reset_settings(
        monkeypatch,
        FORME_SAM3_ENDPOINT_URL="https://dgx.local/sam3/image",
        FORME_SAM3_TEXT_PROMPT="logo",
    )
    body = {
        "width": 1024,
        "height": 1536,
        "masks": [
            {
                "png_b64": base64.b64encode(_tiny_mask_png()).decode(),
                "bbox": [0, 0, 50, 50],
                "area_px": 2500,
                "label": "logo",
                "score": 0.81,
            },
            {
                "png_b64": base64.b64encode(_tiny_mask_png()).decode(),
                "bbox": [60, 60, 110, 110],
                "area_px": 2500,
                "label": "logo",
                "score": 0.77,
            },
        ],
    }
    _patch_sam3_httpx(monkeypatch, _StubResponse(json_body=body))

    result = await segment(_tiny_input_png())

    names = {m.name for m in result.masks}
    assert names == {"logo_1", "logo_2"}
    # But the original semantic label is preserved on both
    assert all(m.label == "logo" for m in result.masks)


@pytest.mark.asyncio
async def test_sam3_tolerates_missing_optional_fields(
    isolated_paths: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A SAM-3 endpoint may omit score, label, or even 'model' — none required."""
    _reset_settings(
        monkeypatch,
        FORME_SAM3_ENDPOINT_URL="https://dgx.local/sam3/image",
    )
    body = {
        # No "model" key.
        "width": 1024,
        "height": 1536,
        "masks": [
            {
                "png_b64": base64.b64encode(_tiny_mask_png()).decode(),
                "bbox": [0, 0, 100, 100],
                "area_px": 10000,
                # label / score absent
            }
        ],
    }
    _patch_sam3_httpx(monkeypatch, _StubResponse(json_body=body))
    result = await segment(_tiny_input_png())
    assert result.provider == "sam3"
    # Default model label kicks in
    assert result.model == "sam3.1-image"
    assert result.masks[0].label is None
    assert result.masks[0].score is None


# --------------------------------------------------------- dispatcher errors


@pytest.mark.asyncio
async def test_sam3_503_when_endpoint_url_missing(
    isolated_paths: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Provider=sam3 but no URL → 503 with the alternatives spelled out."""
    monkeypatch.setenv("FORME_SEGMENTATION_PROVIDER", "sam3")
    monkeypatch.setenv("FORME_SAM3_ENDPOINT_URL", "")
    import app.config as config_module
    config_module._settings = None

    with pytest.raises(Exception) as excinfo:
        await segment(_tiny_input_png())
    err = excinfo.value
    assert getattr(err, "status_code", None) == 503
    detail = getattr(err, "detail", "")
    assert "FORME_SAM3_ENDPOINT_URL" in detail
    assert "replicate" in detail  # the suggested alternatives


@pytest.mark.asyncio
async def test_sam3_502_when_upstream_returns_500(
    isolated_paths: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reset_settings(
        monkeypatch,
        FORME_SAM3_ENDPOINT_URL="https://dgx.local/sam3/image",
    )
    _patch_sam3_httpx(
        monkeypatch,
        _StubResponse(status_code=500, text="GPU OOM, sorry."),
    )

    with pytest.raises(Exception) as excinfo:
        await segment(_tiny_input_png())
    err = excinfo.value
    assert getattr(err, "status_code", None) == 502
    detail = getattr(err, "detail", "")
    assert "500" in detail
    assert "GPU OOM" in detail


@pytest.mark.asyncio
async def test_sam3_504_when_endpoint_times_out(
    isolated_paths: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reset_settings(
        monkeypatch,
        FORME_SAM3_ENDPOINT_URL="https://dgx.local/sam3/image",
        FORME_SEGMENTATION_TIMEOUT_S="60",
    )

    class _TimingOut:
        async def __aenter__(self) -> _TimingOut:
            return self

        async def __aexit__(self, *_exc: Any) -> None:
            return None

        async def post(self, *_a: Any, **_kw: Any) -> _StubResponse:
            raise httpx.TimeoutException("simulated")

    monkeypatch.setattr(
        "app.services.segmentation.httpx.AsyncClient",
        lambda *a, **kw: _TimingOut(),
    )

    with pytest.raises(Exception) as excinfo:
        await segment(_tiny_input_png())
    err = excinfo.value
    assert getattr(err, "status_code", None) == 504
    assert "timed out" in getattr(err, "detail", "")


# --------------------------------------------------------------- unit tests


def test_disambiguate_layer_names_is_idempotent_for_uniques() -> None:
    """A list of already-unique names should be untouched."""
    masks = [
        Mask(name="logo", png_bytes=b"", bbox=(0, 0, 1, 1), area_px=1),
        Mask(name="bottle", png_bytes=b"", bbox=(0, 0, 1, 1), area_px=1),
    ]
    _disambiguate_layer_names(masks)
    assert [m.name for m in masks] == ["logo", "bottle"]


def test_disambiguate_layer_names_handles_three_collisions() -> None:
    masks = [
        Mask(name="logo", png_bytes=b"", bbox=(0, 0, 1, 1), area_px=1),
        Mask(name="logo", png_bytes=b"", bbox=(0, 0, 1, 1), area_px=2),
        Mask(name="logo", png_bytes=b"", bbox=(0, 0, 1, 1), area_px=3),
        Mask(name="wordmark", png_bytes=b"", bbox=(0, 0, 1, 1), area_px=4),
    ]
    _disambiguate_layer_names(masks)
    assert [m.name for m in masks] == ["logo_1", "logo_2", "logo_3", "wordmark"]
