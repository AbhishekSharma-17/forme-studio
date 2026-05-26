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
      "kind": "wordmark"
    },
    {
      "name": "sandalwood_botanical",
      "label": "Sandalwood + saffron botanical",
      "prompt": "Sandalwood leaves intertwined with saffron strands in gold line-art. Transparent background. Isolated. No other elements.",
      "position_mm": [12, 35, 51, 51],
      "size_px": "1024x1024",
      "kind": "graphic"
    },
    {
      "name": "ingredients_block",
      "label": "Ingredients body copy",
      "prompt": "(handled by OCR)",
      "position_mm": [4, 100, 67, 20],
      "size_px": "1024x1024",
      "kind": "body_copy"
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
    """Happy path: stub vision → endpoint returns the parsed manifest."""
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
    assert len(body["elements"]) == 3
    names = {e["name"] for e in body["elements"]}
    assert names == {"imara_wordmark", "sandalwood_botanical", "ingredients_block"}
    # body_copy elements DO appear in the manifest so the UI knows about
    # them (they're skipped during assembly, not discovery).
    body_copies = [e for e in body["elements"] if e["kind"] == "body_copy"]
    assert len(body_copies) == 1


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
    assert "body_copy" in res.json()["detail"]


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
