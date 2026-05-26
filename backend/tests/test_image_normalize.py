"""Unit tests for image_normalize.normalize.

Every reference upload goes through this — bad behaviour here breaks
generation runs with confusing OpenAI errors. We pin the contract:
RGBA PNG out, EXIF rotation baked, ≤ 3840 px on the long edge.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.services.image_normalize import MAX_EDGE_PX, NormalizeError, normalize


def _png_bytes(
    size: tuple[int, int] = (32, 32),
    color: tuple[int, int, int] = (200, 80, 60),
    mode: str = "RGB",
) -> bytes:
    """Build a minimal in-memory image for testing."""
    img = Image.new(mode, size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes(
    size: tuple[int, int] = (32, 32),
    color: tuple[int, int, int] = (10, 20, 30),
) -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


# ----------------------------------------------------------- happy paths


def test_normalize_returns_rgba_png_for_rgb_input() -> None:
    """RGB input → RGBA PNG out with correct dims + mime."""
    raw = _png_bytes(size=(100, 200), mode="RGB")
    result = normalize("input.png", raw)
    assert result.mime_type == "image/png"
    assert result.filename == "input.png"
    assert result.width == 100
    assert result.height == 200

    # Verify the output actually decodes as RGBA
    with Image.open(io.BytesIO(result.data)) as out:
        assert out.mode == "RGBA"
        assert out.size == (100, 200)


def test_normalize_converts_jpeg_to_rgba_png() -> None:
    """JPEG input → RGBA PNG output (format normalization)."""
    raw = _jpeg_bytes(size=(50, 80))
    result = normalize("photo.jpg", raw)
    assert result.filename == "photo.png"  # extension swapped
    assert result.mime_type == "image/png"

    with Image.open(io.BytesIO(result.data)) as out:
        # PNG signature in first 8 bytes
        assert result.data[:8] == b"\x89PNG\r\n\x1a\n"
        assert out.mode == "RGBA"


def test_normalize_strips_filename_path_to_stem() -> None:
    """A full path or weird filename collapses to the bare stem + .png."""
    raw = _png_bytes()
    result = normalize("/some/weird/path/MY IMAGE (1).png", raw)
    # Pathlib's .stem on the original keeps the spaces + parens; we don't
    # slugify the filename (only the workspace slug). Just verify .png.
    assert result.filename.endswith(".png")


def test_normalize_empty_filename_falls_back_to_image() -> None:
    raw = _png_bytes()
    result = normalize("", raw)
    assert result.filename == "image.png"


# ------------------------------------------------------- size / scaling


def test_normalize_passes_small_images_through_unscaled() -> None:
    """An image well under the 3840 px cap keeps its original dimensions."""
    raw = _png_bytes(size=(1024, 1536))
    result = normalize("ok.png", raw)
    assert result.width == 1024
    assert result.height == 1536


def test_normalize_downscales_oversized_landscape() -> None:
    """Long edge > MAX_EDGE_PX → resized so long edge == MAX_EDGE_PX."""
    # Use a deliberately oversized aspect (4500 × 2000) — long edge is 4500.
    raw = _png_bytes(size=(4500, 2000))
    result = normalize("huge.png", raw)
    assert result.width == MAX_EDGE_PX  # 3840
    # Height scales proportionally: 2000 * (3840/4500) = 1706.66 → 1706
    assert result.height == pytest.approx(1706, abs=2)
    # Aspect ratio approximately preserved
    original_aspect = 4500 / 2000
    new_aspect = result.width / result.height
    assert abs(new_aspect - original_aspect) < 0.01


def test_normalize_downscales_oversized_portrait() -> None:
    """Same as above but the long edge is the height."""
    raw = _png_bytes(size=(2000, 5000))
    result = normalize("huge_portrait.png", raw)
    assert result.height == MAX_EDGE_PX
    assert result.width == pytest.approx(1536, abs=2)


def test_normalize_never_produces_zero_dimensions() -> None:
    """The clamp guards against ``int(scale * tiny)`` collapsing to 0."""
    # An ultra-wide near-1px input: long edge = MAX_EDGE_PX + 1, short edge = 1
    raw = _png_bytes(size=(MAX_EDGE_PX + 1, 1))
    result = normalize("razor.png", raw)
    assert result.width == MAX_EDGE_PX
    assert result.height >= 1  # the max(1, ...) clamp kicks in


# ---------------------------------------------------------- error paths


def test_normalize_rejects_non_image_bytes() -> None:
    """Random bytes shouldn't crash with PIL's underlying exceptions —
    NormalizeError must wrap them all."""
    with pytest.raises(NormalizeError) as excinfo:
        normalize("garbage.png", b"not an image at all, just plain bytes")
    assert "Could not read image" in str(excinfo.value)
    assert "garbage.png" in str(excinfo.value)


def test_normalize_rejects_empty_bytes() -> None:
    with pytest.raises(NormalizeError):
        normalize("empty.png", b"")


def test_normalize_rejects_truncated_png() -> None:
    """A PNG header without the rest of the file should also raise."""
    truncated = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20  # signature + a few zero bytes
    with pytest.raises(NormalizeError):
        normalize("truncated.png", truncated)


# --------------------------------------------------- EXIF orientation


def test_normalize_bakes_exif_rotation_into_pixels() -> None:
    """iPhone JPEGs rotate via EXIF; gpt-image-2 ignores EXIF, so we must
    apply the rotation to the actual pixel data before re-encoding.

    Build a 100×200 (portrait) image with EXIF orientation 6 (= rotate 90°
    CW on display). After normalize, the output should be 200×100 because
    the rotation is baked in.
    """
    img = Image.new("RGB", (100, 200), (50, 100, 150))
    buf = io.BytesIO()
    # EXIF orientation tag = 6 (rotate 90° CW)
    exif = img.getexif()
    exif[0x0112] = 6  # Orientation
    img.save(buf, format="JPEG", exif=exif)

    result = normalize("rotated.jpg", buf.getvalue())
    # After EXIF transpose with orientation=6, the 100×200 becomes 200×100.
    assert result.width == 200
    assert result.height == 100

    # Verify the output PNG has NO EXIF (we strip metadata by re-encoding).
    with Image.open(io.BytesIO(result.data)) as out:
        out_exif = out.getexif()
        # PNG can carry EXIF but our save() doesn't pass it through;
        # the orientation field specifically must be absent.
        assert 0x0112 not in out_exif
