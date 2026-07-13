"""Seeded value / fractal noise implemented with numpy.

No external noise dependency (per ARCHITECTURE Contract / stack rules). Every
function is pure and deterministic: identical (seed, shape, params) inputs
always produce byte-identical arrays. All randomness flows from an explicitly
passed ``numpy.random.default_rng`` instance or an integer seed that is used to
construct one locally -- there is no module-level RNG and no time-based entropy.

The core primitive is classic value noise on an integer lattice with a smooth
(quintic) fade and bilinear-style interpolation, then fractal Brownian motion
(fBm) octave stacking on top. Value noise is chosen over gradient/simplex noise
because it is trivial to make fully reproducible with numpy alone while still
producing smooth, organic fields suitable for biome blending, cave carving, and
terrain feathering.
"""

from __future__ import annotations

import numpy as np

__all__ = [
    "rng_from_seed",
    "value_noise_2d",
    "fractal_noise_2d",
    "normalized_fractal_field",
    "domain_warp_field",
]


def rng_from_seed(seed: int) -> np.random.Generator:
    """Construct a numpy Generator from an integer seed.

    Centralised so every module builds its RNG the same way. ``seed`` is masked
    to a non-negative 63-bit integer so that negative or very large seeds (e.g.
    ``seed + retry_count`` overflow paths) remain valid numpy seeds.
    """

    return np.random.default_rng(int(seed) & 0x7FFFFFFFFFFFFFFF)


def _fade(t: np.ndarray) -> np.ndarray:
    """Quintic smoothstep 6t^5 - 15t^4 + 10t^3 (Perlin's improved fade)."""

    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


def _lattice_values(rng: np.random.Generator, gw: int, gh: int) -> np.ndarray:
    """Random scalar value at each integer lattice node, in [0, 1)."""

    return rng.random((gh, gw), dtype=np.float64)


def value_noise_2d(
    width: int,
    height: int,
    scale: float,
    seed: int,
    *,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """Single-frequency value noise, shape ``(height, width)`` in ``[0, 1]``.

    ``scale`` is the lattice cell size in pixels/cells: larger scale => smoother,
    lower-frequency noise. A ``scale`` >= 1 is enforced. When ``rng`` is provided
    the lattice is drawn from it (advancing its state); otherwise a fresh RNG is
    built from ``seed``.
    """

    if width <= 0 or height <= 0:
        return np.zeros((max(height, 0), max(width, 0)), dtype=np.float64)

    scale = max(float(scale), 1.0)
    if rng is None:
        rng = rng_from_seed(seed)

    # Number of lattice cells spanning the field, plus one node past the edge.
    gw = int(np.ceil(width / scale)) + 2
    gh = int(np.ceil(height / scale)) + 2
    lattice = _lattice_values(rng, gw, gh)

    # Fractional coordinate of every output pixel within the lattice grid.
    xs = np.arange(width, dtype=np.float64) / scale
    ys = np.arange(height, dtype=np.float64) / scale

    x0 = np.floor(xs).astype(np.intp)
    y0 = np.floor(ys).astype(np.intp)
    tx = _fade(xs - x0)
    ty = _fade(ys - y0)

    x1 = x0 + 1
    y1 = y0 + 1

    # Gather the four corner values for every pixel (broadcast rows x cols).
    v00 = lattice[np.ix_(y0, x0)]
    v10 = lattice[np.ix_(y0, x1)]
    v01 = lattice[np.ix_(y1, x0)]
    v11 = lattice[np.ix_(y1, x1)]

    txg = tx[None, :]
    tyg = ty[:, None]

    top = v00 * (1.0 - txg) + v10 * txg
    bot = v01 * (1.0 - txg) + v11 * txg
    out = top * (1.0 - tyg) + bot * tyg
    return out


def fractal_noise_2d(
    width: int,
    height: int,
    seed: int,
    *,
    octaves: int = 4,
    base_scale: float = 16.0,
    lacunarity: float = 2.0,
    persistence: float = 0.5,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """Fractal Brownian motion: stacked octaves of value noise.

    Each octave halves the lattice scale (``base_scale / lacunarity**i``) and
    scales its amplitude by ``persistence**i``. A distinct sub-seed per octave is
    derived deterministically so octaves are decorrelated yet reproducible. The
    result is normalised to ``[0, 1]`` by its theoretical amplitude sum, then
    clamped.
    """

    if width <= 0 or height <= 0:
        return np.zeros((max(height, 0), max(width, 0)), dtype=np.float64)

    octaves = max(int(octaves), 1)
    if rng is None:
        rng = rng_from_seed(seed)

    total = np.zeros((height, width), dtype=np.float64)
    amplitude = 1.0
    amp_sum = 0.0
    scale = max(float(base_scale), 1.0)

    for i in range(octaves):
        # Per-octave sub-seed keeps octaves independent but deterministic.
        octave_seed = int(rng.integers(0, 2**31 - 1))
        layer = value_noise_2d(width, height, scale, octave_seed)
        total += layer * amplitude
        amp_sum += amplitude
        amplitude *= persistence
        scale = max(scale / lacunarity, 1.0)

    if amp_sum > 0:
        total /= amp_sum
    return np.clip(total, 0.0, 1.0)


def normalized_fractal_field(
    width: int,
    height: int,
    seed: int,
    *,
    octaves: int = 4,
    base_scale: float = 16.0,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """fBm rescaled so its actual min->0 and max->1 (full contrast stretch).

    Useful when a downstream threshold needs the field to span the whole range
    regardless of how the raw octaves happened to distribute.
    """

    field = fractal_noise_2d(
        width, height, seed, octaves=octaves, base_scale=base_scale, rng=rng
    )
    lo = float(field.min())
    hi = float(field.max())
    if hi - lo < 1e-9:
        return np.zeros_like(field)
    return (field - lo) / (hi - lo)


def domain_warp_field(
    width: int,
    height: int,
    seed: int,
    *,
    strength: float = 6.0,
    base_scale: float = 24.0,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """A fractal field whose sample coordinates are warped by two other fields.

    Domain warping breaks up the axis-aligned regularity of raw value noise,
    yielding more organic, swirling boundaries (used for terrain feathering and
    river meander). Fully deterministic from ``seed``.
    """

    if width <= 0 or height <= 0:
        return np.zeros((max(height, 0), max(width, 0)), dtype=np.float64)

    if rng is None:
        rng = rng_from_seed(seed)

    s1 = int(rng.integers(0, 2**31 - 1))
    s2 = int(rng.integers(0, 2**31 - 1))
    s3 = int(rng.integers(0, 2**31 - 1))

    warp_x = (fractal_noise_2d(width, height, s1, base_scale=base_scale) - 0.5) * strength
    warp_y = (fractal_noise_2d(width, height, s2, base_scale=base_scale) - 0.5) * strength

    base = fractal_noise_2d(width, height, s3, base_scale=base_scale)

    yy, xx = np.mgrid[0:height, 0:width]
    sx = np.clip((xx + warp_x).round().astype(np.intp), 0, width - 1)
    sy = np.clip((yy + warp_y).round().astype(np.intp), 0, height - 1)
    return base[sy, sx]
