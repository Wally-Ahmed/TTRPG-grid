#!/usr/bin/env python3
"""
Phase A asset generator for the TTRPG Grid Map Generator.

Deterministic (fixed master seed 7), re-runnable, offline. Uses ONLY Pillow + numpy.
Procedurally renders the complete top-down tile & prop library described in
docs/ARCHITECTURE.md "Contract 1" and BUILD-PROMPT.md sections 3-5.

Outputs:
  assets/tiles/<terrain>/<terrain>_NN.png   140x140 RGB, seamlessly tiling
  assets/props/<prop>/<prop>_NN.png         RGBA, sized footprint*140px, transparent bg
  assets/manifest.json                      Contract 1

Art approach (painterly, not flat vector):
  - Multi-octave WRAP-AROUND value noise (numpy) gives every tile a fractal base texture
    that tiles seamlessly on all four edges (no visible seams when placed adjacent).
  - A cohesive fantasy palette per terrain, with per-variant hue/value jitter.
  - "Brushwork" is simulated by stamping thousands of soft-edged radial blobs at varying
    opacity/color, plus grain/speckle passes, plus subtle per-tile edge darkening.
  - Props are drawn as painterly top-down shapes with irregular hand-drawn dark outlines
    (width/position jitter), soft internal shading, and NO baked drop shadows.

Run:
  .venv/bin/python scripts/generate_assets.py            # generate everything
  .venv/bin/python scripts/generate_assets.py --validate # generate + validate
  .venv/bin/python scripts/generate_assets.py --check    # validate existing assets only
"""

from __future__ import annotations

import argparse
import colorsys
import json
import math
import os
import random
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

# --------------------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------------------

TILE = 140                     # native px per grid square (Contract 1: px_per_square)
MASTER_SEED = 7                # Contract 1: generator_seed
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
TILES_DIR = os.path.join(ASSETS, "tiles")
PROPS_DIR = os.path.join(ASSETS, "props")


# --------------------------------------------------------------------------------------
# Deterministic RNG helpers
# --------------------------------------------------------------------------------------

def sub_seed(*parts) -> int:
    """Stable integer seed derived from the master seed + string/int parts."""
    h = MASTER_SEED & 0xFFFFFFFF
    for p in parts:
        for ch in str(p):
            h = (h * 131 + ord(ch)) & 0xFFFFFFFF
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def rng_for(*parts) -> np.random.Generator:
    return np.random.default_rng(sub_seed(*parts))


def pyrng_for(*parts) -> random.Random:
    return random.Random(sub_seed(*parts))


# --------------------------------------------------------------------------------------
# Wrap-around (seamless) multi-octave value noise
# --------------------------------------------------------------------------------------

def _smoothstep(t: np.ndarray) -> np.ndarray:
    return t * t * (3.0 - 2.0 * t)


def _tileable_value_noise(size: int, period: int, rng: np.random.Generator) -> np.ndarray:
    """
    Value noise on a `size`x`size` grid that tiles seamlessly with the given `period`
    (number of lattice cells across the tile). Bilinear interpolation with smoothstep,
    lattice wraps modulo `period` so opposite edges match exactly.
    Returns float array in [0, 1].
    """
    lattice = rng.random((period, period)).astype(np.float64)

    coords = (np.arange(size) / size) * period
    i0 = np.floor(coords).astype(int) % period
    i1 = (i0 + 1) % period
    frac = coords - np.floor(coords)
    frac = _smoothstep(frac)

    # rows (y) and cols (x)
    y0, y1, fy = i0[:, None], i1[:, None], frac[:, None]
    x0, x1, fx = i0[None, :], i1[None, :], frac[None, :]

    v00 = lattice[y0, x0]
    v01 = lattice[y0, x1]
    v10 = lattice[y1, x0]
    v11 = lattice[y1, x1]

    top = v00 * (1 - fx) + v01 * fx
    bot = v10 * (1 - fx) + v11 * fx
    return top * (1 - fy) + bot * fy


def fbm(size: int, base_period: int, octaves: int, rng: np.random.Generator,
        persistence: float = 0.5, lacunarity: int = 2) -> np.ndarray:
    """
    Fractal Brownian motion: sum of tileable value-noise octaves. Seamless on all edges.
    Normalized to [0, 1].
    """
    out = np.zeros((size, size), dtype=np.float64)
    amp = 1.0
    period = base_period
    total = 0.0
    for o in range(octaves):
        out += amp * _tileable_value_noise(size, period, rng)
        total += amp
        amp *= persistence
        period *= lacunarity
    out /= total
    out -= out.min()
    mx = out.max()
    if mx > 1e-9:
        out /= mx
    return out


def tileable_directional(size: int, period: int, rng: np.random.Generator,
                         angle: float = 0.0, warp: float = 0.0) -> np.ndarray:
    """
    A directional stripe/grain field that tiles seamlessly, optionally domain-warped by
    tileable noise. Used for wood grain, water ripples, planks. Returns [0,1].
    """
    ang = math.radians(angle)
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float64)
    # projected coordinate along the direction, in [0, period) cycles across the tile
    proj = (math.cos(ang) * xx + math.sin(ang) * yy) / size * period
    if warp > 0:
        w = fbm(size, max(2, period // 2), 3, rng) - 0.5
        proj = proj + w * warp
    val = 0.5 + 0.5 * np.sin(proj * 2 * math.pi)
    return val


# --------------------------------------------------------------------------------------
# Color helpers
# --------------------------------------------------------------------------------------

def hex_rgb(h: str):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def clamp8(x):
    return int(max(0, min(255, round(x))))


def shift_hsv(rgb, dh=0.0, ds=0.0, dv=0.0):
    r, g, b = [c / 255.0 for c in rgb]
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    h = (h + dh) % 1.0
    s = max(0.0, min(1.0, s + ds))
    v = max(0.0, min(1.0, v + dv))
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return (clamp8(r * 255), clamp8(g * 255), clamp8(b * 255))


def lerp_rgb(a, b, t):
    return (clamp8(a[0] + (b[0] - a[0]) * t),
            clamp8(a[1] + (b[1] - a[1]) * t),
            clamp8(a[2] + (b[2] - a[2]) * t))


def gradient_map(field: np.ndarray, stops):
    """
    Map a [0,1] scalar field to RGB via a list of (pos, (r,g,b)) stops.
    Returns uint8 HxWx3 array.
    """
    stops = sorted(stops, key=lambda s: s[0])
    positions = np.array([s[0] for s in stops])
    colors = np.array([s[1] for s in stops], dtype=np.float64)
    f = np.clip(field, 0.0, 1.0)
    idx = np.clip(np.searchsorted(positions, f, side="right") - 1, 0, len(stops) - 2)
    p0 = positions[idx]
    p1 = positions[idx + 1]
    span = np.where((p1 - p0) == 0, 1.0, (p1 - p0))
    t = np.clip((f - p0) / span, 0.0, 1.0)[..., None]
    c0 = colors[idx]
    c1 = colors[idx + 1]
    out = c0 * (1 - t) + c1 * t
    return np.clip(out, 0, 255).astype(np.uint8)


# --------------------------------------------------------------------------------------
# Painterly "brush" stamping (numpy, wrap-around aware)
# --------------------------------------------------------------------------------------

def _soft_disk(radius: int) -> np.ndarray:
    """A soft-edged radial falloff kernel in [0,1], shape (2r+1, 2r+1)."""
    r = max(1, radius)
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1].astype(np.float64)
    d = np.sqrt(xx * xx + yy * yy) / r
    k = np.clip(1.0 - d, 0.0, 1.0)
    return k * k * (3.0 - 2.0 * k)  # smoothstep falloff


def stamp_blobs(img: np.ndarray, rng: np.random.Generator, count: int,
                color_fn, r_min: int, r_max: int, alpha_min: float, alpha_max: float,
                wrap: bool = True, squash_range=(0.7, 1.4)):
    """
    Alpha-composite `count` soft radial blobs onto float image (HxWx3, 0..255).
    color_fn(rng) -> (r,g,b). If wrap, blobs that cross an edge wrap to the opposite
    side so the texture tiles seamlessly. squash gives elliptical brush marks.
    Mutates img in place.
    """
    h, w = img.shape[:2]
    for _ in range(count):
        r = int(rng.integers(r_min, r_max + 1))
        k = _soft_disk(r)
        # elliptical squash
        sq = float(rng.uniform(*squash_range))
        if abs(sq - 1.0) > 0.05:
            kh = max(1, int(round(k.shape[0])))
            kw = max(1, int(round(k.shape[1] * sq)))
            ky = np.linspace(0, k.shape[0] - 1, kh).astype(int)
            kx = np.linspace(0, k.shape[1] - 1, kw).astype(int)
            k = k[np.ix_(ky, kx)]
        kh, kw = k.shape
        a = float(rng.uniform(alpha_min, alpha_max))
        col = np.array(color_fn(rng), dtype=np.float64)
        cy = int(rng.integers(0, h))
        cx = int(rng.integers(0, w))
        y0 = cy - kh // 2
        x0 = cx - kw // 2
        for dy in range(kh):
            iy = y0 + dy
            if wrap:
                iy %= h
            elif iy < 0 or iy >= h:
                continue
            row_a = k[dy] * a
            for dx in range(kw):
                ix = x0 + dx
                if wrap:
                    ix %= w
                elif ix < 0 or ix >= w:
                    continue
                aa = row_a[dx]
                if aa <= 0.003:
                    continue
                img[iy, ix] = img[iy, ix] * (1 - aa) + col * aa


def add_grain(img: np.ndarray, rng: np.random.Generator, amount: float):
    """Additive fine grain/speckle. amount in value units (e.g. 8 => +-8)."""
    noise = (rng.random(img.shape[:2]) - 0.5) * 2 * amount
    img += noise[..., None]


def edge_darken(img: np.ndarray, strength: float = 0.16, feather: int = 14):
    """
    Per-tile edge darkening (vignette toward the tile border). This BREAKS seamless
    tiling (creates a grid of dark seams when tiles are placed adjacent), so it is only
    used for discrete, non-repeating tiles (e.g. the 'pit' hazard). Seamless field
    terrains use `macro_shade` instead.
    """
    h, w = img.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    dx = np.minimum(xx, w - 1 - xx)
    dy = np.minimum(yy, h - 1 - yy)
    d = np.minimum(dx, dy)
    m = np.clip(d / feather, 0.0, 1.0)
    factor = (1.0 - strength) + strength * m
    img *= factor[..., None]


def macro_shade(img: np.ndarray, rng: np.random.Generator, strength: float = 0.10):
    """
    Seamless large-scale light/shadow variation: a low-frequency wrap-around noise field
    that gently darkens/lightens broad regions. Because it is tileable, it adds organic
    depth WITHOUT introducing per-tile border seams. Replaces edge_darken for terrain
    that must tile.
    """
    field = fbm(TILE, 2, 2, rng)          # very low frequency, seamless
    factor = 1.0 + (field - 0.5) * 2 * strength
    img *= factor[..., None]


# --------------------------------------------------------------------------------------
# Tile painters
# --------------------------------------------------------------------------------------
# Each painter takes (variant_index) and returns a 140x140 uint8 RGB numpy array that
# tiles seamlessly. All randomness derives from rng_for(terrain, variant).

def _base_from_gradient(terrain, v, base_period, octaves, stops, persistence=0.5):
    rng = rng_for("base", terrain, v)
    field = fbm(TILE, base_period, octaves, rng, persistence=persistence)
    arr = gradient_map(field, stops).astype(np.float64)
    return arr, field, rng


def _finish(terrain, v, arr, edge=0.16, grain=6.0, seam_safe=True):
    """
    Common finishing pass for a terrain tile. If seam_safe (the default, for all repeating
    field terrains), uses tileable macro_shade so the tile has no border seam. Only the
    discrete 'pit' passes seam_safe=False to get a real border vignette. `edge` is
    interpreted as macro-shade strength when seam_safe, else vignette strength.
    """
    rng = rng_for("finish", terrain, v)
    add_grain(arr, rng, grain)
    if seam_safe:
        macro_shade(arr, rng_for("macro", terrain, v), strength=min(0.12, edge))
    else:
        edge_darken(arr, strength=edge)
    return np.clip(arr, 0, 255).astype(np.uint8)


def _jit(v, terrain):
    """Per-variant hue/value jitter offsets, deterministic."""
    r = pyrng_for("jit", terrain, v)
    return r.uniform(-0.02, 0.02), r.uniform(-0.04, 0.04), r.uniform(-0.05, 0.05)


# ---- Grass family -------------------------------------------------------------------

def _blade_streaks(arr, rng, colors, count, length_range=(4, 10), width=1, alpha=(0.10, 0.28)):
    """
    Draw many short directional 'blade' streaks (thin near-vertical strokes with slight
    lean) that wrap around tile edges, for a grass/reed read. colors is a list of (r,g,b).
    Pure-numpy line stamping with wrap.
    """
    h, w = arr.shape[:2]
    for _ in range(count):
        cx = rng.integers(0, w)
        cy = rng.integers(0, h)
        L = int(rng.integers(length_range[0], length_range[1] + 1))
        lean = float(rng.uniform(-0.4, 0.4))
        col = np.array(colors[int(rng.integers(0, len(colors)))], dtype=np.float64)
        a = float(rng.uniform(*alpha))
        for t in range(L):
            frac = t / max(1, L)
            aa = a * (1.0 - frac)  # fade toward tip
            px = int(round(cx + lean * t)) % w
            py = int(round(cy - t)) % h
            arr[py, px] = arr[py, px] * (1 - aa) + col * aa


def paint_grass(v):
    dh, ds, dv = _jit(v, "grass")
    dark = shift_hsv(hex_rgb("#3f5a2b"), dh, ds, dv)
    mid = shift_hsv(hex_rgb("#5b7d33"), dh, ds, dv)
    light = shift_hsv(hex_rgb("#7ea24a"), dh, ds, dv)
    arr, field, _ = _base_from_gradient("grass", v, 5, 5,
                                        [(0.0, dark), (0.5, mid), (1.0, light)])
    rng = rng_for("grass_brush", v)
    # broad soft mottling for base variation
    def gc(r):
        t = r.random()
        return lerp_rgb(mid, light, t) if t > 0.4 else lerp_rgb(dark, mid, t)
    stamp_blobs(arr, rng, 300, gc, 2, 5, 0.06, 0.16, squash_range=(0.6, 1.2))
    # dense fine blade streaks for a real grass read (directional, wrapping)
    blade_cols = [dark, mid, light, shift_hsv(mid, 0.02, 0.08, 0.05)]
    _blade_streaks(arr, rng_for("grass_blades", v), blade_cols, 2600,
                   length_range=(4, 9), alpha=(0.10, 0.26))
    # occasional dry/yellow tufts and tiny flecks
    dry = shift_hsv(mid, 0.04, -0.12, 0.14)
    _blade_streaks(arr, rng_for("grass_dry", v), [dry], 260, length_range=(3, 7),
                   alpha=(0.08, 0.18))
    return _finish("grass", v, arr, grain=6)


def paint_tall_grass(v):
    dh, ds, dv = _jit(v, "tall_grass")
    dark = shift_hsv(hex_rgb("#35521f"), dh, ds, dv)
    mid = shift_hsv(hex_rgb("#4d7328"), dh, ds, dv)
    light = shift_hsv(hex_rgb("#89ab4d"), dh, ds, dv)
    arr, field, _ = _base_from_gradient("tall_grass", v, 4, 5,
                                        [(0.0, dark), (0.45, mid), (1.0, light)])
    rng = rng_for("tallgrass_brush", v)
    # broad clumps then long dense blades for a taller, wilder read
    stamp_blobs(arr, rng, 200, lambda r: lerp_rgb(dark, mid, r.random()), 3, 7, 0.06, 0.16)
    blade_cols = [dark, mid, light, shift_hsv(mid, 0.02, 0.06, 0.05)]
    _blade_streaks(arr, rng_for("tg_blades", v), blade_cols, 3200,
                   length_range=(8, 16), alpha=(0.12, 0.30))
    return _finish("tall_grass", v, arr, grain=7)


def paint_forest_floor(v):
    dh, ds, dv = _jit(v, "forest_floor")
    dark = shift_hsv(hex_rgb("#33281a"), dh, ds, dv)
    mid = shift_hsv(hex_rgb("#4d3b23"), dh, ds, dv)
    moss = shift_hsv(hex_rgb("#4f6234"), dh, ds, dv)
    leaf = shift_hsv(hex_rgb("#6b4f2a"), dh, ds, dv)
    arr, field, _ = _base_from_gradient("forest_floor", v, 5, 5,
                                        [(0.0, dark), (0.4, mid), (0.75, leaf), (1.0, moss)])
    rng = rng_for("ff_brush", v)
    # scattered leaves & twigs
    def leafc(r):
        t = r.random()
        return lerp_rgb(leaf, shift_hsv(leaf, 0.02, 0.05, 0.1), t)
    stamp_blobs(arr, rng, 500, leafc, 2, 5, 0.10, 0.26, squash_range=(0.5, 1.3))
    # moss patches
    stamp_blobs(arr, rng, 90, lambda r: moss, 4, 9, 0.10, 0.22)
    # dark humus speckle
    stamp_blobs(arr, rng, 400, lambda r: dark, 1, 2, 0.08, 0.2)
    return _finish("forest_floor", v, arr, grain=8)


def paint_jungle_floor(v):
    dh, ds, dv = _jit(v, "jungle_floor")
    dark = shift_hsv(hex_rgb("#24371a"), dh, ds, dv)
    mid = shift_hsv(hex_rgb("#375223"), dh, ds, dv)
    moss = shift_hsv(hex_rgb("#4e7a2f"), dh, ds, dv)
    mud = shift_hsv(hex_rgb("#453a20"), dh, ds, dv)
    arr, field, _ = _base_from_gradient("jungle_floor", v, 4, 5,
                                        [(0.0, dark), (0.35, mud), (0.7, mid), (1.0, moss)])
    rng = rng_for("jf_brush", v)
    # dense foliage litter and vines
    def fol(r):
        t = r.random()
        return lerp_rgb(mid, moss, t)
    stamp_blobs(arr, rng, 1000, fol, 2, 6, 0.12, 0.30, squash_range=(0.4, 1.5))
    stamp_blobs(arr, rng, 150, lambda r: dark, 3, 7, 0.10, 0.22)
    return _finish("jungle_floor", v, arr, grain=8)


# ---- Ground / earth -----------------------------------------------------------------

def paint_dirt(v):
    dh, ds, dv = _jit(v, "dirt")
    dark = shift_hsv(hex_rgb("#4a3521"), dh, ds, dv)
    mid = shift_hsv(hex_rgb("#6b4d30"), dh, ds, dv)
    light = shift_hsv(hex_rgb("#8a6842"), dh, ds, dv)
    arr, field, _ = _base_from_gradient("dirt", v, 5, 5,
                                        [(0.0, dark), (0.5, mid), (1.0, light)])
    rng = rng_for("dirt_brush", v)
    stamp_blobs(arr, rng, 500, lambda r: lerp_rgb(dark, light, r.random()), 2, 6, 0.08, 0.22)
    # pebbles
    stamp_blobs(arr, rng, 120, lambda r: shift_hsv(mid, 0, -0.15, 0.14), 1, 3, 0.15, 0.35)
    return _finish("dirt", v, arr, grain=7)


def paint_road(v):
    dh, ds, dv = _jit(v, "road")
    dark = shift_hsv(hex_rgb("#4d4436"), dh, ds, dv)
    mid = shift_hsv(hex_rgb("#6d6250"), dh, ds, dv)
    light = shift_hsv(hex_rgb("#8b8069"), dh, ds, dv)
    arr, field, _ = _base_from_gradient("road", v, 6, 5,
                                        [(0.0, dark), (0.5, mid), (1.0, light)])
    rng = rng_for("road_brush", v)
    # packed gravel: many small light/dark pebbles
    def peb(r):
        t = r.random()
        return light if t > 0.6 else (dark if t < 0.25 else mid)
    stamp_blobs(arr, rng, 700, peb, 1, 4, 0.12, 0.32)
    # faint wheel ruts (two soft dark directional bands)
    ruts = tileable_directional(TILE, 2, rng_for("road_ruts", v), angle=0, warp=6)
    band = (np.clip((ruts - 0.75) * 4, 0, 1)) * 0.10
    arr *= (1 - band[..., None])
    return _finish("road", v, arr, grain=6)


def paint_cobblestone(v):
    dh, ds, dv = _jit(v, "cobblestone")
    grout = shift_hsv(hex_rgb("#2f2c28"), dh, ds, dv)
    stone_lo = shift_hsv(hex_rgb("#6b6660"), dh, ds, dv)
    stone_hi = shift_hsv(hex_rgb("#94908a"), dh, ds, dv)
    arr = np.zeros((TILE, TILE, 3), dtype=np.float64)
    arr[:] = grout
    rng = pyrng_for("cobble", v)
    nrng = rng_for("cobble_n", v)
    # irregular stone cells via jittered grid; wrap by drawing across tile edges
    cell = 20
    draw_img = Image.fromarray(np.zeros((TILE, TILE, 3), dtype=np.uint8))
    d = ImageDraw.Draw(draw_img, "RGBA")
    stones = []
    for gy in range(-1, TILE // cell + 1):
        for gx in range(-1, TILE // cell + 1):
            cx = gx * cell + cell / 2 + rng.uniform(-4, 4)
            cy = gy * cell + cell / 2 + rng.uniform(-4, 4)
            rw = cell / 2 - rng.uniform(1.5, 4)
            rh = cell / 2 - rng.uniform(1.5, 4)
            tone = rng.uniform(0, 1)
            base = lerp_rgb(stone_lo, stone_hi, tone)
            stones.append((cx, cy, rw, rh, base))
    # rasterize stones with wrap by offsetting into 3x3 neighborhood
    for (cx, cy, rw, rh, base) in stones:
        for ox in (-TILE, 0, TILE):
            for oy in (-TILE, 0, TILE):
                x0, y0, x1, y1 = cx + ox - rw, cy + oy - rh, cx + ox + rw, cy + oy + rh
                if x1 < 0 or y1 < 0 or x0 > TILE or y0 > TILE:
                    continue
                d.ellipse([x0, y0, x1, y1], fill=base + (255,))
    cob = np.asarray(draw_img).astype(np.float64)
    mask = cob.sum(axis=2) > 4
    arr[mask] = cob[mask]
    # painterly shading on each stone via a soft noise highlight
    field = fbm(TILE, 8, 4, rng_for("cobble_shade", v))
    shade = (field - 0.5) * 40
    arr[mask] += shade[mask][..., None]
    # blob speckle for weathering
    stamp_blobs(arr, nrng, 300, lambda r: lerp_rgb(stone_lo, stone_hi, r.random()),
                1, 3, 0.06, 0.16, wrap=True)
    return _finish("cobblestone", v, arr, edge=0.10, grain=5)


def paint_sand(v):
    dh, ds, dv = _jit(v, "sand")
    dark = shift_hsv(hex_rgb("#c2a06a"), dh, ds, dv)
    mid = shift_hsv(hex_rgb("#d8bd88"), dh, ds, dv)
    light = shift_hsv(hex_rgb("#ecd9a8"), dh, ds, dv)
    arr, field, _ = _base_from_gradient("sand", v, 4, 5,
                                        [(0.0, dark), (0.5, mid), (1.0, light)])
    rng = rng_for("sand_brush", v)
    # dune ripples: directional soft bands
    ripple = tileable_directional(TILE, 6, rng_for("sand_ripple", v),
                                  angle=25, warp=10)
    arr += (ripple - 0.5)[..., None] * 22
    stamp_blobs(arr, rng, 200, lambda r: light, 1, 3, 0.05, 0.14)
    return _finish("sand", v, arr, edge=0.10, grain=5)


def paint_snow(v):
    dh, ds, dv = _jit(v, "snow")
    dark = shift_hsv(hex_rgb("#c6cdd8"), dh, ds, dv)
    mid = shift_hsv(hex_rgb("#e2e7ee"), dh, ds, dv)
    light = shift_hsv(hex_rgb("#fbfdff"), dh, ds, dv)
    arr, field, _ = _base_from_gradient("snow", v, 4, 5,
                                        [(0.0, dark), (0.5, mid), (1.0, light)])
    rng = rng_for("snow_brush", v)
    # drift mounds and sparkle
    stamp_blobs(arr, rng, 160, lambda r: light, 4, 10, 0.06, 0.16)
    stamp_blobs(arr, rng, 120, lambda r: shift_hsv(dark, 0.0, 0.02, -0.05), 2, 5, 0.05, 0.12)
    # blue-shadow hollows
    stamp_blobs(arr, rng, 60, lambda r: shift_hsv(dark, -0.02, 0.06, -0.08), 5, 11, 0.05, 0.12)
    return _finish("snow", v, arr, edge=0.08, grain=4)


def paint_ice(v):
    dh, ds, dv = _jit(v, "ice")
    deep = shift_hsv(hex_rgb("#6f9fb4"), dh, ds, dv)
    mid = shift_hsv(hex_rgb("#a3ccdb"), dh, ds, dv)
    light = shift_hsv(hex_rgb("#dcf0f6"), dh, ds, dv)
    # smooth low-frequency base so ice reads as a solid frozen sheet, not lace
    arr, field, _ = _base_from_gradient("ice", v, 2, 3,
                                        [(0.0, deep), (0.5, mid), (1.0, light)])
    # glossy sheen: broad soft highlights
    sheen = fbm(TILE, 3, 3, rng_for("ice_sheen", v))
    arr += (sheen - 0.5)[..., None] * 26
    # a FEW bold fracture lines only (sparse, thresholded high so most of the tile is clear)
    for a in (22, 108):
        cr = tileable_directional(TILE, int(rng_for("ice_c", v, a).integers(2, 4)),
                                  rng_for("ice_cn", v, a), angle=a, warp=16)
        ridge = np.clip((np.abs(cr - 0.5) - 0.475) * 60, 0, 1)
        # crack = thin dark line flanked by a bright glint
        arr -= ridge[..., None] * np.array([26, 24, 18])
        glint = np.clip((np.abs(cr - 0.5) - 0.44) * 30, 0, 1) - ridge
        arr += np.clip(glint, 0, 1)[..., None] * np.array([18, 24, 30])
    return _finish("ice", v, arr, edge=0.08, grain=2)


# ---- Wetlands / water ---------------------------------------------------------------

def paint_swamp(v):
    dh, ds, dv = _jit(v, "swamp")
    water = shift_hsv(hex_rgb("#3a4a33"), dh, ds, dv)
    algae = shift_hsv(hex_rgb("#556b34"), dh, ds, dv)
    mud = shift_hsv(hex_rgb("#4a4128"), dh, ds, dv)
    arr, field, _ = _base_from_gradient("swamp", v, 4, 5,
                                        [(0.0, water), (0.45, mud), (0.8, algae), (1.0, algae)])
    rng = rng_for("swamp_brush", v)
    # algae scum swirls and murky reflections
    stamp_blobs(arr, rng, 500, lambda r: lerp_rgb(water, algae, r.random()), 3, 9, 0.10, 0.26,
                squash_range=(0.5, 1.6))
    # muddy patches
    stamp_blobs(arr, rng, 120, lambda r: mud, 4, 10, 0.10, 0.22)
    # faint water sheen highlights
    stamp_blobs(arr, rng, 60, lambda r: shift_hsv(water, 0, -0.1, 0.2), 2, 5, 0.06, 0.14)
    return _finish("swamp", v, arr, grain=7)


def paint_mud(v):
    dh, ds, dv = _jit(v, "mud")
    dark = shift_hsv(hex_rgb("#33281a"), dh, ds, dv)
    mid = shift_hsv(hex_rgb("#4c3c25"), dh, ds, dv)
    wet = shift_hsv(hex_rgb("#5f4c30"), dh, ds, dv)
    arr, field, _ = _base_from_gradient("mud", v, 4, 5,
                                        [(0.0, dark), (0.5, mid), (1.0, wet)])
    rng = rng_for("mud_brush", v)
    # wet glistening puddles (brighter, desaturated highlights)
    stamp_blobs(arr, rng, 90, lambda r: shift_hsv(wet, 0, -0.2, 0.22), 3, 8, 0.10, 0.24)
    # boot-churned lumps
    stamp_blobs(arr, rng, 300, lambda r: lerp_rgb(dark, wet, r.random()), 2, 5, 0.10, 0.24)
    return _finish("mud", v, arr, grain=8)


def _water(terrain, v, deep_hex, mid_hex, light_hex, base_period, ripple_period,
           highlight_strength, edge=0.12):
    dh, ds, dv = _jit(v, terrain)
    deep = shift_hsv(hex_rgb(deep_hex), dh, ds, dv)
    mid = shift_hsv(hex_rgb(mid_hex), dh, ds, dv)
    light = shift_hsv(hex_rgb(light_hex), dh, ds, dv)
    rng = rng_for("water_base", terrain, v)
    # two-scale depth field
    depth = 0.6 * fbm(TILE, base_period, 5, rng) + 0.4 * fbm(TILE, base_period * 2, 4,
                                                             rng_for("water_base2", terrain, v))
    depth -= depth.min()
    depth /= max(1e-9, depth.max())
    arr = gradient_map(depth, [(0.0, deep), (0.5, mid), (1.0, light)]).astype(np.float64)
    # ripple highlight bands (two directions), tileable
    r1 = tileable_directional(TILE, ripple_period, rng_for("wr1", terrain, v),
                              angle=15, warp=14)
    r2 = tileable_directional(TILE, ripple_period + 2, rng_for("wr2", terrain, v),
                              angle=110, warp=14)
    hi = np.clip((r1 * r2 - 0.55) * 3.2, 0, 1)
    arr += hi[..., None] * np.array(shift_hsv(light, 0, -0.2, 0.25)) * (highlight_strength)
    arr = np.clip(arr, 0, 255)
    # sparkle specular dots
    stamp_blobs(arr, rng_for("water_spark", terrain, v), 40,
                lambda r: shift_hsv(light, 0, -0.3, 0.35), 1, 2, 0.15, 0.4)
    return _finish(terrain, v, arr, edge=edge, grain=4)


def paint_water_shallow(v):
    return _water("water_shallow", v, "#3f7d86", "#5a9ba2", "#a7d6d6", 3, 7, 0.5)


def paint_water_deep(v):
    return _water("water_deep", v, "#173a5c", "#1f5178", "#4b86a8", 3, 6, 0.35, edge=0.16)


def paint_river(v):
    # river has a flow direction: strong directional ripple
    dh, ds, dv = _jit(v, "river")
    deep = shift_hsv(hex_rgb("#255f7a"), dh, ds, dv)
    mid = shift_hsv(hex_rgb("#3a86a0"), dh, ds, dv)
    light = shift_hsv(hex_rgb("#79bccb"), dh, ds, dv)
    rng = rng_for("river_base", v)
    depth = fbm(TILE, 3, 4, rng)
    # bias depth along flow so center reads slightly deeper
    arr = gradient_map(depth, [(0.0, deep), (0.5, mid), (1.0, light)]).astype(np.float64)
    flow = tileable_directional(TILE, 8, rng_for("river_flow", v), angle=90, warp=10)
    hi = np.clip((flow - 0.7) * 4, 0, 1)
    arr += hi[..., None] * np.array(shift_hsv(light, 0, -0.2, 0.25)) * 0.6
    stamp_blobs(arr, rng_for("river_spark", v), 50,
                lambda r: shift_hsv(light, 0, -0.3, 0.3), 1, 2, 0.15, 0.4)
    return _finish("river", v, arr, edge=0.12, grain=4)


# ---- Volcanic -----------------------------------------------------------------------

def paint_lava(v):
    dh, ds, dv = _jit(v, "lava")
    black = hex_rgb("#1c130f")
    crust = shift_hsv(hex_rgb("#4a241a"), dh, 0, dv)
    red = shift_hsv(hex_rgb("#c23a12"), dh, ds, dv)
    orange = shift_hsv(hex_rgb("#f07d1c"), dh, ds, dv)
    yellow = shift_hsv(hex_rgb("#ffde7a"), dh, ds, dv)
    rng = rng_for("lava_base", v)
    field = fbm(TILE, 4, 5, rng)
    # crust cracks: where field is high, glowing veins show through black crust
    veins = fbm(TILE, 5, 4, rng_for("lava_veins", v))
    glow = np.clip((veins - 0.52) * 6, 0, 1)
    arr = gradient_map(field, [(0.0, black), (0.4, crust), (0.7, crust), (1.0, crust)]).astype(np.float64)
    glow_col = gradient_map(field, [(0.0, red), (0.5, orange), (1.0, yellow)]).astype(np.float64)
    g = glow[..., None]
    arr = arr * (1 - g) + glow_col * g
    # bright molten pools
    stamp_blobs(arr, rng_for("lava_pool", v), 40,
                lambda r: lerp_rgb(orange, yellow, r.random()), 2, 6, 0.25, 0.6)
    # dark cooled clumps
    stamp_blobs(arr, rng_for("lava_clump", v), 90, lambda r: black, 2, 6, 0.2, 0.5)
    return _finish("lava", v, arr, edge=0.10, grain=5)


def paint_volcanic_rock(v):
    dh, ds, dv = _jit(v, "volcanic_rock")
    black = shift_hsv(hex_rgb("#26201f"), dh, ds, dv)
    grey = shift_hsv(hex_rgb("#3d3634"), dh, ds, dv)
    ash = shift_hsv(hex_rgb("#544c48"), dh, ds, dv)
    ember = hex_rgb("#7a2a10")
    arr, field, _ = _base_from_gradient("volcanic_rock", v, 5, 5,
                                        [(0.0, black), (0.5, grey), (1.0, ash)])
    rng = rng_for("vrock_brush", v)
    # porous basalt pits
    stamp_blobs(arr, rng, 400, lambda r: black, 1, 4, 0.12, 0.3)
    # faint ember cracks
    veins = fbm(TILE, 6, 3, rng_for("vrock_veins", v))
    ev = np.clip((veins - 0.6) * 8, 0, 1)
    arr += ev[..., None] * np.array(ember) * 0.4
    return _finish("volcanic_rock", v, arr, grain=6)


# ---- Floors -------------------------------------------------------------------------

def paint_stone_floor(v):
    dh, ds, dv = _jit(v, "stone_floor")
    grout = shift_hsv(hex_rgb("#2b2825"), dh, ds, dv)
    lo = shift_hsv(hex_rgb("#6a655d"), dh, ds, dv)
    hi = shift_hsv(hex_rgb("#928c81"), dh, ds, dv)
    arr = np.zeros((TILE, TILE, 3), dtype=np.float64)
    arr[:] = grout
    rng = pyrng_for("sfloor", v)
    # flagstone grid: 2x2 large slabs with jittered seams; seams wrap at tile edges
    draw_img = Image.fromarray(np.zeros((TILE, TILE, 3), dtype=np.uint8))
    d = ImageDraw.Draw(draw_img, "RGBA")
    n = 2
    sz = TILE / n
    for gy in range(n):
        for gx in range(n):
            pad = rng.uniform(3, 6)
            x0 = gx * sz + pad
            y0 = gy * sz + pad
            x1 = (gx + 1) * sz - pad
            y1 = (gy + 1) * sz - pad
            tone = rng.uniform(0, 1)
            base = lerp_rgb(lo, hi, tone)
            d.rounded_rectangle([x0, y0, x1, y1], radius=6, fill=base + (255,))
    slab = np.asarray(draw_img).astype(np.float64)
    mask = slab.sum(axis=2) > 4
    arr[mask] = slab[mask]
    # painterly per-slab noise shading
    field = fbm(TILE, 7, 4, rng_for("sfloor_shade", v))
    arr[mask] += ((field[mask] - 0.5) * 34)[..., None]
    # cracks and wear speckle
    nrng = rng_for("sfloor_n", v)
    stamp_blobs(arr, nrng, 250, lambda r: lerp_rgb(lo, hi, r.random()), 1, 3, 0.06, 0.16)
    stamp_blobs(arr, nrng, 40, lambda r: grout, 1, 2, 0.2, 0.4)
    return _finish("stone_floor", v, arr, edge=0.12, grain=5)


def paint_wood_floor(v):
    dh, ds, dv = _jit(v, "wood_floor")
    dark = shift_hsv(hex_rgb("#5a3d22"), dh, ds, dv)
    mid = shift_hsv(hex_rgb("#7a552f"), dh, ds, dv)
    light = shift_hsv(hex_rgb("#9c7444"), dh, ds, dv)
    rng = pyrng_for("wfloor", v)
    arr = np.zeros((TILE, TILE, 3), dtype=np.float64)
    # planks running horizontally; 4 planks per tile (35px), wrap seamlessly
    plank_h = TILE // 4
    for i in range(4):
        y0 = i * plank_h
        tone = rng.uniform(0, 1)
        base = lerp_rgb(dark, light, tone)
        pfield = fbm(TILE, 3, 4, rng_for("wf_plank", v, i))
        # grain: strong horizontal directional streaks
        grain = tileable_directional(TILE, 1, rng_for("wf_grain", v, i), angle=0, warp=30)
        seg = np.zeros((plank_h, TILE, 3))
        seg[:] = base
        pf = pfield[y0:y0 + plank_h]
        gr = grain[y0:y0 + plank_h]
        seg += (pf - 0.5)[..., None] * 26
        seg += (gr - 0.5)[..., None] * 22
        arr[y0:y0 + plank_h] = seg
    # dark seams between planks (horizontal) + nail dots
    for i in range(4):
        y = i * plank_h
        arr[max(0, y - 1):y + 1] *= 0.55
    # a few vertical board-end seams, jittered per plank
    nrng = rng_for("wf_seams", v)
    for i in range(4):
        xs = int(nrng.integers(20, TILE - 20))
        arr[i * plank_h:(i + 1) * plank_h, xs:xs + 1] *= 0.6
    # knots
    def knot(r):
        return shift_hsv(dark, 0, 0.1, -0.1)
    stamp_blobs(arr, nrng, 8, knot, 2, 4, 0.3, 0.5)
    return _finish("wood_floor", v, arr, edge=0.12, grain=5)


def paint_marble_floor(v):
    dh, ds, dv = _jit(v, "marble_floor")
    base = shift_hsv(hex_rgb("#d8d2c6"), dh, ds, dv)
    vein = shift_hsv(hex_rgb("#8f8778"), dh, ds, dv)
    shadow = shift_hsv(hex_rgb("#b3ab9c"), dh, ds, dv)
    rng = rng_for("marble", v)
    field = fbm(TILE, 4, 5, rng)
    arr = np.zeros((TILE, TILE, 3), dtype=np.float64)
    arr[:] = base
    arr += (field - 0.5)[..., None] * 18
    # marble veining: thin dark filaments via warped directional ridges
    for a in (35, 100, 160):
        vf = tileable_directional(TILE, rng_for("marble_v", v, a).integers(2, 4),
                                  rng_for("marble_vn", v, a), angle=a, warp=30)
        ridge = np.clip((np.abs(vf - 0.5) - 0.45) * 26, 0, 1)
        arr = arr * (1 - ridge[..., None] * 0.5) + np.array(vein) * (ridge[..., None] * 0.5)
    # polished sheen
    sheen = fbm(TILE, 3, 3, rng_for("marble_sheen", v))
    arr += (sheen - 0.5)[..., None] * 14
    return _finish("marble_floor", v, arr, edge=0.10, grain=3)


def paint_ship_deck(v):
    dh, ds, dv = _jit(v, "ship_deck")
    dark = shift_hsv(hex_rgb("#5c4326"), dh, ds, dv)
    mid = shift_hsv(hex_rgb("#7d5c34"), dh, ds, dv)
    light = shift_hsv(hex_rgb("#9c7a48"), dh, ds, dv)
    rng = pyrng_for("deck", v)
    arr = np.zeros((TILE, TILE, 3), dtype=np.float64)
    # narrow planks (7 planks, ~20px) running vertically, caulked seams
    n = 7
    pw = TILE / n
    for i in range(n):
        x0 = int(i * pw)
        x1 = int((i + 1) * pw)
        tone = rng.uniform(0, 1)
        base = lerp_rgb(dark, light, tone)
        pfield = fbm(TILE, 3, 4, rng_for("deck_p", v, i))
        grain = tileable_directional(TILE, 1, rng_for("deck_g", v, i), angle=90, warp=26)
        seg = np.zeros((TILE, x1 - x0, 3))
        seg[:] = base
        seg += (pfield[:, x0:x1] - 0.5)[..., None] * 22
        seg += (grain[:, x0:x1] - 0.5)[..., None] * 18
        arr[:, x0:x1] = seg
    # black caulk seams between planks (vertical)
    for i in range(n + 1):
        x = int(round(i * pw)) % TILE
        arr[:, max(0, x - 1):x + 1] *= 0.35
    # iron nail rows
    nrng = rng_for("deck_nail", v)
    def nail(r):
        return shift_hsv(dark, 0, -0.2, -0.15)
    stamp_blobs(arr, nrng, 26, nail, 1, 2, 0.4, 0.6)
    return _finish("ship_deck", v, arr, edge=0.12, grain=5)


# ---- Caves / rock -------------------------------------------------------------------

def paint_cave_floor(v):
    dh, ds, dv = _jit(v, "cave_floor")
    dark = shift_hsv(hex_rgb("#2c2926"), dh, ds, dv)
    mid = shift_hsv(hex_rgb("#443f39"), dh, ds, dv)
    light = shift_hsv(hex_rgb("#5e574d"), dh, ds, dv)
    arr, field, _ = _base_from_gradient("cave_floor", v, 5, 5,
                                        [(0.0, dark), (0.5, mid), (1.0, light)])
    rng = rng_for("cavef_brush", v)
    # scattered rocks and grit
    stamp_blobs(arr, rng, 300, lambda r: lerp_rgb(dark, light, r.random()), 2, 6, 0.10, 0.24)
    stamp_blobs(arr, rng, 200, lambda r: dark, 1, 3, 0.1, 0.24)
    # damp dark patches
    stamp_blobs(arr, rng, 40, lambda r: shift_hsv(dark, 0, 0.05, -0.05), 5, 11, 0.08, 0.18)
    return _finish("cave_floor", v, arr, edge=0.18, grain=6)


def paint_mountain_rock(v):
    dh, ds, dv = _jit(v, "mountain_rock")
    dark = shift_hsv(hex_rgb("#4a463f"), dh, ds, dv)
    mid = shift_hsv(hex_rgb("#6d675c"), dh, ds, dv)
    light = shift_hsv(hex_rgb("#928b7c"), dh, ds, dv)
    arr, field, _ = _base_from_gradient("mountain_rock", v, 4, 5,
                                        [(0.0, dark), (0.5, mid), (1.0, light)])
    rng = rng_for("mrock_brush", v)
    # striated rock: directional crack ridges + faceted highlights
    for a in (40, 130):
        cr = tileable_directional(TILE, rng_for("mrock_c", v, a).integers(3, 6),
                                  rng_for("mrock_cn", v, a), angle=a, warp=24)
        ridge = np.clip((np.abs(cr - 0.5) - 0.42) * 24, 0, 1)
        arr -= ridge[..., None] * 40
    stamp_blobs(arr, rng, 200, lambda r: lerp_rgb(mid, light, r.random()), 3, 8, 0.08, 0.2)
    return _finish("mountain_rock", v, arr, grain=6)


def paint_rubble(v):
    dh, ds, dv = _jit(v, "rubble")
    dark = shift_hsv(hex_rgb("#40382e"), dh, ds, dv)
    mid = shift_hsv(hex_rgb("#6b6055"), dh, ds, dv)
    light = shift_hsv(hex_rgb("#8f8474"), dh, ds, dv)
    arr, field, _ = _base_from_gradient("rubble", v, 6, 4,
                                        [(0.0, dark), (0.5, mid), (1.0, light)])
    rng = pyrng_for("rubble", v)
    nrng = rng_for("rubble_n", v)
    # chunks of broken stone — angular polygons scattered, wrapping
    draw_img = Image.fromarray(np.zeros((TILE, TILE, 4), dtype=np.uint8), "RGBA")
    d = ImageDraw.Draw(draw_img, "RGBA")
    for _ in range(90):
        cx = rng.uniform(0, TILE)
        cy = rng.uniform(0, TILE)
        sz = rng.uniform(4, 13)
        tone = rng.uniform(0, 1)
        base = lerp_rgb(dark, light, tone)
        pts_n = rng.randint(4, 6)
        for ox in (-TILE, 0, TILE):
            for oy in (-TILE, 0, TILE):
                pts = []
                for k in range(pts_n):
                    ang = 2 * math.pi * k / pts_n + rng.uniform(-0.3, 0.3)
                    rr = sz * rng.uniform(0.6, 1.1)
                    pts.append((cx + ox + math.cos(ang) * rr, cy + oy + math.sin(ang) * rr))
                d.polygon(pts, fill=base + (255,), outline=tuple(dark) + (255,))
    chunks = np.asarray(draw_img).astype(np.float64)
    m = chunks[..., 3] > 4
    arr[m] = chunks[m, :3]
    # top-light each chunk slightly
    shade = fbm(TILE, 8, 3, rng_for("rubble_shade", v))
    arr[m] += ((shade[m] - 0.5) * 30)[..., None]
    stamp_blobs(arr, nrng, 200, lambda r: dark, 1, 2, 0.08, 0.2)
    return _finish("rubble", v, arr, edge=0.14, grain=6)


def paint_pit(v):
    # a dark hole: radial gradient to near-black center, rocky rim
    dh, ds, dv = _jit(v, "pit")
    rim = shift_hsv(hex_rgb("#5a5148"), dh, ds, dv)
    mid = shift_hsv(hex_rgb("#2a2420"), dh, ds, dv)
    deep = hex_rgb("#080605")
    yy, xx = np.mgrid[0:TILE, 0:TILE].astype(np.float64)
    cx = cy = (TILE - 1) / 2
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / (TILE * 0.5)
    d = np.clip(d, 0, 1)
    arr = gradient_map(d, [(0.0, deep), (0.55, mid), (0.9, rim), (1.0, rim)]).astype(np.float64)
    rng = rng_for("pit_brush", v)
    # jagged rocky rim speckle
    stamp_blobs(arr, rng, 120, lambda r: lerp_rgb(mid, rim, r.random()), 2, 5, 0.1, 0.24)
    # NOTE: pit does NOT tile seamlessly on purpose (it is a discrete hazard feature,
    # placed as a single cell, not a repeating field). It gets a real border vignette.
    return _finish("pit", v, arr, edge=0.05, grain=4, seam_safe=False)


# ---- Walls (top-down thickness reads) -----------------------------------------------

def _wall_block_pattern(v, terrain, mortar_hex, lo_hex, hi_hex, brick=False):
    dh, ds, dv = _jit(v, terrain)
    mortar = shift_hsv(hex_rgb(mortar_hex), dh, ds, dv)
    lo = shift_hsv(hex_rgb(lo_hex), dh, ds, dv)
    hi = shift_hsv(hex_rgb(hi_hex), dh, ds, dv)
    arr = np.zeros((TILE, TILE, 3), dtype=np.float64)
    arr[:] = mortar
    rng = pyrng_for(terrain, v)
    draw_img = Image.fromarray(np.zeros((TILE, TILE, 3), dtype=np.uint8))
    d = ImageDraw.Draw(draw_img, "RGBA")
    if brick:
        rows = 5
        cols = 3
        bh = TILE / rows
        bw = TILE / cols
        for r in range(rows):
            offset = (bw / 2) if (r % 2) else 0.0
            for c in range(-1, cols + 1):
                x0 = c * bw + offset + rng.uniform(1, 2.5)
                y0 = r * bh + rng.uniform(1, 2.5)
                x1 = x0 + bw - rng.uniform(2, 4)
                y1 = y0 + bh - rng.uniform(2, 4)
                tone = rng.uniform(0, 1)
                base = lerp_rgb(lo, hi, tone)
                for ox in (-TILE, 0, TILE):
                    d.rounded_rectangle([x0 + ox, y0, x1 + ox, y1], radius=2,
                                        fill=base + (255,))
    else:
        # irregular ashlar stone blocks, 3 rows staggered
        rows = 3
        bh = TILE / rows
        for r in range(rows):
            x = rng.uniform(-30, 0)
            while x < TILE:
                w = rng.uniform(28, 46)
                pad = rng.uniform(1.5, 3)
                tone = rng.uniform(0, 1)
                base = lerp_rgb(lo, hi, tone)
                for ox in (-TILE, 0, TILE):
                    d.rounded_rectangle([x + pad + ox, r * bh + pad,
                                         x + w - pad + ox, (r + 1) * bh - pad],
                                        radius=3, fill=base + (255,))
                x += w
    blocks = np.asarray(draw_img).astype(np.float64)
    mask = blocks.sum(axis=2) > 4
    arr[mask] = blocks[mask]
    # per-block noise + bevel (top edge lighter, bottom darker -> thickness read)
    field = fbm(TILE, 7, 4, rng_for(terrain + "_shade", v))
    arr[mask] += ((field[mask] - 0.5) * 30)[..., None]
    # bevel: brighten upper portion of blocks, darken lower
    yy = np.mgrid[0:TILE, 0:TILE][0].astype(np.float64)
    bevel = np.zeros((TILE, TILE))
    # cheap bevel via vertical gradient within each row band
    band = bh if not brick else (TILE / 5)
    local_y = (yy % band) / band  # 0 top .. 1 bottom
    bevel = (0.5 - local_y) * 26
    arr[mask] += bevel[mask][..., None]
    # weathering speckle
    stamp_blobs(arr, rng_for(terrain + "_n", v), 200,
                lambda r: lerp_rgb(lo, hi, r.random()), 1, 3, 0.06, 0.16)
    return arr, mortar, lo, hi


def paint_stone_wall(v):
    arr, *_ = _wall_block_pattern(v, "stone_wall", "#1f1d1a", "#605a51", "#867f72", brick=False)
    return _finish("stone_wall", v, arr, edge=0.20, grain=5)


def paint_brick_wall(v):
    arr, *_ = _wall_block_pattern(v, "brick_wall", "#2a211c", "#8a4a34", "#b06a4a", brick=True)
    return _finish("brick_wall", v, arr, edge=0.18, grain=5)


def paint_wood_wall(v):
    # top-down thick timber wall: heavy planks + beam edges
    dh, ds, dv = _jit(v, "wood_wall")
    dark = shift_hsv(hex_rgb("#3f2a17"), dh, ds, dv)
    mid = shift_hsv(hex_rgb("#5a3d22"), dh, ds, dv)
    light = shift_hsv(hex_rgb("#78552f"), dh, ds, dv)
    rng = pyrng_for("wwall", v)
    arr = np.zeros((TILE, TILE, 3), dtype=np.float64)
    n = 5
    pw = TILE / n
    for i in range(n):
        x0 = int(i * pw)
        x1 = int((i + 1) * pw)
        base = lerp_rgb(dark, light, rng.uniform(0, 1))
        grain = tileable_directional(TILE, 1, rng_for("wwall_g", v, i), angle=90, warp=28)
        seg = np.zeros((TILE, x1 - x0, 3))
        seg[:] = base
        seg += (grain[:, x0:x1] - 0.5)[..., None] * 24
        arr[:, x0:x1] = seg
    for i in range(n + 1):
        x = int(round(i * pw)) % TILE
        arr[:, max(0, x - 1):x + 1] *= 0.4
    # bevel top/bottom for thickness
    yy = np.mgrid[0:TILE, 0:TILE][0].astype(np.float64) / TILE
    arr += ((0.5 - yy) * 20)[..., None]
    return _finish("wood_wall", v, arr, edge=0.2, grain=5)


def paint_cave_wall(v):
    # top-down rock mass: rough dark stone, no regular blocks
    dh, ds, dv = _jit(v, "cave_wall")
    dark = shift_hsv(hex_rgb("#211d1a"), dh, ds, dv)
    mid = shift_hsv(hex_rgb("#3a332d"), dh, ds, dv)
    light = shift_hsv(hex_rgb("#544b41"), dh, ds, dv)
    arr, field, _ = _base_from_gradient("cave_wall", v, 4, 5,
                                        [(0.0, dark), (0.5, mid), (1.0, light)])
    rng = rng_for("cwall_brush", v)
    # lumpy protrusions (top-lit) and deep crevices
    stamp_blobs(arr, rng, 160, lambda r: lerp_rgb(mid, light, r.random()), 4, 11, 0.12, 0.28)
    stamp_blobs(arr, rng, 200, lambda r: dark, 2, 6, 0.14, 0.34)
    # crevice ridges
    for a in (25, 115):
        cr = tileable_directional(TILE, rng_for("cwall_c", v, a).integers(3, 6),
                                  rng_for("cwall_cn", v, a), angle=a, warp=26)
        ridge = np.clip((np.abs(cr - 0.5) - 0.4) * 22, 0, 1)
        arr -= ridge[..., None] * 46
    return _finish("cave_wall", v, arr, edge=0.22, grain=6)


# --------------------------------------------------------------------------------------
# Terrain registry
# --------------------------------------------------------------------------------------

# (painter, variant_count, manifest flags)
TERRAINS = {
    "grass":         (paint_grass,        5, dict(display="Grass", walkable=True, blocking=False, hazard=False, difficult=False, indoor=False, priority=30)),
    "tall_grass":    (paint_tall_grass,   4, dict(display="Tall Grass", walkable=True, blocking=False, hazard=False, difficult=True, indoor=False, priority=32)),
    "forest_floor":  (paint_forest_floor, 5, dict(display="Forest Floor", walkable=True, blocking=False, hazard=False, difficult=False, indoor=False, priority=34)),
    "jungle_floor":  (paint_jungle_floor, 4, dict(display="Jungle Floor", walkable=True, blocking=False, hazard=False, difficult=True, indoor=False, priority=35)),
    "dirt":          (paint_dirt,         5, dict(display="Dirt", walkable=True, blocking=False, hazard=False, difficult=False, indoor=False, priority=25)),
    "road":          (paint_road,         4, dict(display="Road", walkable=True, blocking=False, hazard=False, difficult=False, indoor=False, priority=45)),
    "cobblestone":   (paint_cobblestone,  4, dict(display="Cobblestone", walkable=True, blocking=False, hazard=False, difficult=False, indoor=False, priority=46)),
    "sand":          (paint_sand,         5, dict(display="Sand", walkable=True, blocking=False, hazard=False, difficult=False, indoor=False, priority=28)),
    "snow":          (paint_snow,         4, dict(display="Snow", walkable=True, blocking=False, hazard=False, difficult=True, indoor=False, priority=29)),
    "ice":           (paint_ice,          4, dict(display="Ice", walkable=True, blocking=False, hazard=False, difficult=True, indoor=False, priority=40)),
    "swamp":         (paint_swamp,        4, dict(display="Swamp", walkable=True, blocking=False, hazard=False, difficult=True, indoor=False, priority=33)),
    "mud":           (paint_mud,          4, dict(display="Mud", walkable=True, blocking=False, hazard=False, difficult=True, indoor=False, priority=27)),
    "water_shallow": (paint_water_shallow,4, dict(display="Shallow Water", walkable=True, blocking=False, hazard=False, difficult=True, indoor=False, priority=50)),
    "water_deep":    (paint_water_deep,   4, dict(display="Deep Water", walkable=False, blocking=False, hazard=True, difficult=False, indoor=False, priority=52)),
    "river":         (paint_river,        4, dict(display="River", walkable=False, blocking=False, hazard=True, difficult=False, indoor=False, priority=51)),
    "lava":          (paint_lava,         4, dict(display="Lava", walkable=False, blocking=False, hazard=True, difficult=False, indoor=False, priority=60)),
    "volcanic_rock": (paint_volcanic_rock,4, dict(display="Volcanic Rock", walkable=True, blocking=False, hazard=False, difficult=False, indoor=False, priority=36)),
    "stone_floor":   (paint_stone_floor,  5, dict(display="Stone Floor", walkable=True, blocking=False, hazard=False, difficult=False, indoor=True, priority=44)),
    "wood_floor":    (paint_wood_floor,   5, dict(display="Wood Floor", walkable=True, blocking=False, hazard=False, difficult=False, indoor=True, priority=43)),
    "cave_floor":    (paint_cave_floor,   5, dict(display="Cave Floor", walkable=True, blocking=False, hazard=False, difficult=False, indoor=True, priority=37)),
    "mountain_rock": (paint_mountain_rock,4, dict(display="Mountain Rock", walkable=True, blocking=False, hazard=False, difficult=True, indoor=False, priority=38)),
    "stone_wall":    (paint_stone_wall,   4, dict(display="Stone Wall", walkable=False, blocking=True, hazard=False, difficult=False, indoor=True, priority=90)),
    "brick_wall":    (paint_brick_wall,   4, dict(display="Brick Wall", walkable=False, blocking=True, hazard=False, difficult=False, indoor=True, priority=91)),
    "wood_wall":     (paint_wood_wall,    4, dict(display="Wood Wall", walkable=False, blocking=True, hazard=False, difficult=False, indoor=True, priority=89)),
    "cave_wall":     (paint_cave_wall,    4, dict(display="Cave Wall", walkable=False, blocking=True, hazard=False, difficult=False, indoor=True, priority=88)),
    "rubble":        (paint_rubble,       4, dict(display="Rubble", walkable=True, blocking=False, hazard=False, difficult=True, indoor=False, priority=42)),
    "ship_deck":     (paint_ship_deck,    4, dict(display="Ship Deck", walkable=True, blocking=False, hazard=False, difficult=False, indoor=True, priority=44)),
    "marble_floor":  (paint_marble_floor, 4, dict(display="Marble Floor", walkable=True, blocking=False, hazard=False, difficult=False, indoor=True, priority=45)),
    "pit":           (paint_pit,          3, dict(display="Pit", walkable=False, blocking=False, hazard=True, difficult=False, indoor=True, priority=58)),
}


# ======================================================================================
# PROP RENDERING TOOLKIT
# ======================================================================================
# Props render on a transparent RGBA canvas at footprint * TILE px. Painterly top-down
# shapes with irregular hand-drawn dark outlines (width/position jitter), soft internal
# shading, NO baked drop shadows. Everything derives from rng_for(prop, variant).

SS = 3  # supersample factor for smooth painterly edges


class Canvas:
    """A supersampled RGBA drawing surface with painterly helpers."""

    def __init__(self, w_cells, h_cells, rng):
        self.wc, self.hc = w_cells, h_cells
        self.W = w_cells * TILE
        self.H = h_cells * TILE
        self.sw = self.W * SS
        self.sh = self.H * SS
        self.img = Image.new("RGBA", (self.sw, self.sh), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img, "RGBA")
        self.rng = rng

    def _s(self, v):
        return v * SS

    def blob(self, cx, cy, r, color, alpha=255, squash=1.0):
        cx, cy, r = self._s(cx), self._s(cy), self._s(r)
        rx, ry = r, r / squash
        self.d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry],
                       fill=tuple(color) + (int(alpha),))

    def poly(self, pts, color, alpha=255, outline=None, ow=0):
        p = [(self._s(x), self._s(y)) for (x, y) in pts]
        kw = {}
        if outline is not None:
            kw["outline"] = tuple(outline) + (255,)
            kw["width"] = max(1, int(self._s(ow)))
        self.d.polygon(p, fill=tuple(color) + (int(alpha),), **kw)

    def line(self, p0, p1, color, w, alpha=255):
        self.d.line([self._s(p0[0]), self._s(p0[1]), self._s(p1[0]), self._s(p1[1])],
                    fill=tuple(color) + (int(alpha),), width=max(1, int(self._s(w))))

    def ellipse(self, cx, cy, rx, ry, color, alpha=255, outline=None, ow=0):
        cx, cy, rx, ry = self._s(cx), self._s(cy), self._s(rx), self._s(ry)
        kw = {}
        if outline is not None:
            kw["outline"] = tuple(outline) + (255,)
            kw["width"] = max(1, int(self._s(ow)))
        self.d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry],
                       fill=tuple(color) + (int(alpha),), **kw)

    def rrect(self, x0, y0, x1, y1, r, color, alpha=255, outline=None, ow=0):
        coords = [self._s(x0), self._s(y0), self._s(x1), self._s(y1)]
        kw = {}
        if outline is not None:
            kw["outline"] = tuple(outline) + (255,)
            kw["width"] = max(1, int(self._s(ow)))
        self.d.rounded_rectangle(coords, radius=self._s(r),
                                 fill=tuple(color) + (int(alpha),), **kw)

    def wobbly_outline(self, pts, color, width, jitter=1.4, closed=True):
        """Hand-drawn dark outline: perturb each vertex and draw thick jointed segments."""
        r = self.rng
        jp = []
        for (x, y) in pts:
            jp.append((x + r.uniform(-jitter, jitter), y + r.uniform(-jitter, jitter)))
        seq = jp + [jp[0]] if closed else jp
        for i in range(len(seq) - 1):
            w = width * r.uniform(0.7, 1.25)
            self.line(seq[i], seq[i + 1], color, w)
        # round the joints so the outline doesn't gap
        for (x, y) in seq:
            self.blob(x, y, width * 0.55, color)

    def finalize(self):
        """Downsample to target size (antialias) and return RGBA numpy-ready image."""
        return self.img.resize((self.W, self.H), Image.LANCZOS)


def soft_shade(img: Image.Image, rng, light_from=(-0.4, -0.6), strength=42):
    """
    Add a soft directional internal shading gradient to opaque pixels of an RGBA image
    (light from upper-left by default) plus fine noise. Returns a new image.
    NO drop shadow (transparent pixels stay transparent). `rng` is a random.Random;
    the grain field is drawn from a numpy generator seeded deterministically from it.
    """
    arr = np.asarray(img).astype(np.float64)
    h, w = arr.shape[:2]
    a = arr[..., 3]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    lx, ly = light_from
    # normalized directional ramp
    ramp = (xx / w * lx + yy / h * ly)
    ramp -= ramp.min()
    ramp /= max(1e-9, ramp.max())
    delta = (0.5 - ramp) * strength
    grain_seed = int(rng.random() * (2 ** 31))
    grain = (np.random.default_rng(grain_seed).random((h, w)) - 0.5) * 8
    for c in range(3):
        arr[..., c] = np.where(a > 0, np.clip(arr[..., c] + delta + grain, 0, 255), arr[..., c])
    return Image.fromarray(arr.astype(np.uint8), "RGBA")


# --------------------------------------------------------------------------------------
# Individual prop painters. Each returns a finalized RGBA PIL image (footprint*TILE).
# --------------------------------------------------------------------------------------

OUTLINE = (26, 20, 14)  # near-black warm outline for hand-drawn look


def _wobble_pts(cx, cy, r, n, rng, irregular=0.12):
    pts = []
    for k in range(n):
        ang = 2 * math.pi * k / n
        rr = r * (1 + rng.uniform(-irregular, irregular))
        pts.append((cx + math.cos(ang) * rr, cy + math.sin(ang) * rr))
    return pts


def prop_rock(v):
    rng = pyrng_for("rock", v)
    c = Canvas(1, 1, rng)
    cx, cy = 70, 74
    base = shift_hsv(hex_rgb("#7c766c"), 0, rng.uniform(-0.05, 0.05), rng.uniform(-0.08, 0.08))
    pts = _wobble_pts(cx, cy, 42, 7, rng, 0.18)
    c.wobbly_outline(pts, OUTLINE, 4, jitter=2.0)
    c.poly(pts, base)
    # facets: lighter top-left plane, darker bottom-right
    top = shift_hsv(base, 0, -0.05, 0.14)
    bot = shift_hsv(base, 0, 0.04, -0.14)
    c.poly([(cx - 30, cy - 20), (cx + 6, cy - 30), (cx + 18, cy - 4), (cx - 18, cy + 4)], top, 200)
    c.poly([(cx - 12, cy + 8), (cx + 24, cy - 2), (cx + 20, cy + 26), (cx - 14, cy + 26)], bot, 150)
    img = c.finalize()
    return soft_shade(img, rng, strength=30)


def prop_crate(v):
    rng = pyrng_for("crate", v)
    c = Canvas(1, 1, rng)
    wood = shift_hsv(hex_rgb("#8a5f30"), 0, 0, rng.uniform(-0.06, 0.06))
    dark = shift_hsv(wood, 0, 0.05, -0.18)
    light = shift_hsv(wood, 0, -0.03, 0.14)
    x0, y0, x1, y1 = 22, 22, 118, 118
    c.rrect(x0, y0, x1, y1, 6, wood, outline=OUTLINE, ow=4)
    # plank cross boards
    for i in range(1, 3):
        yy = y0 + (y1 - y0) * i / 3
        c.line((x0, yy), (x1, yy), dark, 3)
    c.line(((x0 + x1) / 2, y0), ((x0 + x1) / 2, y1), dark, 3)
    # diagonal brace + rim highlight
    c.line((x0 + 6, y1 - 6), (x1 - 6, y0 + 6), light, 3, alpha=160)
    img = c.finalize()
    return soft_shade(img, rng, strength=34)


def prop_barrel(v):
    rng = pyrng_for("barrel", v)
    c = Canvas(1, 1, rng)
    wood = shift_hsv(hex_rgb("#7a5228"), 0, 0, rng.uniform(-0.05, 0.05))
    cx, cy, r = 70, 70, 46
    c.ellipse(cx, cy, r, r, wood, outline=OUTLINE, ow=4)
    # iron rings (concentric)
    for rr in (r - 4, r - 20):
        c.ellipse(cx, cy, rr, rr, (0, 0, 0), alpha=0, outline=hex_rgb("#4a4038"), ow=3)
    # top staves radiating
    for k in range(8):
        ang = 2 * math.pi * k / 8
        c.line((cx, cy), (cx + math.cos(ang) * (r - 3), cy + math.sin(ang) * (r - 3)),
               shift_hsv(wood, 0, 0.04, -0.12), 1)
    # center bung + top highlight
    c.ellipse(cx, cy, 6, 6, shift_hsv(wood, 0, 0.05, -0.2))
    c.ellipse(cx - 12, cy - 12, 16, 16, shift_hsv(wood, 0, -0.05, 0.18), alpha=90)
    img = c.finalize()
    return soft_shade(img, rng, strength=30)


def prop_low_wall(v):
    rng = pyrng_for("low_wall", v)
    c = Canvas(1, 1, rng)
    stone = shift_hsv(hex_rgb("#6f6a60"), 0, 0, rng.uniform(-0.05, 0.05))
    dark = shift_hsv(stone, 0, 0.04, -0.16)
    # a run of stacked stones across the tile
    y0, y1 = 44, 96
    c.rrect(6, y0, 134, y1, 6, stone, outline=OUTLINE, ow=4)
    # individual stone divisions
    x = 12
    while x < 130:
        w = rng.uniform(18, 30)
        c.line((x, y0), (x, y1), dark, 2, alpha=180)
        # per-stone highlight
        c.ellipse(x + w / 2, (y0 + y1) / 2 - 6, w / 2 - 3, 10,
                  shift_hsv(stone, 0, -0.04, 0.12), alpha=120)
        x += w
    img = c.finalize()
    return soft_shade(img, rng, strength=28)


def prop_fallen_log(v):
    rng = pyrng_for("fallen_log", v)
    c = Canvas(2, 1, rng)  # 2x1 footprint
    bark = shift_hsv(hex_rgb("#5c4326"), 0, 0, rng.uniform(-0.05, 0.05))
    inner = hex_rgb("#a9824c")
    y = 70
    c.rrect(14, y - 26, 266, y + 26, 24, bark, outline=OUTLINE, ow=4)
    # bark grain lines along length
    for i in range(-2, 3):
        yy = y + i * 9
        c.line((22, yy + rng.uniform(-2, 2)), (258, yy + rng.uniform(-2, 2)),
               shift_hsv(bark, 0, 0.04, -0.12), 2, alpha=150)
    # cut end rings
    c.ellipse(266, y, 20, 24, inner, outline=OUTLINE, ow=3)
    for rr in (15, 9, 4):
        c.ellipse(266, y, rr, rr * 1.15, (0, 0, 0), alpha=0,
                  outline=shift_hsv(inner, 0, 0.1, -0.15), ow=2)
    img = c.finalize()
    return soft_shade(img, rng, strength=30)


# ---- Furniture ----------------------------------------------------------------------

def prop_table(v):
    rng = pyrng_for("table", v)
    c = Canvas(1, 1, rng)
    wood = shift_hsv(hex_rgb("#7a5630"), 0, 0, rng.uniform(-0.05, 0.05))
    c.rrect(18, 26, 122, 114, 8, wood, outline=OUTLINE, ow=4)
    # plank lines
    for i in range(1, 4):
        yy = 26 + (114 - 26) * i / 4
        c.line((18, yy), (122, yy), shift_hsv(wood, 0, 0.05, -0.14), 2, alpha=150)
    # edge highlight
    c.rrect(24, 32, 116, 108, 6, shift_hsv(wood, 0, -0.04, 0.12), alpha=0, outline=shift_hsv(wood, 0, -0.05, 0.16), ow=2)
    img = c.finalize()
    return soft_shade(img, rng, strength=30)


def prop_chair(v):
    rng = pyrng_for("chair", v)
    c = Canvas(1, 1, rng)
    wood = shift_hsv(hex_rgb("#6f4c2a"), 0, 0, rng.uniform(-0.05, 0.05))
    # seat
    c.rrect(46, 50, 96, 100, 6, wood, outline=OUTLINE, ow=3)
    # backrest (top edge, thicker)
    c.rrect(46, 40, 96, 52, 4, shift_hsv(wood, 0, 0.03, -0.1), outline=OUTLINE, ow=3)
    # legs (corner dots)
    for (lx, ly) in [(50, 54), (92, 54), (50, 96), (92, 96)]:
        c.ellipse(lx, ly, 4, 4, shift_hsv(wood, 0, 0.05, -0.2))
    img = c.finalize()
    return soft_shade(img, rng, strength=26)


def prop_bed(v):
    rng = pyrng_for("bed", v)
    c = Canvas(1, 2, rng)  # 1x2
    frame = shift_hsv(hex_rgb("#5c3d22"), 0, 0, rng.uniform(-0.05, 0.05))
    sheet = shift_hsv(hex_rgb("#b9a988"), 0, rng.uniform(-0.05, 0.05), 0)
    pillow = hex_rgb("#e8ddc4")
    c.rrect(14, 14, 126, 266, 12, frame, outline=OUTLINE, ow=4)
    c.rrect(22, 60, 118, 258, 8, sheet, outline=shift_hsv(frame, 0, 0, 0.1), ow=2)
    # pillow at top
    c.rrect(30, 24, 110, 66, 10, pillow, outline=OUTLINE, ow=2)
    # blanket fold line
    c.line((22, 150), (118, 150), shift_hsv(sheet, 0, 0.06, -0.14), 3, alpha=160)
    img = c.finalize()
    return soft_shade(img, rng, strength=26)


def prop_bookshelf(v):
    rng = pyrng_for("bookshelf", v)
    c = Canvas(1, 1, rng)
    wood = shift_hsv(hex_rgb("#4a3218"), 0, 0, rng.uniform(-0.05, 0.05))
    c.rrect(20, 14, 120, 126, 4, wood, outline=OUTLINE, ow=4)
    # shelves with book spines (top-down: rows of colored rectangles)
    book_cols = [hex_rgb(h) for h in ("#8a3a2a", "#2f5a3a", "#3a4a7a", "#7a6a2a", "#5a2f5a")]
    for row in range(3):
        y0 = 22 + row * 36
        x = 26
        while x < 114:
            w = rng.uniform(5, 11)
            col = shift_hsv(rng.choice(book_cols), 0, 0, rng.uniform(-0.1, 0.1))
            c.rrect(x, y0, x + w, y0 + 28, 1, col)
            x += w + 1.5
        c.line((20, y0 + 32), (120, y0 + 32), shift_hsv(wood, 0, 0.05, -0.16), 3)
    img = c.finalize()
    return soft_shade(img, rng, strength=24)


def prop_market_stall(v):
    rng = pyrng_for("market_stall", v)
    c = Canvas(2, 2, rng)  # 2x2 with awning
    stripe_a = shift_hsv(hex_rgb("#a8352f"), 0, 0, rng.uniform(-0.05, 0.05))
    stripe_b = hex_rgb("#e8ddc4")
    wood = hex_rgb("#6f4c2a")
    # counter frame
    c.rrect(30, 40, 250, 240, 10, wood, outline=OUTLINE, ow=4)
    # striped awning overlaid (top-down we see the awning fabric)
    n = 8
    for i in range(n):
        x0 = 30 + (250 - 30) * i / n
        x1 = 30 + (250 - 30) * (i + 1) / n
        col = stripe_a if i % 2 == 0 else stripe_b
        c.poly([(x0, 40), (x1, 40), (x1, 150), (x0, 150)], col)
    c.rrect(30, 40, 250, 240, 10, (0, 0, 0), alpha=0, outline=OUTLINE, ow=4)
    # goods on the lower counter (little blobs)
    for _ in range(10):
        gx = rng.uniform(50, 230)
        gy = rng.uniform(170, 225)
        col = shift_hsv(rng.choice([hex_rgb("#c98a3a"), hex_rgb("#7a9a3a"), hex_rgb("#a8352f")]), 0, 0, rng.uniform(-0.1, 0.1))
        c.ellipse(gx, gy, rng.uniform(6, 11), rng.uniform(6, 11), col, outline=OUTLINE, ow=2)
    img = c.finalize()
    return soft_shade(img, rng, strength=28)


def prop_anvil(v):
    rng = pyrng_for("anvil", v)
    c = Canvas(1, 1, rng)
    iron = shift_hsv(hex_rgb("#3a3a40"), 0, 0, rng.uniform(-0.03, 0.03))
    top = shift_hsv(iron, 0, -0.02, 0.16)
    # top-down anvil silhouette: body + horn
    pts = [(44, 52), (92, 52), (100, 62), (118, 70), (100, 78), (92, 88), (44, 88), (40, 70)]
    c.wobbly_outline(pts, OUTLINE, 4, jitter=1.3)
    c.poly(pts, iron)
    c.poly([(48, 56), (88, 56), (88, 70), (48, 70)], top, 180)
    img = c.finalize()
    return soft_shade(img, rng, strength=30)


def prop_bar_counter(v):
    rng = pyrng_for("bar_counter", v)
    c = Canvas(2, 1, rng)
    wood = shift_hsv(hex_rgb("#5c3d22"), 0, 0, rng.uniform(-0.05, 0.05))
    top = shift_hsv(wood, 0, -0.03, 0.14)
    c.rrect(14, 40, 266, 100, 10, wood, outline=OUTLINE, ow=4)
    c.rrect(20, 46, 260, 94, 8, top, alpha=200)
    # grain
    for i in range(-1, 2):
        yy = 70 + i * 14
        c.line((22, yy), (258, yy), shift_hsv(wood, 0, 0.05, -0.14), 2, alpha=140)
    # tankards on top
    for _ in range(4):
        gx = rng.uniform(40, 240)
        c.ellipse(gx, 70, 7, 7, hex_rgb("#8a7a4a"), outline=OUTLINE, ow=2)
    img = c.finalize()
    return soft_shade(img, rng, strength=28)


# ---- Nature -------------------------------------------------------------------------

def _tree(v, footprint, canopy_hex, trunk_hex, ragged=0.16, clusters=3):
    rng = pyrng_for("tree" + canopy_hex, v, footprint)
    c = Canvas(footprint, footprint, rng)
    cx = cy = footprint * TILE / 2
    R = footprint * TILE * 0.42
    canopy = shift_hsv(hex_rgb(canopy_hex), 0, rng.uniform(-0.05, 0.05), rng.uniform(-0.06, 0.06))
    dark = shift_hsv(canopy, 0, 0.06, -0.18)
    light = shift_hsv(canopy, 0, -0.04, 0.16)
    # ragged outline canopy
    pts = _wobble_pts(cx, cy, R, 16, rng, ragged)
    c.wobbly_outline(pts, shift_hsv(dark, 0, 0.1, -0.22), 4, jitter=3.0)
    c.poly(pts, canopy)
    # layered foliage clumps for painterly volume (top-lit)
    for _ in range(int(38 * footprint)):
        a = rng.uniform(0, 2 * math.pi)
        rr = rng.uniform(0, R * 0.85)
        bx = cx + math.cos(a) * rr
        by = cy + math.sin(a) * rr
        # lighter toward upper-left
        lit = (by < cy) and (bx < cx)
        col = light if lit and rng.random() > 0.4 else (dark if rng.random() > 0.6 else canopy)
        c.blob(bx, by, rng.uniform(8, 20) * footprint * 0.7, col, alpha=rng.randint(90, 170))
    # trunk hint at center (small)
    tr = shift_hsv(hex_rgb(trunk_hex), 0, 0, rng.uniform(-0.05, 0.05))
    c.ellipse(cx, cy, 8 * footprint, 8 * footprint, tr, alpha=150)
    img = c.finalize()
    return soft_shade(img, rng, strength=34)


def prop_tree_oak(v):
    return _tree(v, 2, "#4a6b2c", "#4a3320", ragged=0.14)


def prop_tree_pine(v):
    # pine: star-shaped darker conifer
    rng = pyrng_for("pine", v, 2)
    fp = 2
    c = Canvas(fp, fp, rng)
    cx = cy = fp * TILE / 2
    R = fp * TILE * 0.42
    canopy = shift_hsv(hex_rgb("#2f4a24"), 0, rng.uniform(-0.04, 0.04), rng.uniform(-0.05, 0.05))
    dark = shift_hsv(canopy, 0, 0.06, -0.16)
    light = shift_hsv(canopy, 0, -0.04, 0.16)
    n = 11
    pts = []
    for k in range(n * 2):
        ang = math.pi * k / n
        rr = R if k % 2 == 0 else R * 0.62
        rr *= (1 + rng.uniform(-0.06, 0.06))
        pts.append((cx + math.cos(ang) * rr, cy + math.sin(ang) * rr))
    c.wobbly_outline(pts, shift_hsv(dark, 0, 0.1, -0.2), 3, jitter=2.2)
    c.poly(pts, canopy)
    for _ in range(60):
        a = rng.uniform(0, 2 * math.pi)
        rr = rng.uniform(0, R * 0.7)
        bx, by = cx + math.cos(a) * rr, cy + math.sin(a) * rr
        col = light if (bx < cx and by < cy and rng.random() > 0.5) else dark
        c.blob(bx, by, rng.uniform(6, 14), col, alpha=rng.randint(80, 150))
    c.ellipse(cx, cy, 6, 6, hex_rgb("#3a2a18"), alpha=140)
    img = c.finalize()
    return soft_shade(img, rng, strength=30)


def prop_tree_palm(v):
    rng = pyrng_for("palm", v, 2)
    fp = 2
    c = Canvas(fp, fp, rng)
    cx = cy = fp * TILE / 2
    frond = shift_hsv(hex_rgb("#4a7a34"), 0, rng.uniform(-0.05, 0.05), 0)
    dark = shift_hsv(frond, 0, 0.06, -0.16)
    # trunk center
    c.ellipse(cx, cy, 12, 12, hex_rgb("#7a5a34"), outline=OUTLINE, ow=3)
    # radiating fronds
    n = rng.randint(7, 9)
    for k in range(n):
        ang = 2 * math.pi * k / n + rng.uniform(-0.15, 0.15)
        L = fp * TILE * 0.42 * rng.uniform(0.8, 1.0)
        ex, ey = cx + math.cos(ang) * L, cy + math.sin(ang) * L
        # leaf as tapered polygon
        perp = ang + math.pi / 2
        w = 14
        pts = [(cx + math.cos(ang) * 12, cy + math.sin(ang) * 12),
               (cx + math.cos(ang) * L * 0.5 + math.cos(perp) * w, cy + math.sin(ang) * L * 0.5 + math.sin(perp) * w),
               (ex, ey),
               (cx + math.cos(ang) * L * 0.5 - math.cos(perp) * w, cy + math.sin(ang) * L * 0.5 - math.sin(perp) * w)]
        c.poly(pts, frond if k % 2 else dark, outline=shift_hsv(dark, 0, 0.1, -0.2), ow=2)
        c.line((cx, cy), (ex, ey), shift_hsv(dark, 0, 0.1, -0.2), 2, alpha=160)
    img = c.finalize()
    return soft_shade(img, rng, strength=28)


def prop_tree_dead(v):
    rng = pyrng_for("dead_tree", v, 2)
    fp = 2
    c = Canvas(fp, fp, rng)
    cx = cy = fp * TILE / 2
    wood = shift_hsv(hex_rgb("#4a3d2e"), 0, 0, rng.uniform(-0.05, 0.05))
    c.ellipse(cx, cy, 14, 14, wood, outline=OUTLINE, ow=3)
    n = rng.randint(5, 7)
    for k in range(n):
        ang = 2 * math.pi * k / n + rng.uniform(-0.2, 0.2)
        L = fp * TILE * 0.4 * rng.uniform(0.7, 1.0)
        ex, ey = cx + math.cos(ang) * L, cy + math.sin(ang) * L
        c.line((cx, cy), (ex, ey), wood, 7, alpha=255)
        c.line((cx, cy), (ex, ey), shift_hsv(wood, 0, 0.06, -0.16), 3, alpha=200)
        # sub-branch
        mx, my = (cx + ex) / 2, (cy + ey) / 2
        ang2 = ang + rng.uniform(-0.8, 0.8)
        c.line((mx, my), (mx + math.cos(ang2) * L * 0.35, my + math.sin(ang2) * L * 0.35), wood, 4)
    img = c.finalize()
    return soft_shade(img, rng, strength=26)


def prop_bush(v):
    rng = pyrng_for("bush", v)
    c = Canvas(1, 1, rng)
    cx = cy = 70
    green = shift_hsv(hex_rgb("#456b2c"), 0, rng.uniform(-0.05, 0.05), rng.uniform(-0.06, 0.06))
    dark = shift_hsv(green, 0, 0.06, -0.16)
    light = shift_hsv(green, 0, -0.04, 0.16)
    pts = _wobble_pts(cx, cy, 46, 13, rng, 0.2)
    c.wobbly_outline(pts, shift_hsv(dark, 0, 0.1, -0.2), 3, jitter=2.4)
    c.poly(pts, green)
    for _ in range(40):
        a = rng.uniform(0, 2 * math.pi)
        rr = rng.uniform(0, 40)
        bx, by = cx + math.cos(a) * rr, cy + math.sin(a) * rr
        col = light if (bx < cx and by < cy and rng.random() > 0.5) else (dark if rng.random() > 0.6 else green)
        c.blob(bx, by, rng.uniform(6, 14), col, alpha=rng.randint(90, 160))
    # a few berries
    if v % 2 == 0:
        for _ in range(6):
            c.ellipse(cx + rng.uniform(-30, 30), cy + rng.uniform(-30, 30), 3, 3, hex_rgb("#a83a3a"))
    img = c.finalize()
    return soft_shade(img, rng, strength=30)


def prop_stump(v):
    rng = pyrng_for("stump", v)
    c = Canvas(1, 1, rng)
    cx = cy = 70
    bark = shift_hsv(hex_rgb("#5c4326"), 0, 0, rng.uniform(-0.05, 0.05))
    inner = hex_rgb("#a9824c")
    pts = _wobble_pts(cx, cy, 40, 11, rng, 0.12)
    c.wobbly_outline(pts, OUTLINE, 4, jitter=2.0)
    c.poly(pts, bark)
    c.ellipse(cx, cy, 30, 30, inner)
    for rr in (24, 17, 10, 4):
        c.ellipse(cx, cy, rr, rr, (0, 0, 0), alpha=0, outline=shift_hsv(inner, 0, 0.1, -0.15), ow=2)
    # radial cracks
    for k in range(rng.randint(2, 4)):
        ang = rng.uniform(0, 2 * math.pi)
        c.line((cx, cy), (cx + math.cos(ang) * 30, cy + math.sin(ang) * 30),
               shift_hsv(inner, 0, 0.15, -0.3), 2)
    img = c.finalize()
    return soft_shade(img, rng, strength=26)


def prop_stalagmite(v):
    rng = pyrng_for("stalagmite", v)
    c = Canvas(1, 1, rng)
    cx = cy = 70
    rock = shift_hsv(hex_rgb("#565049"), 0, 0, rng.uniform(-0.05, 0.05))
    light = shift_hsv(rock, 0, -0.05, 0.2)
    dark = shift_hsv(rock, 0, 0.05, -0.2)
    # concentric rings for a cone seen top-down, small bright tip
    for rr, col in ((44, dark), (32, rock), (20, shift_hsv(rock, 0, -0.02, 0.1)), (9, light)):
        pts = _wobble_pts(cx, cy, rr, 10, rng, 0.12)
        c.poly(pts, col)
    c.wobbly_outline(_wobble_pts(cx, cy, 44, 10, rng, 0.12), OUTLINE, 3, jitter=2.0)
    img = c.finalize()
    return soft_shade(img, rng, strength=24)


def prop_lily_pad(v):
    rng = pyrng_for("lily_pad", v)
    c = Canvas(1, 1, rng)
    for _ in range(rng.randint(2, 3)):
        cx = rng.uniform(40, 100)
        cy = rng.uniform(40, 100)
        r = rng.uniform(22, 32)
        green = shift_hsv(hex_rgb("#3f7a3a"), 0, rng.uniform(-0.05, 0.05), rng.uniform(-0.05, 0.05))
        pts = _wobble_pts(cx, cy, r, 14, rng, 0.06)
        c.poly(pts, green, outline=shift_hsv(green, 0, 0.1, -0.2), ow=2)
        # notch (V slice)
        ang = rng.uniform(0, 2 * math.pi)
        c.poly([(cx, cy), (cx + math.cos(ang - 0.3) * r, cy + math.sin(ang - 0.3) * r),
                (cx + math.cos(ang + 0.3) * r, cy + math.sin(ang + 0.3) * r)], (0, 0, 0), alpha=0)
        c.ellipse(cx, cy, r * 0.5, r * 0.5, shift_hsv(green, 0, -0.05, 0.12), alpha=90)
        if rng.random() > 0.5:
            c.ellipse(cx, cy, 5, 5, hex_rgb("#e8c8e0"))  # flower
    img = c.finalize()
    return soft_shade(img, rng, strength=18)


def prop_coral(v):
    rng = pyrng_for("coral", v)
    c = Canvas(1, 1, rng)
    cx = cy = 70
    cols = [hex_rgb(h) for h in ("#c8663a", "#d88a4a", "#a83a5a", "#c8a04a")]
    base = shift_hsv(rng.choice(cols), 0, rng.uniform(-0.06, 0.06), 0)
    for k in range(rng.randint(5, 8)):
        ang = 2 * math.pi * k / 6 + rng.uniform(-0.3, 0.3)
        L = rng.uniform(24, 42)
        ex, ey = cx + math.cos(ang) * L, cy + math.sin(ang) * L
        col = shift_hsv(base, 0, 0, rng.uniform(-0.1, 0.1))
        c.line((cx, cy), (ex, ey), col, 8)
        c.ellipse(ex, ey, 6, 6, shift_hsv(col, 0, -0.05, 0.12))
    c.ellipse(cx, cy, 14, 14, base)
    img = c.finalize()
    return soft_shade(img, rng, strength=22)


def prop_mushroom(v):
    rng = pyrng_for("mushroom", v)
    c = Canvas(1, 1, rng)
    caps = [hex_rgb(h) for h in ("#a83a3a", "#8a5aa8", "#c88a3a")]
    for _ in range(rng.randint(2, 4)):
        cx = rng.uniform(45, 95)
        cy = rng.uniform(45, 95)
        r = rng.uniform(12, 22)
        cap = shift_hsv(rng.choice(caps), 0, rng.uniform(-0.06, 0.06), 0)
        c.ellipse(cx, cy, r, r, cap, outline=OUTLINE, ow=2)
        # spots
        for _ in range(rng.randint(3, 6)):
            c.ellipse(cx + rng.uniform(-r * 0.6, r * 0.6), cy + rng.uniform(-r * 0.6, r * 0.6),
                      2.5, 2.5, hex_rgb("#f0e8d8"))
        c.ellipse(cx - r * 0.3, cy - r * 0.3, r * 0.35, r * 0.35, shift_hsv(cap, 0, -0.1, 0.18), alpha=110)
    img = c.finalize()
    return soft_shade(img, rng, strength=20)


# ---- Hazards ------------------------------------------------------------------------

def prop_spike_trap(v):
    rng = pyrng_for("spike_trap", v)
    c = Canvas(1, 1, rng)
    plate = shift_hsv(hex_rgb("#4a4640"), 0, 0, rng.uniform(-0.03, 0.03))
    c.rrect(20, 20, 120, 120, 6, plate, outline=OUTLINE, ow=4)
    # metal spikes poking up (top-down: small triangles/diamonds)
    for gy in range(3):
        for gx in range(3):
            sx = 38 + gx * 32
            sy = 38 + gy * 32
            steel = shift_hsv(hex_rgb("#8a8a92"), 0, 0, rng.uniform(-0.05, 0.05))
            c.poly([(sx, sy - 9), (sx + 8, sy), (sx, sy + 9), (sx - 8, sy)], steel, outline=OUTLINE, ow=2)
            c.poly([(sx, sy - 9), (sx + 4, sy - 2), (sx, sy)], shift_hsv(steel, 0, -0.1, 0.25), alpha=200)
    img = c.finalize()
    return soft_shade(img, rng, strength=24)


def prop_bones(v):
    rng = pyrng_for("bones", v)
    c = Canvas(1, 1, rng)
    bone = shift_hsv(hex_rgb("#d8cdb4"), 0, rng.uniform(-0.04, 0.04), 0)
    dark = shift_hsv(bone, 0, 0.08, -0.2)
    # skull
    if v % 2 == 0:
        c.ellipse(58, 60, 20, 22, bone, outline=OUTLINE, ow=3)
        c.ellipse(52, 58, 5, 6, dark)
        c.ellipse(64, 58, 5, 6, dark)
        c.poly([(54, 74), (62, 74), (58, 82)], dark)
    # scattered long bones
    for _ in range(rng.randint(3, 5)):
        x0 = rng.uniform(30, 110)
        y0 = rng.uniform(40, 110)
        ang = rng.uniform(0, math.pi)
        L = rng.uniform(20, 40)
        x1, y1 = x0 + math.cos(ang) * L, y0 + math.sin(ang) * L
        c.line((x0, y0), (x1, y1), bone, 6)
        c.ellipse(x0, y0, 5, 5, bone, outline=OUTLINE, ow=1)
        c.ellipse(x1, y1, 5, 5, bone, outline=OUTLINE, ow=1)
    img = c.finalize()
    return soft_shade(img, rng, strength=18)


# ---- Focal --------------------------------------------------------------------------

def prop_altar(v):
    rng = pyrng_for("altar", v)
    c = Canvas(1, 1, rng)
    stone = shift_hsv(hex_rgb("#8a8478"), 0, 0, rng.uniform(-0.04, 0.04))
    top = shift_hsv(stone, 0, -0.03, 0.14)
    c.rrect(28, 24, 112, 116, 6, shift_hsv(stone, 0, 0.04, -0.16), outline=OUTLINE, ow=4)
    c.rrect(34, 30, 106, 110, 5, stone)
    c.rrect(42, 40, 98, 100, 4, top, alpha=220)
    # rune circle + glow
    glow = hex_rgb("#c8a83a") if v % 2 == 0 else hex_rgb("#4aa8c8")
    c.ellipse(70, 70, 24, 24, (0, 0, 0), alpha=0, outline=glow, ow=3)
    for k in range(8):
        ang = 2 * math.pi * k / 8
        c.line((70 + math.cos(ang) * 20, 70 + math.sin(ang) * 20),
               (70 + math.cos(ang) * 24, 70 + math.sin(ang) * 24), glow, 2)
    img = c.finalize()
    return soft_shade(img, rng, strength=26)


def prop_well(v):
    rng = pyrng_for("well", v)
    c = Canvas(1, 1, rng)
    stone = shift_hsv(hex_rgb("#77726a"), 0, 0, rng.uniform(-0.04, 0.04))
    c.ellipse(70, 70, 48, 48, stone, outline=OUTLINE, ow=4)
    # stone ring segments
    for k in range(10):
        ang = 2 * math.pi * k / 10
        c.line((70 + math.cos(ang) * 34, 70 + math.sin(ang) * 34),
               (70 + math.cos(ang) * 48, 70 + math.sin(ang) * 48),
               shift_hsv(stone, 0, 0.05, -0.16), 2, alpha=170)
    c.ellipse(70, 70, 34, 34, shift_hsv(stone, 0, 0, -0.05))
    # dark water inside
    c.ellipse(70, 70, 26, 26, hex_rgb("#2a4652"))
    c.ellipse(64, 64, 8, 8, hex_rgb("#4a7686"), alpha=140)
    img = c.finalize()
    return soft_shade(img, rng, strength=24)


def prop_statue(v):
    rng = pyrng_for("statue", v)
    c = Canvas(1, 1, rng)
    stone = shift_hsv(hex_rgb("#9a948a"), 0, 0, rng.uniform(-0.04, 0.04))
    dark = shift_hsv(stone, 0, 0.05, -0.18)
    # circular pedestal
    c.ellipse(70, 70, 44, 44, dark, outline=OUTLINE, ow=4)
    c.ellipse(70, 70, 38, 38, shift_hsv(stone, 0, 0.02, -0.06))
    # top-down humanoid figure: head + shoulders
    c.ellipse(70, 62, 12, 12, stone, outline=OUTLINE, ow=2)  # head
    c.poly([(52, 84), (88, 84), (82, 96), (58, 96)], stone, outline=OUTLINE, ow=2)  # shoulders/robe
    c.ellipse(66, 60, 4, 4, shift_hsv(stone, 0, -0.05, 0.2), alpha=180)
    img = c.finalize()
    return soft_shade(img, rng, strength=26)


def prop_campfire(v):
    rng = pyrng_for("campfire", v)
    c = Canvas(1, 1, rng)
    # ring of stones
    for k in range(10):
        ang = 2 * math.pi * k / 10
        sx, sy = 70 + math.cos(ang) * 42, 70 + math.sin(ang) * 42
        col = shift_hsv(hex_rgb("#6b665e"), 0, 0, rng.uniform(-0.06, 0.06))
        c.ellipse(sx, sy, 9, 9, col, outline=OUTLINE, ow=2)
    # charred logs
    for k in range(3):
        ang = 2 * math.pi * k / 3 + 0.4
        c.line((70 - math.cos(ang) * 22, 70 - math.sin(ang) * 22),
               (70 + math.cos(ang) * 22, 70 + math.sin(ang) * 22), hex_rgb("#3a2f26"), 8)
    # flames (layered)
    for (r, col, a) in ((22, hex_rgb("#a8321a"), 220), (16, hex_rgb("#e8781a"), 230), (9, hex_rgb("#ffcf5a"), 240)):
        pts = []
        n = 9
        for k in range(n):
            ang = 2 * math.pi * k / n
            rr = r * (1 + rng.uniform(-0.25, 0.25))
            pts.append((70 + math.cos(ang) * rr, 70 + math.sin(ang) * rr))
        c.poly(pts, col, alpha=a)
    img = c.finalize()
    return soft_shade(img, rng, strength=14)


def prop_fountain(v):
    rng = pyrng_for("fountain", v)
    c = Canvas(2, 2, rng)
    stone = shift_hsv(hex_rgb("#8a847a"), 0, 0, rng.uniform(-0.04, 0.04))
    cx = cy = 140
    c.ellipse(cx, cy, 120, 120, shift_hsv(stone, 0, 0.05, -0.16), outline=OUTLINE, ow=5)
    c.ellipse(cx, cy, 104, 104, stone)
    c.ellipse(cx, cy, 90, 90, hex_rgb("#3a6a7a"))  # water
    # ripples
    for rr in (78, 60, 42):
        c.ellipse(cx, cy, rr, rr, (0, 0, 0), alpha=0, outline=hex_rgb("#5a94a4"), ow=2)
    # central pillar
    c.ellipse(cx, cy, 26, 26, shift_hsv(stone, 0, 0.03, -0.08), outline=OUTLINE, ow=3)
    c.ellipse(cx, cy, 12, 12, hex_rgb("#6ab0c0"))
    c.ellipse(cx - 8, cy - 8, 5, 5, hex_rgb("#b0e0e8"), alpha=180)
    img = c.finalize()
    return soft_shade(img, rng, strength=22)


def prop_brazier(v):
    rng = pyrng_for("brazier", v)
    c = Canvas(1, 1, rng)
    iron = shift_hsv(hex_rgb("#3a352f"), 0, 0, rng.uniform(-0.04, 0.04))
    c.ellipse(70, 70, 40, 40, iron, outline=OUTLINE, ow=4)
    c.ellipse(70, 70, 32, 32, shift_hsv(iron, 0, 0.03, -0.1))
    # coals + flame
    c.ellipse(70, 70, 24, 24, hex_rgb("#6b1f10"))
    for (r, col, a) in ((20, hex_rgb("#c8421a"), 220), (13, hex_rgb("#f0902a"), 235), (7, hex_rgb("#ffe07a"), 245)):
        pts = _wobble_pts(70, 70, r, 8, rng, 0.3)
        c.poly(pts, col, alpha=a)
    img = c.finalize()
    return soft_shade(img, rng, strength=14)


def prop_chest(v):
    rng = pyrng_for("chest", v)
    c = Canvas(1, 1, rng)
    wood = shift_hsv(hex_rgb("#6b4a26"), 0, 0, rng.uniform(-0.05, 0.05))
    iron = hex_rgb("#3a352f")
    gold = hex_rgb("#c8a83a")
    c.rrect(30, 40, 110, 104, 6, wood, outline=OUTLINE, ow=4)
    # lid split
    c.line((30, 62), (110, 62), shift_hsv(wood, 0, 0.06, -0.2), 3)
    # iron bands
    for x in (48, 92):
        c.line((x, 40), (x, 104), iron, 5)
    # lock
    c.rrect(64, 56, 76, 70, 2, gold, outline=OUTLINE, ow=2)
    c.rrect(35, 45, 105, 58, 4, shift_hsv(wood, 0, -0.04, 0.14), alpha=120)
    img = c.finalize()
    return soft_shade(img, rng, strength=26)


def prop_shipwreck(v):
    rng = pyrng_for("shipwreck", v, 2)
    c = Canvas(2, 2, rng)
    wood = shift_hsv(hex_rgb("#5c4326"), 0, 0, rng.uniform(-0.05, 0.05))
    dark = shift_hsv(wood, 0, 0.06, -0.18)
    # broken hull ribs — curved arrangement of planks
    cx, cy = 140, 150
    for k in range(-4, 5):
        off = k * 22
        y0 = cy - 90
        y1 = cy + 90
        bow = 26 * math.cos(k / 5 * math.pi / 2)
        c.line((cx + off - bow, y0), (cx + off, cy), wood, 7)
        c.line((cx + off, cy), (cx + off - bow, y1), wood, 7)
    # keel spine
    c.line((cx, cy - 100), (cx, cy + 100), dark, 10)
    # a few snapped planks scattered
    for _ in range(5):
        x0 = rng.uniform(40, 240)
        y0 = rng.uniform(40, 260)
        ang = rng.uniform(0, math.pi)
        L = rng.uniform(30, 60)
        c.line((x0, y0), (x0 + math.cos(ang) * L, y0 + math.sin(ang) * L), wood, 6)
    img = c.finalize()
    return soft_shade(img, rng, strength=28)


def prop_standing_stone(v):
    rng = pyrng_for("standing_stone", v)
    c = Canvas(1, 1, rng)
    stone = shift_hsv(hex_rgb("#807a70"), 0, 0, rng.uniform(-0.05, 0.05))
    # a monolith seen top-down: thick rounded rectangle
    pts = _wobble_pts(70, 70, 40, 8, rng, 0.14)
    c.wobbly_outline(pts, OUTLINE, 4, jitter=2.2)
    c.poly(pts, stone)
    # top light plane + carved rune
    c.poly([(70 - 24, 70 - 24), (70 + 10, 70 - 28), (70 + 16, 70), (70 - 18, 70 + 4)],
           shift_hsv(stone, 0, -0.04, 0.14), 180)
    runecol = shift_hsv(stone, 0, 0.1, -0.3)
    c.line((70, 56), (70, 84), runecol, 2)
    c.line((70, 64), (78, 58), runecol, 2)
    c.line((70, 72), (62, 78), runecol, 2)
    img = c.finalize()
    return soft_shade(img, rng, strength=28)


def prop_throne(v):
    rng = pyrng_for("throne", v)
    c = Canvas(1, 1, rng)
    stone = shift_hsv(hex_rgb("#6a655d"), 0, 0, rng.uniform(-0.04, 0.04))
    gold = hex_rgb("#b89a3a")
    # high back (top edge)
    c.rrect(34, 20, 106, 44, 6, shift_hsv(stone, 0, 0.03, -0.1), outline=OUTLINE, ow=4)
    # seat
    c.rrect(38, 44, 102, 104, 6, stone, outline=OUTLINE, ow=4)
    # armrests
    c.rrect(30, 50, 44, 100, 4, shift_hsv(stone, 0, 0.04, -0.12), outline=OUTLINE, ow=3)
    c.rrect(96, 50, 110, 100, 4, shift_hsv(stone, 0, 0.04, -0.12), outline=OUTLINE, ow=3)
    # gold trim + cushion
    c.rrect(48, 54, 92, 96, 5, hex_rgb("#6b2f3a"), outline=gold, ow=3)
    img = c.finalize()
    return soft_shade(img, rng, strength=26)


# ---- Structure ----------------------------------------------------------------------

def prop_door(v):
    rng = pyrng_for("door", v)
    c = Canvas(1, 1, rng)
    wood = shift_hsv(hex_rgb("#6b4a26"), 0, 0, rng.uniform(-0.05, 0.05))
    iron = hex_rgb("#3a352f")
    # door fills most of a cell (meant to sit in a wall gap)
    c.rrect(38, 12, 102, 128, 4, wood, outline=OUTLINE, ow=4)
    for i in range(1, 4):
        x = 38 + (102 - 38) * i / 4
        c.line((x, 12), (x, 128), shift_hsv(wood, 0, 0.06, -0.2), 2)
    # iron bands + handle
    for y in (36, 104):
        c.line((38, y), (102, y), iron, 4)
    c.ellipse(94, 70, 5, 5, hex_rgb("#8a7a3a"))
    img = c.finalize()
    return soft_shade(img, rng, strength=24)


def prop_double_door(v):
    rng = pyrng_for("double_door", v, 2)
    c = Canvas(2, 1, rng)
    wood = shift_hsv(hex_rgb("#5c3d22"), 0, 0, rng.uniform(-0.05, 0.05))
    iron = hex_rgb("#3a352f")
    for half in (0, 1):
        x0 = 20 + half * 130
        x1 = x0 + 110
        c.rrect(x0, 18, x1, 122, 4, wood, outline=OUTLINE, ow=4)
        for i in range(1, 4):
            x = x0 + (x1 - x0) * i / 4
            c.line((x, 18), (x, 122), shift_hsv(wood, 0, 0.06, -0.2), 2)
        for y in (42, 98):
            c.line((x0, y), (x1, y), iron, 4)
    # central seam + ring handles
    c.line((140, 18), (140, 122), iron, 3)
    c.ellipse(126, 70, 7, 7, (0, 0, 0), alpha=0, outline=hex_rgb("#8a7a3a"), ow=3)
    c.ellipse(154, 70, 7, 7, (0, 0, 0), alpha=0, outline=hex_rgb("#8a7a3a"), ow=3)
    img = c.finalize()
    return soft_shade(img, rng, strength=24)


def prop_tent(v):
    rng = pyrng_for("tent", v, 2)
    c = Canvas(2, 2, rng)
    canvas_col = shift_hsv(hex_rgb("#b8a878"), 0, rng.uniform(-0.05, 0.05), 0)
    dark = shift_hsv(canvas_col, 0, 0.06, -0.18)
    cx = cy = 140
    # top-down conical/ridge tent: diamond of four sloped panels
    top = (cx, cy - 96)
    bot = (cx, cy + 96)
    left = (cx - 96, cy)
    right = (cx + 96, cy)
    c.poly([top, right, (cx, cy)], canvas_col, outline=OUTLINE, ow=3)
    c.poly([right, bot, (cx, cy)], dark, outline=OUTLINE, ow=3)
    c.poly([bot, left, (cx, cy)], canvas_col, outline=OUTLINE, ow=3)
    c.poly([left, top, (cx, cy)], shift_hsv(canvas_col, 0, -0.03, 0.1), outline=OUTLINE, ow=3)
    # ridge lines and peak
    c.ellipse(cx, cy, 8, 8, shift_hsv(dark, 0, 0.05, -0.1))
    # guy ropes
    for (px, py) in (top, bot, left, right):
        ex = px + (px - cx) * 0.18
        ey = py + (py - cy) * 0.18
        c.line((px, py), (ex, ey), hex_rgb("#5a4a30"), 2)
    img = c.finalize()
    return soft_shade(img, rng, strength=26)


def prop_cart(v):
    rng = pyrng_for("cart", v, 2)
    c = Canvas(2, 1, rng)
    wood = shift_hsv(hex_rgb("#6b4a26"), 0, 0, rng.uniform(-0.05, 0.05))
    iron = hex_rgb("#2f2a24")
    # cart bed
    c.rrect(50, 30, 230, 110, 8, wood, outline=OUTLINE, ow=4)
    for i in range(1, 6):
        x = 50 + (230 - 50) * i / 6
        c.line((x, 30), (x, 110), shift_hsv(wood, 0, 0.06, -0.18), 2)
    # wheels (top-down: dark ellipses on sides)
    for (wx, wy) in ((60, 20), (60, 120), (220, 20), (220, 120)):
        c.ellipse(wx, wy, 16, 10, iron, outline=OUTLINE, ow=2)
        c.ellipse(wx, wy, 5, 3, shift_hsv(wood, 0, 0, 0.1))
    # draw shafts
    c.line((230, 50), (272, 60), wood, 5)
    c.line((230, 90), (272, 80), wood, 5)
    img = c.finalize()
    return soft_shade(img, rng, strength=26)


def prop_boat(v):
    rng = pyrng_for("boat", v, 2)
    c = Canvas(2, 1, rng)  # rowboat 2x1
    wood = shift_hsv(hex_rgb("#7a5836"), 0, 0, rng.uniform(-0.05, 0.05))
    dark = shift_hsv(wood, 0, 0.06, -0.2)
    inner = shift_hsv(wood, 0, -0.03, 0.12)
    # hull outline (pointed ellipse)
    hull = [(20, 70), (70, 34), (210, 34), (268, 70), (210, 106), (70, 106)]
    c.wobbly_outline(hull, OUTLINE, 4, jitter=2.0)
    c.poly(hull, wood)
    inner_pts = [(40, 70), (78, 46), (204, 46), (250, 70), (204, 94), (78, 94)]
    c.poly(inner_pts, inner)
    # thwarts (seats)
    for x in (110, 175):
        c.line((x, 48), (x, 92), dark, 5)
    img = c.finalize()
    return soft_shade(img, rng, strength=26)


# ---- Connection ---------------------------------------------------------------------

def _stairs(v, up=True):
    rng = pyrng_for("stairs" + ("up" if up else "dn"), v)
    c = Canvas(1, 1, rng)
    stone = shift_hsv(hex_rgb("#7a746a"), 0, 0, rng.uniform(-0.04, 0.04))
    c.rrect(16, 16, 124, 124, 4, shift_hsv(stone, 0, 0.05, -0.18), outline=OUTLINE, ow=4)
    n = 6
    for i in range(n):
        # up-stairs: each higher step is lighter and inset (reads as ascending toward top)
        t = i / (n - 1)
        if up:
            shade = shift_hsv(stone, 0, -0.03, -0.05 + 0.24 * t)
            inset = 8 + i * 2
            y0 = 118 - i * ((118 - 22) / n)
            y1 = y0 - ((118 - 22) / n) + 2
            c.rrect(16 + inset, y1, 124 - inset, y0, 2, shade, outline=(40, 36, 30), ow=2)
        else:
            shade = shift_hsv(stone, 0, 0.02, 0.2 - 0.26 * t)
            inset = 8 + i * 2
            y0 = 22 + i * ((118 - 22) / n)
            y1 = y0 + ((118 - 22) / n) - 2
            c.rrect(16 + inset, y0, 124 - inset, y1, 2, shade, outline=(40, 36, 30), ow=2)
    img = c.finalize()
    return soft_shade(img, rng, strength=18)


def prop_stairs_up(v):
    return _stairs(v, up=True)


def prop_stairs_down(v):
    return _stairs(v, up=False)


def prop_ladder(v):
    rng = pyrng_for("ladder", v)
    c = Canvas(1, 1, rng)
    wood = shift_hsv(hex_rgb("#8a6534"), 0, 0, rng.uniform(-0.05, 0.05))
    # two rails
    c.rrect(50, 16, 60, 124, 3, wood, outline=OUTLINE, ow=2)
    c.rrect(80, 16, 90, 124, 3, wood, outline=OUTLINE, ow=2)
    # rungs
    for i in range(7):
        y = 24 + i * 15
        c.rrect(50, y, 90, y + 6, 2, shift_hsv(wood, 0, -0.02, 0.08), outline=OUTLINE, ow=2)
    img = c.finalize()
    return soft_shade(img, rng, strength=20)


def prop_trapdoor(v):
    rng = pyrng_for("trapdoor", v)
    c = Canvas(1, 1, rng)
    wood = shift_hsv(hex_rgb("#5c3d22"), 0, 0, rng.uniform(-0.05, 0.05))
    iron = hex_rgb("#2f2a24")
    c.rrect(24, 24, 116, 116, 4, wood, outline=OUTLINE, ow=5)
    for i in range(1, 4):
        y = 24 + (116 - 24) * i / 4
        c.line((24, y), (116, y), shift_hsv(wood, 0, 0.06, -0.2), 3)
    # iron corner brackets + ring
    for (bx, by) in ((32, 32), (108, 32), (32, 108), (108, 108)):
        c.rrect(bx - 6, by - 6, bx + 6, by + 6, 1, iron)
    c.ellipse(70, 70, 12, 12, (0, 0, 0), alpha=0, outline=iron, ow=4)
    img = c.finalize()
    return soft_shade(img, rng, strength=22)


# ======================================================================================
# PROP REGISTRY
# ======================================================================================
# id -> (painter, variant_count, manifest dict)
# footprint is [w, h] in grid squares; matches the Canvas size used by the painter.

PROPS = {
    # ---- cover ----
    "rock":           (prop_rock,        3, dict(display="Rock", category="cover", footprint=[1, 1], blocking=True,  biomes=["forest", "cave", "mountain", "desert", "coast", "grassland"], rotatable=True)),
    "crate":          (prop_crate,       3, dict(display="Crate", category="cover", footprint=[1, 1], blocking=True,  biomes=["dungeon", "village", "ship", "mine", "market"], rotatable=True)),
    "barrel":         (prop_barrel,      3, dict(display="Barrel", category="cover", footprint=[1, 1], blocking=True,  biomes=["dungeon", "village", "ship", "tavern", "market"], rotatable=False)),
    "low_wall":       (prop_low_wall,    2, dict(display="Low Wall", category="cover", footprint=[1, 1], blocking=True,  biomes=["dungeon", "village", "fortress", "ruin"], rotatable=True)),
    "fallen_log":     (prop_fallen_log,  2, dict(display="Fallen Log", category="cover", footprint=[2, 1], blocking=True,  biomes=["forest", "jungle", "swamp", "grassland"], rotatable=True)),
    # ---- furniture ----
    "table":          (prop_table,       2, dict(display="Table", category="furniture", footprint=[1, 1], blocking=True,  biomes=["tavern", "dungeon", "village", "temple"], rotatable=True)),
    "chair":          (prop_chair,       2, dict(display="Chair", category="furniture", footprint=[1, 1], blocking=False, biomes=["tavern", "dungeon", "village", "temple"], rotatable=True)),
    "bed":            (prop_bed,         2, dict(display="Bed", category="furniture", footprint=[1, 2], blocking=True,  biomes=["village", "dungeon", "tavern"], rotatable=True)),
    "bookshelf":      (prop_bookshelf,   2, dict(display="Bookshelf", category="furniture", footprint=[1, 1], blocking=True,  biomes=["dungeon", "temple", "village"], rotatable=True)),
    "market_stall":   (prop_market_stall,2, dict(display="Market Stall", category="furniture", footprint=[2, 2], blocking=True,  biomes=["village", "market"], rotatable=True)),
    "anvil":          (prop_anvil,       2, dict(display="Anvil", category="furniture", footprint=[1, 1], blocking=True,  biomes=["village", "mine", "dungeon"], rotatable=True)),
    "bar_counter":    (prop_bar_counter, 2, dict(display="Bar Counter", category="furniture", footprint=[2, 1], blocking=True,  biomes=["tavern", "village"], rotatable=True)),
    # ---- nature ----
    "tree_oak":       (prop_tree_oak,    2, dict(display="Oak Tree", category="nature", footprint=[2, 2], blocking=True,  biomes=["forest", "grassland", "village"], rotatable=False)),
    "tree_pine":      (prop_tree_pine,   2, dict(display="Pine Tree", category="nature", footprint=[2, 2], blocking=True,  biomes=["forest", "mountain", "arctic"], rotatable=False)),
    "tree_palm":      (prop_tree_palm,   2, dict(display="Palm Tree", category="nature", footprint=[2, 2], blocking=True,  biomes=["coast", "jungle", "desert"], rotatable=False)),
    "tree_dead":      (prop_tree_dead,   2, dict(display="Dead Tree", category="nature", footprint=[2, 2], blocking=True,  biomes=["swamp", "volcanic", "arctic", "ruin"], rotatable=False)),
    "bush":           (prop_bush,        3, dict(display="Bush", category="nature", footprint=[1, 1], blocking=False, biomes=["forest", "grassland", "jungle", "village"], rotatable=False)),
    "stump":          (prop_stump,       2, dict(display="Tree Stump", category="nature", footprint=[1, 1], blocking=True,  biomes=["forest", "grassland", "swamp"], rotatable=False)),
    "stalagmite":     (prop_stalagmite,  2, dict(display="Stalagmite", category="nature", footprint=[1, 1], blocking=True,  biomes=["cave", "mine"], rotatable=False)),
    "lily_pad":       (prop_lily_pad,    2, dict(display="Lily Pads", category="nature", footprint=[1, 1], blocking=False, biomes=["swamp", "grotto", "coast"], rotatable=False)),
    "coral":          (prop_coral,       2, dict(display="Coral", category="nature", footprint=[1, 1], blocking=False, biomes=["coast", "grotto"], rotatable=False)),
    "mushroom":       (prop_mushroom,    2, dict(display="Mushrooms", category="nature", footprint=[1, 1], blocking=False, biomes=["cave", "swamp", "forest", "grotto"], rotatable=False)),
    # ---- hazard ----
    "spike_trap":     (prop_spike_trap,  2, dict(display="Spike Trap", category="hazard", footprint=[1, 1], blocking=False, biomes=["dungeon", "mine", "ruin"], rotatable=False)),
    "bones":          (prop_bones,       2, dict(display="Bones", category="hazard", footprint=[1, 1], blocking=False, biomes=["dungeon", "cave", "crypt", "volcanic", "ruin"], rotatable=True)),
    # ---- focal ----
    "altar":          (prop_altar,       2, dict(display="Altar", category="focal", footprint=[1, 1], blocking=True,  biomes=["temple", "dungeon", "crypt", "ruin"], rotatable=True)),
    "well":           (prop_well,        2, dict(display="Well", category="focal", footprint=[1, 1], blocking=True,  biomes=["village", "dungeon", "ruin"], rotatable=False)),
    "statue":         (prop_statue,      2, dict(display="Statue", category="focal", footprint=[1, 1], blocking=True,  biomes=["temple", "dungeon", "crypt", "ruin", "fortress"], rotatable=False)),
    "campfire":       (prop_campfire,    2, dict(display="Campfire", category="focal", footprint=[1, 1], blocking=True,  biomes=["forest", "camp", "cave", "grassland", "arctic"], rotatable=False)),
    "fountain":       (prop_fountain,    1, dict(display="Fountain", category="focal", footprint=[2, 2], blocking=True,  biomes=["village", "temple", "fortress"], rotatable=False)),
    "brazier":        (prop_brazier,     2, dict(display="Brazier", category="focal", footprint=[1, 1], blocking=True,  biomes=["temple", "dungeon", "crypt", "fortress"], rotatable=False)),
    "chest":          (prop_chest,       2, dict(display="Chest", category="focal", footprint=[1, 1], blocking=True,  biomes=["dungeon", "cave", "crypt", "ship", "mine"], rotatable=True)),
    "shipwreck":      (prop_shipwreck,   1, dict(display="Shipwreck", category="focal", footprint=[2, 2], blocking=True,  biomes=["coast", "ship", "grotto"], rotatable=True)),
    "standing_stone": (prop_standing_stone, 2, dict(display="Standing Stone", category="focal", footprint=[1, 1], blocking=True, biomes=["grassland", "forest", "ruin", "arctic"], rotatable=False)),
    "throne":         (prop_throne,      2, dict(display="Throne", category="focal", footprint=[1, 1], blocking=True,  biomes=["fortress", "dungeon", "temple", "ruin"], rotatable=True)),
    # ---- structure ----
    "door":           (prop_door,        2, dict(display="Door", category="structure", footprint=[1, 1], blocking=True,  biomes=["dungeon", "village", "fortress", "temple"], rotatable=True)),
    "double_door":    (prop_double_door, 2, dict(display="Double Door", category="structure", footprint=[2, 1], blocking=True,  biomes=["fortress", "temple", "dungeon"], rotatable=True)),
    "tent":           (prop_tent,        2, dict(display="Tent", category="structure", footprint=[2, 2], blocking=True,  biomes=["camp", "grassland", "arctic", "desert"], rotatable=True)),
    "cart":           (prop_cart,        2, dict(display="Cart", category="structure", footprint=[2, 1], blocking=True,  biomes=["village", "market", "camp"], rotatable=True)),
    "boat":           (prop_boat,        2, dict(display="Rowboat", category="structure", footprint=[2, 1], blocking=True,  biomes=["coast", "ship", "grotto"], rotatable=True)),
    # ---- connection ----
    "stairs_up":      (prop_stairs_up,   2, dict(display="Stairs Up", category="connection", footprint=[1, 1], blocking=False, biomes=["dungeon", "tower", "fortress", "mine", "ship"], rotatable=True)),
    "stairs_down":    (prop_stairs_down, 2, dict(display="Stairs Down", category="connection", footprint=[1, 1], blocking=False, biomes=["dungeon", "tower", "fortress", "mine", "ship"], rotatable=True)),
    "ladder":         (prop_ladder,      2, dict(display="Ladder", category="connection", footprint=[1, 1], blocking=False, biomes=["mine", "cave", "ship", "dungeon", "tower"], rotatable=True)),
    "trapdoor":       (prop_trapdoor,    2, dict(display="Trapdoor", category="connection", footprint=[1, 1], blocking=False, biomes=["dungeon", "tavern", "ship", "mine"], rotatable=True)),
}


# ======================================================================================
# GENERATION DRIVER
# ======================================================================================

def _save_png(img: Image.Image, path: str, rgba=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not rgba and img.mode != "RGB":
        img = img.convert("RGB")
    img.save(path, "PNG", optimize=True)


def generate_tiles():
    print("Generating terrain tiles...")
    count = 0
    for terrain, (painter, n, _flags) in TERRAINS.items():
        for v in range(1, n + 1):
            arr = painter(v)
            img = Image.fromarray(arr, "RGB")
            assert img.size == (TILE, TILE), f"{terrain}_{v} wrong size {img.size}"
            path = os.path.join(TILES_DIR, terrain, f"{terrain}_{v:02d}.png")
            _save_png(img, path)
            count += 1
        print(f"  {terrain}: {n} variants")
    print(f"  -> {count} tiles total")
    return count


def generate_props():
    print("Generating props...")
    count = 0
    for prop, (painter, n, flags) in PROPS.items():
        fw, fh = flags["footprint"]
        for v in range(1, n + 1):
            img = painter(v)
            assert img.mode == "RGBA", f"{prop}_{v} not RGBA"
            expect = (fw * TILE, fh * TILE)
            assert img.size == expect, f"{prop}_{v} size {img.size} != {expect}"
            path = os.path.join(PROPS_DIR, prop, f"{prop}_{v:02d}.png")
            _save_png(img, path, rgba=True)
            count += 1
        print(f"  {prop}: {n} variants ({fw}x{fh})")
    print(f"  -> {count} props total")
    return count


def build_manifest():
    terrains = {}
    for terrain, (_painter, n, flags) in TERRAINS.items():
        tiles = [f"tiles/{terrain}/{terrain}_{v:02d}.png" for v in range(1, n + 1)]
        terrains[terrain] = {
            "display": flags["display"],
            "walkable": flags["walkable"],
            "blocking": flags["blocking"],
            "hazard": flags["hazard"],
            "difficult": flags["difficult"],
            "indoor": flags["indoor"],
            "priority": flags["priority"],
            "tiles": tiles,
        }
    props = {}
    for prop, (_painter, n, flags) in PROPS.items():
        variants = [f"props/{prop}/{prop}_{v:02d}.png" for v in range(1, n + 1)]
        props[prop] = {
            "display": flags["display"],
            "category": flags["category"],
            "footprint": flags["footprint"],
            "blocking": flags["blocking"],
            "biomes": flags["biomes"],
            "variants": variants,
            "rotatable": flags["rotatable"],
        }
    manifest = {
        "px_per_square": TILE,
        "generator_seed": MASTER_SEED,
        "terrains": terrains,
        "props": props,
    }
    path = os.path.join(ASSETS, "manifest.json")
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"Wrote manifest: {path}")
    return manifest


# ======================================================================================
# VALIDATION
# ======================================================================================

REQUIRED_TERRAINS = [
    "grass", "tall_grass", "forest_floor", "jungle_floor", "dirt", "road", "cobblestone",
    "sand", "snow", "ice", "swamp", "mud", "water_shallow", "water_deep", "river", "lava",
    "volcanic_rock", "stone_floor", "wood_floor", "cave_floor", "mountain_rock", "stone_wall",
    "brick_wall", "wood_wall", "cave_wall", "rubble", "ship_deck", "marble_floor", "pit",
]

REQUIRED_PROP_CATEGORIES = {
    "cover", "furniture", "nature", "hazard", "focal", "structure", "connection",
}


def validate(manifest=None):
    """Assert the delivered asset library conforms to Contract 1. Raises AssertionError."""
    print("Validating assets against Contract 1...")
    mpath = os.path.join(ASSETS, "manifest.json")
    if manifest is None:
        with open(mpath) as f:
            manifest = json.load(f)

    # manifest structure
    assert manifest["px_per_square"] == TILE, "px_per_square mismatch"
    assert manifest["generator_seed"] == MASTER_SEED, "generator_seed mismatch"
    assert "terrains" in manifest and "props" in manifest

    # every required terrain present with 3-6 variants
    present = set(manifest["terrains"].keys())
    missing = [t for t in REQUIRED_TERRAINS if t not in present]
    assert not missing, f"Missing required terrains: {missing}"
    extra = present - set(REQUIRED_TERRAINS)
    assert not extra, f"Unexpected extra terrains (leg-engine references only required set): {extra}"

    for terrain, spec in manifest["terrains"].items():
        for key in ("display", "walkable", "blocking", "hazard", "difficult", "indoor",
                    "priority", "tiles"):
            assert key in spec, f"terrain {terrain} missing field {key}"
        n = len(spec["tiles"])
        assert 3 <= n <= 6, f"terrain {terrain} has {n} variants (need 3-6)"
        for rel in spec["tiles"]:
            p = os.path.join(ASSETS, rel)
            assert os.path.exists(p), f"missing tile file {rel}"
            with Image.open(p) as im:
                assert im.size == (TILE, TILE), f"{rel} is {im.size}, need {TILE}x{TILE}"
                assert im.mode == "RGB", f"{rel} mode {im.mode}, need RGB"

    # props: >=40 types, all required categories, files exist & match footprint dims
    assert len(manifest["props"]) >= 40, f"only {len(manifest['props'])} props (need >=40)"
    cats = set()
    for prop, spec in manifest["props"].items():
        for key in ("display", "category", "footprint", "blocking", "biomes", "variants",
                    "rotatable"):
            assert key in spec, f"prop {prop} missing field {key}"
        cats.add(spec["category"])
        fw, fh = spec["footprint"]
        for rel in spec["variants"]:
            p = os.path.join(ASSETS, rel)
            assert os.path.exists(p), f"missing prop file {rel}"
            with Image.open(p) as im:
                assert im.size == (fw * TILE, fh * TILE), \
                    f"{rel} is {im.size}, footprint {fw}x{fh} => {(fw*TILE, fh*TILE)}"
                assert im.mode == "RGBA", f"{rel} mode {im.mode}, need RGBA"
    missing_cats = REQUIRED_PROP_CATEGORIES - cats
    assert not missing_cats, f"missing prop categories: {missing_cats}"

    # seamless-tiling sanity check on a handful of field-based terrains: opposite edges
    # of a tile should closely match (wrap-around noise). Walls/floors with block seams
    # and the intentionally-discrete 'pit' are exempted from the strict edge test.
    seam_check = ["grass", "sand", "water_deep", "cave_floor", "dirt", "snow", "lava"]
    for terrain in seam_check:
        rel = manifest["terrains"][terrain]["tiles"][0]
        with Image.open(os.path.join(ASSETS, rel)) as im:
            a = np.asarray(im.convert("RGB")).astype(np.float64)
        top_bottom = np.abs(a[0] - a[-1]).mean()
        left_right = np.abs(a[:, 0] - a[:, -1]).mean()
        assert top_bottom < 26 and left_right < 26, \
            f"{terrain} edges not seamless (tb={top_bottom:.1f} lr={left_right:.1f})"

    # total committed size sanity
    total = 0
    for dirpath, _dn, files in os.walk(ASSETS):
        for fn in files:
            total += os.path.getsize(os.path.join(dirpath, fn))
    mb = total / (1024 * 1024)
    print(f"  terrains: {len(manifest['terrains'])}/29 required")
    print(f"  props: {len(manifest['props'])} types, categories {sorted(cats)}")
    print(f"  total asset size: {mb:.2f} MB")
    assert mb < 25, f"asset size {mb:.1f} MB exceeds 25 MB target"
    print("  VALIDATION PASSED")
    return True


# ======================================================================================
# CONTACT SHEET (for visual self-review during development; not a shipped requirement)
# ======================================================================================

def contact_sheet(kind="tiles", out="/tmp/contact.png"):
    if kind == "tiles":
        items = []
        for terrain, spec in build_manifest()["terrains"].items():
            items.append((terrain, os.path.join(ASSETS, spec["tiles"][0])))
        cols = 6
        cell = TILE
    else:
        items = []
        for prop, spec in build_manifest()["props"].items():
            items.append((prop, os.path.join(ASSETS, spec["variants"][0])))
        cols = 6
        cell = TILE * 2
    rows = (len(items) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell, rows * cell), (40, 40, 46))
    for i, (name, path) in enumerate(items):
        r, cc = divmod(i, cols)
        with Image.open(path) as im:
            im = im.convert("RGBA")
            thumb = im.copy()
            thumb.thumbnail((cell, cell))
            sheet.paste(thumb, (cc * cell, r * cell), thumb)
    sheet.save(out)
    print(f"Wrote contact sheet {out} ({len(items)} items)")


# ======================================================================================
# MAIN
# ======================================================================================

def main():
    ap = argparse.ArgumentParser(description="Phase A asset generator")
    ap.add_argument("--validate", action="store_true", help="generate then validate")
    ap.add_argument("--check", action="store_true", help="validate existing assets only")
    ap.add_argument("--contact", choices=["tiles", "props"], help="write a contact sheet")
    args = ap.parse_args()

    if args.check:
        validate()
        return
    if args.contact:
        contact_sheet(args.contact, f"/tmp/contact_{args.contact}.png")
        return

    print(f"=== Phase A asset generation (seed {MASTER_SEED}) ===")
    tcount = generate_tiles()
    pcount = generate_props()
    manifest = build_manifest()
    print(f"Done: {tcount} tiles across {len(TERRAINS)} terrains, "
          f"{pcount} prop images across {len(PROPS)} prop types.")

    if args.validate:
        validate(manifest)


if __name__ == "__main__":
    sys.exit(main())
