"""Shared pytest fixtures for engine and backend tests.

Engine tests use a minimal Contract-1-conforming stub asset directory
(synthesised in tmp) so geometry/determinism/pixel-math can be tested without
the real art. Backend tests use small Contract-shaped fake engine objects so
the API and library layers can be exercised in isolation on tmpdirs.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest
from PIL import Image

# Make the repo root importable (so `import server.*` works from tests).
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from server.engine import TERRAINS  # noqa: E402

# ---------------------------------------------------------------------------
# Engine-test fixtures: stub Contract-1 asset library
# ---------------------------------------------------------------------------

NATIVE = 140

# Per-terrain flat colour + semantic flags for the stub manifest.
_BLOCKING = {"stone_wall", "brick_wall", "wood_wall", "cave_wall",
             "mountain_rock", "volcanic_rock"}
_HAZARD = {"lava", "water_deep", "pit"}
_DIFFICULT = {"mud", "rubble", "swamp", "tall_grass", "snow"}
_INDOOR = {"stone_floor", "wood_floor", "cave_floor", "marble_floor", "cobblestone"}
_PRIORITY = {
    "grass": 20, "tall_grass": 22, "dirt": 18, "sand": 20, "snow": 24,
    "water_shallow": 40, "water_deep": 42, "river": 41, "lava": 45,
    "road": 30, "cobblestone": 30, "rubble": 34, "mud": 24,
    "stone_wall": 60, "brick_wall": 60, "wood_wall": 58, "cave_wall": 60,
    "mountain_rock": 55, "volcanic_rock": 52, "pit": 44,
}

# A representative colour per terrain so variant tiles are visibly distinct.
_COLORS = {
    "grass": (86, 125, 70), "tall_grass": (70, 110, 58),
    "forest_floor": (74, 96, 60), "jungle_floor": (58, 92, 52),
    "dirt": (120, 96, 68), "road": (140, 126, 104),
    "cobblestone": (128, 128, 120), "sand": (206, 188, 140),
    "snow": (232, 236, 242), "ice": (198, 218, 232),
    "swamp": (78, 92, 66), "mud": (96, 80, 60),
    "water_shallow": (96, 150, 176), "water_deep": (48, 92, 140),
    "river": (80, 132, 168), "lava": (200, 84, 40),
    "volcanic_rock": (64, 56, 56), "stone_floor": (150, 148, 142),
    "wood_floor": (150, 116, 78), "cave_floor": (110, 102, 92),
    "mountain_rock": (108, 104, 100), "stone_wall": (92, 90, 86),
    "brick_wall": (120, 78, 66), "wood_wall": (104, 76, 50),
    "cave_wall": (72, 68, 62), "rubble": (128, 118, 104),
    "ship_deck": (146, 112, 74), "marble_floor": (222, 218, 210),
    "pit": (24, 22, 26),
}

# Stub props: (name, category, footprint, blocking, biomes, rotatable, variants).
_PROPS = [
    ("rock", "cover", [1, 1], True, ["forest", "cave", "mountain", "grassland"], True, 3),
    ("crate", "cover", [1, 1], True, ["dungeon", "village", "ship", "mine"], True, 2),
    ("low_wall", "cover", [2, 1], True, ["dungeon", "village", "fortress"], True, 2),
    ("table", "furniture", [2, 1], True, ["tavern", "building", "dungeon"], True, 2),
    ("chair", "furniture", [1, 1], False, ["tavern", "building"], True, 2),
    ("tree_pine", "nature", [1, 1], True, ["forest", "snow", "mountain"], False, 3),
    ("bush", "nature", [1, 1], False, ["forest", "grassland", "jungle"], False, 2),
    ("spike_trap", "hazard", [1, 1], False, ["dungeon", "crypt"], False, 1),
    ("altar", "focal", [2, 2], True, ["temple", "crypt", "dungeon", "fey"], False, 1),
    ("well", "focal", [2, 2], True, ["village", "grassland", "market"], False, 1),
    ("campfire", "focal", [1, 1], False, ["camp", "forest", "grassland"], False, 1),
    ("statue", "focal", [1, 1], True, ["temple", "crypt", "fortress"], False, 2),
    ("door", "structure", [1, 1], False, ["dungeon", "building", "tower"], True, 1),
    ("stairs_up", "connection", [1, 1], False, ["dungeon", "tower", "building", "cave"], True, 1),
    ("stairs_down", "connection", [1, 1], False, ["dungeon", "tower", "building", "cave"], True, 1),
    ("ladder", "connection", [1, 1], False, ["mine", "cave", "ship"], True, 1),
    ("trapdoor", "connection", [1, 1], False, ["dungeon", "building", "tavern"], False, 1),
]


def _solid_tile(path: str, color, tag: int) -> None:
    """Write a 140x140 RGBA tile of ``color`` with a tiny per-variant marker so
    the different variants are not byte-identical (exercises variant selection)."""

    img = Image.new("RGBA", (NATIVE, NATIVE), color + (255,))
    # A 2px corner swatch shifted per variant, so variant N differs from N+1.
    px = img.load()
    ox = (tag * 5) % (NATIVE - 4)
    for yy in range(2):
        for xx in range(2):
            px[ox + xx, yy] = (255, 255, 255, 255)
    img.save(path)


def _prop_sprite(path: str, w: int, h: int, color, tag: int) -> None:
    img = Image.new("RGBA", (w * NATIVE, h * NATIVE), (0, 0, 0, 0))
    px = img.load()
    pad = NATIVE // 6
    for yy in range(pad, h * NATIVE - pad):
        for xx in range(pad, w * NATIVE - pad):
            px[xx, yy] = color + (235,)
    # variant marker
    px[pad + (tag % 5), pad] = (255, 255, 255, 255)
    img.save(path)


@pytest.fixture(scope="session")
def stub_assets(tmp_path_factory):
    """Create a stub asset dir + manifest; return its path (str)."""

    root = tmp_path_factory.mktemp("assets")
    tiles_dir = root / "tiles"
    props_dir = root / "props"
    tiles_dir.mkdir()
    props_dir.mkdir()

    terrains = {}
    for t in TERRAINS:
        tdir = tiles_dir / t
        tdir.mkdir()
        variants = []
        n_variants = 3
        for i in range(1, n_variants + 1):
            rel = f"tiles/{t}/{t}_{i:02d}.png"
            _solid_tile(str(root / rel), _COLORS[t], i)
            variants.append(rel)
        terrains[t] = {
            "display": t.replace("_", " ").title(),
            "walkable": t not in _BLOCKING and t not in _HAZARD,
            "blocking": t in _BLOCKING,
            "hazard": t in _HAZARD,
            "difficult": t in _DIFFICULT,
            "indoor": t in _INDOOR,
            "priority": _PRIORITY.get(t, 30),
            "tiles": variants,
        }

    props = {}
    for (name, cat, fp, blocking, biomes, rotatable, nvar) in _PROPS:
        pdir = props_dir / name
        pdir.mkdir()
        variants = []
        color = (150, 120, 80)
        for i in range(1, nvar + 1):
            rel = f"props/{name}/{name}_{i:02d}.png"
            _prop_sprite(str(root / rel), fp[0], fp[1], color, i)
            variants.append(rel)
        props[name] = {
            "display": name.replace("_", " ").title(),
            "category": cat,
            "footprint": fp,
            "blocking": blocking,
            "biomes": biomes,
            "variants": variants,
            "rotatable": rotatable,
        }

    manifest = {
        "px_per_square": NATIVE,
        "generator_seed": 7,
        "terrains": terrains,
        "props": props,
    }
    with open(root / "manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)

    return str(root)


@pytest.fixture(scope="session")
def stub_manifest(stub_assets):
    from server.engine.export import load_manifest
    return load_manifest(stub_assets)


# ---------------------------------------------------------------------------
# Backend-test fixtures: Contract-shaped fake engine + tmp library
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Contract-2 layout + Contract-3 MapResult fakes
# ---------------------------------------------------------------------------


def make_layout(cols=6, rows=4, seed=123456789, *, biomes=None, title="Test Map",
                prompt="a test map", density=0.5, mood="neutral",
                px_per_square=140, feet_per_square=5, palette_mode="standard"):
    """A small but Contract-2-valid layout dict."""
    biomes = biomes if biomes is not None else ["dungeon"]
    cells = [["grass" for _ in range(cols)] for _ in range(rows)]
    # a couple of blocking cells so edits have something to toggle
    if rows > 1 and cols > 1:
        cells[0][0] = "stone_wall"
    return {
        "cols": cols,
        "rows": rows,
        "seed": seed,
        "spec": {
            "prompt": prompt,
            "cols": cols,
            "rows": rows,
            "px_per_square": px_per_square,
            "feet_per_square": feet_per_square,
            "seed": seed,
            "density": density,
            "mood": mood,
            "biomes": biomes,
            "palette_mode": palette_mode,
            "title": title,
        },
        "cells": cells,
        "props": [
            {"id": "p001", "type": "crate", "x": 1, "y": 1, "rot": 0, "variant": 0},
            {"id": "p002", "type": "barrel", "x": 2, "y": 2, "rot": 90, "variant": 1},
        ],
        "focal": {"x": cols // 2, "y": rows // 2, "prop_id": "p001", "kind": "altar"},
        "spawns": [{"x": 0, "y": rows - 1, "role": "player"}],
        "connections": [],
        "indoor": True,
        "walls": [],
    }


def make_metadata(layout):
    """A small Contract-6-shaped metadata dict derived from a layout."""
    spec = layout["spec"]
    cols, rows = layout["cols"], layout["rows"]
    px = spec["px_per_square"]
    width, height = cols * px, rows * px
    indoor = layout.get("indoor", True)
    return {
        "title": spec.get("title", "Test Map"),
        "prompt": spec.get("prompt", ""),
        "generated_at": "2026-07-13T00:00:00+00:00",
        "grid": {
            "columns": cols,
            "rows": rows,
            "px_per_square": px,
            "feet_per_square": spec["feet_per_square"],
        },
        "image": {"width": width, "height": height},
        "foundry": {
            "grid_size": px,
            "scene_width": width,
            "scene_height": height,
            "grid_type": "Square",
            "global_illumination": not indoor,
        },
        "owlbear": {
            "grid_size_px": px,
            "grid_type": "Square",
            "cell_size_ft": spec["feet_per_square"],
            "grid_columns": cols,
            "grid_rows": rows,
        },
        "seed": layout["seed"],
        "density": spec["density"],
        "mood": spec["mood"],
        "biomes": spec["biomes"],
        "spawn_points": layout.get("spawns", []),
        "connections": layout.get("connections", []),
        "set_id": None,
        "floor_index": 0,
    }


def make_result(layout=None, *, px=140):
    """A fake MapResult with tiny real PIL images (so PNG/JPEG/thumb work)."""
    if layout is None:
        layout = make_layout()
    cols, rows = layout["cols"], layout["rows"]
    # Keep the fake images small (2px/square) so tests stay fast — width/height in
    # metadata still describe the "real" resolution.
    scale = 2
    gridless = Image.new("RGB", (cols * scale, rows * scale), (60, 120, 60))
    gridded = Image.new("RGB", (cols * scale, rows * scale), (80, 140, 80))
    return SimpleNamespace(
        layout=layout,
        gridless=gridless,
        gridded=gridded,
        metadata=make_metadata(layout),
        warnings=[],
    )


# ---------------------------------------------------------------------------
# Fake engine module (Contract 3 surface)
# ---------------------------------------------------------------------------


@dataclass
class FakeSpec:
    prompt: str = ""
    cols: int = 6
    rows: int = 4
    px_per_square: int = 140
    feet_per_square: int = 5
    seed: int = 0
    density: float = 0.5
    mood: str = "neutral"
    biomes: list = field(default_factory=lambda: ["dungeon"])
    structure: str = None
    features: list = field(default_factory=list)
    multi_level: bool = False
    levels: int = 1
    palette_mode: str = "standard"
    title: str = ""


class FakeEngine:
    """A stand-in for ``server.engine`` implementing the Contract-3 surface."""

    GenerationSpec = FakeSpec

    @staticmethod
    def parse_prompt(text, overrides=None):
        spec = FakeSpec(prompt=text)
        if "cave" in (text or "").lower():
            spec.biomes = ["cave"]
        for k, v in (overrides or {}).items():
            setattr(spec, k, v)
        # keep title sensible
        if not spec.title and text:
            spec.title = text[:40]
        return spec

    @staticmethod
    def generate_map(spec):
        layout = make_layout(
            cols=spec.cols,
            rows=spec.rows,
            seed=spec.seed,
            biomes=list(spec.biomes),
            title=spec.title or spec.prompt or "Test Map",
            prompt=spec.prompt,
            density=spec.density,
            mood=spec.mood,
            px_per_square=spec.px_per_square,
            feet_per_square=spec.feet_per_square,
            palette_mode=spec.palette_mode,
        )
        result = make_result(layout)
        result.warnings = ["fake warning: procedural fallback in use"]
        return result

    @staticmethod
    def generate_level_set(spec):
        results = []
        levels = max(1, spec.levels)
        for i in range(levels):
            sub = FakeSpec(**{**spec.__dict__})
            sub.seed = spec.seed + i
            sub.title = f"{spec.title or spec.prompt or 'Floor'} - Floor {i}"
            results.append(FakeEngine.generate_map(sub))
        return results

    @staticmethod
    def assemble_from_layout(layout, palette_mode="standard"):
        return make_result(layout)

    @staticmethod
    def estimate_size(cols, rows, px):
        width, height = cols * px, rows * px
        mp = (width * height) / 1_000_000.0
        est_bytes = int(width * height * 0.5)
        warnings = []
        if mp > 50:
            warnings.append("map exceeds 50 megapixels")
        if est_bytes > 20 * 1024 * 1024:
            warnings.append("estimated file exceeds 20 MB")
        return {
            "width": width,
            "height": height,
            "megapixels": round(mp, 2),
            "est_bytes": est_bytes,
            "warnings": warnings,
        }


@pytest.fixture()
def fake_engine():
    return FakeEngine


@pytest.fixture()
def library_root(tmp_path):
    return str(tmp_path / "library-data")


@pytest.fixture()
def library(library_root):
    from server.library import Library

    return Library(library_root)


@pytest.fixture()
def app(library, monkeypatch):
    from server import app as app_mod

    # Point the generation routes at the fake engine.
    monkeypatch.setattr(app_mod, "_get_engine", lambda: FakeEngine)
    flask_app = app_mod.create_app(library=library)
    flask_app.config.update(TESTING=True)
    return flask_app


@pytest.fixture()
def client(app):
    return app.test_client()
