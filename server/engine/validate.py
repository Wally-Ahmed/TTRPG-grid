"""Layout validation + light repair pass (Phase B, step 3).

Checks, in order:

1. **Whole-number, positive grid dims** and a well-formed ``cells`` matrix.
2. **At least one contiguous 5x5 block of open walkable floor** ("breathing
   room" -- spec sec.2, the user's explicit requirement).
3. **Reachability**: every open (walkable) region is mutually reachable via
   4-connected flood fill. Disconnected pockets are *repaired* by carving the
   thinnest walkable corridor between the largest region and each stranded
   region. If a pocket cannot be connected, validation fails.
4. **Open-space-to-clutter ratio** sanity: enough of the map is open floor that
   it is not a solid wall / hazard field.

On an unrecoverable failure raises :class:`ValidationError` so the caller can
retry with ``seed + retry_count``. Successful repairs return human-readable
warning strings. ``validate_and_fix`` mutates the layout's ``cells`` in place
when it carves connections, keeping the layout dict authoritative.
"""

from __future__ import annotations

from collections import deque

__all__ = ["ValidationError", "validate_and_fix", "largest_open_region",
           "has_open_5x5", "open_ratio"]


# Terrain semantics duplicated minimally here to avoid importing layout (which
# imports noise/numpy) at validation time; kept in sync with layout.py.
WALL_TERRAINS = frozenset({"stone_wall", "brick_wall", "wood_wall", "cave_wall",
                           "mountain_rock", "volcanic_rock"})
HAZARD_TERRAINS = frozenset({"lava", "water_deep", "pit"})
BLOCKED = WALL_TERRAINS | HAZARD_TERRAINS

MIN_OPEN_RATIO = 0.20      # at least 20% of cells must be open walkable floor
OPEN_BLOCK = 5             # required contiguous open square block edge length


class ValidationError(Exception):
    """Raised when a layout fails validation unrecoverably."""


def _is_open(cells, x, y) -> bool:
    return cells[y][x] not in BLOCKED


def validate_and_fix(layout: dict) -> list[str]:
    """Validate ``layout`` and repair reachability in place. Returns warnings.

    Raises :class:`ValidationError` on any unrecoverable problem.
    """

    warnings: list[str] = []
    cols = layout.get("cols")
    rows = layout.get("rows")

    # 1. dims ----------------------------------------------------------------
    if not isinstance(cols, int) or not isinstance(rows, int):
        raise ValidationError("cols/rows must be integers")
    if cols <= 0 or rows <= 0:
        raise ValidationError(f"non-positive dims {cols}x{rows}")
    if cols != int(cols) or rows != int(rows):  # pragma: no cover - int already
        raise ValidationError("fractional grid dims")

    cells = layout.get("cells")
    if not isinstance(cells, list) or len(cells) != rows:
        raise ValidationError("cells row count mismatch")
    for row in cells:
        if not isinstance(row, list) or len(row) != cols:
            raise ValidationError("cells column count mismatch")

    # A map smaller than the open block requirement can never satisfy it.
    if cols < OPEN_BLOCK or rows < OPEN_BLOCK:
        raise ValidationError(f"map {cols}x{rows} smaller than {OPEN_BLOCK}x{OPEN_BLOCK}")

    # 3. reachability + repair (do before the 5x5 test so carving can help) ---
    regions = _open_regions(cells, cols, rows)
    if not regions:
        raise ValidationError("no open walkable cells at all")

    if len(regions) > 1:
        regions.sort(key=len, reverse=True)
        main = regions[0]
        carved = 0
        for pocket in regions[1:]:
            if _carve_connection(cells, main, pocket, cols, rows):
                carved += 1
                # Merge pocket into main so subsequent carves reference it.
                main = main | pocket
            else:
                raise ValidationError("unreachable region could not be connected")
        if carved:
            warnings.append(f"Carved {carved} corridor(s) to connect isolated regions.")

    # 4. open ratio ----------------------------------------------------------
    ratio = open_ratio(cells, cols, rows)
    if ratio < MIN_OPEN_RATIO:
        raise ValidationError(f"open ratio {ratio:.2f} below {MIN_OPEN_RATIO}")

    # 2. 5x5 open block ------------------------------------------------------
    if not has_open_5x5(cells, cols, rows):
        # Attempt a repair: clear a 5x5 block in the largest open region's
        # bounding neighbourhood before failing.
        if not _force_open_block(cells, cols, rows):
            raise ValidationError("no contiguous 5x5 open block and none could be cleared")
        warnings.append("Cleared a 5x5 open block to satisfy breathing-room requirement.")

    return warnings


# --------------------------------------------------------------------------- #
# Reachability
# --------------------------------------------------------------------------- #
def _open_regions(cells, cols, rows) -> list[set[tuple[int, int]]]:
    """4-connected connected components of open walkable cells."""

    seen = [[False] * cols for _ in range(rows)]
    regions: list[set[tuple[int, int]]] = []
    for sy in range(rows):
        for sx in range(cols):
            if seen[sy][sx] or not _is_open(cells, sx, sy):
                continue
            comp: set[tuple[int, int]] = set()
            q = deque([(sx, sy)])
            seen[sy][sx] = True
            while q:
                x, y = q.popleft()
                comp.add((x, y))
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if 0 <= nx < cols and 0 <= ny < rows and not seen[ny][nx] \
                            and _is_open(cells, nx, ny):
                        seen[ny][nx] = True
                        q.append((nx, ny))
            regions.append(comp)
    return regions


def largest_open_region(cells, cols, rows) -> set[tuple[int, int]]:
    regions = _open_regions(cells, cols, rows)
    return max(regions, key=len) if regions else set()


def _carve_connection(cells, region_a, region_b, cols, rows) -> bool:
    """Carve a straight-ish corridor from the closest pair of cells between two
    regions, turning intervening blocked cells into a neutral walkable floor.

    Uses the nearest cell pair (Manhattan). Returns True on success.
    """

    # Find nearest pair (bounded search: sample region_b against region_a).
    a_list = list(region_a)
    b_list = list(region_b)
    # To keep it fast on big maps, subsample if huge but deterministically
    # (sorted, strided) so the choice is reproducible.
    a_list.sort()
    b_list.sort()

    best = None
    best_d = None
    step_a = max(1, len(a_list) // 400)
    step_b = max(1, len(b_list) // 400)
    for ax, ay in a_list[::step_a]:
        for bx, by in b_list[::step_b]:
            d = abs(ax - bx) + abs(ay - by)
            if best_d is None or d < best_d:
                best_d = d
                best = ((ax, ay), (bx, by))
    if best is None:
        return False

    (ax, ay), (bx, by) = best
    floor = _neutral_floor(cells, cols, rows)
    # L-shaped carve: horizontal then vertical.
    for x in range(min(ax, bx), max(ax, bx) + 1):
        if cells[ay][x] in BLOCKED:
            cells[ay][x] = floor
    for y in range(min(ay, by), max(ay, by) + 1):
        if cells[y][bx] in BLOCKED:
            cells[y][bx] = floor
    return True


def _neutral_floor(cells, cols, rows) -> str:
    """Pick a walkable floor terrain already present to carve corridors with, so
    the carve blends visually. Falls back to a stone floor id."""

    counts: dict[str, int] = {}
    for row in cells:
        for t in row:
            if t not in BLOCKED:
                counts[t] = counts.get(t, 0) + 1
    if counts:
        # Most common open terrain (deterministic tie-break by name).
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    return "stone_floor"


# --------------------------------------------------------------------------- #
# Open block + ratio
# --------------------------------------------------------------------------- #
def has_open_5x5(cells, cols, rows) -> bool:
    """True if some contiguous OPEN_BLOCK x OPEN_BLOCK block is fully open.

    Uses a DP of "largest open square ending at each cell" (O(rows*cols))."""

    if cols < OPEN_BLOCK or rows < OPEN_BLOCK:
        return False
    dp = [[0] * cols for _ in range(rows)]
    for y in range(rows):
        for x in range(cols):
            if not _is_open(cells, x, y):
                dp[y][x] = 0
                continue
            if x == 0 or y == 0:
                dp[y][x] = 1
            else:
                dp[y][x] = 1 + min(dp[y - 1][x], dp[y][x - 1], dp[y - 1][x - 1])
            if dp[y][x] >= OPEN_BLOCK:
                return True
    return False


def open_ratio(cells, cols, rows) -> float:
    total = cols * rows
    if total == 0:
        return 0.0
    open_count = sum(1 for row in cells for t in row if t not in BLOCKED)
    return open_count / total


def _force_open_block(cells, cols, rows) -> bool:
    """Clear a 5x5 open block centred in the largest open region. Returns True on
    success (there is always room since we checked cols/rows >= OPEN_BLOCK)."""

    region = largest_open_region(cells, cols, rows)
    if not region:
        return False
    # Centre the block on the region's centroid, clamped to fit.
    cx = sum(p[0] for p in region) // len(region)
    cy = sum(p[1] for p in region) // len(region)
    x0 = min(max(0, cx - OPEN_BLOCK // 2), cols - OPEN_BLOCK)
    y0 = min(max(0, cy - OPEN_BLOCK // 2), rows - OPEN_BLOCK)
    floor = _neutral_floor(cells, cols, rows)
    for y in range(y0, y0 + OPEN_BLOCK):
        for x in range(x0, x0 + OPEN_BLOCK):
            if cells[y][x] in BLOCKED:
                cells[y][x] = floor
    return has_open_5x5(cells, cols, rows)
