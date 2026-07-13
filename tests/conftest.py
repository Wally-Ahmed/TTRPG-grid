"""Shared pytest fixtures for the engine tests.

The real ``assets/`` tile library is produced in parallel by ``leg-assets``. To
test the engine in isolation we synthesise a *minimal but Contract-1-conforming*
stub asset directory in a tmp path: all 29 terrains (each with a couple of plain
coloured 140x140 PNG variants) plus a handful of props across every category
(1x1 and 2x2, some rotatable), each with real RGBA PNG variants. Plain colours
are fine -- the tests exercise geometry, determinism, and pixel math, not art.
"""

from __future__ import annotations

import json
import os

import pytest
from PIL import Image

import sys

# Make the repo root importable so ``import server.engine`` works from tests.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from server.engine import TERRAINS  # noqa: E402

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
