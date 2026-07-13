"""Shared pytest fixtures and a fake engine (Contract 3) for backend tests.

The real generation engine (``server.engine``) is built by another leg and is not
present in this worktree. These fixtures build small, Contract-shaped fakes so the
API and library layers can be exercised in isolation on tmpdirs.
"""

from __future__ import annotations

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
