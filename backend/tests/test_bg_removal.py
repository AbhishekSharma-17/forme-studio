"""Unit tests for ``app.services.bg_removal``.

Hermetic — we never actually load the u2net ONNX model in tests. Instead
we monkey-patch rembg's ``remove`` / ``new_session`` so the orchestration
code paths (session caching, fallback to Pillow, async-to-thread bridge)
are exercised without the 176 MB download.

The real rembg call is exercised in the live smoke run.
"""

from __future__ import annotations

import asyncio
import io
import sys
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from PIL import Image

import app.services.bg_removal as bg


def _opaque_png(size: tuple[int, int] = (32, 32), color: tuple[int, int, int] = (255, 255, 255)) -> bytes:
    """Build a fully opaque PNG to feed the bg-removal pipeline."""
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _two_zone_png(size: tuple[int, int] = (32, 32)) -> bytes:
    """Build a PNG with a near-white border + dark centre.

    Used to verify the Pillow luminance-keying fallback removes the white
    border while keeping the dark centre opaque.
    """
    img = Image.new("RGB", size, (250, 250, 250))  # near-white backdrop
    # Paint a 12×12 dark square in the middle.
    inner = Image.new("RGB", (12, 12), (40, 40, 40))
    img.paste(inner, ((size[0] - 12) // 2, (size[1] - 12) // 2))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _reset_bg_module_state() -> None:
    """Tests share module state via ``_rembg_session`` / ``_rembg_unavailable``."""
    bg._rembg_session = None
    bg._rembg_unavailable = False
    yield
    bg._rembg_session = None
    bg._rembg_unavailable = False


async def test_remove_background_uses_rembg_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """When rembg.new_session + rembg.remove succeed, we use their output."""
    sentinel_session = object()
    sentinel_out = b"RGBA-from-rembg"

    fake_module = SimpleNamespace(
        new_session=lambda _name: sentinel_session,
        remove=lambda _data, session=None: (
            sentinel_out if session is sentinel_session else b"WRONG"
        ),
    )
    monkeypatch.setitem(sys.modules, "rembg", fake_module)

    out = await bg.remove_background(_opaque_png())
    assert out == sentinel_out


async def test_remove_background_falls_back_when_rembg_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing rembg import → Pillow luminance keying takes over."""

    # Force the rembg import to fail.
    import builtins

    real_import = builtins.__import__

    def _fail_rembg(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "rembg" or name.startswith("rembg."):
            raise ImportError("rembg not installed (simulated)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fail_rembg)

    out = await bg.remove_background(_two_zone_png())
    # Verify alpha keying actually happened — the corner should be transparent
    # and the dark centre should remain opaque.
    img = Image.open(io.BytesIO(out)).convert("RGBA")
    a = np.array(img)[:, :, 3]
    assert a[0, 0] == 0, "corner (near-white) should be keyed to transparent"
    cx, cy = img.size[0] // 2, img.size[1] // 2
    assert a[cy, cx] == 255, "dark centre should remain opaque"


async def test_remove_background_falls_back_when_rembg_remove_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """rembg session builds fine but the remove call blows up → Pillow keying."""

    def _boom(_data: bytes, session: Any = None) -> bytes:
        raise RuntimeError("onnx kaput")

    fake_module = SimpleNamespace(
        new_session=lambda _name: object(),
        remove=_boom,
    )
    monkeypatch.setitem(sys.modules, "rembg", fake_module)

    out = await bg.remove_background(_two_zone_png())
    img = Image.open(io.BytesIO(out)).convert("RGBA")
    a = np.array(img)[:, :, 3]
    # Pillow keying ran → corner transparent.
    assert a[0, 0] == 0


async def test_rembg_session_is_cached_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Once new_session succeeds we don't rebuild it on subsequent calls."""
    new_session_calls = {"count": 0}

    def _counting_new_session(_name: str) -> object:
        new_session_calls["count"] += 1
        return object()

    fake_module = SimpleNamespace(
        new_session=_counting_new_session,
        remove=lambda _data, session=None: b"matte",
    )
    monkeypatch.setitem(sys.modules, "rembg", fake_module)

    out1 = await bg.remove_background(_opaque_png())
    out2 = await bg.remove_background(_opaque_png())
    out3 = await bg.remove_background(_opaque_png())

    assert out1 == out2 == out3 == b"matte"
    assert new_session_calls["count"] == 1, (
        f"expected new_session to be called once, got {new_session_calls['count']}"
    )


def test_pillow_luminance_key_handles_corrupt_input() -> None:
    """Garbage in → garbage out, but no exception (defensive)."""
    garbage = b"\x00\x01\x02NOT-A-PNG"
    out = bg._pillow_luminance_key(garbage)
    # Falls back to returning the input unchanged.
    assert out == garbage


async def test_remove_background_is_async() -> None:
    """The public API is awaitable — the rembg call is offloaded to a thread."""
    coro = bg.remove_background(_opaque_png())
    assert asyncio.iscoroutine(coro)
    # Drain so the test doesn't leak the coroutine.
    coro.close()
