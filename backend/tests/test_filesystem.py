"""Unit tests for filesystem.slugify + workspace_root + ensure_workspace_dir.

slugify is the canonical "name → URL-safe folder name" function and
shows up in every workspace path. Bugs here are wide-blast-radius —
broken slugs mean broken folders mean broken exports.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.filesystem import (
    WORKSPACE_SUBDIRS,
    ensure_workspace_dir,
    slugify,
    workspace_exists_on_disk,
    workspace_root,
)

# --------------------------------------------------------------- slugify


@pytest.mark.parametrize(
    "name,expected",
    [
        # Happy path: simple ASCII title
        ("Glow Serenity Lotion", "glow-serenity-lotion"),
        # Trailing/leading whitespace + punctuation get stripped to hyphens
        ("  Aurora! Vitamin-C Serum 30ml  ", "aurora-vitamin-c-serum-30ml"),
        # Multiple consecutive disallowed chars collapse to single hyphen
        ("foo!!!@@@bar", "foo-bar"),
        # Existing hyphens preserved (a-z/0-9/- are the allowed set)
        ("lotion-bottle-label-v2", "lotion-bottle-label-v2"),
        # Uppercase + numbers handled
        ("SKU-42 BLUE", "sku-42-blue"),
        # Unicode → all stripped to hyphens (we don't transliterate)
        ("café résumé", "caf-r-sum"),
        # All-disallowed → fallback to "workspace"
        ("!!!@@@", "workspace"),
        ("", "workspace"),
        ("   ", "workspace"),
        # Hyphens at edges trimmed
        ("--foo--", "foo"),
    ],
)
def test_slugify_canonical_cases(name: str, expected: str) -> None:
    assert slugify(name) == expected


def test_slugify_caps_length_at_80() -> None:
    """Very long names get truncated so they fit as folder/URL segments."""
    s = slugify("a" * 200)
    assert len(s) == 80
    assert s == "a" * 80


def test_slugify_is_idempotent() -> None:
    """slugify(slugify(x)) == slugify(x) — composing is safe."""
    for name in ("Glow Serenity!", "  -foo-bar-  ", "SKU-42 BLUE"):
        once = slugify(name)
        twice = slugify(once)
        assert once == twice, f"{name!r}: {once!r} != {twice!r}"


def test_slugify_lowercases_aggressively() -> None:
    assert slugify("AaAa BbBb") == "aaaa-bbbb"


# ------------------------------------------------------- workspace_root + ensure


def test_workspace_root_uses_configured_dir(isolated_paths: Path) -> None:
    """``workspace_root`` joins the configured root with the slug — no IO."""
    root = workspace_root("glow-serenity")
    assert root == isolated_paths / "workspaces" / "glow-serenity"
    # Pure path operation — must NOT actually create the directory.
    assert not root.exists()


def test_ensure_workspace_dir_creates_tree_and_brief(isolated_paths: Path) -> None:
    """First call creates root + all three subdirs + brief.md template."""
    root = ensure_workspace_dir("aurora-test")
    assert root.is_dir()
    for sub in WORKSPACE_SUBDIRS:
        assert (root / sub).is_dir(), f"missing subdir: {sub}"
    brief = root / "brief.md"
    assert brief.is_file()
    text = brief.read_text(encoding="utf-8")
    assert "aurora-test" in text  # slug is in the template
    assert "design brief" in text.lower()


def test_ensure_workspace_dir_idempotent(isolated_paths: Path) -> None:
    """Second call must NOT clobber an existing brief.md."""
    root = ensure_workspace_dir("idempotent-slug")
    brief = root / "brief.md"
    original_text = "# Custom brief written by the user\n\nProduct copy here."
    brief.write_text(original_text, encoding="utf-8")

    # Call again — should leave the file untouched.
    again = ensure_workspace_dir("idempotent-slug")
    assert again == root
    assert brief.read_text(encoding="utf-8") == original_text


def test_workspace_exists_on_disk_reflects_filesystem(isolated_paths: Path) -> None:
    assert workspace_exists_on_disk("never-created") is False
    ensure_workspace_dir("now-exists")
    assert workspace_exists_on_disk("now-exists") is True
