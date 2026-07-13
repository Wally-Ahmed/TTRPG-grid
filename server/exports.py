"""Export helpers for the TTRPG Grid Map Generator.

Four export paths, all pure-local, no network:

1. ``bulk_zip``            — a zip of every file for the selected maps.
2. ``foundry_module_zip``  — a ready-to-install Foundry VTT module folder
                             (valid ``module.json`` + map PNGs) that drops into
                             ``Data/modules``.
3. ``print_pdf``           — the gridded image tiled across US-Letter / A4 pages
                             at exactly 1 inch per grid square, with overlap
                             margins and corner alignment / crop marks.
4. library backup export / import live in ``library.py``; this module exposes
   thin wrappers for symmetry.

Filename slugs follow Contract 6.
"""

from __future__ import annotations

import io
import json
import os
import re
import zipfile
from typing import Iterable

# ---------------------------------------------------------------------------
# Naming (Contract 6)
# ---------------------------------------------------------------------------


def slugify(title: str, max_len: int = 60) -> str:
    """title → lowercase, ``[^a-z0-9]+`` → ``-``, trimmed, <= ``max_len`` chars."""
    s = (title or "").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    if len(s) > max_len:
        s = s[:max_len].rstrip("-")
    return s or "map"


def download_name(title: str, cols: int, rows: int, kind: str) -> str:
    """Content-Disposition download name per Contract 6.

    ``kind`` ∈ {gridless, gridded, metadata, thumb}. The ``<cols>x<rows>`` token
    is load-bearing (Owlbear auto-detects grid dimensions from it).
    """
    slug = slugify(title)
    dims = f"{cols}x{rows}"
    if kind == "gridless":
        return f"{slug}_{dims}.png"
    if kind == "gridded":
        return f"{slug}_{dims}_gridded.png"
    if kind == "thumb":
        return f"{slug}_{dims}_thumb.jpg"
    if kind == "metadata":
        return f"{slug}_{dims}.json"
    if kind == "layout":
        return f"{slug}_{dims}_layout.json"
    if kind == "print-pdf":
        return f"{slug}_{dims}_print.pdf"
    return f"{slug}_{dims}_{kind}"


# ---------------------------------------------------------------------------
# 1. Bulk zip
# ---------------------------------------------------------------------------


def bulk_zip(library, ids: Iterable[str]) -> io.BytesIO:
    """Zip every file for each map id into an in-memory ``BytesIO``.

    Each map's files land under ``<slug>_<cols>x<rows>/`` inside the archive, with
    the two PNGs renamed to their Contract-6 download names so the extracted files
    are Owlbear-ready. Unknown ids are skipped silently (the API validates first).
    """
    from .library import (
        FILE_GRIDLESS,
        FILE_GRIDDED,
        FILE_METADATA,
        FILE_THUMB,
        FILE_LAYOUT,
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for map_id in ids:
            if not library.exists(map_id):
                continue
            record = library.get(map_id)
            slug = slugify(record.title)
            folder = f"{slug}_{record.cols}x{record.rows}_{map_id}"
            d = library.map_dir(map_id)
            file_map = {
                FILE_GRIDLESS: download_name(record.title, record.cols, record.rows, "gridless"),
                FILE_GRIDDED: download_name(record.title, record.cols, record.rows, "gridded"),
                FILE_METADATA: download_name(record.title, record.cols, record.rows, "metadata"),
                FILE_THUMB: download_name(record.title, record.cols, record.rows, "thumb"),
                FILE_LAYOUT: download_name(record.title, record.cols, record.rows, "layout"),
            }
            for src_name, out_name in file_map.items():
                fp = os.path.join(d, src_name)
                if os.path.isfile(fp):
                    zf.write(fp, arcname=f"{folder}/{out_name}")
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# 2. Foundry module export
# ---------------------------------------------------------------------------


def _module_id_slug(name: str) -> str:
    """A valid Foundry module id: lowercase, hyphen-separated, no special chars."""
    s = (name or "ttrpg-grid-maps").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "ttrpg-grid-maps"


def build_module_json(module_id: str, title: str, version: str = "1.0.0") -> dict:
    """Return a valid current-Foundry (V13-generation) module manifest.

    Verified against the official Foundry "Introduction to Module Development"
    article (foundryvtt.com/article/module-development/). Required keys: id,
    title, description, version. ``compatibility`` uses the generation-wide format
    (``verified: "13"``). ``authors`` is an array of author objects.
    """
    return {
        "id": module_id,
        "title": title,
        "description": (
            "Battle maps exported from the TTRPG Grid Map Generator. "
            "Drop this folder into your Foundry Data/modules directory, enable the "
            "module, then browse the images under modules/"
            f"{module_id}/maps/ from any scene's background picker."
        ),
        "version": version,
        "authors": [
            {"name": "TTRPG Grid Map Generator"}
        ],
        "compatibility": {
            "minimum": "12",
            "verified": "13",
        },
    }


def foundry_module_zip(library, ids: Iterable[str], name: str = "ttrpg-grid-maps") -> io.BytesIO:
    """Zip a ready-to-install Foundry module folder for the selected maps.

    Archive layout (folder name == module id, so it drops into ``Data/modules``)::

        <module_id>/
            module.json
            maps/
                <slug>_<cols>x<rows>.png           (gridless — the play surface)
                <slug>_<cols>x<rows>_gridded.png   (reference)
                <slug>_<cols>x<rows>.json          (metadata sidecar)

    All PNGs live under ``maps/`` so they are browsable from Foundry's file
    picker once the module is enabled.
    """
    from .library import FILE_GRIDLESS, FILE_GRIDDED, FILE_METADATA

    module_id = _module_id_slug(name)
    manifest = build_module_json(module_id, title=name or "TTRPG Grid Maps")

    buf = io.BytesIO()
    seen_names: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            f"{module_id}/module.json",
            json.dumps(manifest, ensure_ascii=False, indent=2),
        )
        for map_id in ids:
            if not library.exists(map_id):
                continue
            record = library.get(map_id)
            d = library.map_dir(map_id)
            base_names = {
                FILE_GRIDLESS: download_name(record.title, record.cols, record.rows, "gridless"),
                FILE_GRIDDED: download_name(record.title, record.cols, record.rows, "gridded"),
                FILE_METADATA: download_name(record.title, record.cols, record.rows, "metadata"),
            }
            for src_name, out_name in base_names.items():
                fp = os.path.join(d, src_name)
                if not os.path.isfile(fp):
                    continue
                # De-duplicate names across maps that share a slug/dimensions.
                final = out_name
                if final in seen_names:
                    stem, ext = os.path.splitext(out_name)
                    final = f"{stem}_{map_id}{ext}"
                seen_names.add(final)
                zf.write(fp, arcname=f"{module_id}/maps/{final}")
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# 3. Print PDF
# ---------------------------------------------------------------------------

# Physical page sizes in inches (portrait).
_PAPER_INCHES = {
    "letter": (8.5, 11.0),
    "a4": (8.2677, 11.6929),  # 210mm x 297mm
}

_DEFAULT_OVERLAP_IN = 0.25  # ~0.25in overlap margin on the trailing edges
_MARK_LEN_PX_FRAC = 0.5     # crop-mark length as a fraction of one inch (of DPI)


def print_pdf(
    library,
    map_id: str,
    paper: str = "letter",
    overlap_in: float = _DEFAULT_OVERLAP_IN,
) -> io.BytesIO:
    """Tile a map's gridded image across pages at exactly 1 inch per grid square.

    ``px_per_square`` is treated as the DPI, so one grid square == one inch on
    paper. Pages carry ``overlap_in`` of shared image on their right/bottom edges
    (so adjacent sheets overlap for taping) and corner alignment / crop marks.

    Returns a ``BytesIO`` holding a multi-page PDF (Pillow native PDF save).
    """
    from PIL import Image, ImageDraw

    paper = (paper or "letter").lower()
    if paper not in _PAPER_INCHES:
        raise ValueError(f"unknown paper size: {paper}")

    record = library.get(map_id)
    dpi = record.px_per_square  # px per inch == px per grid square
    if dpi <= 0:
        raise ValueError("px_per_square must be positive")

    gridded_path = os.path.join(library.map_dir(map_id), "gridded.png")
    src = Image.open(gridded_path).convert("RGB")

    page_w_in, page_h_in = _PAPER_INCHES[paper]
    page_w_px = int(round(page_w_in * dpi))
    page_h_px = int(round(page_h_in * dpi))

    overlap_px = int(round(overlap_in * dpi))
    mark_len = max(6, int(round(_MARK_LEN_PX_FRAC * dpi)))

    # Printable "content" area per page = full page minus the overlap on the
    # trailing (right/bottom) edges, so the overlap band is duplicated on the
    # neighbouring sheet for alignment when taping.
    step_x = max(1, page_w_px - overlap_px)
    step_y = max(1, page_h_px - overlap_px)

    n_cols = max(1, -(-src.width // step_x))   # ceil division
    n_rows = max(1, -(-src.height // step_y))

    pages: list[Image.Image] = []
    for ry in range(n_rows):
        for cx in range(n_cols):
            left = cx * step_x
            top = ry * step_y
            crop_w = min(page_w_px, src.width - left)
            crop_h = min(page_h_px, src.height - top)
            if crop_w <= 0 or crop_h <= 0:
                continue

            page = Image.new("RGB", (page_w_px, page_h_px), (255, 255, 255))
            tile = src.crop((left, top, left + crop_w, top + crop_h))
            page.paste(tile, (0, 0))

            draw = ImageDraw.Draw(page)
            _draw_alignment_marks(
                draw,
                content_w=crop_w,
                content_h=crop_h,
                mark_len=mark_len,
                page_w=page_w_px,
                page_h=page_h_px,
            )
            # Small page label in the corner for assembly order.
            label = f"R{ry + 1}C{cx + 1}"
            draw.text((4, 4), label, fill=(120, 120, 120))
            pages.append(page)

    if not pages:  # degenerate: empty image
        pages = [Image.new("RGB", (page_w_px, page_h_px), (255, 255, 255))]

    buf = io.BytesIO()
    first, rest = pages[0], pages[1:]
    first.save(
        buf,
        format="PDF",
        save_all=True,
        append_images=rest,
        resolution=float(dpi),
    )
    buf.seek(0)
    return buf


def _draw_alignment_marks(draw, *, content_w, content_h, mark_len, page_w, page_h) -> None:
    """Draw corner crop marks at the content box corners plus a content border."""
    color = (40, 40, 40)
    # Content border (light) so the taping seam is visible.
    draw.rectangle([0, 0, content_w - 1, content_h - 1], outline=(160, 160, 160))
    corners = [
        (0, 0),
        (content_w - 1, 0),
        (0, content_h - 1),
        (content_w - 1, content_h - 1),
    ]
    for (x, y) in corners:
        # Horizontal + vertical ticks pointing into the page from each corner.
        hx = mark_len if x == 0 else -mark_len
        vy = mark_len if y == 0 else -mark_len
        draw.line([(x, y), (x + hx, y)], fill=color, width=2)
        draw.line([(x, y), (x, y + vy)], fill=color, width=2)


# ---------------------------------------------------------------------------
# 4. Library backup export / import (thin wrappers over Library)
# ---------------------------------------------------------------------------


def library_backup(library) -> io.BytesIO:
    """Return the whole-library backup archive as a ``BytesIO``."""
    return io.BytesIO(library.export_archive())


def library_restore(library, data: bytes) -> int:
    """Merge a backup archive into ``library``; return count imported."""
    return library.import_archive(data)
