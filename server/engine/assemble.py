"""Art assembly (Phase B, step 4): LayoutGrid + assets -> composited PIL image.

Composites at the manifest's native resolution (140 px/square), then rescales the
whole image once with Lanczos to the requested ``px_per_square`` (per the
Rendering rules). Pipeline per cell / pass:

1. **Base tiles** -- pick a tile variant per cell via ``hash(x, y, seed)`` so the
   same map is byte-identical and neighbouring cells vary.
2. **Terrain transitions** -- runtime, noise-feathered alpha blending: where two
   terrains meet, the higher-``priority`` terrain bleeds organically over the
   lower one using a per-boundary seeded noise mask (no hard edges, no
   pre-rendered transition art).
3. **Props** -- placed at their grid cell with rotation + variant.
4. **Post** -- soft SE drop shadows under blocking props / wall terrain; a mood
   colour grade (peaceful warm/bright, eerie cool desaturated + vignette, combat
   high contrast, tense slightly dark); ``colorblind`` mode adds contrast +
   diagonal hatching baked on hazard cells + a dotted boundary on blocking
   terrain.

Determinism: all randomness is derived from ``layout['seed']`` via stable hashes
and seeded numpy fields -- no module RNG, no time. Missing assets degrade
gracefully to flat colour fills so the engine works against a stub manifest.
"""

from __future__ import annotations

import hashlib
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageEnhance

from .noise import fractal_noise_2d, rng_from_seed

__all__ = ["assemble_map"]

NATIVE = 140  # native tile px per square (manifest px_per_square)

# Terrain semantics (kept in sync with layout.py / validate.py).
WALL_TERRAINS = frozenset({"stone_wall", "brick_wall", "wood_wall", "cave_wall",
                           "mountain_rock", "volcanic_rock"})
HAZARD_TERRAINS = frozenset({"lava", "water_deep", "pit"})
BLOCKING = WALL_TERRAINS

# Fallback flat colours per terrain (used when a tile PNG is missing). Chosen for
# tactical readability and to keep colorblind post-processing meaningful.
FALLBACK_COLORS: dict[str, tuple[int, int, int]] = {
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
DEFAULT_COLOR = (100, 100, 100)


# --------------------------------------------------------------------------- #
# Tile / prop asset cache
# --------------------------------------------------------------------------- #
class _Assets:
    """Loads and caches tile/prop images from disk, with flat-colour fallback."""

    def __init__(self, manifest: dict):
        self.manifest = manifest or {}
        self.root = self.manifest.get("_root", "assets")
        self._tile_cache: dict[str, Image.Image] = {}
        self._prop_cache: dict[str, Image.Image] = {}

    def _load(self, rel: str) -> Image.Image | None:
        path = os.path.join(self.root, rel)
        if not os.path.isfile(path):
            return None
        try:
            return Image.open(path).convert("RGBA")
        except Exception:  # pragma: no cover - corrupt file
            return None

    def terrain_tile(self, terrain: str, variant_idx: int) -> Image.Image:
        tdef = self.manifest.get("terrains", {}).get(terrain, {})
        tiles = tdef.get("tiles", []) or []
        img = None
        if tiles:
            rel = tiles[variant_idx % len(tiles)]
            key = f"t:{rel}"
            if key not in self._tile_cache:
                loaded = self._load(rel)
                if loaded is not None:
                    if loaded.size != (NATIVE, NATIVE):
                        loaded = loaded.resize((NATIVE, NATIVE), Image.LANCZOS)
                    self._tile_cache[key] = loaded
                else:
                    self._tile_cache[key] = self._flat(terrain)
            img = self._tile_cache[key]
        if img is None:
            img = self._flat(terrain)
        return img

    def _flat(self, terrain: str) -> Image.Image:
        color = FALLBACK_COLORS.get(terrain, DEFAULT_COLOR)
        return Image.new("RGBA", (NATIVE, NATIVE), color + (255,))

    def prop_image(self, prop_type: str, variant_idx: int) -> Image.Image | None:
        pdef = self.manifest.get("props", {}).get(prop_type, {})
        variants = pdef.get("variants", []) or []
        if not variants:
            return None
        rel = variants[variant_idx % len(variants)]
        key = f"p:{rel}"
        if key not in self._prop_cache:
            loaded = self._load(rel)
            self._prop_cache[key] = loaded  # may be None
        return self._prop_cache[key]

    def priority(self, terrain: str) -> int:
        return int(self.manifest.get("terrains", {}).get(terrain, {}).get("priority", 30))

    def prop_footprint(self, prop_type: str) -> tuple[int, int]:
        fp = self.manifest.get("props", {}).get(prop_type, {}).get("footprint", [1, 1])
        try:
            return int(fp[0]), int(fp[1])
        except Exception:  # pragma: no cover
            return 1, 1

    def prop_blocking(self, prop_type: str) -> bool:
        return bool(self.manifest.get("props", {}).get(prop_type, {}).get("blocking", False))


# --------------------------------------------------------------------------- #
# Deterministic hashing
# --------------------------------------------------------------------------- #
def _cell_hash(x: int, y: int, seed: int) -> int:
    """Stable 32-bit hash of (x, y, seed) -- reproducible across processes.

    Uses blake2b (not Python's salted hash()) so results never depend on
    PYTHONHASHSEED. This drives tile-variant selection per cell.
    """

    h = hashlib.blake2b(digest_size=8)
    h.update(x.to_bytes(4, "little", signed=True))
    h.update(y.to_bytes(4, "little", signed=True))
    h.update((seed & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "little"))
    return int.from_bytes(h.digest(), "little")


# --------------------------------------------------------------------------- #
# Public entry
# --------------------------------------------------------------------------- #
def assemble_map(layout: dict, manifest: dict, palette_mode: str = "standard",
                 px_per_square: int = NATIVE) -> Image.Image:
    """Composite ``layout`` into a gridless RGBA image at ``px_per_square``.

    Composites natively at 140 px/square, then Lanczos-rescales once if the
    requested resolution differs.
    """

    assets = _Assets(manifest)
    cols = int(layout["cols"])
    rows = int(layout["rows"])
    seed = int(layout.get("seed", 0))
    cells = layout["cells"]

    W, H = cols * NATIVE, rows * NATIVE
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 255))

    # 1. base tiles ---------------------------------------------------------
    for y in range(rows):
        for x in range(cols):
            terrain = cells[y][x]
            vcount = _tile_variant_count(assets, terrain)
            vidx = _cell_hash(x, y, seed) % max(1, vcount)
            tile = assets.terrain_tile(terrain, vidx)
            canvas.alpha_composite(tile, (x * NATIVE, y * NATIVE))

    # 2. terrain transitions (noise-feathered blends by priority) -----------
    _blend_transitions(canvas, cells, cols, rows, seed, assets)

    # 3. props --------------------------------------------------------------
    shadow_boxes: list[tuple[int, int, int, int]] = []
    for prop in sorted(layout.get("props", []), key=lambda p: (p["y"], p["x"], p["id"])):
        box = _place_prop(canvas, prop, assets)
        if box is not None and assets.prop_blocking(prop["type"]):
            shadow_boxes.append(box)

    # 4a. drop shadows (SE) from blocking props + wall terrain --------------
    _apply_shadows(canvas, cells, cols, rows, shadow_boxes)

    # 4b. mood colour grade + vignette --------------------------------------
    mood = (layout.get("spec", {}) or {}).get("mood", "neutral")
    canvas = _apply_mood(canvas, mood)

    # 4c. colorblind overlays ----------------------------------------------
    if palette_mode == "colorblind":
        _apply_colorblind(canvas, cells, cols, rows)

    # 5. final Lanczos rescale once (if requested px differs) ---------------
    if px_per_square != NATIVE:
        out_w, out_h = cols * px_per_square, rows * px_per_square
        canvas = canvas.resize((out_w, out_h), Image.LANCZOS)

    return canvas.convert("RGBA")


def _tile_variant_count(assets: _Assets, terrain: str) -> int:
    tiles = assets.manifest.get("terrains", {}).get(terrain, {}).get("tiles", [])
    return max(1, len(tiles) if tiles else 1)


# --------------------------------------------------------------------------- #
# Terrain transition blending
# --------------------------------------------------------------------------- #
def _blend_transitions(canvas: Image.Image, cells, cols, rows, seed, assets: _Assets):
    """Feather the higher-priority terrain over the lower across every boundary.

    For each cell, if a 4-neighbour has strictly higher priority, that neighbour's
    tile is alpha-composited over this cell through a noise mask that is strong
    near the shared edge and fades inward -- giving an organic, hand-painted
    boundary instead of a hard seam.
    """

    # Precompute a single low-frequency noise field over the whole image; sample
    # per-cell windows from it so blends are coherent and deterministic.
    field = fractal_noise_2d(cols * 4, rows * 4, seed ^ 0x00B1E4,
                             base_scale=6, octaves=3)

    for y in range(rows):
        for x in range(cols):
            here = cells[y][x]
            hp = assets.priority(here)
            for (nx, ny) in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if not (0 <= nx < cols and 0 <= ny < rows):
                    continue
                other = cells[ny][nx]
                if other == here:
                    continue
                op = assets.priority(other)
                if op <= hp:
                    continue  # only higher-priority neighbour bleeds inward
                mask = _edge_mask(nx - x, ny - y, field, x, y, cols, rows)
                if mask is None:
                    continue
                vcount = _tile_variant_count(assets, other)
                vidx = _cell_hash(nx, ny, seed) % max(1, vcount)
                otile = assets.terrain_tile(other, vidx).copy()
                otile.putalpha(mask)
                canvas.alpha_composite(otile, (x * NATIVE, y * NATIVE))


def _edge_mask(dx, dy, field, cx, cy, cols, rows) -> Image.Image | None:
    """Build a NATIVE-sized alpha mask that is opaque toward the (dx,dy) edge and
    fades to transparent across ~40% of the tile, modulated by noise."""

    grad = np.zeros((NATIVE, NATIVE), dtype=np.float64)
    span = int(NATIVE * 0.42)
    if span <= 0:
        return None
    ramp = np.linspace(1.0, 0.0, span)

    if dx == 1:      # neighbour to the right -> opaque on right edge
        grad[:, NATIVE - span:] = ramp[::-1][None, :]
    elif dx == -1:   # neighbour to the left
        grad[:, :span] = ramp[None, :]
    elif dy == 1:    # neighbour below
        grad[NATIVE - span:, :] = ramp[::-1][:, None]
    elif dy == -1:   # neighbour above
        grad[:span, :] = ramp[:, None]

    # Modulate the gradient with a noise window so the boundary wobbles.
    win = field[cy * 4:cy * 4 + NATIVE // 35 + 1, cx * 4:cx * 4 + NATIVE // 35 + 1]
    if win.size:
        noise_val = float(win.mean())
    else:
        noise_val = 0.5
    # Full noise texture sampled at native res for organic feathering.
    nf = _tile_noise(cx, cy, dx, dy)
    grad = np.clip(grad * (0.65 + 0.7 * nf) * (0.7 + 0.6 * noise_val), 0.0, 1.0)

    alpha = (grad * 255).astype(np.uint8)
    if alpha.max() == 0:
        return None
    return Image.fromarray(alpha, mode="L")


_NOISE_TILE_CACHE: dict[tuple, np.ndarray] = {}


def _tile_noise(cx, cy, dx, dy) -> np.ndarray:
    """A small deterministic noise texture per boundary, cached by direction."""

    key = (dx, dy)
    if key not in _NOISE_TILE_CACHE:
        seed = 0xCAFE ^ (dx * 131) ^ (dy * 17)
        _NOISE_TILE_CACHE[key] = fractal_noise_2d(NATIVE, NATIVE, seed,
                                                  base_scale=18, octaves=3)
    base = _NOISE_TILE_CACHE[key]
    # Shift by cell coords so adjacent cells don't share identical texture.
    sx = (cx * 37) % NATIVE
    sy = (cy * 53) % NATIVE
    return np.roll(np.roll(base, sx, axis=1), sy, axis=0)


# --------------------------------------------------------------------------- #
# Props
# --------------------------------------------------------------------------- #
def _place_prop(canvas: Image.Image, prop: dict, assets: _Assets):
    """Composite one prop; returns its pixel bounding box (for shadows) or None."""

    ptype = prop["type"]
    img = assets.prop_image(ptype, int(prop.get("variant", 0)))
    fw, fh = assets.prop_footprint(ptype)
    px = int(prop["x"]) * NATIVE
    py = int(prop["y"]) * NATIVE
    rot = int(prop.get("rot", 0)) % 360

    if img is None:
        # Fallback: draw a simple shape marker so props are still visible.
        img = _fallback_prop(ptype, fw, fh, assets)

    # Size the sprite to its footprint (native).
    target = (fw * NATIVE, fh * NATIVE)
    if img.size != target:
        img = img.resize(target, Image.LANCZOS)
    if rot:
        img = img.rotate(-rot, expand=True, resample=Image.BICUBIC)

    ox = px + (fw * NATIVE - img.size[0]) // 2
    oy = py + (fh * NATIVE - img.size[1]) // 2
    canvas.alpha_composite(img, (ox, oy))
    return (px, py, px + fw * NATIVE, py + fh * NATIVE)


def _fallback_prop(ptype: str, fw: int, fh: int, assets: _Assets) -> Image.Image:
    """A readable colored placeholder for a prop with no art in the manifest."""

    pdef = assets.manifest.get("props", {}).get(ptype, {})
    category = pdef.get("category", "cover")
    color = {
        "cover": (110, 92, 64, 235), "furniture": (140, 108, 70, 235),
        "nature": (60, 104, 56, 235), "hazard": (176, 60, 48, 235),
        "focal": (196, 168, 96, 245), "structure": (96, 84, 72, 245),
        "connection": (70, 70, 90, 245),
    }.get(category, (120, 120, 120, 235))
    img = Image.new("RGBA", (fw * NATIVE, fh * NATIVE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = NATIVE // 6
    d.rounded_rectangle([pad, pad, fw * NATIVE - pad, fh * NATIVE - pad],
                        radius=NATIVE // 8, fill=color,
                        outline=(20, 20, 20, 200), width=3)
    if category == "focal":
        d.ellipse([fw * NATIVE // 3, fh * NATIVE // 3,
                   2 * fw * NATIVE // 3, 2 * fh * NATIVE // 3],
                  fill=(240, 220, 150, 255))
    return img


# --------------------------------------------------------------------------- #
# Post-processing passes
# --------------------------------------------------------------------------- #
def _apply_shadows(canvas: Image.Image, cells, cols, rows, prop_boxes):
    """Soft drop shadows offset south-east under blocking props + wall terrain."""

    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    off = max(6, NATIVE // 14)

    # Wall-terrain shadows: shade the cell just SE of each blocking terrain cell.
    for y in range(rows):
        for x in range(cols):
            if cells[y][x] in BLOCKING:
                x0 = x * NATIVE + off
                y0 = y * NATIVE + off
                sd.rectangle([x0, y0, x0 + NATIVE, y0 + NATIVE], fill=(0, 0, 0, 90))
    # Prop shadows.
    for (x0, y0, x1, y1) in prop_boxes:
        sd.rectangle([x0 + off, y0 + off, x1 + off, y1 + off], fill=(0, 0, 0, 80))

    shadow = shadow.filter(ImageFilter.GaussianBlur(off * 0.6))
    # Composite shadow UNDER existing art? We already drew art; to keep shadows
    # subtle and beneath edges we multiply-darken instead of covering detail.
    canvas.alpha_composite(shadow)


def _apply_mood(canvas: Image.Image, mood: str) -> Image.Image:
    rgb = canvas.convert("RGB")
    if mood == "peaceful":
        rgb = ImageEnhance.Brightness(rgb).enhance(1.08)
        rgb = _tint(rgb, (255, 244, 224), 0.10)          # warm
    elif mood == "eerie":
        rgb = ImageEnhance.Color(rgb).enhance(0.65)       # desaturate
        rgb = _tint(rgb, (200, 214, 236), 0.12)           # cool
        rgb = _vignette(rgb, 0.55)
    elif mood == "combat":
        rgb = ImageEnhance.Contrast(rgb).enhance(1.22)
        rgb = ImageEnhance.Color(rgb).enhance(1.08)
    elif mood == "tense":
        rgb = ImageEnhance.Brightness(rgb).enhance(0.92)
        rgb = _vignette(rgb, 0.35)
    else:  # neutral -- a whisper of vignette for depth
        rgb = _vignette(rgb, 0.22)
    out = rgb.convert("RGBA")
    return out


def _tint(img: Image.Image, color, strength: float) -> Image.Image:
    overlay = Image.new("RGB", img.size, color)
    return Image.blend(img, overlay, strength)


def _vignette(img: Image.Image, strength: float) -> Image.Image:
    w, h = img.size
    # Radial falloff mask computed with numpy (deterministic).
    yy, xx = np.mgrid[0:h, 0:w]
    cx, cy = w / 2.0, h / 2.0
    d = np.sqrt(((xx - cx) / cx) ** 2 + ((yy - cy) / cy) ** 2)
    d = np.clip(d, 0, 1.4)
    darken = np.clip(1.0 - strength * (d ** 2), 0.0, 1.0)
    arr = np.asarray(img).astype(np.float64)
    arr *= darken[:, :, None]
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def _apply_colorblind(canvas: Image.Image, cells, cols, rows):
    """Higher contrast + diagonal hatching on hazard cells + dotted boundary on
    blocking terrain, so meaning never rides on hue alone."""

    # Contrast boost first.
    rgb = canvas.convert("RGB")
    rgb = ImageEnhance.Contrast(rgb).enhance(1.25)
    canvas.paste(rgb.convert("RGBA"))

    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    # Diagonal hatching on hazard cells.
    for y in range(rows):
        for x in range(cols):
            t = cells[y][x]
            if t in HAZARD_TERRAINS:
                x0, y0 = x * NATIVE, y * NATIVE
                for i in range(0, NATIVE * 2, 18):
                    d.line([(x0 + i, y0), (x0 + i - NATIVE, y0 + NATIVE)],
                           fill=(0, 0, 0, 150), width=3)

    # Dotted boundary on blocking terrain edges.
    for y in range(rows):
        for x in range(cols):
            if cells[y][x] not in BLOCKING:
                continue
            x0, y0 = x * NATIVE, y * NATIVE
            _dotted_edges(d, cells, cols, rows, x, y, x0, y0)

    canvas.alpha_composite(overlay)


def _dotted_edges(d, cells, cols, rows, x, y, x0, y0):
    def blocking(ax, ay):
        return not (0 <= ax < cols and 0 <= ay < rows) or cells[ay][ax] in BLOCKING
    dot = (250, 250, 250, 220)
    step = 14
    if not blocking(x, y - 1):
        for px in range(x0, x0 + NATIVE, step):
            d.ellipse([px, y0 - 2, px + 4, y0 + 2], fill=dot)
    if not blocking(x, y + 1):
        yb = y0 + NATIVE
        for px in range(x0, x0 + NATIVE, step):
            d.ellipse([px, yb - 2, px + 4, yb + 2], fill=dot)
    if not blocking(x - 1, y):
        for py in range(y0, y0 + NATIVE, step):
            d.ellipse([x0 - 2, py, x0 + 2, py + 4], fill=dot)
    if not blocking(x + 1, y):
        xr = x0 + NATIVE
        for py in range(y0, y0 + NATIVE, step):
            d.ellipse([xr - 2, py, xr + 2, py + 4], fill=dot)
