"""Engine integration tests: determinism, density monotonicity, parser, multi-
level connection alignment, re-assembly, colorblind, and grid-overlay pixels."""

from __future__ import annotations

import json

import pytest

from server.engine import (
    GenerationSpec, generate_map, generate_level_set, assemble_from_layout,
    parse_prompt, TERRAINS,
)


def _spec(assets, **kw):
    spec = GenerationSpec(**kw)
    spec._assets_path = assets
    return spec


def _canonical(layout):
    """Stable JSON of the layout MINUS non-deterministic fields (there are none,
    but drop 'spec' float repr differences by round-tripping)."""

    copy = dict(layout)
    return json.dumps(copy, sort_keys=True)


# --- determinism ----------------------------------------------------------- #

@pytest.mark.parametrize("seed", [1, 99, 250000])
@pytest.mark.parametrize("structure", ["dungeon", "cave", "village", "none", "ship", "tower"])
def test_same_seed_identical_layout(stub_assets, seed, structure):
    kw = dict(prompt="a place", cols=30, rows=22, seed=seed,
              structure=structure, density=0.6, biomes=["forest"])
    a = generate_map(_spec(stub_assets, **kw)).layout
    b = generate_map(_spec(stub_assets, **kw)).layout
    assert _canonical(a) == _canonical(b)
    assert a["cells"] == b["cells"]
    assert a["props"] == b["props"]
    assert a["spawns"] == b["spawns"]
    assert a["focal"] == b["focal"]


def test_different_seed_different_layout(stub_assets):
    a = generate_map(_spec(stub_assets, prompt="cave", cols=30, rows=22, seed=1,
                           structure="cave")).layout
    b = generate_map(_spec(stub_assets, prompt="cave", cols=30, rows=22, seed=2,
                           structure="cave")).layout
    assert a["cells"] != b["cells"]


def test_assembled_image_is_deterministic(stub_assets):
    kw = dict(prompt="dungeon", cols=20, rows=16, seed=77, structure="dungeon")
    a = generate_map(_spec(stub_assets, **kw)).gridless.tobytes()
    b = generate_map(_spec(stub_assets, **kw)).gridless.tobytes()
    assert a == b


# --- density monotonicity -------------------------------------------------- #

def _clutter_count(layout):
    """Props + difficult/hazard terrain cells = a proxy for clutter."""

    from server.engine.validate import BLOCKED  # walls+hazards
    difficult = {"mud", "rubble", "swamp", "tall_grass", "snow"}
    n_props = len(layout["props"])
    n_terrain = sum(1 for row in layout["cells"] for t in row
                    if t in difficult or t in ({"lava", "water_deep", "pit"}))
    return n_props + n_terrain


@pytest.mark.parametrize("structure,biome", [
    ("dungeon", "dungeon"), ("none", "forest"), ("cave", "cave"),
    ("village", "village"),
])
def test_density_monotonic_clutter(stub_assets, structure, biome):
    """Higher density => at least as much clutter (averaged over seeds to avoid
    a single unlucky seed inverting the trend)."""

    def avg_clutter(density):
        total = 0
        seeds = [1, 2, 3, 4, 5, 6]
        for s in seeds:
            spec = _spec(stub_assets, prompt=biome, cols=36, rows=28, seed=s,
                         density=density, structure=structure, biomes=[biome])
            total += _clutter_count(generate_map(spec).layout)
        return total / len(seeds)

    sparse = avg_clutter(0.0)
    dense = avg_clutter(1.0)
    assert dense >= sparse, f"dense {dense} < sparse {sparse}"


def test_density_extremes_still_validate(stub_assets):
    for d in (0.0, 1.0):
        spec = _spec(stub_assets, prompt="dense ambush forest road", cols=40,
                     rows=30, seed=9, density=d, structure="none",
                     biomes=["forest"], features=["road"])
        res = generate_map(spec)  # must not raise
        assert res.layout["focal"] is not None


# --- parser ---------------------------------------------------------------- #

def test_parser_biomes_and_synonyms():
    spec = parse_prompt("a snowy frozen crypt")
    assert "snow" in spec.biomes
    assert "crypt" in spec.biomes
    assert spec.structure == "dungeon"


def test_parser_mine_synonyms():
    spec = parse_prompt("a collapsed quarry shaft")
    assert "mine" in spec.biomes
    assert spec.structure == "cave"
    assert "scenario:collapsed" in spec.features


def test_parser_coast_synonyms():
    spec = parse_prompt("a pirate cove on the beach")
    assert "coast" in spec.biomes
    assert "ship" in spec.biomes


def test_parser_features():
    spec = parse_prompt("a forest with a river running through it and a bridge and a well")
    assert "river" in spec.features
    assert "bridge" in spec.features
    assert "well" in spec.features


def test_parser_scenario_shapes_mood():
    spec = parse_prompt("an ambush on a forest road")
    assert spec.mood == "combat"
    assert "scenario:ambush" in spec.features
    assert "road" in spec.features


def test_parser_ruined_scenario():
    spec = parse_prompt("the ruined overgrown elven ruins")
    assert "scenario:ruined" in spec.features


def test_overrides_win_over_parse():
    spec = parse_prompt("a tiny peaceful village",
                        overrides={"cols": 55, "rows": 44, "mood": "combat",
                                   "density": 0.9, "biomes": ["desert"],
                                   "seed": 123})
    assert spec.cols == 55 and spec.rows == 44
    assert spec.mood == "combat"
    assert spec.density == 0.9
    assert spec.biomes == ["desert"]
    assert spec.seed == 123


def test_parser_multilevel_hint():
    spec = parse_prompt("a wizard tower with 3 floors")
    assert spec.multi_level is True
    assert spec.levels == 3


# --- multi-level ----------------------------------------------------------- #

def test_level_set_connection_alignment(stub_assets):
    spec = _spec(stub_assets, prompt="a multi-level dungeon", cols=28, rows=22,
                 seed=500, structure="dungeon", multi_level=True, levels=3)
    floors = generate_level_set(spec)
    assert len(floors) == 3

    # Each floor uses seed + i.
    assert floors[0].layout["seed"] == 500
    assert floors[1].layout["seed"] == 501
    assert floors[2].layout["seed"] == 502

    # Down connector of floor i aligns with up connector of floor i+1.
    for i in range(len(floors) - 1):
        downs = [c for c in floors[i].layout["connections"] if c["kind"] == "stairs_down"]
        ups = [c for c in floors[i + 1].layout["connections"] if c["kind"] == "stairs_up"]
        assert downs and ups
        assert (downs[0]["x"], downs[0]["y"]) == (ups[0]["x"], ups[0]["y"])

    # First floor has no up connector; last floor has no down connector.
    assert not any(c["kind"] == "stairs_up" for c in floors[0].layout["connections"])
    assert not any(c["kind"] == "stairs_down" for c in floors[-1].layout["connections"])

    # set_id groups the floors; floor_index is set in metadata.
    set_ids = {f.metadata["set_id"] for f in floors}
    assert len(set_ids) == 1 and next(iter(set_ids)) is not None
    assert [f.metadata["floor_index"] for f in floors] == [0, 1, 2]


def test_level_set_is_deterministic(stub_assets):
    kw = dict(prompt="a tower", cols=24, rows=24, seed=11, structure="tower",
              multi_level=True, levels=2)
    a = generate_level_set(_spec(stub_assets, **kw))
    b = generate_level_set(_spec(stub_assets, **kw))
    for fa, fb in zip(a, b):
        assert fa.layout["cells"] == fb.layout["cells"]
        assert fa.layout["connections"] == fb.layout["connections"]


# --- re-assembly ----------------------------------------------------------- #

def test_assemble_from_layout_roundtrip(stub_assets):
    spec = _spec(stub_assets, prompt="dungeon", cols=22, rows=18, seed=8,
                 structure="dungeon")
    res = generate_map(spec)
    layout = res.layout
    # ensure the layout carries the assets path so re-assembly finds tiles
    layout["spec"]["_assets_path"] = stub_assets

    re = assemble_from_layout(layout, palette_mode="standard")
    assert re.gridless.size == res.gridless.size
    # Same layout -> same image bytes as the original assemble.
    assert re.gridless.tobytes() == res.gridless.tobytes()


def test_edit_then_reassemble_changes_image(stub_assets):
    spec = _spec(stub_assets, prompt="grassland", cols=20, rows=16, seed=4,
                 structure="none", biomes=["grassland"])
    res = generate_map(spec)
    layout = res.layout
    layout["spec"]["_assets_path"] = stub_assets
    before = assemble_from_layout(layout).gridless.tobytes()

    # Flip a swath of cells to lava (hazard) and re-assemble.
    for y in range(3):
        for x in range(3):
            layout["cells"][y][x] = "lava"
    after = assemble_from_layout(layout).gridless.tobytes()
    assert before != after


# --- colorblind + grid overlay -------------------------------------------- #

def test_colorblind_mode_differs(stub_assets):
    kw = dict(prompt="volcanic lava field", cols=20, rows=16, seed=3,
              structure="none", biomes=["volcanic"])
    std = generate_map(_spec(stub_assets, palette_mode="standard", **kw))
    cb = generate_map(_spec(stub_assets, palette_mode="colorblind", **kw))
    assert std.gridless.tobytes() != cb.gridless.tobytes()


def test_grid_overlay_draws_lines(stub_assets):
    spec = _spec(stub_assets, prompt="grassland", cols=6, rows=6,
                 px_per_square=140, seed=1, structure="none", biomes=["grassland"])
    res = generate_map(spec)
    gridless = res.gridless.convert("RGB")
    gridded = res.gridded.convert("RGB")
    # The gridded image must differ from gridless (lines baked in).
    assert gridless.tobytes() != gridded.tobytes()

    # A vertical grid line at x=140 should darken column pixels vs. gridless.
    import numpy as np
    gl = np.asarray(gridless).astype(int)
    gd = np.asarray(gridded).astype(int)
    # Column at x=140 (a grid line) should be on-average darker in gridded.
    col = 140
    assert gd[:, col, :].mean() <= gl[:, col, :].mean()


def test_gridless_has_no_grid_lines(stub_assets):
    """The gridless image should not contain the periodic dark line the gridded
    one adds -- verify the far-from-line interior column is unchanged."""

    spec = _spec(stub_assets, prompt="grassland", cols=6, rows=6,
                 px_per_square=140, seed=1, structure="none", biomes=["grassland"])
    res = generate_map(spec)
    import numpy as np
    gl = np.asarray(res.gridless.convert("RGB")).astype(int)
    gd = np.asarray(res.gridded.convert("RGB")).astype(int)
    # A single interior pixel at (x=70, y=70) sits mid-cell on BOTH axes, so it
    # lies on no grid line and must be identical between gridless and gridded.
    assert np.array_equal(gl[70, 70, :], gd[70, 70, :])
    # And an interior block away from all grid lines (rows/cols 65..75, 205..215)
    # must be identical too.
    assert np.array_equal(gl[65:75, 65:75, :], gd[65:75, 65:75, :])
