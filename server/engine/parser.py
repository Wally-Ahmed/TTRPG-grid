"""Fully-local free-text prompt parser (no LLM, no network).

Turns a free-text prompt into a :class:`GenerationSpec` using keyword / synonym
matching over a generous hand-built vocabulary. It extracts:

* **biomes** -- natural + built environment tags,
* **structure** -- dungeon | building | cave | village | ship | tower | none,
* **mood** -- peaceful | neutral | tense | combat | eerie,
* **size hints** -- named presets ("small skirmish", "large") and explicit
  ``NNxMM`` dimensions,
* **features** -- river, road, bridge, well, ... (things to carve/place),
* **scenario words** -- ambush, collapsed, ruined ... which influence *layout*
  (recorded as features so layout can react), not merely palette.

The UI ``overrides`` dict always wins over anything parsed.

This module imports the dataclass from :mod:`server.engine` (the public API) via
a late import inside the function to avoid a circular import at module load.
"""

from __future__ import annotations

import re

__all__ = [
    "parse_prompt",
    "BIOME_SYNONYMS",
    "STRUCTURE_SYNONYMS",
    "MOOD_SYNONYMS",
    "FEATURE_SYNONYMS",
    "SCENARIO_SYNONYMS",
]


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #
# Each mapping: canonical tag -> list of trigger words / phrases (already
# lowercased, matched as whole words unless they contain a space, in which case
# they are matched as a substring phrase). Order does not matter; all matches
# accumulate. Keep phrases specific enough to avoid absurd false positives.

BIOME_SYNONYMS: dict[str, list[str]] = {
    # --- natural biomes ---
    "forest": [
        "forest", "woodland", "woods", "wood", "grove", "thicket", "timberland",
        "wooded", "copse", "pinewood", "boreal",
    ],
    "jungle": [
        "jungle", "rainforest", "rain forest", "tropical", "overgrown",
        "vines", "canopy", "junglelike",
    ],
    "grassland": [
        "grassland", "plains", "plain", "meadow", "prairie", "steppe",
        "savanna", "savannah", "field", "fields", "heath", "moor",
    ],
    "desert": [
        "desert", "dune", "dunes", "sandy", "arid", "wasteland", "badlands",
        "oasis", "sun-scorched", "scorched sand",
    ],
    "swamp": [
        "swamp", "marsh", "marshland", "bog", "fen", "mire", "wetland",
        "quagmire", "bayou", "mangrove", "moor",
    ],
    "snow": [
        "snow", "snowy", "frozen", "arctic", "tundra", "glacier", "glacial",
        "ice", "icy", "frost", "frostbitten", "wintry", "winter", "blizzard",
        "permafrost", "polar",
    ],
    "mountain": [
        "mountain", "mountainous", "cliff", "cliffside", "crag", "crags",
        "ridge", "highland", "alpine", "rocky pass", "mountain pass", "summit",
        "peak", "escarpment",
    ],
    "cave": [
        "cave", "cavern", "caverns", "grotto", "underground", "subterranean",
        "tunnel", "tunnels", "burrow", "cave-in", "cave in", "spelunk",
    ],
    "coast": [
        "coast", "coastal", "coastline", "beach", "shore", "shoreline", "cove",
        "bay", "seaside", "tidal", "lagoon", "sandbar",
    ],
    "ocean": [
        "ocean", "sea", "open water", "islands", "island", "archipelago",
        "reef", "atoll", "seas", "high seas", "watery expanse",
    ],
    "river": [
        "riverbank", "riverside", "river delta", "estuary", "fen",
    ],
    "volcanic": [
        "volcanic", "volcano", "lava", "magma", "molten", "obsidian",
        "ashland", "cinder", "brimstone", "fire terrain", "lava flow",
    ],
    # --- built environments treated as biome-ish tags ---
    "dungeon": [
        "dungeon", "catacomb", "catacombs", "labyrinth", "oubliette",
        "prison", "gaol", "cell block", "vault",
    ],
    "crypt": [
        "crypt", "tomb", "tombs", "graveyard", "cemetery", "sepulcher",
        "sepulchre", "mausoleum", "necropolis", "ossuary", "burial",
        "boneyard", "sarcophagus",
    ],
    "temple": [
        "temple", "shrine", "sanctum", "sanctuary", "chapel", "cathedral",
        "monastery", "altar room", "holy site", "reliquary",
    ],
    "sewer": [
        "sewer", "sewers", "drainage", "aqueduct", "culvert", "cistern",
        "undercity",
    ],
    "mine": [
        "mine", "mines", "quarry", "shaft", "mineshaft", "excavation",
        "dig site", "pit mine", "ore", "dwarven mine",
    ],
    "market": [
        "market", "bazaar", "marketplace", "stalls", "trade square", "souk",
        "fairground", "fair",
    ],
    "ship": [
        "ship", "boat", "galleon", "vessel", "deck", "pirate", "frigate",
        "schooner", "caravel", "sloop", "shipwreck", "man-o-war", "warship",
    ],
    "camp": [
        "camp", "encampment", "campsite", "bivouac", "warcamp", "war camp",
        "bandit camp", "goblin camp", "tents",
    ],
    "fortress": [
        "fortress", "keep", "castle", "citadel", "stronghold", "bastion",
        "battlement", "rampart", "fort", "garrison", "watchtower keep",
    ],
    "tavern": [
        "tavern", "inn", "bar", "alehouse", "pub", "taproom", "brewery",
        "guesthouse", "public house",
    ],
    "village": [
        "village", "town", "hamlet", "settlement", "township", "square",
        "town square", "village square",
    ],
    "fey": [
        "fey", "feywild", "fairy", "faerie", "enchanted", "otherworldly",
        "elven glade", "glade", "elemental plane", "planar", "astral",
        "magical", "arcane", "eldritch grove",
    ],
    "building": [
        "building", "interior", "house", "manor", "mansion", "hall",
        "great hall", "throne room", "library interior", "study", "chamber",
        "room", "estate",
    ],
    "tower": [
        "tower", "spire", "belfry", "turret", "wizard tower", "mage tower",
        "lighthouse",
    ],
}

# Which structure a biome tag implies, when the prompt gives no explicit
# structure word. First matching biome in scan order wins for structure.
BIOME_IMPLIED_STRUCTURE: dict[str, str] = {
    "dungeon": "dungeon",
    "crypt": "dungeon",
    "sewer": "dungeon",
    "temple": "building",
    "tavern": "building",
    "building": "building",
    "fortress": "building",
    "market": "village",
    "village": "village",
    "camp": "village",
    "cave": "cave",
    "mine": "cave",
    "ship": "ship",
    "tower": "tower",
}

STRUCTURE_SYNONYMS: dict[str, list[str]] = {
    "dungeon": [
        "dungeon", "catacomb", "catacombs", "crypt", "tomb", "labyrinth",
        "sewer", "sewers", "vault", "prison", "oubliette", "temple interior",
        "shrine interior",
    ],
    "building": [
        "building", "interior", "tavern", "inn", "house", "manor", "mansion",
        "hall", "throne room", "keep interior", "castle interior", "temple",
        "shrine", "chapel", "library", "fortress", "keep", "castle",
        "watchtower interior",
    ],
    "cave": [
        "cave", "cavern", "caverns", "grotto", "mine", "mineshaft", "quarry",
        "tunnel", "tunnels", "underground grotto", "cave system",
    ],
    "village": [
        "village", "town", "hamlet", "market", "bazaar", "square",
        "settlement", "encampment", "camp", "marketplace",
    ],
    "ship": [
        "ship", "boat", "galleon", "deck", "vessel", "ship deck",
        "pirate ship", "shipwreck",
    ],
    "tower": [
        "tower", "spire", "turret", "wizard tower", "mage tower", "lighthouse",
        "belfry",
    ],
}

MOOD_SYNONYMS: dict[str, list[str]] = {
    "peaceful": [
        "peaceful", "calm", "serene", "tranquil", "quiet", "idyllic",
        "restful", "safe", "pastoral", "gentle", "sunny", "cozy", "sleepy",
    ],
    "tense": [
        "tense", "ominous", "foreboding", "uneasy", "suspenseful", "dread",
        "tension", "wary", "on edge", "brooding", "looming", "menacing",
    ],
    "combat": [
        "combat", "battle", "battlefield", "war", "warzone", "skirmish",
        "fight", "ambush", "assault", "siege", "raid", "clash", "melee",
        "combat-ready", "encounter", "showdown",
    ],
    "eerie": [
        "eerie", "haunted", "spooky", "creepy", "ghostly", "cursed",
        "sinister", "macabre", "grim", "unsettling", "spectral", "undead",
        "necrotic", "forsaken", "abandoned", "desolate", "nightmarish",
    ],
}

# Features are things layout should carve or place. Scenario words are folded in
# here too (see SCENARIO_SYNONYMS) because the spec requires scenario to shape
# layout, and features is the layout-visible channel on GenerationSpec.
FEATURE_SYNONYMS: dict[str, list[str]] = {
    "river": [
        "river", "stream", "creek", "brook", "waterway", "running water",
        "river running", "flowing water", "rivulet", "tributary",
    ],
    "road": [
        "road", "path", "pathway", "trail", "track", "highway", "cobbled road",
        "forest road", "dirt road", "kings road", "king's road", "route",
    ],
    "bridge": [
        "bridge", "footbridge", "crossing", "span", "rope bridge", "drawbridge",
    ],
    "well": [
        "well", "wellspring", "water well", "fountain",
    ],
    "lake": [
        "lake", "pond", "pool", "tarn", "reservoir", "watering hole",
    ],
    "chasm": [
        "chasm", "ravine", "gorge", "gulch", "crevasse", "fissure", "abyss",
        "canyon",
    ],
    "waterfall": [
        "waterfall", "cascade", "falls",
    ],
    "campfire": [
        "campfire", "bonfire", "cooking fire", "fire pit", "firepit", "hearth",
    ],
    "altar": [
        "altar", "shrine", "sacrificial", "ritual circle", "offering",
    ],
    "statue": [
        "statue", "monument", "effigy", "idol", "obelisk", "standing stone",
        "standing stones", "menhir", "megalith",
    ],
    "gate": [
        "gate", "gateway", "portcullis", "entrance arch", "archway",
    ],
    "wall": [
        "wall", "walls", "barricade", "palisade", "stockade", "rampart",
        "fortification",
    ],
    "pillars": [
        "pillar", "pillars", "columns", "colonnade",
    ],
    "trees": [
        "trees", "tree", "grove of trees",
    ],
    "treasure": [
        "treasure", "hoard", "loot", "chest", "chests", "gold pile",
    ],
    "lava_flow": [
        "lava flow", "lava river", "magma flow", "molten river",
    ],
}

# Scenario words: they map to a scenario tag stored in features (prefixed with
# "scenario:") AND, where obvious, they may imply mood. Layout reads these tags.
SCENARIO_SYNONYMS: dict[str, list[str]] = {
    "ambush": ["ambush", "ambushed", "waylay", "surprise attack", "trap set"],
    "collapsed": [
        "collapsed", "collapse", "cave-in", "cave in", "caved in", "crumbling",
        "unstable", "partial cave-in",
    ],
    "ruined": [
        "ruined", "ruin", "ruins", "derelict", "decayed", "dilapidated",
        "crumbled", "broken down", "overrun", "wrecked", "shattered",
    ],
    "flooded": ["flooded", "flooding", "waterlogged", "submerged", "inundated"],
    "burning": ["burning", "aflame", "ablaze", "smoldering", "on fire", "scorched"],
    "siege": ["siege", "besieged", "under attack", "assault", "storming"],
    "defensive": [
        "defensive", "last stand", "hold the line", "chokepoint", "choke point",
        "defend", "fortified position",
    ],
}

# Named size presets -> (cols, rows). Explicit NNxMM in the prompt overrides.
SIZE_PRESETS: dict[str, tuple[int, int]] = {
    "small": (20, 20),
    "skirmish": (20, 20),
    "small skirmish": (20, 20),
    "tiny": (16, 16),
    "standard": (30, 20),
    "medium": (30, 20),
    "encounter": (30, 20),
    "normal": (30, 20),
    "large": (50, 40),
    "big": (50, 40),
    "huge": (60, 60),
    "massive": (60, 60),
    "sprawling": (60, 50),
    "vast": (60, 60),
    "regional": (60, 60),
}

# Words that hint multi-level generation is desired.
MULTILEVEL_HINTS: list[str] = [
    "multi-level", "multi level", "multilevel", "multiple floors",
    "multiple levels", "two floors", "three floors", "basement",
    "upper floor", "lower level", "several floors", "floors", "stories",
    "storied", "cross-section", "levels connected",
]

_DIM_RE = re.compile(r"\b(\d{1,3})\s*[x×by]\s*(\d{1,3})\b")
_WORD_RE = re.compile(r"[a-z0-9']+")


def _phrase_present(text: str, phrase: str) -> bool:
    """True if ``phrase`` appears in ``text``.

    Multi-word phrases match as substrings (bounded by word breaks); single
    words match on whole-word boundaries so "sea" does not fire on "season".
    """

    if " " in phrase or "-" in phrase:
        return phrase in text
    return re.search(r"\b" + re.escape(phrase) + r"\b", text) is not None


def _collect(text: str, table: dict[str, list[str]]) -> list[str]:
    """Return canonical keys whose any synonym is present, in table order."""

    found: list[str] = []
    for canonical, words in table.items():
        for w in words:
            if _phrase_present(text, w):
                found.append(canonical)
                break
    return found


def parse_prompt(text: str, overrides: dict | None = None):
    """Parse ``text`` into a :class:`GenerationSpec`. ``overrides`` win.

    Local only: pure string processing, no I/O, no randomness.
    """

    # Late import avoids circular import (server.engine imports this module).
    from . import GenerationSpec

    overrides = dict(overrides or {})
    raw = text or ""
    lowered = raw.lower()

    # --- biomes ---
    biomes = _collect(lowered, BIOME_SYNONYMS)

    # --- structure: explicit words first, else implied by a biome ---
    structure: str | None = None
    struct_hits = _collect(lowered, STRUCTURE_SYNONYMS)
    if struct_hits:
        structure = struct_hits[0]
    else:
        for b in biomes:
            if b in BIOME_IMPLIED_STRUCTURE:
                structure = BIOME_IMPLIED_STRUCTURE[b]
                break

    # --- mood: strongest scenario/combat cue wins; default neutral ---
    mood_hits = _collect(lowered, MOOD_SYNONYMS)
    if mood_hits:
        # Priority: combat > eerie > tense > peaceful when several present, so
        # that e.g. "a peaceful village ambush" reads as combat.
        for pref in ("combat", "eerie", "tense", "peaceful"):
            if pref in mood_hits:
                mood = pref
                break
        else:  # pragma: no cover - defensive
            mood = mood_hits[0]
    else:
        mood = "neutral"

    # --- features + scenario tags (both live in features) ---
    features = _collect(lowered, FEATURE_SYNONYMS)
    scenario_tags: list[str] = []
    for scen, words in SCENARIO_SYNONYMS.items():
        for w in words:
            if _phrase_present(lowered, w):
                scenario_tags.append(scen)
                break
    for scen in scenario_tags:
        tag = "scenario:" + scen
        if tag not in features:
            features.append(tag)

    # Scenario words nudge mood if the prompt gave no explicit mood word.
    if not mood_hits:
        if any(s in scenario_tags for s in ("ambush", "siege", "burning")):
            mood = "combat"
        elif any(s in scenario_tags for s in ("ruined", "collapsed", "flooded")):
            mood = "eerie"
        elif "defensive" in scenario_tags:
            mood = "tense"

    # --- size: explicit NNxMM beats named preset beats default ---
    cols, rows = 30, 20
    size_source = "default"
    m = _DIM_RE.search(lowered)
    if m:
        cols = max(4, min(200, int(m.group(1))))
        rows = max(4, min(200, int(m.group(2))))
        size_source = "explicit"
    else:
        # Longest matching preset phrase wins (so "small skirmish" beats "small").
        best = None
        best_len = -1
        for phrase, dims in SIZE_PRESETS.items():
            if _phrase_present(lowered, phrase) and len(phrase) > best_len:
                best = dims
                best_len = len(phrase)
        if best is not None:
            cols, rows = best
            size_source = "preset"

    # --- multi-level ---
    multi_level = any(_phrase_present(lowered, h) for h in MULTILEVEL_HINTS)
    levels = 2 if multi_level else 1
    m3 = re.search(r"\b(\d)\s+(?:floors|levels|stories)\b", lowered)
    if m3:
        multi_level = True
        levels = max(2, min(6, int(m3.group(1))))

    spec = GenerationSpec(
        prompt=raw,
        cols=cols,
        rows=rows,
        seed=0,  # caller randomizes before generate when 0
        density=0.5,
        mood=mood,
        biomes=biomes,
        structure=structure,
        features=features,
        multi_level=multi_level,
        levels=levels,
        palette_mode="standard",
        title=_derive_title(raw),
    )

    # --- overrides always win ---
    _apply_overrides(spec, overrides, size_source)
    return spec


def _derive_title(prompt: str) -> str:
    """A short human title from the prompt (first clause, ~6 words)."""

    if not prompt.strip():
        return "Untitled Map"
    clause = re.split(r"[.;,\n]", prompt.strip())[0]
    words = clause.split()
    title = " ".join(words[:6]).strip()
    return title[:60] if title else "Untitled Map"


def _apply_overrides(spec, overrides: dict, size_source: str) -> None:
    """Apply UI overrides onto a spec in place. Overrides always win."""

    # Simple scalar / string passthroughs when present and non-None.
    for field_name in (
        "prompt", "cols", "rows", "px_per_square", "feet_per_square", "seed",
        "density", "mood", "structure", "multi_level", "levels",
        "palette_mode", "title",
    ):
        if field_name in overrides and overrides[field_name] is not None:
            setattr(spec, field_name, overrides[field_name])

    # List fields: replace when provided (UI multi-select supersedes parse).
    if overrides.get("biomes"):
        spec.biomes = list(overrides["biomes"])
    if overrides.get("features"):
        spec.features = list(overrides["features"])

    # Clamp / sanitise after overrides.
    spec.cols = int(max(4, min(400, spec.cols)))
    spec.rows = int(max(4, min(400, spec.rows)))
    spec.px_per_square = int(max(20, min(400, spec.px_per_square)))
    spec.feet_per_square = int(max(1, spec.feet_per_square))
    spec.density = float(min(1.0, max(0.0, spec.density)))
    spec.levels = int(max(1, min(12, spec.levels)))
    if spec.multi_level and spec.levels < 2:
        spec.levels = 2
    if spec.mood not in ("peaceful", "neutral", "tense", "combat", "eerie"):
        spec.mood = "neutral"
    if spec.palette_mode not in ("standard", "colorblind"):
        spec.palette_mode = "standard"
