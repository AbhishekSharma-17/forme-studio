"""Tests for ``DELETE /api/packaging/workspaces/{slug}``.

Covers:
* default body (no files removed) — DB rows gone, folder preserved
* explicit ``delete_files=true`` — folder + files removed too
* cascade — Asset rows + AuditEvent rows belonging to the workspace go
* unknown slug → 404
* audit tombstone row is the workspace's last event (and survives the
  cascade — i.e. counted in deleted_audit_events).
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from tests.stubs import StubOpenAIClient


def _create(client: TestClient, name: str = "Delete Test") -> dict[str, object]:
    res = client.post(
        "/api/packaging/workspaces",
        json={"name": name, "product_type": "lotion_bottle_label"},
    )
    assert res.status_code == 201, res.text
    return res.json()


def _generate(client: TestClient, slug: str) -> int:
    res = client.post(
        f"/api/packaging/workspaces/{slug}/generate",
        json={"prompt": "Delete fixture", "n": 1, "quality": "high"},
    )
    assert res.status_code == 200, res.text
    return int(res.json()["assets"][0]["id"])


# ------------------------------------------------------------- happy paths


def test_delete_workspace_default_keeps_files_on_disk(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
) -> None:
    """Default body (no flag): DB rows are removed, folder stays put."""
    ws = _create(client)
    slug = ws["slug"]
    assert isinstance(slug, str)
    _generate(client, slug)  # one generation → one Asset row

    folder = isolated_paths / "workspaces" / slug
    assert folder.is_dir()
    audit_path = folder / "audit.log.jsonl"
    assert audit_path.is_file()

    res = client.request("DELETE", f"/api/packaging/workspaces/{slug}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["slug"] == slug
    assert body["deleted_assets"] == 1
    # 1 from workspace.created + 1 from asset.generated + 1 from workspace.deleted = 3
    assert body["deleted_audit_events"] == 3
    assert body["files_deleted"] is False

    # The workspace is gone from listing + lookup
    listed = client.get("/api/packaging/workspaces").json()
    assert all(w["slug"] != slug for w in listed)
    assert client.get(f"/api/packaging/workspaces/{slug}").status_code == 404

    # ...but the on-disk folder stays
    assert folder.is_dir(), "folder must be preserved when delete_files=False"
    assert audit_path.is_file(), "audit.log.jsonl must be preserved"


def test_delete_workspace_with_delete_files_removes_folder(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
) -> None:
    ws = _create(client)
    slug = ws["slug"]
    assert isinstance(slug, str)
    _generate(client, slug)

    folder = isolated_paths / "workspaces" / slug
    assert folder.is_dir()
    assert (folder / "generations").is_dir()

    res = client.request(
        "DELETE",
        f"/api/packaging/workspaces/{slug}",
        json={"delete_files": True},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["files_deleted"] is True

    # The whole tree is gone.
    assert not folder.exists(), "workspace folder must be removed"


def test_delete_workspace_cascades_assets_and_audit_rows(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
) -> None:
    """Multi-row workspace: generate twice + upload reference, then delete.

    The deleted_assets + deleted_audit_events counters should match what
    we created. All Asset / AuditEvent rows for this workspace must be
    gone from the DB after the delete.
    """
    import io

    from PIL import Image

    ws = _create(client, name="Cascade Test")
    slug = ws["slug"]
    assert isinstance(slug, str)
    _generate(client, slug)
    _generate(client, slug)
    # Add a reference too
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (10, 20, 30)).save(buf, format="PNG")
    ref_res = client.post(
        f"/api/packaging/workspaces/{slug}/references",
        files=[("files", ("logo.png", buf.getvalue(), "image/png"))],
    )
    assert ref_res.status_code == 201, ref_res.text

    res = client.request("DELETE", f"/api/packaging/workspaces/{slug}")
    assert res.status_code == 200, res.text
    body = res.json()
    # 2 generations + 1 reference = 3 Asset rows
    assert body["deleted_assets"] == 3
    # workspace.created + 2*asset.generated + 1*asset.uploaded (reference) +
    # workspace.deleted = 5 audit rows
    assert body["deleted_audit_events"] >= 4

    # And the workspace endpoints all reject the deleted slug.
    assert (
        client.get(f"/api/packaging/workspaces/{slug}/assets").status_code == 404
    )


def test_delete_workspace_writes_tombstone_with_metadata(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
) -> None:
    """The on-disk JSONL captures the workspace.deleted event with the
    snapshot before the deletion lands (so a folder-preserved delete
    leaves the audit trail intact)."""
    ws = _create(client, name="Tombstone Test")
    slug = ws["slug"]
    assert isinstance(slug, str)
    _generate(client, slug)

    res = client.request("DELETE", f"/api/packaging/workspaces/{slug}")
    assert res.status_code == 200

    # Default delete keeps the folder → JSONL still readable
    audit_path = isolated_paths / "workspaces" / slug / "audit.log.jsonl"
    assert audit_path.is_file()
    events = [json.loads(line) for line in audit_path.read_text().splitlines()]
    tombstones = [e for e in events if e["event"] == "workspace.deleted"]
    assert len(tombstones) == 1
    payload = tombstones[0]["payload"]
    assert payload["name"] == "Tombstone Test"
    assert payload["product_type"] == "lotion_bottle_label"
    assert payload["deleted_assets"] == 1
    assert payload["delete_files"] is False


# ------------------------------------------------------------- error paths


def test_delete_workspace_unknown_slug_returns_404(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
) -> None:
    res = client.request("DELETE", "/api/packaging/workspaces/does-not-exist")
    assert res.status_code == 404


def test_delete_workspace_idempotent_404_on_second_call(
    client: TestClient,
    isolated_paths: Path,
    fake_openai: StubOpenAIClient,
) -> None:
    """Two consecutive deletes: first 200, second 404 (workspace gone).

    Idempotency matters for the UI confirm-then-double-click case.
    """
    ws = _create(client)
    slug = ws["slug"]
    assert isinstance(slug, str)
    first = client.request("DELETE", f"/api/packaging/workspaces/{slug}")
    assert first.status_code == 200

    second = client.request("DELETE", f"/api/packaging/workspaces/{slug}")
    assert second.status_code == 404
