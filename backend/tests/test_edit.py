"""Tests for the workspace edit + stream endpoint.

Drives the SSE flow end-to-end against a stubbed OpenAI client. Verifies:

* SDK call shape (image= argument carries the right files in the right order).
* Output is persisted as a new generation Asset under generations/.
* ``asset.edited`` audit row is written with the edit-chain payload.
* Cost from usage tokens flows through to the final ``cost`` SSE event.
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image

from tests.stubs import TINY_PNG_B64, StubOpenAIClient


def _create_workspace(client: TestClient) -> dict[str, Any]:
    res = client.post(
        "/api/packaging/workspaces",
        json={"name": "Edit Test", "product_type": "lotion_bottle_label"},
    )
    assert res.status_code == 201
    return res.json()


def _png_bytes(size: tuple[int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 80, 40)).save(buf, format="PNG")
    return buf.getvalue()


def _generate_one(client: TestClient, slug: str) -> int:
    res = client.post(
        f"/api/packaging/workspaces/{slug}/generate",
        json={"prompt": "base", "n": 1, "quality": "high"},
    )
    assert res.status_code == 200, res.text
    return int(res.json()["assets"][0]["id"])


def _upload_reference(client: TestClient, slug: str) -> int:
    res = client.post(
        f"/api/packaging/workspaces/{slug}/references",
        files=[("files", ("logo.png", _png_bytes((128, 128)), "image/png"))],
    )
    assert res.status_code == 201, res.text
    return int(res.json()["references"][0]["id"])


def _drain_sse(client: TestClient, url: str, body: dict[str, Any]) -> list[dict[str, Any]]:
    """POST and read every SSE event, returning a list of {event, data} dicts."""
    events: list[dict[str, Any]] = []
    with client.stream("POST", url, json=body) as res:
        assert res.status_code == 200, res.read().decode()
        buffer = ""
        for chunk in res.iter_text():
            buffer += chunk
            while "\n\n" in buffer:
                frame, buffer = buffer.split("\n\n", 1)
                evt = "message"
                data_lines: list[str] = []
                for line in frame.splitlines():
                    if line.startswith("event:"):
                        evt = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].strip())
                if data_lines:
                    events.append({"event": evt, "data": json.loads("\n".join(data_lines))})
    return events


def test_edit_stream_passes_base_and_references_to_sdk(
    client: TestClient, isolated_paths: Path, fake_openai: StubOpenAIClient
) -> None:
    ws = _create_workspace(client)
    base_id = _generate_one(client, ws["slug"])
    ref_id = _upload_reference(client, ws["slug"])

    fake_openai.images.edit_calls.clear()
    events = _drain_sse(
        client,
        f"/api/packaging/workspaces/{ws['slug']}/edit/stream",
        {
            "prompt": "Recolour the background to sage",
            "base_asset_id": base_id,
            "reference_asset_ids": [ref_id],
            "n": 1,
            "quality": "high",
        },
    )

    # SSE produced the expected event types in order.
    types = [e["event"] for e in events]
    assert "partial" in types
    assert "asset" in types
    assert "cost" in types
    assert types[-1] == "done"

    # SDK was called with two files (base + reference) in that order.
    calls: list[dict[str, Any]] = fake_openai.images.edit_calls
    assert len(calls) == 1
    image_arg = calls[0]["image"]
    assert isinstance(image_arg, list)
    assert len(image_arg) == 2
    # Each is a (filename, bytes, mime) tuple
    assert all(isinstance(t, tuple) and len(t) == 3 for t in image_arg)
    # The base PNG bytes we just generated match the stub's TINY_PNG_B64
    expected = base64.b64decode(TINY_PNG_B64)
    assert image_arg[0][1] == expected

    # Cost event reported a non-zero provider cost
    cost_event = next(e for e in events if e["event"] == "cost")
    assert cost_event["data"]["provider_cost_usd"] > 0
    assert cost_event["data"]["edit_chain"] == [base_id, ref_id]


def test_edit_stream_persists_new_asset_and_writes_audit(
    client: TestClient, isolated_paths: Path, fake_openai: StubOpenAIClient
) -> None:
    ws = _create_workspace(client)
    base_id = _generate_one(client, ws["slug"])

    _drain_sse(
        client,
        f"/api/packaging/workspaces/{ws['slug']}/edit/stream",
        {
            "prompt": "Subtle warmer tone, keep typography",
            "base_asset_id": base_id,
            "n": 1,
            "quality": "high",
        },
    )

    # Listing should now show 2 generations (the original base + the edit output).
    list_res = client.get(f"/api/packaging/workspaces/{ws['slug']}/assets?kind=generation")
    assert list_res.status_code == 200
    assert len(list_res.json()) == 2

    # File on disk for the new asset
    gen_dir = isolated_paths / "workspaces" / ws["slug"] / "generations"
    assert len(list(gen_dir.glob("*.png"))) == 2

    # asset.edited row is in audit.log.jsonl
    audit = (isolated_paths / "workspaces" / ws["slug"] / "audit.log.jsonl").read_text()
    assert "asset.edited" in audit
    # Find the asset.edited line and confirm edit_of references the base
    edit_lines = [
        json.loads(line)
        for line in audit.strip().splitlines()
        if json.loads(line)["event"] == "asset.edited"
    ]
    assert len(edit_lines) == 1
    assert edit_lines[0]["payload"]["edit_of"] == base_id


def test_edit_stream_rejects_cross_workspace_asset(
    client: TestClient, fake_openai: StubOpenAIClient
) -> None:
    """Asset IDs must belong to the target workspace."""
    ws_a = _create_workspace(client)
    base_a = _generate_one(client, ws_a["slug"])

    res = client.post(
        "/api/packaging/workspaces",
        json={"name": "Other", "product_type": "lotion_bottle_label"},
    )
    ws_b = res.json()

    bad = client.post(
        f"/api/packaging/workspaces/{ws_b['slug']}/edit/stream",
        json={"prompt": "delta", "base_asset_id": base_a, "n": 1, "quality": "high"},
    )
    assert bad.status_code == 422
    assert "does not belong" in bad.json()["detail"]


def test_edit_stream_404_when_workspace_unknown(
    client: TestClient, fake_openai: StubOpenAIClient
) -> None:
    res = client.post(
        "/api/packaging/workspaces/no-such-slug/edit/stream",
        json={"prompt": "delta", "base_asset_id": 1, "n": 1, "quality": "high"},
    )
    assert res.status_code == 404
