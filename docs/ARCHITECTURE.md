# ARCHITECTURE — TTRPG Grid Map Generator

Authoritative interface contracts for the codebase. Each subsystem conforms to these contracts;
changes to a contract must update every consumer listed here.

## Stack (final decision)

- **Backend / engine:** Python 3.10+. Runtime dependencies: `flask`, `pillow`, `numpy` ONLY.
  Noise (simplex/value noise) is implemented in-repo with numpy — no extra noise dependency.
  SQLite via stdlib `sqlite3`. Multi-page PDF export via Pillow's native PDF save.
- **Frontend:** vanilla HTML/CSS/JS single-page app, served as static files by Flask. NO build
  step, NO npm, NO CDN links, NO web fonts fetched at runtime (system font stacks only —
  e.g. Georgia/Palatino serif stack for headings). The app must work fully offline.
- **Tests:** pytest.
- **Hard constraint (spec §5):** after install, zero network calls at runtime. Nothing in
  `server/` or `web/` may fetch a remote URL.

## Repository layout

```
assets/                     # Phase A tile library (committed PNGs)
  manifest.json
  tiles/<terrain>/<terrain>_NN.png       # 140×140 base tiles, 3–6 variants each
  props/<prop>/<prop>_NN.png             # RGBA sprites, sized to footprint × 140px
scripts/
  generate_assets.py        # deterministic Phase A generator (seeded)
server/
  app.py                    # Flask app, all HTTP routes
  library.py                # SQLite + on-disk library storage
  exports.py                # zip bulk, Foundry module, print PDF,
                            #   library backup/import
  engine/                   # generation engine package
    __init__.py             # exports the public API (Contract 3)
    parser.py               # prompt → GenerationSpec
    noise.py                # seeded value/simplex noise (numpy)
    layout.py               # GenerationSpec → LayoutGrid
    validate.py             # 5×5 open block, reachability, ratios
    assemble.py             # LayoutGrid + assets → PIL images
    export.py               # gridded/gridless PNGs, metadata dict
web/                        # static SPA
  index.html  app.js  style.css
tests/
  test_grid_math.py  test_validation.py  test_engine.py
  test_api.py  test_library.py
library-data/               # runtime map storage (created on demand; samples committed at ship)
docs/ARCHITECTURE.md        # this file
```

## Contract 1 — `assets/manifest.json`

```jsonc
{
  "px_per_square": 140,                // native tile resolution
  "generator_seed": 7,                 // seed used by scripts/generate_assets.py
  "terrains": {
    "grass": {
      "display": "Grass",
      "walkable": true,                // tokens can occupy
      "blocking": false,               // blocks movement AND sight (walls, solid rock)
      "hazard": false,                 // lava, deep water, pits
      "difficult": false,              // difficult terrain (mud, rubble fields)
      "indoor": false,                 // used for global-illumination default
      "priority": 30,                  // higher priority draws OVER lower at boundaries
      "tiles": ["tiles/grass/grass_01.png", "..."]
    }
    // ... every terrain in the list below
  },
  "props": {
    "crate": {
      "display": "Crate",
      "category": "cover",             // cover | furniture | nature | hazard | focal | structure | connection
      "footprint": [1, 1],             // grid squares [w, h]
      "blocking": true,
      "biomes": ["dungeon", "village", "ship", "mine"],
      "variants": ["props/crate/crate_01.png", "..."],
      "rotatable": true
    }
  }
}
```

**Required terrain ids (the asset library ships all; the engine references only these):**
`grass, tall_grass, forest_floor, jungle_floor, dirt, road, cobblestone, sand, snow, ice,
swamp, mud, water_shallow, water_deep, river, lava, volcanic_rock, stone_floor, wood_floor,
cave_floor, mountain_rock, stone_wall, brick_wall, wood_wall, cave_wall, rubble, ship_deck,
marble_floor, pit`

**Terrain transitions are NOT pre-rendered art.** `assemble.py` blends neighbouring terrains at
runtime using seeded noise-feathered alpha masks, with `priority` deciding which terrain
overlaps which. This is deterministic per map seed.

**Required prop categories** (the asset library ships ≥40 prop types, ≥2 variants where sensible):
cover (rocks, crates, barrels, low walls, fallen logs), furniture (table, chair, bed, bookshelf,
market stall, anvil, bar counter), nature (trees ×several species, bushes, stumps, stalagmites,
lily pads, coral), hazard (pit, spike trap plate, bones), focal (altar, well, statue, campfire,
fountain, brazier, chest, shipwreck piece, standing stone, throne), structure (door, double door,
tent, cart, boat), connection (stairs_up, stairs_down, ladder, trapdoor).

## Contract 2 — `layout.json` (per generated map, stored in its library dir)

```jsonc
{
  "cols": 30, "rows": 20,
  "seed": 123456789,
  "spec": { /* the full GenerationSpec that produced this map, verbatim */ },
  "cells": [ ["grass", "grass", "..."], /* rows arrays × cols entries, terrain ids */ ],
  "props": [
    {"id": "p001", "type": "crate", "x": 3, "y": 4, "rot": 90, "variant": 1}
  ],
  "focal": {"x": 15, "y": 10, "prop_id": "p017", "kind": "altar"},
  "spawns": [ {"x": 2, "y": 17, "role": "player"}, {"x": 24, "y": 4, "role": "enemy"} ],
  "connections": [ {"x": 5, "y": 5, "kind": "stairs_down", "to_floor": 1} ],
  "indoor": true,
  "walls": [ [[x1,y1],[x2,y2]], ... ]   // optional polyline segments in GRID coords (stretch goal)
}
```

(x, y) are grid coordinates, x = column 0-based from left, y = row 0-based from top. Prop (x, y)
is the top-left square of its footprint. `rot` ∈ {0, 90, 180, 270}.

## Contract 3 — engine Python API (`server/engine/__init__.py`)

```python
@dataclass
class GenerationSpec:
    prompt: str = ""
    cols: int = 30
    rows: int = 20
    px_per_square: int = 140
    feet_per_square: int = 5
    seed: int = 0                    # 0 = caller must randomize BEFORE calling generate
    density: float = 0.5             # 0.0 sparse .. 1.0 dense
    mood: str = "neutral"            # peaceful | neutral | tense | combat | eerie
    biomes: list[str] = field(...)   # explicit biome tags; may be empty (parser fills)
    structure: str | None = None     # dungeon | building | cave | village | ship | tower | none
    features: list[str] = field(...) # parsed feature requests: "river", "road", "bridge", ...
    multi_level: bool = False
    levels: int = 1
    palette_mode: str = "standard"   # standard | colorblind
    title: str = ""

def parse_prompt(text: str, overrides: dict | None = None) -> GenerationSpec: ...
    # local keyword/synonym parsing only; overrides (from UI controls) win over parsed values

@dataclass
class MapResult:
    layout: dict                     # Contract 2 structure
    gridless: PIL.Image.Image
    gridded: PIL.Image.Image
    metadata: dict                   # Contract 6 structure
    warnings: list[str]

def generate_map(spec: GenerationSpec) -> MapResult: ...
    # full pipeline: layout → validate (retry on seed+retry_count) → assemble → metadata
    # multi_level=True: returns the FIRST floor; generate_level_set() returns all floors

def generate_level_set(spec: GenerationSpec) -> list[MapResult]: ...
    # floor i uses seed + i; connection coordinates line up between floors

def assemble_from_layout(layout: dict, palette_mode: str = "standard") -> MapResult: ...
    # re-composite an (edited) layout without re-running procgen — used by manual editing

def estimate_size(cols: int, rows: int, px: int) -> dict: ...
    # {"width": int, "height": int, "megapixels": float, "est_bytes": int, "warnings": [str]}
```

Determinism rule: ALL randomness inside the engine flows from `spec.seed` through
`numpy.random.default_rng(seed)` / `random.Random(seed)` instances passed explicitly.
No module-level RNG, no `time`-based entropy. Validation retries use `seed + retry_count`
(max 8 retries, then raise `GenerationError`).

## Contract 4 — HTTP API (Flask, all under `/api`)

```
POST /api/generate            {prompt?, cols?, rows?, px_per_square?, feet_per_square?,
                               seed?, density?, mood?, biomes?, structure?, multi_level?,
                               levels?, palette_mode?, title?}
                              → 200 {map: MapRecord}  (or {maps: [...]} when multi_level)
POST /api/estimate            {cols, rows, px_per_square} → estimate_size() output
GET  /api/maps                ?search=&tag=&biome=&favorite=1&sort=newest|oldest&set=<set_id>
                              → {maps: [MapRecord], total_count, total_bytes}
GET  /api/maps/<id>           → {map: MapRecord, layout: {...}}
PATCH /api/maps/<id>          {title?, tags?, favorite?} → {map: MapRecord}
DELETE /api/maps/<id>         → 204   (removes DB row AND the map's directory on disk)
GET  /api/maps/<id>/file/<kind>   kind ∈ gridless|gridded|thumb|metadata
                              → file download, Content-Disposition per Contract 6 naming
POST /api/maps/<id>/edit      {set_cells?: [{x,y,terrain}], props_add?: [...],
                               props_remove?: [prop_id], props_update?: [{id, rot?, variant?, type?}]}
                              → re-composites via assemble_from_layout, saves, → {map, layout}
POST /api/maps/<id>/revert    → restore last *generated* layout/images → {map, layout}
GET  /api/maps/<id>/print-pdf ?paper=letter|a4 → PDF download (1 inch per square, tiled,
                                                 overlap margins + alignment marks)
GET  /api/export/bulk         ?ids=a,b,c → zip of all files for those maps
GET  /api/export/foundry-module ?ids=a,b,c&name=<module-name> → zip: valid module.json +
                                                                 maps organized for Data/modules
GET  /api/library/export      → single archive (zip) of DB + all map dirs
POST /api/library/import      multipart archive upload → merges into library → {imported: n}
GET  /api/stats               → {map_count, total_bytes, by_biome: {...}}
GET  /api/assets/palette      → manifest-derived list of terrains + props for the editor UI
GET  /                        → web/index.html (static SPA); /static/* → web assets
```

`MapRecord` (JSON): `{id, title, prompt, created_at (ISO), cols, rows, px_per_square,
feet_per_square, seed, density, mood, biomes: [], tags: [], favorite: bool, set_id, floor_index,
width_px, height_px, bytes, thumb_url, warnings: []}`

Errors: JSON `{error: "message"}` with 4xx/5xx. Unknown id → 404.

## Contract 5 — library storage

- SQLite at `library-data/library.db`, table `maps`:
  `id TEXT PRIMARY KEY, title TEXT, prompt TEXT, created_at TEXT, cols INT, rows INT,
   px INT, feet INT, seed INT, density REAL, mood TEXT, biomes TEXT(json), tags TEXT(json),
   favorite INT, set_id TEXT, floor_index INT, dir TEXT, bytes INT`
- Per-map directory `library-data/maps/<id>/` containing:
  `gridless.png, gridded.png, thumb.jpg (≤400px wide), layout.json, layout.generated.json
   (pristine copy for revert), metadata.json`
- `id` = 12-char lowercase hex (uuid4 prefix). `set_id` groups multi-level floors.

## Contract 6 — export naming & metadata sidecar

- Download names: `<slug>_<cols>x<rows>.png` (gridless), `<slug>_<cols>x<rows>_gridded.png`,
  `<slug>_<cols>x<rows>.json`. Slug = title lowercased, `[^a-z0-9]+` → `-`, trimmed, ≤60 chars.
  The `<cols>x<rows>` token is load-bearing: Owlbear auto-detects grid dimensions from it.
- `metadata.json`:

```jsonc
{
  "title": "...", "prompt": "...", "generated_at": "ISO8601",
  "grid": {"columns": 30, "rows": 20, "px_per_square": 140, "feet_per_square": 5},
  "image": {"width": 4200, "height": 2800},
  "foundry": {"grid_size": 140, "scene_width": 4200, "scene_height": 2800,
              "grid_type": "Square", "global_illumination": false},
  "owlbear": {"grid_size_px": 140, "grid_type": "Square", "cell_size_ft": 5,
              "grid_columns": 30, "grid_rows": 20},
  "seed": 123456789, "density": 0.5, "mood": "eerie", "biomes": ["dungeon"],
  "spawn_points": [{"x": 2, "y": 17, "role": "player"}],
  "connections": [], "set_id": null, "floor_index": 0
}
```

- `global_illumination`: true when `layout.indoor == false`, else false.
- Gridded overlay: 1px lines at 30% black (60% on `colorblind` palette), drawn every
  `px_per_square` px starting at (0,0).

## Rendering / palette rules

- Assembly composites at native 140px/square, then rescales the FULL image once to the requested
  `px_per_square` (Lanczos). This keeps art quality consistent at any export resolution.
- Directional lighting: single post-processing pass — soft drop shadows offset south-east from
  blocking props/walls, plus a subtle global vignette. Mood shifts the color grade
  (peaceful=warm/bright, eerie=cool/desaturated+vignette, combat=high contrast).
- `colorblind` mode: same tiles, post-pass with increased contrast, PLUS pattern overlays baked
  onto hazard cells (diagonal hatching) and blocking terrain boundaries (dotted edge) so meaning
  never rides on hue alone.

## Size guardrails (spec §2)

`estimate_size` warns when width×height > 50 MP or est file > 20 MB (comfortably under Owlbear's
~67 MP / 25 MB ceiling); the UI must surface these warnings BEFORE generation.

