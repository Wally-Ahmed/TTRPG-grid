"""Export layer (Phase B, step 5): manifest loading, grid overlay, metadata.

* :func:`load_manifest` reads ``assets/manifest.json`` (Contract 1) and stashes
  the asset root on it so :mod:`assemble` can resolve tile/prop paths. It is
  tolerant of a missing manifest (returns a minimal stub) so the engine can run
  before ``leg-assets`` lands.
* :func:`apply_grid_overlay` returns a *copy* of the gridless image with 1px grid
  lines at 30% black (60% for colorblind) drawn every ``px_per_square`` starting
  at (0, 0). The gridless image is never mutated.
* :func:`build_metadata` produces the Contract 6 metadata dict EXACTLY:
  foundry/owlbear blocks, ``global_illumination`` from ``indoor``, spawn_points,
  connections, set_id, floor_index.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from PIL import Image, ImageDraw

__all__ = ["load_manifest", "apply_grid_overlay", "build_metadata", "slugify"]


# --------------------------------------------------------------------------- #
# Manifest
# --------------------------------------------------------------------------- #
def load_manifest(assets_path: str = "assets") -> dict:
    """Load ``<assets_path>/manifest.json`` (Contract 1); stub if absent.

    The returned dict gains a private ``_root`` key = the asset directory, used
    by :mod:`assemble` to resolve relative tile/prop paths.
    """

    manifest_file = os.path.join(assets_path, "manifest.json")
    if os.path.isfile(manifest_file):
        with open(manifest_file, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    else:
        manifest = _stub_manifest()
    manifest["_root"] = assets_path
    manifest.setdefault("px_per_square", 140)
    manifest.setdefault("terrains", {})
    manifest.setdefault("props", {})
    return manifest


def _stub_manifest() -> dict:
    """Minimal manifest covering all 29 terrains with flat-colour fallback tiles
    and no props. Lets the pipeline run end-to-end before real assets exist."""

    from . import TERRAINS

    terrains = {}
    for t in TERRAINS:
        terrains[t] = {
            "display": t.replace("_", " ").title(),
            "walkable": t not in _STUB_BLOCKING and t not in _STUB_HAZARD,
            "blocking": t in _STUB_BLOCKING,
            "hazard": t in _STUB_HAZARD,
            "difficult": t in _STUB_DIFFICULT,
            "indoor": t in _STUB_INDOOR,
            "priority": _STUB_PRIORITY.get(t, 30),
            "tiles": [],  # empty -> assemble falls back to flat colour
        }
    return {"px_per_square": 140, "generator_seed": 7,
            "terrains": terrains, "props": {}}


_STUB_BLOCKING = {"stone_wall", "brick_wall", "wood_wall", "cave_wall",
                  "mountain_rock", "volcanic_rock"}
_STUB_HAZARD = {"lava", "water_deep", "pit"}
_STUB_DIFFICULT = {"mud", "rubble", "swamp", "tall_grass", "snow"}
_STUB_INDOOR = {"stone_floor", "wood_floor", "cave_floor", "marble_floor", "cobblestone"}
# Higher priority draws over lower at boundaries (walls/water on top of ground).
_STUB_PRIORITY = {
    "grass": 20, "tall_grass": 22, "dirt": 18, "sand": 20, "snow": 24,
    "water_shallow": 40, "water_deep": 42, "river": 41, "lava": 45,
    "road": 30, "cobblestone": 30, "rubble": 34, "mud": 24,
    "stone_wall": 60, "brick_wall": 60, "wood_wall": 58, "cave_wall": 60,
    "mountain_rock": 55, "volcanic_rock": 52, "pit": 44,
}


# --------------------------------------------------------------------------- #
# Grid overlay
# --------------------------------------------------------------------------- #
def apply_grid_overlay(gridless: Image.Image, px_per_square: int,
                       palette_mode: str = "standard") -> Image.Image:
    """Return a COPY of ``gridless`` with 1px grid lines baked in.

    Lines are black at 30% opacity (60% for colorblind), drawn every
    ``px_per_square`` px starting at (0, 0). The line at the far edge is included
    so the last row/column is bounded.
    """

    img = gridless.convert("RGBA").copy()
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    alpha = 153 if palette_mode == "colorblind" else 77  # 60% vs 30% of 255
    color = (0, 0, 0, alpha)
    w, h = img.size

    px = max(1, int(px_per_square))
    for x in range(0, w + 1, px):
        gx = min(x, w - 1)
        d.line([(gx, 0), (gx, h - 1)], fill=color, width=1)
    for y in range(0, h + 1, px):
        gy = min(y, h - 1)
        d.line([(0, gy), (w - 1, gy)], fill=color, width=1)

    img.alpha_composite(overlay)
    return img


# --------------------------------------------------------------------------- #
# Metadata (Contract 6)
# --------------------------------------------------------------------------- #
def build_metadata(spec, layout: dict, image_size: tuple[int, int], *,
                   set_id=None, floor_index: int = 0) -> dict:
    """Build the Contract-6 metadata sidecar dict EXACTLY.

    ``global_illumination`` is true when ``layout['indoor'] == False`` (outdoor
    lit scenes default GI on; indoor dark scenes default off).
    """

    width, height = int(image_size[0]), int(image_size[1])
    cols = int(layout["cols"])
    rows = int(layout["rows"])
    px = int(spec.px_per_square)
    feet = int(spec.feet_per_square)
    indoor = bool(layout.get("indoor", False))
    global_illum = not indoor

    spawns = [
        {"x": int(s["x"]), "y": int(s["y"]), "role": s["role"]}
        for s in layout.get("spawns", [])
    ]
    connections = [
        {"x": int(c["x"]), "y": int(c["y"]), "kind": c["kind"],
         "to_floor": c.get("to_floor")}
        for c in layout.get("connections", [])
    ]

    return {
        "title": spec.title or _fallback_title(spec),
        "prompt": spec.prompt,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "grid": {
            "columns": cols, "rows": rows,
            "px_per_square": px, "feet_per_square": feet,
        },
        "image": {"width": width, "height": height},
        "foundry": {
            "grid_size": px,
            "scene_width": width,
            "scene_height": height,
            "grid_type": "Square",
            "global_illumination": global_illum,
        },
        "owlbear": {
            "grid_size_px": px,
            "grid_type": "Square",
            "cell_size_ft": feet,
            "grid_columns": cols,
            "grid_rows": rows,
        },
        "seed": int(layout.get("seed", spec.seed)),
        "density": float(spec.density),
        "mood": spec.mood,
        "biomes": list(spec.biomes),
        "spawn_points": spawns,
        "connections": connections,
        "set_id": set_id,
        "floor_index": int(floor_index),
    }


def _fallback_title(spec) -> str:
    if spec.prompt.strip():
        return spec.prompt.strip()[:60]
    return "Untitled Map"


# --------------------------------------------------------------------------- #
# Slug (Contract 6 naming)
# --------------------------------------------------------------------------- #
def slugify(title: str) -> str:
    """title -> slug: lowercase, ``[^a-z0-9]+`` -> ``-``, trimmed, <=60 chars."""

    import re

    s = (title or "").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:60] or "map"
