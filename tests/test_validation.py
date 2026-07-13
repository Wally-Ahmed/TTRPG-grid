"""Validation tests (spec sec.7 requirement (b) and the validation pass).

* Every generated layout has a contiguous 5x5 open block and is fully reachable,
  across many seeds / biomes / densities / structures.
* The validator's primitives (5x5 DP, flood fill, ratio, connection carving)
  behave correctly on hand-built grids.
* ``generate_map`` retries on validation failure and eventually raises
  ``GenerationError`` when no valid layout can be produced.
"""

from __future__ import annotations

import itertools

import pytest

from server.engine import GenerationSpec, generate_map, GenerationError, TERRAINS
from server.engine.validate import (
    has_open_5x5, open_ratio, validate_and_fix, ValidationError,
    largest_open_region,
)


def _spec(assets, **kw):
    spec = GenerationSpec(**kw)
    spec._assets_path = assets
    return spec


BIOME_STRUCTS = [
    ("dungeon", "dungeon"), ("crypt", "dungeon"), ("cave", "cave"),
    ("mine", "cave"), ("village", "village"), ("market", "village"),
    ("ship", "ship"), ("tower", "tower"), ("forest", "none"),
    ("grassland", "none"), ("swamp", "none"), ("desert", "none"),
    ("snow", "none"), ("volcanic", "none"), ("coast", "none"),
]
DENSITIES = [0.0, 0.5, 1.0]
SEEDS = [1, 7, 42, 1000, 55555]


@pytest.mark.parametrize("biome,structure", BIOME_STRUCTS)
@pytest.mark.parametrize("density", DENSITIES)
@pytest.mark.parametrize("seed", SEEDS)
def test_every_layout_has_open_5x5_and_is_reachable(stub_assets, biome, structure,
                                                    density, seed):
    spec = _spec(stub_assets, prompt=f"a {biome}", cols=32, rows=24,
                 seed=seed, density=density, biomes=[biome], structure=structure)
    res = generate_map(spec)
    layout = res.layout
    cols, rows = layout["cols"], layout["rows"]
    cells = layout["cells"]

    # Only Contract-1 terrain ids appear.
    for row in cells:
        for t in row:
            assert t in TERRAINS, f"non-contract terrain {t!r}"

    # 5x5 breathing room.
    assert has_open_5x5(cells, cols, rows), "no contiguous 5x5 open block"

    # Fully reachable: exactly one open region after validation's repairs.
    # (validate_and_fix already ran inside generate_map and mutated cells.)
    from server.engine.validate import _open_regions  # noqa: PLC0415
    regions = _open_regions(cells, cols, rows)
    assert len(regions) == 1, f"{len(regions)} disconnected open regions remain"

    # Ratio sanity.
    assert open_ratio(cells, cols, rows) >= 0.20

    # Exactly-one focal point guaranteed.
    assert layout["focal"] is not None
    # Spawns present, both roles.
    roles = {s["role"] for s in layout["spawns"]}
    assert "player" in roles and "enemy" in roles


@pytest.mark.parametrize("cols,rows", [(20, 20), (30, 20), (40, 30), (60, 50)])
def test_various_sizes_validate(stub_assets, cols, rows):
    spec = _spec(stub_assets, prompt="dungeon corridors", cols=cols, rows=rows,
                 seed=3, structure="dungeon", density=0.8)
    layout = generate_map(spec).layout
    assert has_open_5x5(layout["cells"], cols, rows)


# --- validator primitives -------------------------------------------------- #

def _grid(rows_strs):
    """Build a cells grid from strings: '.'=floor, '#'=wall, '~'=deep water."""

    mapping = {".": "grass", "#": "stone_wall", "~": "water_deep", "L": "lava"}
    return [[mapping[c] for c in row] for row in rows_strs]


def test_has_open_5x5_true():
    cells = _grid(["....." for _ in range(5)])
    assert has_open_5x5(cells, 5, 5)


def test_has_open_5x5_false_when_wall_in_block():
    rows = ["....." for _ in range(4)] + ["..#.."]
    cells = _grid(rows)
    assert not has_open_5x5(cells, 5, 5)


def test_open_ratio():
    cells = _grid(["##", ".."])
    assert open_ratio(cells, 2, 2) == 0.5


def test_hazard_counts_as_blocked_for_open_block():
    rows = ["....." for _ in range(4)] + ["..~.."]
    cells = _grid(rows)
    assert not has_open_5x5(cells, 5, 5)


def test_validate_carves_disconnected_regions():
    # Two 5x5 open rooms separated by a wall column; validator must connect them.
    rows = []
    for _ in range(6):
        rows.append("#####" + "#" + "#####")
    grid = []
    for r in range(6):
        left = "....." if r < 5 else "#####"
        right = "....." if r < 5 else "#####"
        grid.append(left + "#" + right)
    cells = _grid(grid)
    cols = len(grid[0])
    rows_n = len(grid)
    layout = {"cols": cols, "rows": rows_n, "cells": cells}
    warns = validate_and_fix(layout)
    from server.engine.validate import _open_regions
    assert len(_open_regions(cells, cols, rows_n)) == 1
    assert any("Carved" in w for w in warns)


def test_validate_raises_on_solid_wall_map():
    cells = _grid(["#####" for _ in range(5)])
    layout = {"cols": 5, "rows": 5, "cells": cells}
    with pytest.raises(ValidationError):
        validate_and_fix(layout)


def test_validate_raises_on_too_small_map():
    cells = _grid(["....", "....", "....", "...."])
    layout = {"cols": 4, "rows": 4, "cells": cells}
    with pytest.raises(ValidationError):
        validate_and_fix(layout)


def test_validate_raises_on_bad_dims():
    with pytest.raises(ValidationError):
        validate_and_fix({"cols": 0, "rows": 5, "cells": []})


# --- retry / GenerationError ---------------------------------------------- #

def test_generate_retries_then_raises_via_monkeypatch(stub_assets, monkeypatch):
    """Force validation to always fail and confirm GenerationError is raised
    after exhausting the retry budget."""

    import server.engine as engine

    def always_fail(layout):
        raise ValidationError("forced failure for test")

    monkeypatch.setattr("server.engine.validate.validate_and_fix", always_fail)
    spec = _spec(stub_assets, prompt="dungeon", cols=24, rows=18, seed=1,
                 structure="dungeon")
    with pytest.raises(GenerationError):
        generate_map(spec)
