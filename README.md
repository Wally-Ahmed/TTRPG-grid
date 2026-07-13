# Cartographer's Table — TTRPG Grid Map Generator

Type a sentence, get a print-ready, grid-perfect fantasy battle map. Runs entirely on
your own computer — no account, no API key, no internet, no per-map cost.

---

## Table of contents

1. [What this is](#1-what-this-is)
2. [Requirements & setup](#2-requirements--setup)
3. [How it works, end to end](#3-how-it-works-end-to-end)
4. [Using the app](#4-using-the-app)
5. [Importing a map into Foundry VTT](#5-importing-a-map-into-foundry-vtt)
6. [Importing a map into Owlbear Rodeo](#6-importing-a-map-into-owlbear-rodeo)
7. [Grid & sizing conventions](#7-grid--sizing-conventions)
8. [Cost & offline behavior](#8-cost--offline-behavior)
9. [Supported biomes & environments](#9-supported-biomes--environments)
10. [Known limitations & fallbacks used](#10-known-limitations--fallbacks-used)
11. [Project structure](#11-project-structure)
12. [Usage rights of generated maps](#12-usage-rights-of-generated-maps)

---

## 1. What this is

**Cartographer's Table** is a desktop web app that turns a plain-English description into a
top-down, grid-aligned fantasy battle map ready to drop straight into a virtual tabletop
(VTT) like **Foundry VTT** or **Owlbear Rodeo**, or to print out for in-person play.

You type something like *"a collapsed dwarven mine with an underground river and
rubble-choked side passages"*, tweak a few optional dials if you want (map size, mood,
how crowded it is), and click **Generate Map**. A few seconds later you have:

- a **gridless PNG** — the clean map image you actually import into a VTT (the VTT draws its
  own grid on top);
- a **gridded PNG** — the same map with a grid baked in, handy for printing or a quick look;
- a **metadata JSON** file — a small "cheat sheet" that tells you the exact numbers to type
  into Foundry or Owlbear (grid size, scene dimensions, suggested spawn points, and more);
- and, on demand, a **print-ready PDF** (tiled across letter- or A4-size pages at exactly
  one inch per square) and a ready-to-install **Foundry module**.

Every map you make is saved to a local **Library** so you can find it, rename it, tag it,
favorite it, download it again, back the whole collection up, or permanently delete it.

The important thing to understand up front: **the artwork is not drawn live and nothing is
fetched over the internet.** All the visual building blocks (terrain textures and props like
crates, trees, altars, and stairs) were created once, ahead of time, and ship inside this
project as image files. Every time you generate a map, the app is simply *arranging and
blending those pre-made pieces* on a grid. That's why it's fast, free, and works with your
Wi-Fi turned off.

---

## 2. Requirements & setup

### What you need

- **Python 3.10 or newer.** That's the only prerequisite. Everything else (a tiny web server
  and image libraries) installs automatically into a self-contained folder the first time you
  run the app — it does not touch your system-wide Python.
- No Node.js, no npm, no build step, no database server. The web interface is plain
  HTML/CSS/JavaScript served locally by the app.

### The one command (recommended)

From the project folder:

**macOS / Linux**

```bash
./start.sh
```

**Windows**

```bat
start.bat
```

That single command is *everything*. On the **first** run it:

1. checks that Python 3.10+ is installed (and prints a clear message telling you what to do if
   it isn't),
2. creates a private virtual environment in a `.venv/` folder,
3. installs the dependencies (Flask, Pillow, NumPy) into it,
4. then launches the app and opens it in your browser at **http://127.0.0.1:8420**.

On **every run after that** it skips the setup (in well under a second) and just launches. So
`./start.sh` (or `start.bat`) is *also* your everyday "start the app" command — there's nothing
else to remember.

You can pass extra options straight through; they're forwarded to the app:

```bash
./start.sh --port 8080      # serve on a different port
./start.sh --no-browser     # don't auto-open a browser tab
```

> If `./start.sh` reports "permission denied," make it executable once with
> `chmod +x start.sh`, then run it again. (It's already committed with the executable bit set,
> so this is rarely needed.)

> **Port already in use?** On macOS, the AirPlay Receiver occupies port 5000 (which is why the
> app defaults to **8420**). If 8420 is also taken, the app automatically picks the next free
> port and prints which one it chose — or you can pick your own with `./start.sh --port <n>`.

To stop the app, return to the terminal window and press **Ctrl+C**.

### Manual setup (fallback, if you'd rather do it by hand)

The bootstrap scripts just automate these standard steps. You can run them yourself:

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

**Windows (PowerShell)**

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py
```

Then open **http://127.0.0.1:8420** in your browser if it didn't open on its own.

`run.py` also accepts `--port <n>`, `--host <addr>` (defaults to loopback-only `127.0.0.1`),
`--no-browser`, and `--library-root <dir>`. A `PORT` environment variable works too.

### Tested on

- **macOS** with **Python 3.10+** (primary development and test platform).
- **Linux** and **Windows** are expected to work — the code uses only cross-platform Python
  standard-library features plus Flask/Pillow/NumPy, and `start.bat` mirrors `start.sh` — but
  the polished, day-to-day testing was done on macOS. If you hit a platform-specific snag,
  the manual steps above are the reliable fallback.

---

## 3. How it works, end to end

The whole system is built on a simple two-phase idea:

- **Phase A — "commission the art once."** Before you ever run the app, a complete library of
  top-down tiles and props was generated and saved as image files inside `assets/`. Think of
  this as hiring an artist a single time to paint every kind of floor, wall, tree, crate, and
  altar you might need. This already happened; the finished art is in the repo.
- **Phase B — "arrange it forever after, for free."** Every time you generate a map, the app
  is *only* choosing which of those pre-made pieces to place, where to place them on the grid,
  and how to blend their edges together. Nothing is drawn from scratch and **nothing is
  requested over the internet.**

Here is what actually happens from the moment you click **Generate Map**:

```
   You type a prompt  ─────────────────────────────────────────────┐
   + optional dials (size, mood, density, seed, biomes…)           │
                                                                    ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │ 1. UNDERSTAND THE PROMPT                                                 │
 │    A local keyword reader scans your words ("mine", "river", "temple",  │
 │    "eerie"…) and turns them into a plan: which biomes, what kind of     │
 │    structure, how crowded, what mood. Your on-screen dials override     │
 │    anything the reader guessed. No language model, no network.          │
 └────────────────────────────────────────────────────────────────────────┘
                                    ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │ 2. LAY OUT THE MAP  (procedural, seeded)                                │
 │    Rooms/corridors for dungeons & buildings (BSP splitting), organic    │
 │    caverns (cellular automata), natural biomes (noise fields), villages │
 │    (plot partitioning). The random seed makes this repeatable: same     │
 │    seed + same settings = the exact same map, every time.               │
 └────────────────────────────────────────────────────────────────────────┘
                                    ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │ 3. VALIDATE IT'S PLAYABLE                                               │
 │    Guarantees at least one open 5×5 block of squares, checks that the   │
 │    map is fully connected (auto-carving a path if something got walled  │
 │    off), and keeps dimensions to whole squares. If a layout fails, it   │
 │    retries with a nudged seed (up to 8 times) before giving up.         │
 └────────────────────────────────────────────────────────────────────────┘
                                    ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │ 4. PAINT IT FROM THE TILE LIBRARY                                       │
 │    Places the chosen terrain tiles and props, feathers the seams        │
 │    between terrains with soft noise masks, adds drop shadows and a mood │
 │    color grade (warm & bright, cool & eerie, high-contrast combat…).    │
 │    Colorblind mode adds patterns so meaning never rides on color alone. │
 └────────────────────────────────────────────────────────────────────────┘
                                    ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │ 5. EXPORT & SAVE TO THE LIBRARY                                         │
 │    Writes the gridless PNG, the gridded PNG, a thumbnail, and the       │
 │    metadata JSON into a folder for this map, and records it in the      │
 │    local library so it shows up under "Library". Print PDFs and Foundry │
 │    modules are produced on demand when you ask for them.                │
 └────────────────────────────────────────────────────────────────────────┘
                                    ▼
              Files land in your local Library — done.
```

Because steps 1–5 all run on your machine against art that already exists on disk, generating
a map is instant-feeling, endlessly repeatable, and costs nothing.

---

## 4. Using the app

The window has a **sidebar** on the left with two views — **Generate** and **Library** — plus
a **Colorblind mode** toggle and a live storage readout at the bottom.

### The Generate view

**1. Describe your map.** Type into the big **Prompt** box, e.g. *"a coastal smugglers' cove
with sea caves and a wrecked ship."* Not sure where to start? Click one of the **Try:** example
chips (Frozen dwarven crypt, Pirate cove ambush, Overgrown elven ruins, Collapsed mineshaft) to
drop a prompt in.

**2. (Optional) Adjust the Guided Controls** on the right. Every one of these is optional — a
bare prompt works fine — but they let you steer the result:

- **Map size** — a dropdown with presets **Small · 20×20**, **Standard · 30×20**,
  **Large · 60×60**, or **Custom…**. Choosing *Custom…* reveals **Columns × Rows** number
  fields so you can set any dimensions (5–200 each).
- **Feet / square** — the real-world scale of one grid square. Default **5** (the standard for
  most fantasy TTRPGs). This is metadata for your VTT; it doesn't change the picture.
- **Pixels / square** — how detailed/large the exported image is per square. Default **140**.
  Higher = crisper but bigger files; lower = smaller files. (See the size warnings below.)
- **Mood** — **Peaceful**, **Neutral**, **Tense**, **Combat**, or **Eerie**. Shifts the color
  grade and atmosphere (warm/bright vs. cool/desaturated vs. high-contrast, etc.).
- **Estimated output** — a live panel showing the resulting **Resolution**, **Megapixels**, and
  **Est. size** *before* you generate, updated as you change size/px. If the combination gets
  large, an inline warning appears here and above the Generate button so you can dial it back
  before waiting on a huge file.
- **Biomes** — an optional multi-select of terrain/environment tags. Leave it alone and the
  prompt drives it; or click tags to force specific biomes (great for hybrids, e.g. forest +
  ruins).
- **Encounter density** — a **Sparse ↔ Balanced ↔ Dense** slider controlling how many props,
  obstacles, and cover pieces get placed.
- **Seed** — the number that makes generation repeatable. Leave it blank to get a fresh random
  map each time, type a specific number to reproduce an exact map, or click the dice button to
  **randomize** it. *Same seed + same settings = the exact same map.*
- **Multi-level (linked floors)** — a toggle; when on, set **Floors** (2–6) to generate a stack
  of connected floors whose stairs line up between levels.

**3. Click Generate Map.** A short "Assembling terrain…" spinner runs, then your map appears.

### Reading and refining the output

- **Gridless / Gridded toggle** — switch the preview between the clean image (import this into
  VTTs) and the grid-baked version.
- **Reroll** — regenerate with the *same parameters but a new seed* — a quick way to try
  variations of the same idea.
- **Edit** — enter manual editing. Click a **square** to swap its terrain; click a **prop** to
  rotate, replace, or remove it. A **Revert** button appears to restore the last generated
  state if you don't like your edits.
- **Floors switcher** — for multi-level maps, tabs let you flip between floors.
- **Download panel** — one-click downloads of the **Gridless PNG**, **Gridded PNG**,
  **Metadata JSON**, and **Thumbnail**, plus **Print PDF · Letter / A4** and a **Foundry
  module** zip.
- **Ready-to-paste setup panel** — shows the exact **Foundry** and **Owlbear** values for this
  map (grid size, scene width/height, grid type, cell size in feet) so you can copy them
  straight into your VTT (see sections 5 and 6).
- **Details / Warnings** — the map's grid, scale, resolution, seed, mood, creation time, file
  size, and any size warnings.

Downloaded PNG filenames carry a `_<columns>x<rows>` token (e.g. `pirate-cove_30x20.png`).
**Don't rename that away** — Owlbear reads it to auto-detect the grid.

### The Library (your saved maps)

Every map you generate is saved locally and appears under **Library** in the sidebar. There you
can:

- **Browse thumbnails** of every saved map; click one to open its **detail view** with a big
  preview, the full download panel, ready-to-paste VTT values, and metadata.
- **Search** by title or prompt text, **filter** by biome, **sort** newest/oldest, and toggle a
  **Favorites**-only filter.
- **Rename** a map — click its title in the detail view and type a new name.
- **Tag** a map — add your own free-text tags (campaign name, session number, "boss fight",
  anything) in the tag editor; remove them anytime.
- **Favorite** a map — click the star (on the card or in detail) to mark it; use the Favorites
  filter to jump back to your best maps.
- **Download** any file type for a map, or a **Print PDF** / **Foundry module**, from the detail
  view's Download panel.
- **Select** multiple maps (the **Select** button in the toolbar) to bulk **Download ZIP** or
  build one combined **Foundry module** from several maps at once.
- **Backup / Import** the whole library — **Backup** downloads a single archive of every map
  plus its records; **Import** merges an archive back in (handy for moving to another machine or
  keeping a safe copy).
- **Permanently delete** a map — the red **Delete map** button asks you to confirm
  ("Permanently delete … and free its disk space? This cannot be undone."), then removes both
  its database record **and** its files, reclaiming the disk space.

### Checking your local disk usage

Two places show it, live:

- the **sidebar footer** shows a running "*N* maps · *size*" readout across the whole app;
- the **Library** view has a storage bar at the top: "**N** maps saved · **size** on disk," plus
  a per-biome breakdown.

Deleting maps updates both immediately.

---

## 5. Importing a map into Foundry VTT

Works with **Foundry VTT v12 and v13.** Use the map's own **gridless PNG** and the values from
its **metadata JSON** / the app's "Ready-to-paste setup" panel. (In the metadata file, the
relevant numbers live under the `foundry` key.)

Two ways in:

### A) The one-click Foundry module (easiest)

1. In the app, download the **Foundry module** zip (Download panel, or bulk-build one from
   several maps in the Library).
2. **Unzip** it and drop the resulting module folder into your Foundry data directory under
   `Data/modules/` (in Foundry: *Configuration → "User Data Path"* tells you where that is).
3. Restart Foundry (or return to Setup), enable the module for your world, and the map images
   are browsable via Foundry's file picker when you create a scene.

### B) Import the PNG by hand (full control)

1. Download the map's **Gridless PNG**.
2. In Foundry, open your world → **Scenes** → **Create Scene**.
3. On the **Basics** tab, set the scene name, then set **Background Image** to the gridless PNG
   (upload it or point the file picker at it).
4. Switch to the **Grid** tab. **Image Dimensions (pixels)** auto-fills from the image — it will
   equal the PNG's pixel width and height (the app's metadata reports these as
   `foundry.scene_width` / `foundry.scene_height`, e.g. `4200 × 2800` for a 30×20 map at 140
   px/square). Leave them as detected.
5. Set **Grid Size (pixels)** to the map's **pixels-per-square** value — the app shows this as
   *Grid Size* in the Ready-to-paste panel and as `foundry.grid_size` in the metadata (140 for
   the default; 70 for the bundled samples). Foundry's own default is 100, so you will usually be
   changing it.
6. Set **Grid Type = Square**.
7. **Set "Padding Percentage" to 0.** Foundry defaults this to 0.25, which adds empty margin and
   throws the grid out of flush alignment with the art — set it to **0** so squares line up
   perfectly.
8. *(Optional but recommended)* On the **Lighting** tab, enable **Global Illumination** for
   outdoor or well-lit maps. The metadata tells you which: `foundry.global_illumination` is
   `true` for outdoor/lit maps and `false` for dark interiors like the mine or temple.
9. Save. The map's grid now matches Foundry's grid exactly — one image square = one Foundry
   square = the feet-per-square you chose.

---

## 6. Importing a map into Owlbear Rodeo

Works with **Owlbear Rodeo 2.x.** Owlbear is even simpler because it can read the grid straight
from the filename.

1. Download the map's **Gridless PNG**. Keep its filename — it ends in a `_<columns>x<rows>`
   token, e.g. `pirate-cove_30x20.png`.
2. Open (or create) a scene in Owlbear Rodeo and **drag the PNG into it** (or use the map/asset
   upload).
3. Owlbear **auto-detects the grid** from that filename token: `..._30x20.png` means **30
   columns × 20 rows of squares** (that's grid *squares*, not pixels). A `###DPI`-style token in
   the name also works if present.
4. If you ever need to adjust it, Owlbear's manual grid controls and alignment ruler let you set
   the number of columns/rows or nudge the grid by hand — but with the filename intact you
   usually won't have to. For reference, the app's Ready-to-paste panel lists **Grid Size**,
   **Grid Type = Square**, and **Cell Size** in feet.

> **File-size note for Owlbear:** the free tier caps images at **25 MB / 67 megapixels**. At the
> default 140 px/square that's roughly a 58×58-square map. Larger maps: drop **Pixels / square**
> to **100** or **70** (the app warns you before generating when you're near the limit). The
> bundled sample maps were exported at 70 px/square specifically to stay comfortably small.

---

## 7. Grid & sizing conventions

**Defaults:** **5 feet per square** and **140 pixels per square**. Both are set in the Generate
view's Guided Controls and can be changed per map.

- **Feet / square (default 5).** The tabletop scale — how much real space one square represents.
  Five feet is the near-universal standard for fantasy TTRPGs. This value is written into the
  metadata for your VTT; it does not change the image itself.
- **Pixels / square (default 140).** The rendering resolution — how many pixels make up one
  square in the exported image. 140 is a crisp, widely-compatible size (Foundry's docs list it
  as a common value; Foundry's out-of-the-box default is 100). Raising it sharpens the art but
  multiplies the file size; lowering it keeps files small for very large maps or for Owlbear's
  free tier.

**Why whole-number grid dimensions matter.** A battle map is a grid of *discrete* squares —
tokens sit *in* squares, not between them. The app always renders whole squares
(image width = columns × px-per-square, exactly), so the picture's edges land on square
boundaries with no half-square slivers. That's what lets a VTT drop a perfectly aligned grid on
top with zero fiddling.

**Why there's both a gridless and a gridded export (the "double-grid" problem).** VTTs draw
their *own* grid over your map. If the map image *also* had a grid baked in and the two didn't
line up exactly, you'd see an ugly doubled or offset grid. To avoid that:

- **Import the *gridless* PNG into a VTT** and let the VTT draw the only grid. Since the app
  reports the exact grid size, they line up perfectly and there's just one clean grid.
- **Use the *gridded* PNG for printing, quick previews, or gridless-only tools** — anywhere you
  want the squares visible in the image itself and *aren't* layering another grid on top.

**Printing.** The on-demand **Print PDF** is already tiled at **exactly one inch per square**
across letter or A4 pages, with overlap margins and corner alignment marks so you can trim and
tape sheets into a seamless mat. **Always print at 100% / "Actual size" — never "Fit to page,"**
which would rescale the squares away from one inch.

---

## 8. Cost & offline behavior

**After setup, this app requires no API key, no account, no internet connection, and no
per-generation cost.** Generate as many maps as you like, forever, for free.

This isn't a marketing line — it's structural. The tile and prop artwork was created once and
lives in `assets/` as plain image files (Phase A). Generating a map only *arranges* that
existing art (Phase B). The local web server binds to **127.0.0.1** (loopback only, i.e. your
own machine), and the code makes **zero network requests** at runtime — no fonts, no CDNs, no
telemetry, no cloud image service.

**Verify it yourself:**

1. Start the app (`./start.sh` or `start.bat`) and confirm a map generates.
2. **Turn off your Wi-Fi / unplug your network cable.**
3. Generate several more maps, open the Library, download PNGs, export a Print PDF.

Everything keeps working with the network off, because nothing about generating maps ever needed
it. (The only time you touch the internet is the very first setup, to download the Python
packages Flask/Pillow/NumPy once. After that, you can stay offline permanently.)

---

## 9. Supported biomes & environments

This version ships the **full** planned library — nothing was cut. Prompts can freely combine
these (e.g. *"snowy elven ruins by a frozen river"*); the parser and biome picker mix terrains
and structures to match.

**Terrain types (29):**

`grass`, `tall_grass`, `forest_floor`, `jungle_floor`, `dirt`, `road`, `cobblestone`, `sand`,
`snow`, `ice`, `swamp`, `mud`, `water_shallow`, `water_deep`, `river`, `lava`, `volcanic_rock`,
`stone_floor`, `wood_floor`, `cave_floor`, `mountain_rock`, `stone_wall`, `brick_wall`,
`wood_wall`, `cave_wall`, `rubble`, `ship_deck`, `marble_floor`, `pit`.

Each terrain ships in 3–6 hand-varied versions so repeated squares don't look tiled, and the
engine blends the seams between different terrains at runtime.

**Structure & environment types:**

- **Dungeons** — rooms and corridors (crypts, ruins).
- **Building interiors** — taverns, temples, towers, and other rooms with walls, doors, and
  furniture.
- **Caves & mines** — organic caverns and dug-out mine works.
- **Villages, markets & camps** — plotted buildings, stalls, tents, and open commons.
- **Ships** — decked vessels and coves.
- **Natural biomes** — forests, grasslands, jungles, deserts, snowfields, swamps, coasts/oceans,
  rivers, and volcanic terrain, driven by noise fields for organic shapes.

**Props (43 types)** span cover (rocks, crates, barrels, logs), furniture (tables, chairs, beds,
bookshelves, market stalls, anvils, bar counters), nature (trees, bushes, stumps, stalagmites,
lily pads, coral), hazards (pits, traps, bones), focal features (altars, wells, statues,
campfires, fountains, braziers, chests, thrones, standing stones), structures (doors, tents,
carts, boats), and connections (stairs, ladders, trapdoors) — placed according to biome and the
encounter-density slider.

Four **sample maps** ship pre-generated in the Library as proof the pipeline works end to end:
**Collapsed Dwarven Mine**, **Forest Road Ambush**, **Pirate Cove**, and **Ruined Temple** —
each with both PNGs plus metadata.

---

## 10. Known limitations & fallbacks used

Everything in the specification shipped and works. A few honest caveats:

- **Tile art is procedurally rendered, not hand-painted.** The Phase A tile and prop artwork is
  produced by a deterministic image-generation script (`scripts/generate_assets.py`, using
  Pillow + NumPy: multi-octave wraparound noise, brush-blob stamping, and hand-jittered
  outlines) rather than commissioned, hand-painted illustration. The result is a cohesive,
  readable, grid-perfect painterly *raster* style (29 terrains × 3–6 variants each, plus 43 prop
  types), but it is simpler than bespoke artwork a professional illustrator would produce. This
  was a deliberate choice to keep the entire pipeline **code-only, dependency-free, and fully
  offline** — no external art service and no per-image cost. The art is committed in `assets/`;
  it is *not* regenerated at run time.
- **Wall-vector export is partial.** The engine traces and stores wall polylines inside each
  map's `layout.json`, but a dedicated **Universal VTT (`.dd2vtt`) export** — which some tools
  use to auto-import walls and lighting — was **not** built. Walls are fully rendered into the
  image; you just re-draw them in your VTT's wall tool if you want dynamic lighting. A future
  pass could emit `.dd2vtt` from the polylines that are already captured.
- **The bundled sample maps are exported at 70 px/square, not the 140 default.** This keeps the
  repository download light (a 30×20 map at 140 px/square is roughly 17 MB *per PNG*). Maps you
  generate yourself use the 140 default unless you change it.
- **Large maps make large files.** At the 140 px/square default, a 30×20 map is about 11.8
  megapixels (~17 MB per PNG). Owlbear's free tier caps at 25 MB / 67 MP (≈58×58 squares at
  140 px). The app **warns you before generating** when you approach these limits; for very large
  maps, drop **Pixels / square** to 100 or 70.
- **Manual editing has single-step Revert, not full multi-step undo.** In Edit mode you can
  freely swap terrain and modify props, and **Revert** restores the last *generated* state — but
  there is no incremental per-action undo/redo stack. Revert is all-or-nothing back to the
  generated map.

No other fallbacks were needed: the full 29-terrain biome library shipped, and the full Library
(search, filter, sort, tags, favorites, rename, permanent delete, bulk export, backup/import,
storage stats) shipped.

---

## 11. Project structure

```
TTRPG-grid/
├── start.sh                  One-command bootstrap + launcher (macOS/Linux)
├── start.bat                 Same, for Windows
├── run.py                    App entry point (launches the local Flask server)
├── requirements.txt          Python dependencies: flask, pillow, numpy, pytest
├── README.md                 This file
│
├── assets/                   PHASE A: the pre-made tile & prop art library (committed)
│   ├── manifest.json           Catalog of every terrain & prop + their properties
│   ├── tiles/<terrain>/…       Terrain tiles (3–6 variants each, native 140px)
│   └── props/<prop>/…          Prop sprites (crates, trees, altars, stairs, …)
│
├── scripts/
│   └── generate_assets.py    The deterministic Phase A art generator (run once, not at runtime)
│
├── server/                   PHASE B: the generation engine + local web server
│   ├── app.py                  Flask app — all the HTTP routes the UI calls
│   ├── library.py              Local library storage (SQLite + on-disk map folders)
│   ├── exports.py              Bulk ZIP, Foundry module, print PDF, library backup/import
│   └── engine/                 The map-generation pipeline
│       ├── parser.py             Prompt text → a generation plan
│       ├── noise.py              Seeded noise (organic shapes, terrain blending)
│       ├── layout.py             Plan → the grid of terrain + prop placements
│       ├── validate.py           5×5-open-space, reachability, whole-square checks
│       ├── assemble.py           Grid + tiles → the finished PNG images
│       └── export.py             Gridded/gridless PNGs + metadata
│
├── web/                      The browser interface (no build step)
│   ├── index.html              Page layout & controls
│   ├── app.js                  All UI behavior
│   └── style.css               Styling
│
├── library-data/             YOUR saved maps live here (created/grown as you generate)
│   ├── library.db              Index of saved maps
│   └── maps/<id>/…             Per-map folder: PNGs, thumbnail, metadata, layout
│
└── docs/
    └── ARCHITECTURE.md       Deeper technical reference for developers (the interface contracts)
```

Developers: `docs/ARCHITECTURE.md` documents the internal contracts (asset manifest, layout
format, engine API, HTTP API, library storage, export naming). The automated test suite lives in
`tests/` (run it with `.venv/bin/pytest` after setup) and covers the grid math, the 5×5-open
guarantee across biomes/densities/seeds, metadata math, determinism, and density behavior.

---

## 12. Usage rights of generated maps

**The maps you generate are yours to use freely** — in home games, on stream or in recorded
sessions, in published or commercial products, however you like.

Every map is assembled **entirely on your own machine** from this project's original,
procedurally-generated tile and prop library. **No third-party or externally-licensed art is
used anywhere in the pipeline** — all of the tile and prop art is original work created by the
generator script in this repository (`scripts/generate_assets.py`) and committed in `assets/`.
The app never fetches, embeds, or depends on any outside asset, font, or service at generation
time.

Because of that, there are **no attribution requirements, no per-use royalties, and no licensing
strings attached to the map images you produce.** They are your own creations. (Naturally, any
*names, characters, lore, or trademarks you type into a prompt or add yourself* remain governed
by whatever rights apply to those — the app makes no claim over your own creative input.)
