"""End-to-end tests for the packaging workspaces endpoints.

Covers the slice-1 contract:

  * create + list workspaces (happy path)
  * duplicate slug returns 409
  * unknown product_type returns 422
  * folder + audit JSONL mirror land on disk

Each test uses an isolated SQLite + workspaces dir via the ``client`` fixture.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient


def _create(client: TestClient, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "name": "Test Lotion 250 ml",
        "product_type": "lotion_bottle_label",
        "description": "unit test fixture",
    }
    body.update(overrides)
    res = client.post("/api/packaging/workspaces", json=body)
    return res.json()


def test_create_workspace_persists_db_folder_and_audit(
    client: TestClient, isolated_paths: Path
) -> None:
    res = client.post(
        "/api/packaging/workspaces",
        json={"name": "Glow Lotion", "product_type": "lotion_bottle_label"},
    )
    assert res.status_code == 201, res.text
    ws = res.json()

    # Slug derived from name, specs frozen from preset.
    assert ws["slug"] == "glow-lotion"
    assert ws["module"] == "packaging"
    assert ws["product_type"] == "lotion_bottle_label"
    assert ws["specs"]["trim_mm"] == {"w": 70.0, "h": 100.0}
    assert ws["specs"]["bleed_mm"] == 3.0
    assert ws["specs"]["dpi"] == 300
    assert ws["specs"]["color_space"] == "CMYK"

    # Folder tree exists on disk.
    folder = isolated_paths / "workspaces" / "glow-lotion"
    assert folder.is_dir()
    assert (folder / "references").is_dir()
    assert (folder / "generations").is_dir()
    assert (folder / "exports").is_dir()
    assert (folder / "brief.md").is_file()

    # Audit JSONL mirror written.
    audit_path = folder / "audit.log.jsonl"
    assert audit_path.is_file()
    line = json.loads(audit_path.read_text().strip().splitlines()[0])
    assert line["event"] == "workspace.created"
    assert line["payload"]["product_type"] == "lotion_bottle_label"


def test_list_workspaces_orders_newest_first(client: TestClient) -> None:
    _create(client, name="First Lotion")
    _create(client, name="Second Lotion")

    res = client.get("/api/packaging/workspaces")
    assert res.status_code == 200
    names = [w["name"] for w in res.json()]
    assert names == ["Second Lotion", "First Lotion"]


def test_duplicate_slug_returns_409(client: TestClient) -> None:
    _create(client, name="Same Name")
    res = client.post(
        "/api/packaging/workspaces",
        json={"name": "Same Name", "product_type": "lotion_bottle_label"},
    )
    assert res.status_code == 409
    assert "already exists" in res.json()["detail"]


def test_unknown_product_type_returns_422(client: TestClient) -> None:
    res = client.post(
        "/api/packaging/workspaces",
        json={"name": "Bad", "product_type": "no_such_thing"},
    )
    assert res.status_code == 422
    assert "Unknown product_type" in res.json()["detail"]


def test_get_unknown_workspace_returns_404(client: TestClient) -> None:
    res = client.get("/api/packaging/workspaces/never-existed")
    assert res.status_code == 404


def test_get_workspace_round_trip(client: TestClient) -> None:
    created = _create(client, name="Round Trip", product_type="cream_jar_label")
    res = client.get(f"/api/packaging/workspaces/{created['slug']}")
    assert res.status_code == 200
    fetched = res.json()
    assert fetched["id"] == created["id"]
    assert fetched["product_type"] == "cream_jar_label"
    assert fetched["specs"]["trim_mm"] == {"w": 60.0, "h": 60.0}
    # Slice 10d — design_mode defaults to False.
    assert fetched["design_mode"] is False


def test_create_workspace_with_design_mode_true(
    client: TestClient, isolated_paths: Path
) -> None:
    """Slice 10d: design_mode=true round-trips through create + get."""
    res = client.post(
        "/api/packaging/workspaces",
        json={
            "name": "Brainstorm Test",
            "product_type": "lotion_bottle_label",
            "design_mode": True,
        },
    )
    assert res.status_code == 201, res.text
    ws = res.json()
    assert ws["design_mode"] is True

    # Audit row carries the flag.
    audit_path = isolated_paths / "workspaces" / ws["slug"] / "audit.log.jsonl"
    events = [json.loads(line) for line in audit_path.read_text().splitlines()]
    created = next(e for e in events if e["event"] == "workspace.created")
    assert created["payload"]["design_mode"] is True


def test_patch_workspace_flips_design_mode(
    client: TestClient, isolated_paths: Path
) -> None:
    """The PATCH route flips design_mode and writes a workspace.updated audit."""
    ws = _create(client, name="Patch Target")
    assert ws["design_mode"] is False

    res = client.patch(
        f"/api/packaging/workspaces/{ws['slug']}",
        json={"design_mode": True},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["design_mode"] is True

    # Audit captures the diff with from/to values.
    audit_path = isolated_paths / "workspaces" / ws["slug"] / "audit.log.jsonl"
    events = [json.loads(line) for line in audit_path.read_text().splitlines()]
    updates = [e for e in events if e["event"] == "workspace.updated"]
    assert len(updates) == 1
    assert updates[0]["payload"]["changed"] == {
        "design_mode": {"from": False, "to": True},
    }


def test_patch_workspace_name_and_description(
    client: TestClient, isolated_paths: Path
) -> None:
    """Multiple fields can be updated at once; specs stay frozen."""
    ws = _create(client, name="Original Name", description="original desc")
    original_specs = ws["specs"]

    res = client.patch(
        f"/api/packaging/workspaces/{ws['slug']}",
        json={"name": "New Name", "description": "new desc"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["name"] == "New Name"
    assert body["description"] == "new desc"
    # Frozen specs unaffected.
    assert body["specs"] == original_specs


def test_patch_workspace_no_op_returns_unchanged(client: TestClient) -> None:
    """An empty PATCH (no changes) returns the workspace without auditing."""
    ws = _create(client, name="No-Op")
    res = client.patch(
        f"/api/packaging/workspaces/{ws['slug']}",
        json={"design_mode": False},  # already False
    )
    assert res.status_code == 200
    assert res.json()["design_mode"] is False


def test_patch_workspace_rejects_unknown_field(client: TestClient) -> None:
    """Specs and other frozen fields are NOT mutable through PATCH."""
    ws = _create(client, name="Frozen Test")
    # `product_type` is intentionally not in WorkspaceUpdate; pydantic
    # quietly ignores unknown fields by default. Verify the field stays.
    res = client.patch(
        f"/api/packaging/workspaces/{ws['slug']}",
        json={"product_type": "cream_jar_label"},
    )
    assert res.status_code == 200
    assert res.json()["product_type"] == ws["product_type"]


def test_presets_endpoint_lists_all_five(client: TestClient) -> None:
    res = client.get("/api/packaging/presets")
    assert res.status_code == 200
    ids = {p["id"] for p in res.json()}
    assert ids == {
        "lotion_bottle_label",
        "cream_jar_label",
        "cream_box_tuck_end",
        "serum_dropper_label",
        "shampoo_pouch",
    }
