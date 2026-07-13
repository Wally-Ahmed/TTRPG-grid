"""Seeded procedural layout (Phase B, step 2).

Consumes a :class:`GenerationSpec` + a seed + the asset manifest and produces a
Contract-2 ``layout`` dict. Setting (biome/structure) AND scenario both shape the
space, per spec sec.4's hybrid model:

* dungeons / buildings  -> BSP room-and-corridor partition
* caves / caverns / mine -> cellular automata
* villages / markets / camps -> plot partitioning with roads + buildings
* ships -> decked hull with railings and a hold
* towers -> concentric ringed rooms
* natural biomes -> value/fractal noise fields with biome blending

On top of the base space, features (river/road/bridge/well/...) are carved or
placed, then cover / clutter / hazards / difficult terrain are scattered (counts
scale with ``spec.density``), a focal point is guaranteed, and player/enemy spawn
suggestions are derived. Every random choice flows from the passed RNG so the
same (spec, seed) yields a byte-identical dict.

Only the 29 Contract-1 terrain ids are emitted; prop types are read from the
manifest (``manifest['props']``), never hard-coded.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np

from . import TERRAIN_SET
from .noise import rng_from_seed, fractal_noise_2d, normalized_fractal_field, domain_warp_field

__all__ = ["build_layout", "build_level_set"]


# --------------------------------------------------------------------------- #
# Biome palettes -- floor / wall / accent terrains for each biome tag.
# Every id here MUST be one of the 29 Contract-1 terrains.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Palette:
    floor: str
    wall: str
    accent: tuple[str, ...] = ()      # extra ground textures blended in
    water: str | None = None
    hazard: str | None = None
    difficult: str | None = None
    indoor: bool = False


BIOME_PALETTES: dict[str, Palette] = {
    "forest": Palette("forest_floor", "mountain_rock", ("grass", "tall_grass", "dirt"),
                      water="water_shallow", difficult="tall_grass"),
    "jungle": Palette("jungle_floor", "mountain_rock", ("tall_grass", "swamp"),
                      water="water_shallow", difficult="swamp"),
    "grassland": Palette("grass", "mountain_rock", ("tall_grass", "dirt"),
                         water="water_shallow", difficult="tall_grass"),
    "desert": Palette("sand", "mountain_rock", ("dirt", "rubble"),
                      hazard="pit", difficult="rubble"),
    "swamp": Palette("swamp", "mountain_rock", ("mud", "tall_grass"),
                     water="water_shallow", hazard="water_deep", difficult="mud"),
    "snow": Palette("snow", "mountain_rock", ("ice", "dirt"),
                    water="ice", hazard="water_deep", difficult="snow"),
    "mountain": Palette("mountain_rock", "mountain_rock", ("dirt", "rubble", "snow"),
                        hazard="pit", difficult="rubble"),
    "cave": Palette("cave_floor", "cave_wall", ("rubble", "dirt"),
                    water="water_shallow", hazard="pit", difficult="rubble", indoor=True),
    "coast": Palette("sand", "mountain_rock", ("dirt", "grass"),
                     water="water_shallow", hazard="water_deep"),
    "ocean": Palette("water_shallow", "mountain_rock", ("sand",),
                     water="water_deep", hazard="water_deep"),
    "river": Palette("grass", "mountain_rock", ("dirt", "mud", "sand"),
                     water="water_shallow", hazard="water_deep", difficult="mud"),
    "volcanic": Palette("volcanic_rock", "mountain_rock", ("rubble", "dirt"),
                        water="lava", hazard="lava", difficult="rubble"),
    "dungeon": Palette("stone_floor", "stone_wall", ("cobblestone", "rubble"),
                       hazard="pit", difficult="rubble", indoor=True),
    "crypt": Palette("stone_floor", "brick_wall", ("cobblestone", "marble_floor", "rubble"),
                     hazard="pit", difficult="rubble", indoor=True),
    "temple": Palette("marble_floor", "brick_wall", ("stone_floor", "cobblestone"),
                      indoor=True),
    "sewer": Palette("cobblestone", "brick_wall", ("stone_floor", "mud"),
                     water="water_shallow", hazard="water_deep", difficult="mud", indoor=True),
    "mine": Palette("cave_floor", "cave_wall", ("rubble", "dirt", "wood_floor"),
                    hazard="pit", difficult="rubble", indoor=True),
    "market": Palette("cobblestone", "stone_wall", ("dirt", "stone_floor"),
                      difficult="rubble"),
    "ship": Palette("ship_deck", "wood_wall", ("wood_floor",),
                    water="water_deep", hazard="water_deep", indoor=False),
    "camp": Palette("dirt", "wood_wall", ("grass", "mud"),
                    difficult="mud"),
    "fortress": Palette("stone_floor", "stone_wall", ("cobblestone", "dirt"),
                        hazard="pit", difficult="rubble", indoor=True),
    "tavern": Palette("wood_floor", "wood_wall", ("stone_floor", "cobblestone"),
                      indoor=True),
    "village": Palette("dirt", "wood_wall", ("grass", "cobblestone", "road"),
                       water="water_shallow", difficult="mud"),
    "fey": Palette("grass", "mountain_rock", ("tall_grass", "forest_floor", "marble_floor"),
                   water="water_shallow", difficult="tall_grass"),
    "building": Palette("wood_floor", "stone_wall", ("stone_floor", "cobblestone"),
                        indoor=True),
    "tower": Palette("stone_floor", "stone_wall", ("marble_floor", "cobblestone"),
                     hazard="pit", indoor=True),
}

DEFAULT_PALETTE = Palette("grass", "mountain_rock", ("dirt", "tall_grass"),
                          water="water_shallow", difficult="tall_grass")

# Structure -> generator kind.
STRUCTURE_KIND = {
    "dungeon": "rooms",
    "building": "rooms",
    "cave": "cellular",
    "village": "village",
    "ship": "ship",
    "tower": "tower",
    "none": "natural",
    None: "natural",
}

# Walls terrain ids -- treated as blocking for validation/spawns/walls tracing.
WALL_TERRAINS = frozenset({"stone_wall", "brick_wall", "wood_wall", "cave_wall"})
BLOCKING_TERRAINS = WALL_TERRAINS | {"mountain_rock", "volcanic_rock"}
HAZARD_TERRAINS = frozenset({"lava", "water_deep", "pit"})
WATER_TERRAINS = frozenset({"water_shallow", "water_deep", "river", "swamp", "ice"})


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #
def build_layout(spec, seed: int, manifest: dict) -> dict:
    """Build one Contract-2 layout for ``spec`` at the given ``seed``.

    For a ``multi_level`` spec this is floor 0 of a set, so it is NOT the last
    floor -- it gets a downward connector to floor 1.
    """

    ctx = _Context(spec, seed, manifest,
                   is_last_floor=not bool(spec.multi_level))
    return ctx.generate()


def build_level_set(spec, n_levels: int, manifest: dict, validator) -> list[tuple[dict, list]]:
    """Build ``n_levels`` linked floors. Floor i uses ``spec.seed + i``.

    Connection props (stairs/ladder/trapdoor) are placed at coordinates that line
    up between adjacent floors: floor i gets a *down* connector and floor i+1 gets
    an *up* connector at the SAME (x, y). Returns ``[(layout, warnings), ...]``.
    """

    from . import MAX_RETRIES
    from .validate import ValidationError

    results: list[tuple[dict, list]] = []
    prev_down: tuple[int, int] | None = None

    for i in range(n_levels):
        floor_seed = int(spec.seed) + i
        last_err = None
        for retry in range(MAX_RETRIES + 1):
            try:
                ctx = _Context(spec, floor_seed + retry, manifest,
                               floor_index=i, incoming_up=prev_down,
                               is_last_floor=(i == n_levels - 1))
                layout = ctx.generate()
                warns = validator(layout)
                break
            except ValidationError as exc:
                last_err = exc
                continue
        else:
            from . import GenerationError
            raise GenerationError(
                f"Floor {i} invalid after {MAX_RETRIES} retries: {last_err}"
            )

        # Determine this floor's down-connector for the next floor (if any).
        prev_down = ctx.chosen_down_coord if i < n_levels - 1 else None
        layout["floor_index"] = i
        results.append((layout, warns))

    return results


# --------------------------------------------------------------------------- #
# Generation context
# --------------------------------------------------------------------------- #
class _Context:
    """Holds all mutable state for one floor generation. Deterministic."""

    def __init__(self, spec, seed: int, manifest: dict, floor_index: int = 0,
                 incoming_up: tuple[int, int] | None = None,
                 is_last_floor: bool = True):
        self.spec = spec
        self.seed = int(seed)
        self.manifest = manifest or {}
        self.floor_index = floor_index
        self.incoming_up = incoming_up
        self.is_last_floor = is_last_floor

        self.cols = int(spec.cols)
        self.rows = int(spec.rows)
        self.rng = rng_from_seed(self.seed)
        self.pyrng = _py_random(self.seed)

        self.biomes = list(spec.biomes or [])
        self.features = list(spec.features or [])
        self.scenario = {f.split(":", 1)[1] for f in self.features if f.startswith("scenario:")}
        self.density = float(spec.density)
        self.mood = spec.mood

        self.kind = STRUCTURE_KIND.get(spec.structure, "natural")
        self.palette = self._resolve_palette()

        # cells[y][x]; props/spawns/connections accumulate during generation.
        self.cells = [[self.palette.floor for _ in range(self.cols)]
                      for _ in range(self.rows)]
        self.props: list[dict] = []
        self.spawns: list[dict] = []
        self.connections: list[dict] = []
        self.focal: dict | None = None
        self._prop_counter = 0
        self.chosen_down_coord: tuple[int, int] | None = None
        self._occupied: set[tuple[int, int]] = set()  # cells taken by props

    # ---- palette / biome resolution ---------------------------------- #
    def _resolve_palette(self) -> Palette:
        # Prefer a biome that matches the structure kind, else first biome, else
        # a structure-implied default, else the neutral default.
        for b in self.biomes:
            if b in BIOME_PALETTES:
                p = BIOME_PALETTES[b]
                # Keep scanning only if structure demands indoor/outdoor mismatch
                return self._structure_adjust(p)
        # No known biome: derive from structure.
        struct_default = {
            "rooms": "dungeon", "cellular": "cave", "village": "village",
            "ship": "ship", "tower": "tower",
        }.get(self.kind)
        if struct_default and struct_default in BIOME_PALETTES:
            return BIOME_PALETTES[struct_default]
        return DEFAULT_PALETTE

    def _structure_adjust(self, p: Palette) -> Palette:
        return p

    # ---- prop helpers ------------------------------------------------- #
    def _next_prop_id(self) -> str:
        self._prop_counter += 1
        return f"p{self._prop_counter:03d}"

    def _prop_defs(self) -> dict:
        return self.manifest.get("props", {}) or {}

    def _props_by_category(self, category: str) -> list[str]:
        """Prop type names of a category, biome-filtered where the manifest says
        so. Sorted for determinism (dict order in JSON is stable but we sort to
        be safe across manifest producers)."""

        out = []
        biome_key = self._biome_key()
        for name, d in self._prop_defs().items():
            if d.get("category") != category:
                continue
            allowed = d.get("biomes")
            if allowed and biome_key not in allowed and not (set(allowed) & set(self.biomes)):
                # Not matched to this biome -- still allow generic cover/furniture
                # only if the prop lists no strong biome preference overlap.
                continue
            out.append(name)
        return sorted(out)

    def _biome_key(self) -> str:
        """A single representative biome word used to filter props."""

        if self.biomes:
            return self.biomes[0]
        return {
            "rooms": "dungeon", "cellular": "cave", "village": "village",
            "ship": "ship", "tower": "tower",
        }.get(self.kind, "grassland")

    def _footprint(self, prop_type: str) -> tuple[int, int]:
        d = self._prop_defs().get(prop_type, {})
        fp = d.get("footprint", [1, 1])
        try:
            return int(fp[0]), int(fp[1])
        except Exception:  # pragma: no cover - defensive
            return 1, 1

    def _variant_count(self, prop_type: str) -> int:
        d = self._prop_defs().get(prop_type, {})
        return max(1, len(d.get("variants", []) or [1]))

    def _rotatable(self, prop_type: str) -> bool:
        return bool(self._prop_defs().get(prop_type, {}).get("rotatable", False))

    # ---- terrain classification -------------------------------------- #
    def _is_walkable(self, x: int, y: int) -> bool:
        t = self.cells[y][x]
        return t not in BLOCKING_TERRAINS and t not in HAZARD_TERRAINS

    def _is_open_floor(self, x: int, y: int) -> bool:
        """Walkable AND not already occupied by a prop AND not water/difficult-only."""

        if not self._is_walkable(x, y):
            return False
        return (x, y) not in self._occupied

    def _can_place(self, x: int, y: int, w: int, h: int, *, need_walkable=True) -> bool:
        if x < 0 or y < 0 or x + w > self.cols or y + h > self.rows:
            return False
        for dy in range(h):
            for dx in range(w):
                cx, cy = x + dx, y + dy
                if (cx, cy) in self._occupied:
                    return False
                if need_walkable and not self._is_walkable(cx, cy):
                    return False
        return True

    def _place_prop(self, prop_type: str, x: int, y: int, *, rot: int | None = None,
                    kind_focal: bool = False) -> dict | None:
        if prop_type not in self._prop_defs():
            return None
        w, h = self._footprint(prop_type)
        blocking = bool(self._prop_defs()[prop_type].get("blocking", False))
        # For rotated 90/270 the footprint swaps; try both orientations.
        candidates = []
        if rot is None:
            rots = [0, 90, 180, 270] if self._rotatable(prop_type) else [0]
        else:
            rots = [rot]
        placed = None
        for r in rots:
            ww, hh = (w, h) if r in (0, 180) else (h, w)
            if self._can_place(x, y, ww, hh, need_walkable=True):
                placed = (r, ww, hh)
                break
        if placed is None:
            return None
        r, ww, hh = placed
        variant = int(self.rng.integers(0, self._variant_count(prop_type)))
        pid = self._next_prop_id()
        prop = {"id": pid, "type": prop_type, "x": int(x), "y": int(y),
                "rot": int(r), "variant": variant}
        self.props.append(prop)
        # Mark cells occupied; blocking props also block walkability implicitly
        # (validation treats prop cells conservatively via terrain only, so we
        # additionally keep them out of spawn/other prop placement here).
        for dy in range(hh):
            for dx in range(ww):
                self._occupied.add((x + dx, y + dy))
        return prop

    # ---- top-level generate ------------------------------------------ #
    def generate(self) -> dict:
        # 1. base space
        if self.kind == "rooms":
            self._gen_rooms()
        elif self.kind == "cellular":
            self._gen_cellular()
        elif self.kind == "village":
            self._gen_village()
        elif self.kind == "ship":
            self._gen_ship()
        elif self.kind == "tower":
            self._gen_tower()
        else:
            self._gen_natural()

        # 2. biome blending / accents for natural-ish maps
        self._blend_accents()

        # 3. carved features (river/road/bridge/lake/chasm...)
        self._apply_features()

        # 4. scenario reshaping (ambush cover corridor, ruined rubble, ...)
        self._apply_scenario()

        # 5. focal point (guarantee >= 1)
        self._place_focal()

        # 6. cover / clutter / hazards / difficult terrain (density-scaled)
        self._scatter_cover_and_hazards()

        # 7. connections (stairs/ladder/trapdoor) for multi-level
        self._place_connections()

        # 8. spawns (players near entrance/edge; enemies near focal/defensible)
        self._place_spawns()

        # 9. optional walls polylines (stretch goal)
        walls = self._trace_walls()

        return self._to_layout(walls)

    # ------------------------------------------------------------------ #
    # Base-space generators
    # ------------------------------------------------------------------ #
    def _fill(self, t: str) -> None:
        for y in range(self.rows):
            for x in range(self.cols):
                self.cells[y][x] = t

    def _gen_rooms(self) -> None:
        """BSP partition -> rooms of ``floor`` inside a solid ``wall`` field,
        connected by corridors. Doorways become cover-free chokepoints."""

        wall = self.palette.wall
        floor = self.palette.floor
        self._fill(wall)

        # BSP split of the interior (leave a 1-cell wall border).
        region = (1, 1, self.cols - 2, self.rows - 2)  # x, y, w, h
        leaves = self._bsp_split(region, depth=0, max_depth=self._bsp_depth())
        rooms = []
        for (rx, ry, rw, rh) in leaves:
            if rw < 4 or rh < 4:
                continue
            # Carve a room slightly inset within the leaf.
            pad_x = int(self.rng.integers(0, max(1, rw // 4)))
            pad_y = int(self.rng.integers(0, max(1, rh // 4)))
            ix = rx + pad_x
            iy = ry + pad_y
            iw = max(3, rw - pad_x - int(self.rng.integers(0, max(1, rw // 4))))
            ih = max(3, rh - pad_y - int(self.rng.integers(0, max(1, rh // 4))))
            iw = min(iw, self.cols - 1 - ix)
            ih = min(ih, self.rows - 1 - iy)
            if iw < 3 or ih < 3:
                continue
            for yy in range(iy, iy + ih):
                for xx in range(ix, ix + iw):
                    self.cells[yy][xx] = floor
            rooms.append((ix, iy, iw, ih))

        if not rooms:
            # Fallback: single big room.
            self._carve_rect(2, 2, self.cols - 4, self.rows - 4, floor)
            rooms = [(2, 2, self.cols - 4, self.rows - 4)]

        # Connect room centres in a spanning chain + a few extra loops.
        centres = [(rx + rw // 2, ry + rh // 2) for (rx, ry, rw, rh) in rooms]
        centres.sort()
        for i in range(1, len(centres)):
            self._carve_corridor(centres[i - 1], centres[i], floor)
        # Extra connections for loops (density-dependent).
        extra = int(len(centres) * (0.2 + 0.4 * self.density))
        for _ in range(extra):
            if len(centres) < 2:
                break
            a = centres[int(self.rng.integers(0, len(centres)))]
            b = centres[int(self.rng.integers(0, len(centres)))]
            if a != b:
                self._carve_corridor(a, b, floor)

        self._rooms = rooms

    def _bsp_depth(self) -> int:
        area = self.cols * self.rows
        return int(np.clip(np.log2(max(area, 16)) - 3, 2, 5))

    def _bsp_split(self, region, depth, max_depth):
        x, y, w, h = region
        if depth >= max_depth or (w < 10 and h < 10):
            return [region]
        # Choose split axis by the longer dimension, with jitter.
        if w > h:
            split_vertical = True
        elif h > w:
            split_vertical = False
        else:
            split_vertical = bool(self.rng.integers(0, 2))
        if split_vertical and w >= 10:
            cut = int(self.rng.integers(w // 3, max(w // 3 + 1, 2 * w // 3)))
            left = (x, y, cut, h)
            right = (x + cut, y, w - cut, h)
            return (self._bsp_split(left, depth + 1, max_depth)
                    + self._bsp_split(right, depth + 1, max_depth))
        if not split_vertical and h >= 10:
            cut = int(self.rng.integers(h // 3, max(h // 3 + 1, 2 * h // 3)))
            top = (x, y, w, cut)
            bot = (x, y + cut, w, h - cut)
            return (self._bsp_split(top, depth + 1, max_depth)
                    + self._bsp_split(bot, depth + 1, max_depth))
        return [region]

    def _carve_rect(self, x, y, w, h, t):
        for yy in range(max(0, y), min(self.rows, y + h)):
            for xx in range(max(0, x), min(self.cols, x + w)):
                self.cells[yy][xx] = t

    def _carve_corridor(self, a, b, floor, width=1):
        (ax, ay), (bx, by) = a, b
        # L-shaped corridor: horizontal then vertical (order jittered).
        if self.rng.integers(0, 2):
            self._carve_hline(ay, ax, bx, floor, width)
            self._carve_vline(bx, ay, by, floor, width)
        else:
            self._carve_vline(ax, ay, by, floor, width)
            self._carve_hline(by, ax, bx, floor, width)

    def _carve_hline(self, y, x0, x1, t, width=1):
        for w in range(width):
            yy = min(self.rows - 1, max(0, y + w))
            for x in range(min(x0, x1), max(x0, x1) + 1):
                if 0 <= x < self.cols:
                    self.cells[yy][x] = t

    def _carve_vline(self, x, y0, y1, t, width=1):
        for w in range(width):
            xx = min(self.cols - 1, max(0, x + w))
            for y in range(min(y0, y1), max(y0, y1) + 1):
                if 0 <= y < self.rows:
                    self.cells[y][xx] = t

    def _gen_cellular(self) -> None:
        """Cellular-automata cave: random fill -> smoothing iterations."""

        wall = self.palette.wall
        floor = self.palette.floor

        # Initial random wall map (~45% wall), border forced wall.
        fill_prob = 0.45
        grid = (self.rng.random((self.rows, self.cols)) < fill_prob)
        grid[0, :] = grid[-1, :] = True
        grid[:, 0] = grid[:, -1] = True

        for _ in range(5):
            grid = self._ca_step(grid)

        for y in range(self.rows):
            for x in range(self.cols):
                self.cells[y][x] = wall if grid[y, x] else floor

        # Ensure connectivity is handled by validation; keep the largest region
        # by carving small tunnels between components later if needed. Here we
        # just make sure there's at least *some* floor.
        if not np.any(~grid):
            self._carve_rect(self.cols // 4, self.rows // 4,
                             self.cols // 2, self.rows // 2, floor)

    def _ca_step(self, grid):
        # Count wall neighbours (Moore, 8-neighbourhood) via padded shifts.
        padded = np.ones((self.rows + 2, self.cols + 2), dtype=bool)
        padded[1:-1, 1:-1] = grid
        neigh = np.zeros((self.rows, self.cols), dtype=np.int32)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                neigh += padded[1 + dy:self.rows + 1 + dy,
                                1 + dx:self.cols + 1 + dx].astype(np.int32)
        new = neigh >= 5
        # Fill isolated open pockets that are fully surrounded.
        new |= neigh >= 5
        new[0, :] = new[-1, :] = True
        new[:, 0] = new[:, -1] = True
        return new

    def _gen_village(self) -> None:
        """Open ground with a road grid and a handful of building footprints."""

        ground = self.palette.floor
        self._fill(ground)
        road = "road"
        # A cross/grid of roads.
        n_h = 1 + int(self.rng.integers(0, 2))
        n_v = 1 + int(self.rng.integers(0, 2))
        for _ in range(n_h):
            y = int(self.rng.integers(self.rows // 4, max(self.rows // 4 + 1, 3 * self.rows // 4)))
            self._carve_hline(y, 1, self.cols - 2, road, width=1 + (self.rows > 30))
        for _ in range(n_v):
            x = int(self.rng.integers(self.cols // 4, max(self.cols // 4 + 1, 3 * self.cols // 4)))
            self._carve_vline(x, 1, self.rows - 2, road, width=1 + (self.cols > 30))

        # Buildings: small solid-walled footprints with a wood/stone floor.
        wall = "wood_wall" if self.rng.random() < 0.5 else "stone_wall"
        bfloor = "wood_floor"
        n_buildings = 2 + int(self.density * 5)
        self._rooms = []
        for _ in range(n_buildings * 3):
            if len([r for r in getattr(self, "_rooms", [])]) >= n_buildings:
                break
            bw = int(self.rng.integers(3, 7))
            bh = int(self.rng.integers(3, 6))
            bx = int(self.rng.integers(1, max(2, self.cols - bw - 1)))
            by = int(self.rng.integers(1, max(2, self.rows - bh - 1)))
            # Don't drop a building straight on a road cell centre.
            if self.cells[by + bh // 2][bx + bw // 2] == road:
                continue
            for yy in range(by, by + bh):
                for xx in range(bx, bx + bw):
                    edge = (xx in (bx, bx + bw - 1)) or (yy in (by, by + bh - 1))
                    self.cells[yy][xx] = wall if edge else bfloor
            # Carve a doorway.
            door_x = bx + bw // 2
            self.cells[by + bh - 1][door_x] = bfloor
            self._rooms.append((bx, by, bw, bh))

    def _gen_ship(self) -> None:
        """A hull of deck surrounded by water, wood-wall railings, a hold."""

        water = self.palette.water or "water_deep"
        deck = self.palette.floor
        rail = self.palette.wall
        self._fill(water)
        # Hull inset from edges, rounded corners.
        margin_x = max(2, self.cols // 6)
        margin_y = max(2, self.rows // 8)
        hx0, hx1 = margin_x, self.cols - margin_x - 1
        hy0, hy1 = margin_y, self.rows - margin_y - 1
        for y in range(hy0, hy1 + 1):
            # Taper the bow/stern for a ship silhouette.
            t = 0.0
            span = max(1, (hy1 - hy0))
            frac = (y - hy0) / span
            taper = int(round(abs(frac - 0.5) * 2 * margin_x * 0.5))
            for x in range(hx0 + taper, hx1 - taper + 1):
                edge = (x == hx0 + taper or x == hx1 - taper or y == hy0 or y == hy1)
                self.cells[y][x] = rail if edge else deck
        # A below-deck hold (wood floor block).
        hold_w = max(3, (hx1 - hx0) // 3)
        hold_h = max(2, (hy1 - hy0) // 3)
        hcx = (hx0 + hx1) // 2 - hold_w // 2
        hcy = (hy0 + hy1) // 2 - hold_h // 2
        for yy in range(hcy, hcy + hold_h):
            for xx in range(hcx, hcx + hold_w):
                if 0 <= yy < self.rows and 0 <= xx < self.cols and self.cells[yy][xx] == deck:
                    self.cells[yy][xx] = "wood_floor"
        self._rooms = [(hx0, hy0, hx1 - hx0, hy1 - hy0)]

    def _gen_tower(self) -> None:
        """Concentric ringed rooms inside a round-ish tower footprint."""

        wall = self.palette.wall
        floor = self.palette.floor
        outside = self.palette.accent[0] if self.palette.accent else "dirt"
        self._fill(outside)
        cx, cy = self.cols / 2.0, self.rows / 2.0
        radius = min(self.cols, self.rows) / 2.0 - 1
        for y in range(self.rows):
            for x in range(self.cols):
                d = ((x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2) ** 0.5
                if d <= radius - 1:
                    self.cells[y][x] = floor
                elif d <= radius:
                    self.cells[y][x] = wall
        # Inner ring wall + door.
        inner = radius * 0.55
        for y in range(self.rows):
            for x in range(self.cols):
                d = ((x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2) ** 0.5
                if inner - 0.6 <= d <= inner + 0.6:
                    self.cells[y][x] = wall
        # Doorway through inner ring (straight down from centre).
        for y in range(int(cy), self.rows):
            if self.cells[y][int(cx)] == wall:
                self.cells[y][int(cx)] = floor
                break
        self._rooms = [(int(cx - inner), int(cy - inner), int(inner * 2), int(inner * 2))]

    def _gen_natural(self) -> None:
        """Open outdoor biome: a fractal field selects floor vs. accent terrains,
        creating organic patches; blocking rock clusters give partial cover."""

        floor = self.palette.floor
        accents = list(self.palette.accent)
        field = normalized_fractal_field(self.cols, self.rows, self.seed, base_scale=max(6, self.cols // 4))
        rock = self.palette.wall

        for y in range(self.rows):
            for x in range(self.cols):
                v = field[y, x]
                if accents and v < 0.35:
                    idx = int((v / 0.35) * len(accents)) % len(accents)
                    self.cells[y][x] = accents[idx]
                else:
                    self.cells[y][x] = floor

        # Scatter a few rock outcrops (blocking) for partial cover, but never a
        # wall-locked map -- keep them sparse and away from centre.
        n_out = int((self.cols * self.rows) / 220 * (0.5 + self.density))
        rock_seed = rng_from_seed(self.seed ^ 0x5151)
        for _ in range(n_out):
            ox = int(rock_seed.integers(0, self.cols))
            oy = int(rock_seed.integers(0, self.rows))
            r = int(rock_seed.integers(1, 3))
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    if dx * dx + dy * dy <= r * r:
                        xx, yy = ox + dx, oy + dy
                        if 0 <= xx < self.cols and 0 <= yy < self.rows:
                            if abs(xx - self.cols // 2) > 3 or abs(yy - self.rows // 2) > 3:
                                self.cells[yy][xx] = rock
        self._rooms = [(2, 2, self.cols - 4, self.rows - 4)]

    # ------------------------------------------------------------------ #
    # Accents / blending
    # ------------------------------------------------------------------ #
    def _blend_accents(self) -> None:
        """For multi-biome prompts, blend a second biome's floor via a noise mask
        so two environments meet organically (e.g. forest + coast)."""

        extra = [b for b in self.biomes[1:] if b in BIOME_PALETTES]
        if not extra or self.kind in ("rooms", "ship", "tower"):
            return
        second = BIOME_PALETTES[extra[0]]
        mask = domain_warp_field(self.cols, self.rows, self.seed ^ 0x2A2A, base_scale=max(8, self.cols // 3))
        thresh = 0.58
        for y in range(self.rows):
            for x in range(self.cols):
                if mask[y, x] > thresh and self.cells[y][x] == self.palette.floor:
                    self.cells[y][x] = second.floor

    # ------------------------------------------------------------------ #
    # Features
    # ------------------------------------------------------------------ #
    def _apply_features(self) -> None:
        feats = set(f for f in self.features if not f.startswith("scenario:"))
        # Order matters: water bodies, then rivers, then roads, then bridges.
        if "lake" in feats:
            self._carve_lake()
        if "river" in feats or ("river" in self.biomes):
            self._carve_river()
        if "chasm" in feats:
            self._carve_chasm()
        if "road" in feats:
            self._carve_road()
        if "bridge" in feats:
            self._place_bridge()
        # Feature props handled in _scatter (well/altar/statue/campfire/treasure).
        self._feature_props = feats

    def _carve_river(self) -> None:
        """A winding river carved across the map using a warped noise centreline."""

        water = "river"
        edge = self.palette.water or "water_shallow"
        vertical = self.rng.random() < 0.5
        warp = fractal_noise_2d(max(self.cols, self.rows), 1, self.seed ^ 0x1234, base_scale=8)[0]
        width = 1 + int(self.density * 2) + (min(self.cols, self.rows) > 30)
        if vertical:
            base_x = self.cols // 2
            for y in range(self.rows):
                cx = int(base_x + (warp[y % len(warp)] - 0.5) * self.cols * 0.4)
                self._paint_disc(cx, y, width, water, edge)
        else:
            base_y = self.rows // 2
            for x in range(self.cols):
                cy = int(base_y + (warp[x % len(warp)] - 0.5) * self.rows * 0.4)
                self._paint_disc(x, cy, width, water, edge)

    def _paint_disc(self, cx, cy, r, core, edge):
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                xx, yy = cx + dx, cy + dy
                if not (0 <= xx < self.cols and 0 <= yy < self.rows):
                    continue
                dist = (dx * dx + dy * dy) ** 0.5
                if dist <= r - 1:
                    if self.cells[yy][xx] not in WALL_TERRAINS:
                        self.cells[yy][xx] = core
                elif dist <= r + 0.5:
                    if self.cells[yy][xx] not in WALL_TERRAINS and self.cells[yy][xx] != core:
                        self.cells[yy][xx] = edge

    def _carve_lake(self) -> None:
        deep = self.palette.hazard if self.palette.hazard in WATER_TERRAINS else "water_deep"
        shallow = self.palette.water or "water_shallow"
        cx = int(self.rng.integers(self.cols // 4, 3 * self.cols // 4))
        cy = int(self.rng.integers(self.rows // 4, 3 * self.rows // 4))
        rad = max(2, min(self.cols, self.rows) // 5)
        field = normalized_fractal_field(self.cols, self.rows, self.seed ^ 0x9A9A, base_scale=6)
        for y in range(self.rows):
            for x in range(self.cols):
                d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
                edge = rad * (0.7 + 0.5 * field[y, x])
                if self.cells[y][x] in WALL_TERRAINS:
                    continue
                if d < edge * 0.6:
                    self.cells[y][x] = deep
                elif d < edge:
                    self.cells[y][x] = shallow

    def _carve_chasm(self) -> None:
        """A winding pit/chasm (blocking hazard) across part of the map."""

        warp = fractal_noise_2d(max(self.cols, self.rows), 1, self.seed ^ 0x7777, base_scale=7)[0]
        vertical = self.rng.random() < 0.5
        width = 1 + int(self.density * 2)
        if vertical:
            base = int(self.rng.integers(self.cols // 4, 3 * self.cols // 4))
            for y in range(self.rows):
                cx = int(base + (warp[y % len(warp)] - 0.5) * self.cols * 0.25)
                for w in range(-width, width + 1):
                    xx = cx + w
                    if 0 <= xx < self.cols and self.cells[y][xx] not in WALL_TERRAINS:
                        self.cells[y][xx] = "pit"
        else:
            base = int(self.rng.integers(self.rows // 4, 3 * self.rows // 4))
            for x in range(self.cols):
                cy = int(base + (warp[x % len(warp)] - 0.5) * self.rows * 0.25)
                for w in range(-width, width + 1):
                    yy = cy + w
                    if 0 <= yy < self.rows and self.cells[yy][x] not in WALL_TERRAINS:
                        self.cells[yy][x] = "pit"

    def _carve_road(self) -> None:
        """A winding road/path across the map (walkable, non-difficult)."""

        road = "road"
        warp = fractal_noise_2d(max(self.cols, self.rows), 1, self.seed ^ 0x4321, base_scale=10)[0]
        horizontal = self.rng.random() < 0.6
        width = 1 + (min(self.cols, self.rows) > 25)
        if horizontal:
            base_y = self.rows // 2
            for x in range(self.cols):
                cy = int(base_y + (warp[x % len(warp)] - 0.5) * self.rows * 0.3)
                for w in range(width):
                    yy = cy + w
                    if 0 <= yy < self.rows and self.cells[yy][x] not in WALL_TERRAINS:
                        self.cells[yy][x] = road
        else:
            base_x = self.cols // 2
            for y in range(self.rows):
                cx = int(base_x + (warp[y % len(warp)] - 0.5) * self.cols * 0.3)
                for w in range(width):
                    xx = cx + w
                    if 0 <= xx < self.cols and self.cells[y][xx] not in WALL_TERRAINS:
                        self.cells[y][xx] = road
        self._has_road = True

    def _place_bridge(self) -> None:
        """Lay bridge (wood_floor) over water/pit cells spanning a gap, and add a
        bridge/plank prop line if the manifest offers one."""

        # Find a water/pit band and lay a plank crossing over it.
        for y in range(self.rows):
            run = [x for x in range(self.cols)
                   if self.cells[y][x] in (WATER_TERRAINS | {"pit"})]
            if len(run) >= 3:
                bx = run[len(run) // 2]
                for xx in range(max(0, bx - self.cols // 4), min(self.cols, bx + self.cols // 4)):
                    if self.cells[y][xx] in (WATER_TERRAINS | {"pit"}):
                        self.cells[y][xx] = "wood_floor"
                # widen the crossing by 1 row if possible
                if y + 1 < self.rows:
                    for xx in range(max(0, bx - self.cols // 4), min(self.cols, bx + self.cols // 4)):
                        if self.cells[y + 1][xx] in (WATER_TERRAINS | {"pit"}):
                            self.cells[y + 1][xx] = "wood_floor"
                return

    # ------------------------------------------------------------------ #
    # Scenario reshaping
    # ------------------------------------------------------------------ #
    def _apply_scenario(self) -> None:
        if "collapsed" in self.scenario or "ruined" in self.scenario:
            self._add_rubble_and_breaks()
        if "flooded" in self.scenario:
            self._flood_low_areas()
        if "burning" in self.scenario:
            self._add_fire_hazards()
        # "ambush"/"defensive" are handled by biasing cover placement counts and
        # spawn logic (see _scatter and _place_spawns).

    def _add_rubble_and_breaks(self) -> None:
        """Sprinkle rubble (difficult) and knock holes in walls (broken walls)."""

        n = int((self.cols * self.rows) / 60 * (0.5 + self.density))
        srng = rng_from_seed(self.seed ^ 0xBEEF)
        for _ in range(n):
            x = int(srng.integers(0, self.cols))
            y = int(srng.integers(0, self.rows))
            if self.cells[y][x] not in WALL_TERRAINS and self.cells[y][x] not in HAZARD_TERRAINS:
                self.cells[y][x] = "rubble"
        # Break some wall segments to rubble (partial cave-in / ruin).
        for _ in range(n // 2):
            x = int(srng.integers(0, self.cols))
            y = int(srng.integers(0, self.rows))
            if self.cells[y][x] in WALL_TERRAINS and 0 < x < self.cols - 1 and 0 < y < self.rows - 1:
                self.cells[y][x] = "rubble"

    def _flood_low_areas(self) -> None:
        field = normalized_fractal_field(self.cols, self.rows, self.seed ^ 0xF10D, base_scale=8)
        for y in range(self.rows):
            for x in range(self.cols):
                if field[y, x] < 0.25 and self.cells[y][x] not in WALL_TERRAINS \
                        and self.cells[y][x] not in HAZARD_TERRAINS:
                    self.cells[y][x] = "water_shallow"

    def _add_fire_hazards(self) -> None:
        n = int((self.cols * self.rows) / 200 * (0.5 + self.density))
        srng = rng_from_seed(self.seed ^ 0xF1E5)
        for _ in range(n):
            x = int(srng.integers(0, self.cols))
            y = int(srng.integers(0, self.rows))
            if self.cells[y][x] not in WALL_TERRAINS and self.cells[y][x] not in HAZARD_TERRAINS:
                self.cells[y][x] = "lava" if self.palette.hazard == "lava" else "rubble"

    # ------------------------------------------------------------------ #
    # Focal point
    # ------------------------------------------------------------------ #
    def _place_focal(self) -> None:
        """Guarantee exactly one focal point. Prefer a focal prop from the
        manifest near an open central area; fall back to a well/altar-ish terrain
        marker if no focal props exist."""

        focal_types = self._props_by_category("focal")
        # Feature-requested focal beats a random one.
        preferred = None
        feats = getattr(self, "_feature_props", set())
        for want in ("altar", "well", "statue", "campfire"):
            if want in feats:
                for ft in focal_types:
                    if want in ft:
                        preferred = ft
                        break
            if preferred:
                break

        spot = self._find_open_area(prefer_center=True)
        if spot is None:
            spot = (self.cols // 2, self.rows // 2)
        fx, fy = spot

        chosen = None
        if focal_types:
            ftype = preferred or focal_types[int(self.rng.integers(0, len(focal_types)))]
            prop = self._place_prop(ftype, fx, fy)
            if prop is not None:
                chosen = {"x": prop["x"], "y": prop["y"], "prop_id": prop["id"],
                          "kind": ftype}
        if chosen is None:
            # Terrain-based focal fallback: a marble dais / cobblestone ring.
            marker = "marble_floor" if "marble_floor" in TERRAIN_SET else "cobblestone"
            if 0 <= fy < self.rows and 0 <= fx < self.cols:
                self.cells[fy][fx] = marker
            chosen = {"x": fx, "y": fy, "prop_id": None, "kind": "landmark"}
        self.focal = chosen

    def _find_open_area(self, prefer_center=True, size=3):
        """Find the top-left of an open ``size``x``size`` walkable block, biased
        toward the map centre. Returns (x, y) of its centre or None."""

        best = None
        best_score = -1.0
        cx, cy = self.cols / 2.0, self.rows / 2.0
        for y in range(0, self.rows - size + 1):
            for x in range(0, self.cols - size + 1):
                ok = True
                for dy in range(size):
                    for dx in range(size):
                        if not self._is_open_floor(x + dx, y + dy):
                            ok = False
                            break
                    if not ok:
                        break
                if not ok:
                    continue
                mx, my = x + size // 2, y + size // 2
                if prefer_center:
                    dist = ((mx - cx) ** 2 + (my - cy) ** 2) ** 0.5
                    score = -dist
                else:
                    score = mx + my
                if score > best_score:
                    best_score = score
                    best = (mx, my)
        return best

    # ------------------------------------------------------------------ #
    # Cover / clutter / hazards -- density scales counts
    # ------------------------------------------------------------------ #
    def _scatter_cover_and_hazards(self) -> None:
        area = self.cols * self.rows
        # Base rates per cell, scaled linearly by density (0..1).
        # density directly scales counts, independent of mood; mood only tweaks
        # a mild multiplier so it doesn't dominate density.
        mood_mult = {"peaceful": 0.6, "neutral": 1.0, "tense": 1.15,
                     "combat": 1.3, "eerie": 1.05}.get(self.mood, 1.0)

        cover_rate = 0.012 * (0.15 + self.density) * mood_mult
        clutter_rate = 0.010 * (0.15 + self.density) * mood_mult
        hazard_rate = 0.006 * (0.10 + self.density) * mood_mult
        difficult_rate = 0.010 * (0.10 + self.density)

        # Scenario biasing: ambush -> more cover along the road; defensive ->
        # more cover near focal; collapsed/ruined already added rubble.
        n_cover = int(area * cover_rate)
        n_clutter = int(area * clutter_rate)
        n_hazard = int(area * hazard_rate)
        n_difficult = int(area * difficult_rate)

        if "ambush" in self.scenario:
            n_cover = int(n_cover * 1.6)
        if "defensive" in self.scenario:
            n_cover = int(n_cover * 1.4)

        cover_types = self._props_by_category("cover")
        furniture_types = self._props_by_category("furniture")
        nature_types = self._props_by_category("nature")
        hazard_types = self._props_by_category("hazard")

        clutter_pool = furniture_types if self.palette.indoor else nature_types
        if not clutter_pool:
            clutter_pool = furniture_types or nature_types

        # Feature props (well/altar/statue/campfire/treasure) placed once each.
        self._place_feature_props()

        # Cover: biased toward the road for ambush, else uniform open floor.
        road_cells = self._road_cells() if ("ambush" in self.scenario) else None
        self._scatter_props(cover_types, n_cover, near=road_cells)
        self._scatter_props(clutter_pool, n_clutter)
        self._scatter_props(hazard_types, max(0, n_hazard // 2))  # some hazard props

        # Terrain hazards + difficult patches (only where palette defines them).
        self._scatter_terrain(self.palette.hazard, n_hazard, hazardous=True)
        self._scatter_terrain(self.palette.difficult, n_difficult, hazardous=False)

    def _place_feature_props(self) -> None:
        feats = getattr(self, "_feature_props", set())
        mapping = {
            "well": "focal", "altar": "focal", "statue": "focal",
            "campfire": "focal", "treasure": "focal",
        }
        for want, cat in mapping.items():
            if want not in feats:
                continue
            # Focal already used one; place an extra feature prop elsewhere.
            candidates = [p for p in self._props_by_category(cat) if want in p]
            if not candidates:
                continue
            spot = self._find_open_area(prefer_center=False)
            if spot is None:
                continue
            self._place_prop(candidates[0], spot[0], spot[1])

    def _road_cells(self):
        cells = [(x, y) for y in range(self.rows) for x in range(self.cols)
                 if self.cells[y][x] == "road"]
        return cells or None

    def _scatter_props(self, types, count, near=None):
        if not types or count <= 0:
            return
        srng = self.rng
        attempts = count * 6
        placed = 0
        for _ in range(attempts):
            if placed >= count:
                break
            if near:
                base = near[int(srng.integers(0, len(near)))]
                x = int(np.clip(base[0] + srng.integers(-2, 3), 0, self.cols - 1))
                y = int(np.clip(base[1] + srng.integers(-2, 3), 0, self.rows - 1))
            else:
                x = int(srng.integers(0, self.cols))
                y = int(srng.integers(0, self.rows))
            ptype = types[int(srng.integers(0, len(types)))]
            # Keep a clear focal neighbourhood and avoid the guaranteed 5x5 later
            # by not over-cluttering; validation will still guard the ratio.
            if self._place_prop(ptype, x, y) is not None:
                placed += 1

    def _scatter_terrain(self, terrain, count, hazardous):
        if not terrain or terrain not in TERRAIN_SET or count <= 0:
            return
        srng = rng_from_seed(self.seed ^ (0xA1 if hazardous else 0xB2))
        placed = 0
        for _ in range(count * 5):
            if placed >= count:
                break
            x = int(srng.integers(0, self.cols))
            y = int(srng.integers(0, self.rows))
            if (x, y) in self._occupied:
                continue
            if self.cells[y][x] in WALL_TERRAINS:
                continue
            # Don't convert the focal cell.
            if self.focal and self.focal["x"] == x and self.focal["y"] == y:
                continue
            self.cells[y][x] = terrain
            placed += 1

    # ------------------------------------------------------------------ #
    # Connections (multi-level)
    # ------------------------------------------------------------------ #
    def _place_connections(self) -> None:
        if not self.spec.multi_level:
            return
        conn_types = self._props_by_category("connection")
        up_type = self._pick_conn(conn_types, ("stairs_up", "ladder", "stairs"))
        down_type = self._pick_conn(conn_types, ("stairs_down", "trapdoor", "ladder", "stairs"))

        # Incoming UP connector must sit at the coordinate the floor below chose.
        if self.incoming_up is not None:
            ux, uy = self.incoming_up
            ux, uy = self._nearest_open(ux, uy)
            if up_type:
                self._place_prop(up_type, ux, uy, rot=0)
            self.connections.append({"x": ux, "y": uy, "kind": "stairs_up",
                                     "to_floor": self.floor_index - 1})

        # Choose a DOWN connector location for the next floor to mirror -- but
        # only when there IS a floor below (not on the last floor).
        if self.is_last_floor:
            self.chosen_down_coord = None
            return
        dspot = self._find_open_area(prefer_center=False)
        if dspot is None:
            dspot = self._nearest_open(self.cols - 3, self.rows - 3)
        dx, dy = dspot
        if down_type:
            self._place_prop(down_type, dx, dy, rot=0)
        self.connections.append({"x": dx, "y": dy, "kind": "stairs_down",
                                 "to_floor": self.floor_index + 1})
        self.chosen_down_coord = (dx, dy)

    def _pick_conn(self, conn_types, preferences):
        for pref in preferences:
            for c in conn_types:
                if pref in c:
                    return c
        return conn_types[0] if conn_types else None

    def _nearest_open(self, x, y):
        x = int(np.clip(x, 0, self.cols - 1))
        y = int(np.clip(y, 0, self.rows - 1))
        if self._is_open_floor(x, y):
            return (x, y)
        for r in range(1, max(self.cols, self.rows)):
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    xx, yy = x + dx, y + dy
                    if 0 <= xx < self.cols and 0 <= yy < self.rows and self._is_open_floor(xx, yy):
                        return (xx, yy)
        return (x, y)

    # ------------------------------------------------------------------ #
    # Spawns
    # ------------------------------------------------------------------ #
    def _place_spawns(self) -> None:
        """Players near an entrance/edge; enemies near the focal / defensible."""

        # Player spawns: cluster near a walkable edge cell.
        edge = self._pick_edge_open()
        n_players = 2 + int(self.rng.integers(0, 3))
        for cell in self._cells_around(edge, n_players):
            self.spawns.append({"x": cell[0], "y": cell[1], "role": "player"})

        # Enemy spawns: near the focal point (defensible), a few tiles off.
        fx = self.focal["x"] if self.focal else self.cols // 2
        fy = self.focal["y"] if self.focal else self.rows // 2
        enemy_anchor = self._nearest_open(fx + 2, fy + 1)
        n_enemies = 2 + int(self.rng.integers(0, 4))
        used = {(s["x"], s["y"]) for s in self.spawns}
        for cell in self._cells_around(enemy_anchor, n_enemies, exclude=used):
            self.spawns.append({"x": cell[0], "y": cell[1], "role": "enemy"})

    def _pick_edge_open(self):
        # Prefer the edge nearest a corridor opening; scan all border cells.
        border = []
        for x in range(self.cols):
            border.append((x, 0))
            border.append((x, self.rows - 1))
        for y in range(self.rows):
            border.append((0, y))
            border.append((self.cols - 1, y))
        open_border = [c for c in border if self._is_open_floor(*c)]
        if open_border:
            return open_border[int(self.rng.integers(0, len(open_border)))]
        # No open border: pick nearest open to a corner.
        return self._nearest_open(1, 1)

    def _cells_around(self, anchor, n, exclude=None):
        exclude = set(exclude or ())
        ax, ay = anchor
        out = []
        seen = set(exclude)
        for r in range(0, max(self.cols, self.rows)):
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    if abs(dx) != r and abs(dy) != r and r > 0:
                        continue
                    xx, yy = ax + dx, ay + dy
                    if (xx, yy) in seen:
                        continue
                    if 0 <= xx < self.cols and 0 <= yy < self.rows and self._is_open_floor(xx, yy):
                        out.append((xx, yy))
                        seen.add((xx, yy))
                        if len(out) >= n:
                            return out
        return out

    # ------------------------------------------------------------------ #
    # Walls tracing (stretch goal)
    # ------------------------------------------------------------------ #
    def _trace_walls(self) -> list:
        """Rectilinear boundary tracing of blocking terrain.

        Emits axis-aligned unit segments on the grid lines that separate a
        blocking cell from a non-blocking neighbour (or the map edge). Segments
        are given in GRID coordinates (cell-corner units), matching Contract 2's
        ``walls`` polyline shape. This is a conservative but correct boundary
        set; it is not merged into long polylines (kept as unit edges) which is
        still valid and easy for a VTT importer to consume.
        """

        blocking = np.zeros((self.rows, self.cols), dtype=bool)
        for y in range(self.rows):
            for x in range(self.cols):
                if self.cells[y][x] in BLOCKING_TERRANS_CACHE:
                    blocking[y, x] = True

        segments: list = []
        for y in range(self.rows):
            for x in range(self.cols):
                if not blocking[y, x]:
                    continue
                # Left edge
                if x == 0 or not blocking[y, x - 1]:
                    segments.append([[x, y], [x, y + 1]])
                # Right edge
                if x == self.cols - 1 or not blocking[y, x + 1]:
                    segments.append([[x + 1, y], [x + 1, y + 1]])
                # Top edge
                if y == 0 or not blocking[y - 1, x]:
                    segments.append([[x, y], [x + 1, y]])
                # Bottom edge
                if y == self.rows - 1 or not blocking[y + 1, x]:
                    segments.append([[x, y + 1], [x + 1, y + 1]])
        # Merge collinear unit segments into longer polylines for compactness.
        return _merge_segments(segments)

    # ------------------------------------------------------------------ #
    # Serialisation
    # ------------------------------------------------------------------ #
    def _to_layout(self, walls) -> dict:
        indoor = self._decide_indoor()
        layout = {
            "cols": self.cols,
            "rows": self.rows,
            "seed": self.seed,
            "spec": self.spec.to_dict(),
            "cells": self.cells,
            "props": self.props,
            "focal": self.focal,
            "spawns": self.spawns,
            "connections": self.connections,
            "indoor": indoor,
            "walls": walls,
        }
        return layout

    def _decide_indoor(self) -> bool:
        # Palette carries an indoor hint; structure also implies it.
        if self.kind in ("rooms", "tower"):
            return True
        if self.kind in ("village", "ship", "natural"):
            return self.palette.indoor
        if self.kind == "cellular":
            return True
        return self.palette.indoor


# Cache blocking terrain set at import (avoids per-cell attribute lookups).
BLOCKING_TERRANS_CACHE = BLOCKING_TERRAINS


def _py_random(seed: int):
    import random
    return random.Random(int(seed) & 0x7FFFFFFFFFFFFFFF)


def _merge_segments(segments: list) -> list:
    """Merge unit-length axis-aligned segments into maximal collinear runs.

    Deterministic: sorts inputs, then greedily extends horizontal and vertical
    runs. Reduces ``walls`` size substantially without changing geometry.
    """

    if not segments:
        return []

    horiz: dict[int, set[tuple[int, int]]] = {}
    vert: dict[int, set[tuple[int, int]]] = {}
    for (a, b) in segments:
        (x1, y1), (x2, y2) = a, b
        if y1 == y2:  # horizontal edge at row y1, from x1..x2
            horiz.setdefault(y1, set()).add((min(x1, x2), max(x1, x2)))
        else:         # vertical edge at col x1, from y1..y2
            vert.setdefault(x1, set()).add((min(y1, y2), max(y1, y2)))

    out: list = []
    for y in sorted(horiz):
        xs = sorted(horiz[y])
        run_start, run_end = xs[0]
        for (s, e) in xs[1:]:
            if s == run_end:
                run_end = e
            else:
                out.append([[run_start, y], [run_end, y]])
                run_start, run_end = s, e
        out.append([[run_start, y], [run_end, y]])
    for x in sorted(vert):
        ys = sorted(vert[x])
        run_start, run_end = ys[0]
        for (s, e) in ys[1:]:
            if s == run_end:
                run_end = e
            else:
                out.append([[x, run_start], [x, run_end]])
                run_start, run_end = s, e
        out.append([[x, run_start], [x, run_end]])
    return out
