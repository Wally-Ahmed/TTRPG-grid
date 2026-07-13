"""Grid / pixel-math tests (spec sec.7 requirement (a) and (c)).

* Exported image dimensions are ALWAYS exact whole-number multiples of the
  chosen px-per-square, with zero partial squares, over a range of sizes and
  resolutions.
* The Foundry / Owlbear pixel-math values in the metadata sidecar are computed
  correctly for a range of size / resolution inputs.
* ``estimate_size`` reports correct dimensions and fires the size guardrails.
"""

from __future__ import annotations

import itertools

import pytest

from server.engine import (
    GenerationSpec, generate_map, estimate_size, parse_prompt,
)


def _spec(assets, **kw):
    spec = GenerationSpec(**kw)
    spec._assets_path = assets
    return spec


# --- (a) exact whole-square image dimensions ------------------------------- #

SIZES = [(20, 20), (30, 20), (40, 30), (17, 23), (60, 40), (5, 5)]
RESOLUTIONS = [70, 100, 140, 150]


@pytest.mark.parametrize("cols,rows", SIZES)
@pytest.mark.parametrize("px", RESOLUTIONS)
def test_image_dims_are_exact_multiples(stub_assets, cols, rows, px):
    spec = _spec(stub_assets, prompt="grassland", cols=cols, rows=rows,
                 px_per_square=px, seed=101, structure="none")
    res = generate_map(spec)

    for img in (res.gridless, res.gridded):
        w, h = img.size
        assert w == cols * px, f"width {w} != {cols}*{px}"
        assert h == rows * px, f"height {h} != {rows}*{px}"
        # No partial squares at any edge.
        assert w % px == 0
        assert h % px == 0
        assert (w // px, h // px) == (cols, rows)


def test_gridded_matches_gridless_dims(stub_assets):
    spec = _spec(stub_assets, prompt="dungeon", cols=25, rows=18,
                 px_per_square=120, seed=5, structure="dungeon")
    res = generate_map(spec)
    assert res.gridless.size == res.gridded.size


# --- (c) Foundry / Owlbear metadata math ----------------------------------- #

@pytest.mark.parametrize("cols,rows,px,feet", [
    (30, 20, 140, 5), (40, 30, 100, 10), (20, 20, 70, 5), (60, 40, 150, 5),
])
def test_metadata_pixel_math(stub_assets, cols, rows, px, feet):
    spec = _spec(stub_assets, prompt="forest", cols=cols, rows=rows,
                 px_per_square=px, feet_per_square=feet, seed=42, structure="none")
    md = generate_map(spec).metadata

    assert md["grid"]["columns"] == cols
    assert md["grid"]["rows"] == rows
    assert md["grid"]["px_per_square"] == px
    assert md["grid"]["feet_per_square"] == feet
    assert md["image"]["width"] == cols * px
    assert md["image"]["height"] == rows * px

    assert md["foundry"]["grid_size"] == px
    assert md["foundry"]["scene_width"] == cols * px
    assert md["foundry"]["scene_height"] == rows * px
    assert md["foundry"]["grid_type"] == "Square"

    assert md["owlbear"]["grid_size_px"] == px
    assert md["owlbear"]["grid_type"] == "Square"
    assert md["owlbear"]["cell_size_ft"] == feet
    assert md["owlbear"]["grid_columns"] == cols
    assert md["owlbear"]["grid_rows"] == rows


def test_global_illumination_from_indoor(stub_assets):
    # Indoor structure (dungeon) -> GI off; outdoor (grassland) -> GI on.
    indoor = generate_map(_spec(stub_assets, prompt="dungeon", cols=24, rows=18,
                                seed=7, structure="dungeon")).metadata
    outdoor = generate_map(_spec(stub_assets, prompt="grassy plains", cols=24, rows=18,
                                 seed=7, structure="none", biomes=["grassland"])).metadata
    assert indoor["foundry"]["global_illumination"] is False
    assert outdoor["foundry"]["global_illumination"] is True


# --- estimate_size --------------------------------------------------------- #

def test_estimate_size_basic():
    est = estimate_size(30, 20, 140)
    assert est["width"] == 4200
    assert est["height"] == 2800
    assert est["megapixels"] == pytest.approx(11.76, abs=0.01)
    assert est["warnings"] == []


def test_estimate_size_warns_on_huge_maps():
    # 60x60 at 150px = 9000x9000 = 81 MP -> both MP and byte warnings fire.
    est = estimate_size(60, 60, 150)
    assert est["megapixels"] > 50
    assert any("MP" in w for w in est["warnings"])
    assert any("MB" in w for w in est["warnings"])


def test_estimate_size_zero_dims():
    est = estimate_size(0, 0, 140)
    assert est["width"] == 0 and est["height"] == 0
    assert est["megapixels"] == 0
    assert est["warnings"] == []


# --- parser produces integral whole-number dims ---------------------------- #

def test_parser_explicit_dims_are_whole_numbers():
    spec = parse_prompt("a 45x33 battlefield")
    assert spec.cols == 45 and spec.rows == 33
    assert isinstance(spec.cols, int) and isinstance(spec.rows, int)


def test_parser_size_presets():
    assert parse_prompt("a small skirmish").cols == 20
    assert parse_prompt("a large sprawling dungeon").cols >= 50
