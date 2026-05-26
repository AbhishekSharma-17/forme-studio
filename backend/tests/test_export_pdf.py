"""Tests for the print PDF/X-4 export endpoint."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.stubs import StubOpenAIClient

# 1 mm in PostScript points (1/72") — keep parity with reportlab.lib.units.mm.
_MM_PT = 72.0 / 25.4

# /TrimBox /BleedBox /MediaBox in PDF dictionaries are written as plain ASCII
# (they live in the page/catalog dict, not the compressed content stream), so
# we can pull them straight out of the raw bytes.
_BOX_RE = re.compile(
    rb"/(?P<name>TrimBox|BleedBox|MediaBox)\s*\[\s*(?P<vals>[-0-9.\s]+)\]"
)


def _parse_boxes(raw: bytes) -> dict[str, tuple[float, ...]]:
    """Extract page-box rectangles from raw PDF bytes."""
    boxes: dict[str, tuple[float, ...]] = {}
    for m in _BOX_RE.finditer(raw):
        name = m.group("name").decode()
        nums = tuple(float(v) for v in m.group("vals").split())
        # First write wins — page-level boxes appear before any later
        # objects that might reference the same name.
        boxes.setdefault(name, nums)
    return boxes


def _create_workspace(client: TestClient) -> dict[str, object]:
    res = client.post(
        "/api/packaging/workspaces",
        json={"name": "PDF Test", "product_type": "lotion_bottle_label"},
    )
    assert res.status_code == 201, res.text
    return res.json()


def _generate(client: TestClient, slug: str) -> int:
    res = client.post(
        f"/api/packaging/workspaces/{slug}/generate",
        json={"prompt": "PDF source variant", "n": 1, "quality": "high"},
    )
    assert res.status_code == 200
    return int(res.json()["assets"][0]["id"])


def test_pdf_export_writes_trim_and_bleed_boxes(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Point ICC at the test tmp dir so no ICC is found — we still expect
    # a valid PDF, just without an OutputIntent.
    fake_icc = isolated_paths / "missing.icc"
    monkeypatch.setenv("FORME_PRINT_ICC_PATH", str(fake_icc))
    import app.config as config_module
    config_module._settings = None

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])

    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/pdf",
        json={"source_asset_id": src_id, "dpi": 300},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["trim_mm"] == {"w": 70.0, "h": 100.0}
    assert body["bleed_mm"] == 3.0
    assert body["trim_marks"] is True
    assert body["registration_marks"] is True
    assert body["icc_embedded"] is False  # no ICC at fake path
    assert body["icc_profile"].startswith("Pillow baseline")

    pdf_path = (
        isolated_paths / "workspaces" / ws["slug"] / body["asset"]["relative_path"]
    )
    assert pdf_path.is_file()
    raw = pdf_path.read_bytes()
    assert raw.startswith(b"%PDF-")

    # The press cares about the actual coordinates, not just that the keys
    # were spelled correctly — assert exact values derived from the lotion
    # bottle preset (70 × 100 mm trim + 3 mm bleed → 76 × 106 mm media).
    boxes = _parse_boxes(raw)
    assert {"TrimBox", "BleedBox", "MediaBox"} <= boxes.keys(), boxes

    bleed_pt = 3.0 * _MM_PT
    trim_w_pt = 70.0 * _MM_PT
    trim_h_pt = 100.0 * _MM_PT
    media_w_pt = 76.0 * _MM_PT
    media_h_pt = 106.0 * _MM_PT

    assert boxes["TrimBox"] == pytest.approx(
        (bleed_pt, bleed_pt, bleed_pt + trim_w_pt, bleed_pt + trim_h_pt),
        abs=1e-2,
    )
    assert boxes["BleedBox"] == pytest.approx(
        (0.0, 0.0, media_w_pt, media_h_pt), abs=1e-2
    )
    assert boxes["MediaBox"] == pytest.approx(
        (0.0, 0.0, media_w_pt, media_h_pt), abs=1e-2
    )
    # TrimBox is strictly inside BleedBox/MediaBox (sanity on inset direction).
    assert boxes["TrimBox"][0] > boxes["BleedBox"][0]
    assert boxes["TrimBox"][2] < boxes["BleedBox"][2]

    audit = (isolated_paths / "workspaces" / ws["slug"] / "audit.log.jsonl").read_text()
    events = [json.loads(line)["event"] for line in audit.strip().splitlines()]
    assert "export.pdf.created" in events


def test_pdf_export_embeds_icc_when_present(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the configured ICC file exists, the PDF carries an OutputIntent."""
    macos_icc = "/System/Library/ColorSync/Profiles/Generic CMYK Profile.icc"
    if not Path(macos_icc).is_file():
        pytest.skip("macOS Generic CMYK ICC not available in this environment.")

    monkeypatch.setenv("FORME_PRINT_ICC_PATH", macos_icc)
    monkeypatch.setenv("FORME_PRINT_ICC_NAME", "Generic CMYK")
    import app.config as config_module
    config_module._settings = None

    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])

    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/pdf",
        json={"source_asset_id": src_id},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["icc_embedded"] is True

    pdf_path = (
        isolated_paths / "workspaces" / ws["slug"] / body["asset"]["relative_path"]
    )
    raw = pdf_path.read_bytes()
    assert b"/OutputIntent" in raw
    assert b"/GTS_PDFX" in raw


def test_pdf_export_marks_can_be_disabled(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
) -> None:
    """Toggling marks must actually change PDF content, not just echo flags."""
    ws = _create_workspace(client)
    src_id = _generate(client, ws["slug"])
    base = f"/api/packaging/workspaces/{ws['slug']}/exports/pdf"
    ws_root = isolated_paths / "workspaces" / ws["slug"]

    def _post(trim: bool, reg: bool) -> bytes:
        res = client.post(
            base,
            json={
                "source_asset_id": src_id,
                "trim_marks": trim,
                "registration_marks": reg,
            },
        )
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["trim_marks"] is trim
        assert body["registration_marks"] is reg
        return (ws_root / body["asset"]["relative_path"]).read_bytes()

    both_on = _post(True, True)
    both_off = _post(False, False)
    trim_only = _post(True, False)
    reg_only = _post(False, True)

    # Page boxes must be identical regardless of marks (the press tolerates
    # marks outside the trim, but trim/bleed/media stay anchored to specs).
    for raw in (both_on, both_off, trim_only, reg_only):
        boxes = _parse_boxes(raw)
        assert {"TrimBox", "BleedBox", "MediaBox"} <= boxes.keys()

    # Each toggle must produce a measurably different PDF — bytes-wise *and*
    # bigger than the marks-off baseline (drawing strokes only adds content).
    assert both_on != both_off
    assert trim_only != both_off
    assert reg_only != both_off
    assert len(both_on) > len(both_off)
    assert len(trim_only) > len(both_off)
    assert len(reg_only) > len(both_off)
    # Both marks together draw more than either alone.
    assert len(both_on) >= len(trim_only)
    assert len(both_on) >= len(reg_only)


def test_pdf_export_rejects_unknown_source(
    client: TestClient, fake_openai: StubOpenAIClient
) -> None:
    ws = _create_workspace(client)
    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/pdf",
        json={"source_asset_id": 99999},
    )
    assert res.status_code == 422
    assert "does not belong" in res.json()["detail"]


def test_pdf_export_rejects_reference_kind(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
) -> None:
    import io

    from PIL import Image

    ws = _create_workspace(client)
    buf = io.BytesIO()
    Image.new("RGB", (256, 256), (0, 0, 0)).save(buf, format="PNG")
    ref = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/references",
        files=[("files", ("logo.png", buf.getvalue(), "image/png"))],
    ).json()["references"][0]

    res = client.post(
        f"/api/packaging/workspaces/{ws['slug']}/exports/pdf",
        json={"source_asset_id": ref["id"]},
    )
    assert res.status_code == 422
    assert "Can only export 'generation'" in res.json()["detail"]


def test_extend_with_mirror_bleed_preserves_trim_region_and_mirrors_edges() -> None:
    """Regression test for the image-stretch bug fix.

    The bleed extension helper must:
    * return a new image whose dimensions are ``(w + 2*bx, h + 2*by)``,
    * leave the trim region (the inner ``w × h``) pixel-identical to the
      source so the design never warps inside the trim,
    * fill the side strips by mirroring the outer columns (left↔right),
    * fill the top/bottom strips by flipping the outer rows (top↔bottom).
    """
    from PIL import Image

    from app.services.export_pdf import _extend_with_mirror_bleed

    # Distinctive colour bands so mirrored regions can be checked pixel-wise.
    w, h = 200, 300
    img = Image.new("RGB", (w, h), (255, 255, 255))
    # Make the leftmost column red, rightmost blue, top green, bottom yellow.
    for y in range(h):
        img.putpixel((0, y), (255, 0, 0))
        img.putpixel((w - 1, y), (0, 0, 255))
    for x in range(w):
        img.putpixel((x, 0), (0, 255, 0))
        img.putpixel((x, h - 1), (255, 255, 0))

    bx, by = 20, 30
    out = _extend_with_mirror_bleed(img, bx, by)

    # 1. Size grows by 2*bleed on each axis.
    assert out.size == (w + 2 * bx, h + 2 * by)

    # 2. Trim region — exact pixel match against the source (no resampling,
    #    no stretching). This is the bug we fixed: previously the source was
    #    drawn at media-aspect, distorting every pixel.
    trim = out.crop((bx, by, bx + w, by + h))
    assert trim.tobytes() == img.tobytes()

    # 3. Left strip's outer column came from mirroring the original's left
    #    edge — so the column at x=bx-1 (just outside trim) must equal the
    #    original x=0 column (red).
    assert out.getpixel((bx - 1, by + h // 2)) == (255, 0, 0)
    # And the very-outer column of the left strip equals the original x=bx-1
    # column (white), because ImageOps.mirror flips the bx-wide slice.
    assert out.getpixel((0, by + h // 2)) == (255, 255, 255)

    # 4. Right strip's inner column mirrors the original's right edge (blue).
    assert out.getpixel((bx + w, by + h // 2)) == (0, 0, 255)

    # 5. Top strip flipped from top edge: the row just above the trim should
    #    be green (mirror of original top row).
    assert out.getpixel((bx + w // 2, by - 1)) == (0, 255, 0)

    # 6. Bottom strip flipped from bottom edge: row just below trim is
    #    yellow.
    assert out.getpixel((bx + w // 2, by + h)) == (255, 255, 0)


def test_extend_with_mirror_bleed_passthrough_when_zero() -> None:
    """Zero bleed on both axes returns the same image without copying."""
    from PIL import Image

    from app.services.export_pdf import _extend_with_mirror_bleed

    img = Image.new("RGB", (32, 32), (128, 128, 128))
    out = _extend_with_mirror_bleed(img, 0, 0)
    assert out is img
