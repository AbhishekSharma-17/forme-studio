"""Packaging product-type presets.

A preset bundles the print specs we freeze onto every workspace of that
type: trim size, bleed, DPI, color space, and a baseline generation aspect.
Adding a new product type is a one-line addition to ``PRESETS``.
"""

from __future__ import annotations

from typing import Any, TypedDict


class PackagingPreset(TypedDict):
    """Print-ready spec bundle for a packaging product type."""

    id: str
    label: str            # human-friendly name for UI
    description: str
    # Trim size in millimetres (width × height).
    trim_mm: tuple[float, float]
    bleed_mm: float
    dpi: int
    color_space: str      # "CMYK" | "RGB"
    # Best aspect ratio to ask gpt-image-2 for. We then crop/extend later.
    generation_size: str  # one of gpt-image-2 native sizes
    notes: str


PRESETS: dict[str, PackagingPreset] = {
    "lotion_bottle_label": {
        "id": "lotion_bottle_label",
        "label": "Lotion bottle label (250 ml)",
        "description": "Wrap-around label for a 250 ml cosmetic lotion bottle.",
        "trim_mm": (70.0, 100.0),
        "bleed_mm": 3.0,
        "dpi": 300,
        "color_space": "CMYK",
        "generation_size": "1024x1536",
        "notes": "Portrait label. Leave 4 mm safety margin around copy.",
    },
    "cream_jar_label": {
        "id": "cream_jar_label",
        "label": "Cream jar top label (50 g)",
        "description": "Circular top label for a 50 g cream jar.",
        "trim_mm": (60.0, 60.0),
        "bleed_mm": 3.0,
        "dpi": 300,
        "color_space": "CMYK",
        "generation_size": "1024x1024",
        "notes": "Square crop; vector ornaments work best around the rim.",
    },
    "cream_box_tuck_end": {
        "id": "cream_box_tuck_end",
        "label": "Cream box (tuck-end carton, 50 ml)",
        "description": "Folding carton for a 50 ml cream tube. 5-panel dieline.",
        "trim_mm": (140.0, 50.0),  # main front panel only — dieline expanded later
        "bleed_mm": 3.0,
        "dpi": 300,
        "color_space": "CMYK",
        "generation_size": "1536x1024",
        "notes": "Front panel only at generation time; dieline assembly later.",
    },
    "serum_dropper_label": {
        "id": "serum_dropper_label",
        "label": "Serum dropper bottle label (30 ml)",
        "description": "Narrow wrap label for a 30 ml dropper bottle.",
        "trim_mm": (50.0, 80.0),
        "bleed_mm": 3.0,
        "dpi": 300,
        "color_space": "CMYK",
        "generation_size": "1024x1536",
        "notes": "Tall narrow ratio — copy must be short and centered.",
    },
    "shampoo_pouch": {
        "id": "shampoo_pouch",
        "label": "Shampoo sachet pouch (10 ml)",
        "description": "Single-use flat sachet for shampoo/conditioner.",
        "trim_mm": (90.0, 120.0),
        "bleed_mm": 3.0,
        "dpi": 300,
        "color_space": "CMYK",
        "generation_size": "1024x1536",
        "notes": "Two-sided printing; this preset covers the front face.",
    },
}


def list_presets() -> list[PackagingPreset]:
    return list(PRESETS.values())


def get_preset(preset_id: str) -> PackagingPreset | None:
    return PRESETS.get(preset_id)


def preset_to_specs(preset: PackagingPreset) -> dict[str, Any]:
    """Project a preset into the workspace ``specs`` JSON blob.

    The DB stores this snapshot so the workspace is decoupled from later
    edits to the preset table (frozen specs for compliance/audit).
    """
    trim_w, trim_h = preset["trim_mm"]
    return {
        "preset_id": preset["id"],
        "trim_mm": {"w": trim_w, "h": trim_h},
        "bleed_mm": preset["bleed_mm"],
        "dpi": preset["dpi"],
        "color_space": preset["color_space"],
        "generation_size": preset["generation_size"],
        "notes": preset["notes"],
    }
