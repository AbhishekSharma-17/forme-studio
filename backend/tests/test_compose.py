"""Tests for the composable-PSD pipeline (slice 8).

Mocks every external call:
* OpenAI vision (chat.completions for element discovery)
* gpt-image-2 (images.generate for per-element generation)
* psd_tools writes a real PSD to disk so we can verify layer counts.

Covers:
* discover endpoint returns a manifest under workspace's frozen trim_mm
* assemble endpoint generates each element, persists Asset rows, writes
  a layered PSD, and audits 'export.psd.composable.created'
* body_copy elements are skipped during per-element generation
* error path: vision returns invalid JSON → 502
* error path: gpt-image-2 returns no image for one element → 502
"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from tests.stubs import StubOpenAIClient

# ──────────────────────────────────────────────────────────────────────
#  helpers
# ──────────────────────────────────────────────────────────────────────


def _create_workspace(client: TestClient) -> dict[str, Any]:
    res = client.post(
        "/api/packaging/workspaces",
        json={"name": "Compose Test", "product_type": "lotion_bottle_label"},
    )
    assert res.status_code == 201, res.text
    return res.json()


def _generate(client: TestClient, slug: str) -> int:
    res = client.post(
        f"/api/packaging/workspaces/{slug}/generate",
        json={"prompt": "Compose source variant", "n": 1, "quality": "high"},
    )
    assert res.status_code == 200, res.text
    return int(res.json()["assets"][0]["id"])


def _transparent_png(w: int = 256, h: int = 256) -> str:
    """Build a fake RGBA PNG and return its base64 string.

    Used to stand in for what gpt-image-2 would return for a single
    element with transparent_background=True.
    """
    img = Image.new("RGBA", (w, h), (10, 20, 30, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


# ──────────────────────────────────────────────────────────────────────
#  monkey-patch helpers
# ──────────────────────────────────────────────────────────────────────


class _StubChatMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _StubChatChoice:
    def __init__(self, content: str) -> None:
        self.message = _StubChatMessage(content)


class _StubChatResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_StubChatChoice(content)]


def _patch_vision(
    monkeypatch: pytest.MonkeyPatch,
    fake_openai: StubOpenAIClient,
    manifest_json: str,
) -> None:
    """Attach a stub chat.completions.create that returns a fixed JSON."""

    class _StubChatCompletions:
        async def create(self, **_kwargs: Any) -> _StubChatResponse:
            return _StubChatResponse(manifest_json)

    class _StubChat:
        completions = _StubChatCompletions()

    fake_openai.chat = _StubChat()  # type: ignore[attr-defined]


def _patch_image_gen(
    monkeypatch: pytest.MonkeyPatch,
    fake_openai: StubOpenAIClient,
    *,
    fail_on_name: str | None = None,
) -> None:
    """Patch the StubOpenAIClient's images.generate to honour transparent_bg.

    Returns a fake transparent PNG per call. If ``fail_on_name`` is set,
    any call whose prompt contains that string raises — used to test the
    per-element failure path.
    """

    async def _gen(**kwargs: Any) -> Any:
        prompt = str(kwargs.get("prompt", ""))
        if fail_on_name and fail_on_name in prompt:
            raise RuntimeError(f"simulated failure for {fail_on_name}")

        class _D:
            def __init__(self, b64: str) -> None:
                self.b64_json = b64

        class _U:
            def model_dump(self) -> dict[str, Any]:
                return {"input_tokens": 100, "output_tokens": 100}

        class _R:
            def __init__(self) -> None:
                self.data = [_D(_transparent_png())]
                self.usage = _U()

        return _R()

    fake_openai.images.generate = _gen  # type: ignore[method-assign]

    # Bypass rembg in tests — model download (176 MB) + ONNX inference is
    # both slow and unnecessary here: the stub PNG is already RGBA. The
    # bg-removal call site is exercised end-to-end live in the smoke run.
    import app.services.compose as compose_module

    async def _identity_remove(png_bytes: bytes) -> bytes:
        return png_bytes

    monkeypatch.setattr(compose_module, "remove_background", _identity_remove)


# ──────────────────────────────────────────────────────────────────────
#  DISCOVER
# ──────────────────────────────────────────────────────────────────────


_HAPPY_MANIFEST = """
{
  "elements": [
    {
      "name": "imara_wordmark",
      "label": "IMARA wordmark",
      "prompt": "IMARA wordmark in gold serif. Transparent background. Isolated. No other elements.",
      "position_mm": [16, 8, 43, 18],
      "size_px": "1024x1024",
      "kind": "wordmark",
      "vectorizable": true
    },
    {
      "name": "sandalwood_botanical",
      "label": "Sandalwood + saffron botanical",
      "prompt": "Sandalwood leaves intertwined with saffron strands in gold line-art. Transparent background. Isolated. No other elements.",
      "position_mm": [12, 35, 51, 51],
      "size_px": "1024x1024",
      "kind": "graphic",
      "vectorizable": false
    }
  ]
}
"""


def test_compose_discover_returns_manifest_with_trim_mm(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: stub vision → unified analyzer returns the manifest.

    OCR is NOT available in the test (Tesseract isn't on PATH in CI),
    so the unified manifest contains only the graphic elements from
    vision; ``ocr_available`` is False.
    """
    _patch_vision(monkeypatch, fake_openai, _HAPPY_MANIFEST)

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])

    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/compose/discover",
        json={"source_asset_id": src_id},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["source_asset_id"] == src_id
    # trim_mm comes from the lotion preset (70 × 100)
    assert body["trim_mm"] == {"w": 70.0, "h": 100.0}
    assert len(body["elements"]) == 2
    names = {e["name"] for e in body["elements"]}
    assert names == {"imara_wordmark", "sandalwood_botanical"}
    # Slice 10a: vectorizable hint flows through.
    by_name = {e["name"]: e for e in body["elements"]}
    assert by_name["imara_wordmark"]["vectorizable"] is True
    assert by_name["sandalwood_botanical"]["vectorizable"] is False
    # text + confidence are None for vision-discovered graphics
    for e in body["elements"]:
        assert e["text"] is None
        assert e["confidence"] is None
        assert e["kind"] != "text"
    # `ocr_available` mirrors the host environment — True when Tesseract
    # is on PATH (dev machine), False in clean CI. Either is fine here;
    # we just assert the key is present and boolean.
    assert isinstance(body["ocr_available"], bool)


def test_compose_discover_rejects_invalid_json_from_vision(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vision model returning garbage → 502 with a clear message."""
    _patch_vision(monkeypatch, fake_openai, "not valid json at all")

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])

    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/compose/discover",
        json={"source_asset_id": src_id},
    )
    assert res.status_code == 502
    assert "invalid JSON" in res.json()["detail"]


def test_compose_discover_rejects_reference_kind(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
) -> None:
    """Only 'generation' assets can be composed (same rule as exports)."""
    import io as _io

    ws = _create_workspace(client)
    buf = _io.BytesIO()
    Image.new("RGB", (64, 64), (0, 0, 0)).save(buf, format="PNG")
    ref = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/references",
        files=[("files", ("logo.png", buf.getvalue(), "image/png"))],
    ).json()["references"][0]

    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/compose/discover",
        json={"source_asset_id": ref["id"]},
    )
    assert res.status_code == 422
    assert "'generation'" in res.json()["detail"]


# ──────────────────────────────────────────────────────────────────────
#  ASSEMBLE
# ──────────────────────────────────────────────────────────────────────


def _assemble_payload(src_id: int) -> dict[str, Any]:
    return {
        "source_asset_id": src_id,
        "quality": "medium",
        "elements": [
            {
                "name": "imara_wordmark",
                "label": "IMARA wordmark",
                "prompt": "IMARA wordmark. Transparent. Isolated.",
                "position_mm": [16, 8, 43, 18],
                "size_px": "1024x1024",
                "kind": "wordmark",
            },
            {
                "name": "sandalwood_botanical",
                "label": "Botanical",
                "prompt": "Sandalwood leaves. Transparent. Isolated.",
                "position_mm": [12, 35, 51, 51],
                "size_px": "1024x1024",
                "kind": "graphic",
            },
            {
                "name": "ingredients_block",
                "label": "Ingredients copy",
                "prompt": "(handled by OCR)",
                "position_mm": [4, 100, 67, 20],
                "size_px": "1024x1024",
                "kind": "body_copy",
            },
        ],
    }


def test_compose_assemble_skips_body_copy_and_writes_layered_psd(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: 2 renderable + 1 body_copy = 2 element layers + base."""
    _patch_image_gen(monkeypatch, fake_openai)

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])

    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/psd-composable",
        json=_assemble_payload(src_id),
    )
    assert res.status_code == 201, res.text
    body = res.json()
    # body_copy ingredient block is skipped → 2 elements generated
    assert body["element_count"] == 2
    # 1 base layer + 2 elements = 3 total
    assert body["layer_count"] == 3
    assert body["dpi"] == 300
    assert body["color_space"] == "CMYK"
    # Both elements were persisted as their own Assets
    assert len(body["elements"]) == 2
    names = {e["name"] for e in body["elements"]}
    assert names == {"imara_wordmark", "sandalwood_botanical"}
    for e in body["elements"]:
        assert e["asset_id"] > 0
        assert e["width_px"] == 256
        assert e["height_px"] == 256

    # The assembled PSD exists on disk and is non-empty
    psd_path = (
        isolated_paths
        / "workspaces"
        / ws["slug"]
        / body["asset"]["relative_path"]
    )
    assert psd_path.is_file()
    assert psd_path.stat().st_size > 100  # at least a header + some pixels


def test_compose_assemble_rejects_all_body_copy_manifest(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If every element is body_copy, there's nothing to render → 422."""
    _patch_image_gen(monkeypatch, fake_openai)

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])

    payload = {
        "source_asset_id": src_id,
        "elements": [
            {
                "name": "block_a",
                "label": "Block A",
                "prompt": "body copy a",
                "position_mm": [0, 0, 10, 10],
                "size_px": "1024x1024",
                "kind": "body_copy",
            },
            {
                "name": "block_b",
                "label": "Block B",
                "prompt": "body copy b",
                "position_mm": [0, 10, 10, 10],
                "size_px": "1024x1024",
                "kind": "body_copy",
            },
        ],
    }
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/psd-composable",
        json=payload,
    )
    assert res.status_code == 422
    # The error message guides the user to add at least one graphic OR text
    # element. The exact text changed in slice 10b — body_copy elements get
    # routed to OCR which produces kind="text" entries instead.
    assert "No renderable elements" in res.json()["detail"]


def test_compose_assemble_per_element_failure_surfaces_502(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If gpt-image-2 raises mid-batch, the whole assemble fails fast."""
    # Make the 'sandalwood_botanical' prompt fail; the first element
    # ('imara_wordmark') will succeed before we abort.
    _patch_image_gen(monkeypatch, fake_openai, fail_on_name="Sandalwood leaves")

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])

    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/psd-composable",
        json=_assemble_payload(src_id),
    )
    assert res.status_code == 502
    assert "Per-element generation failed" in res.json()["detail"]


def test_compose_assemble_audits_export_event(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The composable PSD must produce one export.psd.composable.created
    event in the on-disk audit JSONL."""
    import json as _json

    _patch_image_gen(monkeypatch, fake_openai)

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])

    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/psd-composable",
        json=_assemble_payload(src_id),
    )
    assert res.status_code == 201

    audit_path = isolated_paths / "workspaces" / ws["slug"] / "audit.log.jsonl"
    events = [_json.loads(line) for line in audit_path.read_text().splitlines()]
    composable_events = [
        e for e in events if e["event"] == "export.psd.composable.created"
    ]
    assert len(composable_events) == 1
    payload = composable_events[0]["payload"]
    assert payload["tier"] == "Composable"
    assert payload["element_count"] == 2
    assert payload["layer_count"] == 3
    assert payload["dpi"] == 300
    assert payload["color_space"] == "CMYK"


# ──────────────────────────────────────────────────────────────────────
#  TEXT ELEMENTS (slice 10b)
# ──────────────────────────────────────────────────────────────────────


def test_compose_assemble_text_element_renders_via_pillow_not_openai(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Text elements must NOT hit gpt-image-2 — Pillow renders them.

    If the assemble endpoint mistakenly routes a text element to the
    image-gen path, this fails: the stub openai client tracks every call,
    and we assert it was only called for the graphic element.
    """
    call_log: list[str] = []

    async def _gen(**kwargs: Any) -> Any:
        call_log.append(str(kwargs.get("prompt", "")))

        class _D:
            def __init__(self, b64: str) -> None:
                self.b64_json = b64

        class _U:
            def model_dump(self) -> dict[str, Any]:
                return {"input_tokens": 100, "output_tokens": 100}

        class _R:
            def __init__(self) -> None:
                self.data = [_D(_transparent_png())]
                self.usage = _U()

        return _R()

    fake_openai.images.generate = _gen  # type: ignore[method-assign]

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])

    payload = {
        "source_asset_id": src_id,
        "quality": "medium",
        "elements": [
            {
                "name": "imara_wordmark",
                "label": "IMARA wordmark",
                "prompt": "IMARA wordmark in gold. Transparent. Isolated.",
                "position_mm": [16, 8, 43, 18],
                "size_px": "1024x1024",
                "kind": "wordmark",
            },
            {
                "name": "text_01",
                "label": "Headline",
                "prompt": "IMARA SANDALWOOD",
                "position_mm": [4, 60, 60, 12],
                "size_px": "1536x1024",
                "kind": "text",
                "text": "IMARA SANDALWOOD",
                "confidence": 96.5,
            },
        ],
    }
    # Clear any leftover prompts from the initial _generate() call —
    # we only care about the assemble-stage calls.
    call_log.clear()

    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/psd-composable",
        json=payload,
    )
    assert res.status_code == 201, res.text
    body = res.json()
    # Both elements get persisted + layered in.
    assert body["element_count"] == 2
    assert body["layer_count"] == 3  # base + 2

    # gpt-image-2 was only called for the graphic, not the text.
    image_gen_prompts = [p for p in call_log if "Transparent" in p or "wordmark" in p]
    assert len(image_gen_prompts) == 1
    assert all("SANDALWOOD" not in p for p in image_gen_prompts)


def test_compose_assemble_text_element_missing_content_returns_422(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a kind='text' element comes through without `text`, hard-fail."""
    _patch_image_gen(monkeypatch, fake_openai)

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])

    payload = {
        "source_asset_id": src_id,
        "elements": [
            {
                "name": "ghost_text",
                "label": "Empty text",
                "prompt": "irrelevant",
                "position_mm": [4, 60, 60, 12],
                "size_px": "1024x1024",
                "kind": "text",
                # text is intentionally missing
            },
        ],
    }
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/psd-composable",
        json=payload,
    )
    assert res.status_code == 422
    assert "ghost_text" in res.json()["detail"]


def test_compose_text_layer_name_carries_type_hint() -> None:
    """`_layer_name_for` encodes the text into the PSD layer name.

    Designers reading the layer panel see the actual content, and a
    future Photoshop script can parse the [type:"..."] suffix to
    auto-convert into a real type layer.
    """
    from app.services.compose import _layer_name_for
    from app.services.vision import ElementSpec

    spec = ElementSpec(
        name="text_01",
        label="Headline",
        prompt="IMARA SANDALWOOD",
        position_mm=(0, 0, 60, 12),
        size_px="1536x1024",
        kind="text",
        text="IMARA SANDALWOOD",
    )
    name = _layer_name_for(spec)
    assert name.startswith("text_01")
    assert '[type:"IMARA SANDALWOOD"]' in name


def test_compose_text_layer_name_truncates_long_text() -> None:
    """Long text is truncated at 60 chars with ellipsis in the layer name."""
    from app.services.compose import _layer_name_for
    from app.services.vision import ElementSpec

    long_text = "x" * 200
    spec = ElementSpec(
        name="text_blob",
        label="Body",
        prompt=long_text,
        position_mm=(0, 0, 60, 30),
        size_px="1024x1024",
        kind="text",
        text=long_text,
    )
    name = _layer_name_for(spec)
    assert "…" in name
    # Total length is bounded — name + space + [type:"...60..."]
    assert len(name) < 100


def test_compose_text_renders_visible_pixels() -> None:
    """The Pillow-rendered text element must produce non-empty alpha."""
    import io as _io

    from PIL import Image as _Image

    from app.services.compose import _render_text_element
    from app.services.vision import ElementSpec

    spec = ElementSpec(
        name="text_01",
        label="Headline",
        prompt="IMARA",
        position_mm=(0, 0, 40, 12),
        size_px="1024x1024",
        kind="text",
        text="IMARA",
    )
    elem = _render_text_element(spec)
    assert elem.cost_usd == 0.0
    assert elem.width_px == 1024
    assert elem.height_px == 1024
    # Decode the PNG and verify some pixels are opaque (the text glyphs).
    img = _Image.open(_io.BytesIO(elem.png_bytes)).convert("RGBA")
    alpha_bytes = img.getchannel("A").tobytes()
    opaque_count = sum(1 for a in alpha_bytes if a > 0)
    assert opaque_count > 100, "rendered text has no visible pixels"


def test_compose_layer_name_for_graphic_is_plain() -> None:
    """Non-text elements get just the slug as the layer name."""
    from app.services.compose import _layer_name_for
    from app.services.vision import ElementSpec

    spec = ElementSpec(
        name="imara_wordmark",
        label="IMARA wordmark",
        prompt="...",
        position_mm=(0, 0, 40, 12),
        size_px="1024x1024",
        kind="wordmark",
    )
    assert _layer_name_for(spec) == "imara_wordmark"


# ──────────────────────────────────────────────────────────────────────
#  SELECTIVE AUTO-VECTORIZATION (slice 10c)
# ──────────────────────────────────────────────────────────────────────


def test_should_vectorize_picks_line_art_kinds() -> None:
    """wordmark / ornament / seal get auto-vectorized by default.

    text is NEVER vectorized (rendered crisp by Pillow already).
    photo-realistic graphic stays raster unless vectorizable=True.
    """
    from app.services.compose import _should_vectorize
    from app.services.vision import ElementSpec

    def _spec(kind: str, vectorizable: bool = False) -> ElementSpec:
        return ElementSpec(
            name="x",
            label="x",
            prompt="x",
            position_mm=(0, 0, 1, 1),
            size_px="1024x1024",
            kind=kind,  # type: ignore[arg-type]
            vectorizable=vectorizable,
        )

    assert _should_vectorize(_spec("wordmark")) is True
    assert _should_vectorize(_spec("ornament")) is True
    assert _should_vectorize(_spec("seal")) is True
    assert _should_vectorize(_spec("text", vectorizable=True)) is False  # never
    assert _should_vectorize(_spec("graphic")) is False                  # default off
    assert _should_vectorize(_spec("graphic", vectorizable=True)) is True  # hint
    assert _should_vectorize(_spec("headline")) is False


async def test_maybe_vectorize_attaches_svg_for_line_art(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When enabled + a vectorizable element, the SVG is attached."""
    from app.services import compose as compose_module
    from app.services.compose import GeneratedElement, maybe_vectorize_element
    from app.services.vector import VectorResult
    from app.services.vision import ElementSpec

    captured: dict[str, Any] = {}

    async def _fake_vectorize(
        png_bytes: bytes, *, provider: str | None = None
    ) -> VectorResult:
        captured["bytes"] = len(png_bytes)
        captured["provider"] = provider
        return VectorResult(
            svg_bytes=b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><path d="M0,0 L10,10"/></svg>',
            provider="vectorizer_ai",
            mode="production",
            size_bytes=120,
        )

    monkeypatch.setattr(compose_module, "run_vectorize", _fake_vectorize)

    elem = GeneratedElement(
        spec=ElementSpec(
            name="imara_wordmark",
            label="IMARA",
            prompt="IMARA wordmark",
            position_mm=(0, 0, 40, 12),
            size_px="1024x1024",
            kind="wordmark",
        ),
        png_bytes=_transparent_png().encode() if False else b"\x89PNG\r\n",
        width_px=512,
        height_px=512,
        cost_usd=0.05,
    )

    result = await maybe_vectorize_element(elem, enabled=True)
    assert result.svg_bytes is not None
    assert b"<path" in result.svg_bytes
    # Production mode cost = $0.20
    assert result.vector_cost_usd == 0.20
    # The PNG and other fields are preserved.
    assert result.cost_usd == 0.05


async def test_maybe_vectorize_skips_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`enabled=False` short-circuits — vectorizer must not be called."""
    from app.services import compose as compose_module
    from app.services.compose import GeneratedElement, maybe_vectorize_element
    from app.services.vision import ElementSpec

    async def _no_call(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("vectorize must not be called when enabled=False")

    monkeypatch.setattr(compose_module, "run_vectorize", _no_call)

    elem = GeneratedElement(
        spec=ElementSpec(
            name="x",
            label="x",
            prompt="x",
            position_mm=(0, 0, 1, 1),
            size_px="1024x1024",
            kind="wordmark",
        ),
        png_bytes=b"\x89PNG",
        width_px=10,
        height_px=10,
        cost_usd=0.0,
    )
    result = await maybe_vectorize_element(elem, enabled=False)
    assert result.svg_bytes is None


async def test_maybe_vectorize_skips_text_elements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Text elements never get auto-vectorized (already crisp via Pillow)."""
    from app.services import compose as compose_module
    from app.services.compose import GeneratedElement, maybe_vectorize_element
    from app.services.vision import ElementSpec

    async def _no_call(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("text elements must not vectorize")

    monkeypatch.setattr(compose_module, "run_vectorize", _no_call)

    elem = GeneratedElement(
        spec=ElementSpec(
            name="text_01",
            label="Headline",
            prompt="IMARA",
            position_mm=(0, 0, 40, 12),
            size_px="1024x1024",
            kind="text",
            text="IMARA",
        ),
        png_bytes=b"\x89PNG",
        width_px=1024,
        height_px=1024,
        cost_usd=0.0,
    )
    result = await maybe_vectorize_element(elem, enabled=True)
    assert result.svg_bytes is None


async def test_maybe_vectorize_failure_falls_back_to_raster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If vectorize raises, the element is returned unchanged (raster only).

    A failed vector pass MUST NOT abort the whole assemble — the PSD
    still ships; the SVG just embeds the raster <image> for this layer.
    """
    from app.services import compose as compose_module
    from app.services.compose import GeneratedElement, maybe_vectorize_element
    from app.services.vision import ElementSpec

    async def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("vectorizer 503")

    monkeypatch.setattr(compose_module, "run_vectorize", _boom)

    elem = GeneratedElement(
        spec=ElementSpec(
            name="wordmark",
            label="W",
            prompt="W",
            position_mm=(0, 0, 1, 1),
            size_px="1024x1024",
            kind="wordmark",
        ),
        png_bytes=b"\x89PNG",
        width_px=10,
        height_px=10,
        cost_usd=0.0,
    )
    result = await maybe_vectorize_element(elem, enabled=True)
    assert result.svg_bytes is None
    assert result.vector_cost_usd == 0.0


def test_assemble_composable_svg_mixes_vector_and_raster(
    tmp_path: Path,
) -> None:
    """SVG composite carries vectorized elements as <g>, others as <image>."""
    from app.services.compose import GeneratedElement, assemble_composable_svg
    from app.services.vision import ElementSpec

    # Two elements: one with svg_bytes (line-art), one without (photo).
    vec_elem = GeneratedElement(
        spec=ElementSpec(
            name="imara_wordmark",
            label="IMARA",
            prompt="...",
            position_mm=(10, 5, 30, 15),
            size_px="1024x1024",
            kind="wordmark",
        ),
        png_bytes=b"PNG-vec",
        width_px=512,
        height_px=512,
        cost_usd=0.0,
        svg_bytes=(
            b'<?xml version="1.0"?>'
            b'<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512">'
            b'<path d="M0,0 L10,10" stroke="black"/></svg>'
        ),
    )
    img_b64 = _transparent_png(64, 64)
    raster_elem = GeneratedElement(
        spec=ElementSpec(
            name="sandalwood",
            label="Sandalwood",
            prompt="...",
            position_mm=(8, 30, 50, 40),
            size_px="1024x1024",
            kind="graphic",
            vectorizable=False,
        ),
        png_bytes=base64.b64decode(img_b64),
        width_px=64,
        height_px=64,
        cost_usd=0.0,
    )

    out = tmp_path / "composite.svg"
    result = assemble_composable_svg(
        elements=[vec_elem, raster_elem],
        trim_mm=(70.0, 100.0),
        bleed_mm=3.0,
        out_path=out,
    )
    assert result.vector_count == 1
    assert result.raster_count == 1
    assert result.width_mm == 76.0   # 70 + 2 × 3
    assert result.height_mm == 106.0  # 100 + 2 × 3
    assert out.is_file()

    body = out.read_text()
    # Vector path inlined inside a <g> with our transform.
    assert '<g id="imara_wordmark"' in body
    assert "translate(13.0000,8.0000)" in body  # trim x=10 + bleed 3
    assert 'd="M0,0 L10,10"' in body
    # Raster fallback for the photo element.
    assert '<image id="sandalwood"' in body
    assert "data:image/png;base64," in body
    # Top-level SVG header carries mm dimensions for press.
    assert 'width="76.0mm"' in body or 'width="76mm"' in body
    assert 'viewBox="0 0 76.0 106.0"' in body


def test_compose_assemble_produces_psd_and_svg_siblings(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slice 10c happy path: assemble produces BOTH a PSD AND a sibling SVG.

    SVG export is best-effort and shouldn't fail the PSD if vectorization
    glitches — but in the happy path we expect both to land on disk.
    """
    import json as _json

    _patch_image_gen(monkeypatch, fake_openai)

    # Stub vectorize to return a tiny valid SVG so the wordmark element
    # ends up in the SVG composite as a <g>, while sandalwood (raster)
    # ends up as <image>.
    from app.services import compose as compose_module
    from app.services.vector import VectorResult

    async def _fake_vectorize(
        png_bytes: bytes, *, provider: str | None = None
    ) -> VectorResult:
        return VectorResult(
            svg_bytes=(
                b'<?xml version="1.0"?>'
                b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
                b'<path d="M0,0 L1,1"/></svg>'
            ),
            provider="vectorizer_ai",
            mode="test",
            size_bytes=120,
        )

    monkeypatch.setattr(compose_module, "run_vectorize", _fake_vectorize)

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])

    payload = {
        "source_asset_id": src_id,
        "quality": "medium",
        "vectorize": True,
        "elements": [
            {
                "name": "imara_wordmark",
                "label": "IMARA wordmark",
                "prompt": "IMARA wordmark. Transparent. Isolated.",
                "position_mm": [16, 8, 43, 18],
                "size_px": "1024x1024",
                "kind": "wordmark",  # vectorize → True
            },
            {
                "name": "sandalwood",
                "label": "Botanical",
                "prompt": "Sandalwood leaves. Transparent. Isolated.",
                "position_mm": [12, 35, 51, 51],
                "size_px": "1024x1024",
                "kind": "graphic",  # default → no vector
            },
        ],
    }
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/psd-composable",
        json=payload,
    )
    assert res.status_code == 201, res.text
    body = res.json()

    # PSD lives where it always did.
    assert body["element_count"] == 2
    assert body["layer_count"] == 3
    # SVG sibling — both vector + raster present.
    assert body["svg_asset_id"] is not None
    assert body["svg_url"] is not None
    assert body["svg_vector_count"] == 1
    assert body["svg_raster_count"] == 1
    # Vectorizer.AI in test mode → $0.02 per element vectorized = $0.02 total.
    assert body["vector_cost_usd"] == pytest.approx(0.02, abs=0.001)

    # The audit JSONL has both events.
    audit_path = isolated_paths / "workspaces" / ws["slug"] / "audit.log.jsonl"
    events = [_json.loads(line)["event"] for line in audit_path.read_text().splitlines()]
    assert "export.psd.composable.created" in events
    assert "export.svg.composable.created" in events


def test_compose_assemble_skips_svg_step_when_vectorize_off(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """vectorize=False keeps SVG composite as all-raster (still produced)."""
    _patch_image_gen(monkeypatch, fake_openai)
    # Sentinel: vectorizer must not be called.
    from app.services import compose as compose_module

    async def _no_call(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("vectorize must not be called when vectorize=False")

    monkeypatch.setattr(compose_module, "run_vectorize", _no_call)

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])

    payload = {
        "source_asset_id": src_id,
        "quality": "medium",
        "vectorize": False,  # off
        "elements": [
            {
                "name": "imara_wordmark",
                "label": "IMARA wordmark",
                "prompt": "IMARA wordmark. Transparent. Isolated.",
                "position_mm": [16, 8, 43, 18],
                "size_px": "1024x1024",
                "kind": "wordmark",
            },
        ],
    }
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/psd-composable",
        json=payload,
    )
    assert res.status_code == 201, res.text
    body = res.json()
    # SVG is still produced (all raster); vector_count=0, raster_count=1.
    assert body["svg_vector_count"] == 0
    assert body["svg_raster_count"] == 1
    assert body["vector_cost_usd"] == 0.0
