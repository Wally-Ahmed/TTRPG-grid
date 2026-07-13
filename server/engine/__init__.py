"""TTRPG Grid Map Generator -- generation engine (Contract 3 public API).

This package implements the fully-local, deterministic Phase-B pipeline:

    parse_prompt(text) -> GenerationSpec
    generate_map(spec) -> MapResult          (layout -> validate -> assemble -> metadata)
    generate_level_set(spec) -> [MapResult]  (floor i uses seed + i)
    assemble_from_layout(layout) -> MapResult (re-composite an edited layout)
    estimate_size(cols, rows, px) -> dict

Determinism is absolute: every random choice flows from ``spec.seed`` through
explicitly-constructed RNGs. No module-level RNG, no time entropy, no network.
Validation failures retry with ``seed + retry_count`` (max 8) then raise
:class:`GenerationError`.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

# The 29 canonical terrain ids (Contract 1). Engine references ONLY these.
TERRAINS: tuple[str, ...] = (
    "grass", "tall_grass", "forest_floor", "jungle_floor", "dirt", "road",
    "cobblestone", "sand", "snow", "ice", "swamp", "mud", "water_shallow",
    "water_deep", "river", "lava", "volcanic_rock", "stone_floor", "wood_floor",
    "cave_floor", "mountain_rock", "stone_wall", "brick_wall", "wood_wall",
    "cave_wall", "rubble", "ship_deck", "marble_floor", "pit",
)
TERRAIN_SET = frozenset(TERRAINS)

MOODS = ("peaceful", "neutral", "tense", "combat", "eerie")
STRUCTURES = ("dungeon", "building", "cave", "village", "ship", "tower", "none")

MAX_RETRIES = 8


class GenerationError(Exception):
    """Raised when a layout cannot be produced within the retry budget."""


@dataclass
class GenerationSpec:
    prompt: str = ""
    cols: int = 30
    rows: int = 20
    px_per_square: int = 140
    feet_per_square: int = 5
    seed: int = 0                       # 0 = caller must randomize before generate
    density: float = 0.5                # 0.0 sparse .. 1.0 dense
    mood: str = "neutral"               # peaceful | neutral | tense | combat | eerie
    biomes: list[str] = field(default_factory=list)
    structure: str | None = None        # dungeon | building | cave | village | ship | tower | none
    features: list[str] = field(default_factory=list)
    multi_level: bool = False
    levels: int = 1
    palette_mode: str = "standard"      # standard | colorblind
    title: str = ""

    def to_dict(self) -> dict:
        """Plain-dict form (used verbatim inside layout['spec'])."""

        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "GenerationSpec":
        allowed = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in allowed})


@dataclass
class MapResult:
    layout: dict                        # Contract 2 structure
    gridless: "object"                  # PIL.Image.Image
    gridded: "object"                   # PIL.Image.Image
    metadata: dict                      # Contract 6 structure
    warnings: list[str] = field(default_factory=list)


# Re-export the local prompt parser.
from .parser import parse_prompt  # noqa: E402


def estimate_size(cols: int, rows: int, px: int) -> dict:
    """Estimate output dimensions / weight and surface size guardrail warnings.

    Returns ``{width, height, megapixels, est_bytes, warnings}``. Warnings fire
    when width*height > 50 MP or the estimated PNG > 20 MB (comfortably under
    Owlbear's ~67 MP / 25 MB ceiling, per the Size-guardrails section).
    """

    cols = int(max(0, cols))
    rows = int(max(0, rows))
    px = int(max(0, px))

    width = cols * px
    height = rows * px
    pixels = width * height
    megapixels = pixels / 1_000_000.0

    # Empirical PNG heuristic for dense painterly RGB art: ~1.5 bytes/pixel.
    # Deliberately conservative (over-estimates) so the UI warns early.
    est_bytes = int(pixels * 1.5)

    warnings: list[str] = []
    if megapixels > 50.0:
        warnings.append(
            f"Image is {megapixels:.1f} MP (>50 MP). Approaching Owlbear's "
            f"~67 MP ceiling; consider fewer squares or lower resolution."
        )
    if est_bytes > 20 * 1024 * 1024:
        warnings.append(
            f"Estimated file ~{est_bytes / (1024 * 1024):.1f} MB (>20 MB). "
            f"Approaching Owlbear's ~25 MB ceiling; consider lower resolution."
        )

    return {
        "width": width,
        "height": height,
        "megapixels": round(megapixels, 3),
        "est_bytes": est_bytes,
        "warnings": warnings,
    }


def generate_map(spec: GenerationSpec) -> MapResult:
    """Full single-map pipeline: layout -> validate (retry) -> assemble -> metadata.

    ``multi_level=True`` returns the FIRST floor only; use
    :func:`generate_level_set` for all floors. Retries on validation failure
    with ``seed + retry_count`` up to :data:`MAX_RETRIES`, then raises
    :class:`GenerationError`.
    """

    from .layout import build_layout
    from .validate import validate_and_fix, ValidationError
    from .assemble import assemble_map
    from .export import build_metadata, apply_grid_overlay, load_manifest

    manifest = load_manifest(getattr(spec, "_assets_path", "assets"))

    warnings: list[str] = []
    last_err: Exception | None = None
    base_seed = int(spec.seed)

    for retry in range(MAX_RETRIES + 1):
        attempt_seed = base_seed + retry
        try:
            layout = build_layout(spec, attempt_seed, manifest)
            fix_warnings = validate_and_fix(layout)
            warnings.extend(fix_warnings)
            break
        except ValidationError as exc:
            last_err = exc
            continue
    else:
        raise GenerationError(
            f"Could not produce a valid layout after {MAX_RETRIES} retries: {last_err}"
        )

    if retry > 0:
        warnings.append(f"Layout succeeded on retry {retry} (seed {base_seed + retry}).")

    gridless = assemble_map(layout, manifest, spec.palette_mode, spec.px_per_square)
    gridded = apply_grid_overlay(gridless, spec.px_per_square, spec.palette_mode)
    metadata = build_metadata(spec, layout, gridless.size)

    return MapResult(
        layout=layout,
        gridless=gridless,
        gridded=gridded,
        metadata=metadata,
        warnings=warnings,
    )


def generate_level_set(spec: GenerationSpec) -> list[MapResult]:
    """Generate all floors of a multi-level location.

    Floor ``i`` uses ``spec.seed + i``; stair/ladder/trapdoor connection props
    are placed at coordinates that line up between adjacent floors. Each floor is
    a full :class:`MapResult`; layouts carry ``connections`` per Contract 2.
    """

    from .layout import build_level_set
    from .validate import validate_and_fix, ValidationError
    from .assemble import assemble_map
    from .export import build_metadata, apply_grid_overlay, load_manifest

    manifest = load_manifest(getattr(spec, "_assets_path", "assets"))
    n_levels = max(1, int(spec.levels) if spec.multi_level else 1)
    set_id = _derive_set_id(spec)

    layouts = build_level_set(spec, n_levels, manifest, validate_and_fix)

    results: list[MapResult] = []
    for idx, (layout, warns) in enumerate(layouts):
        gridless = assemble_map(layout, manifest, spec.palette_mode, spec.px_per_square)
        gridded = apply_grid_overlay(gridless, spec.px_per_square, spec.palette_mode)
        metadata = build_metadata(
            spec, layout, gridless.size, set_id=set_id, floor_index=idx
        )
        results.append(
            MapResult(
                layout=layout,
                gridless=gridless,
                gridded=gridded,
                metadata=metadata,
                warnings=list(warns),
            )
        )
    return results


def assemble_from_layout(layout: dict, palette_mode: str = "standard") -> MapResult:
    """Re-composite an (edited) layout without re-running procgen.

    Used by the manual-editing path: the caller mutated ``cells``/``props`` and
    wants fresh images + metadata. The layout's stored ``spec`` supplies the
    resolution/mood; ``palette_mode`` argument overrides for colorblind toggling.
    """

    from .assemble import assemble_map
    from .export import build_metadata, apply_grid_overlay, load_manifest

    spec_data = layout.get("spec", {}) or {}
    spec = GenerationSpec.from_dict(spec_data) if spec_data else GenerationSpec()
    spec.palette_mode = palette_mode

    manifest = load_manifest(spec_data.get("_assets_path", "assets"))
    px = int(spec.px_per_square)

    gridless = assemble_map(layout, manifest, palette_mode, px)
    gridded = apply_grid_overlay(gridless, px, palette_mode)
    metadata = build_metadata(
        spec, layout, gridless.size,
        set_id=layout.get("set_id"),
        floor_index=layout.get("floor_index", 0),
    )

    return MapResult(
        layout=layout,
        gridless=gridless,
        gridded=gridded,
        metadata=metadata,
        warnings=[],
    )


def _derive_set_id(spec: GenerationSpec) -> str | None:
    """Deterministic set id for a multi-level group (seed-derived hex)."""

    if not spec.multi_level or spec.levels < 2:
        return None
    import hashlib

    key = f"{spec.seed}:{spec.cols}x{spec.rows}:{spec.structure}:{spec.prompt}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


__all__ = [
    "GenerationSpec",
    "MapResult",
    "GenerationError",
    "TERRAINS",
    "TERRAIN_SET",
    "MOODS",
    "STRUCTURES",
    "MAX_RETRIES",
    "parse_prompt",
    "generate_map",
    "generate_level_set",
    "assemble_from_layout",
    "estimate_size",
]
