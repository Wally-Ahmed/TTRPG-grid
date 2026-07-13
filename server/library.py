"""Library storage layer for the TTRPG Grid Map Generator (Contract 5).

Persists generated maps to a SQLite database plus a per-map directory of image /
layout / metadata files. Everything here is local-only and offline — no network
access ever happens. The library root is injectable so tests can run on tmpdirs.

On-disk shape::

    <root>/
        library.db                 # SQLite, table `maps`
        maps/<id>/
            gridless.png
            gridded.png
            thumb.jpg              # <= 400px wide
            layout.json           # current (possibly edited) layout, Contract 2
            layout.generated.json # pristine copy for revert
            metadata.json         # Contract 6 sidecar

`id` is a 12-char lowercase hex string (uuid4 prefix). `set_id` groups the floors
of a multi-level generation.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

THUMB_MAX_WIDTH = 400  # px — spec: thumb.jpg <= 400px wide

# File names stored inside each per-map directory.
FILE_GRIDLESS = "gridless.png"
FILE_GRIDDED = "gridded.png"
FILE_THUMB = "thumb.jpg"
FILE_LAYOUT = "layout.json"
FILE_LAYOUT_GENERATED = "layout.generated.json"
FILE_METADATA = "metadata.json"

# Map the API "kind" tokens (Contract 4) to their on-disk file names.
KIND_TO_FILE = {
    "gridless": FILE_GRIDLESS,
    "gridded": FILE_GRIDDED,
    "thumb": FILE_THUMB,
    "metadata": FILE_METADATA,
    "layout": FILE_LAYOUT,
}


class LibraryError(Exception):
    """Raised for library-level failures (missing map, bad import, ...)."""


class MapNotFound(LibraryError):
    """Raised when an id does not exist in the library."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Current UTC time as an ISO-8601 string (seconds precision, Z suffix)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_map_id() -> str:
    """A fresh 12-char lowercase hex id (uuid4 prefix)."""
    return uuid.uuid4().hex[:12]


def _pil_save_thumb(image, dest_path: str) -> None:
    """Write a <=400px-wide JPEG thumbnail of ``image`` to ``dest_path``."""
    from PIL import Image

    img = image
    if img.width > THUMB_MAX_WIDTH:
        ratio = THUMB_MAX_WIDTH / float(img.width)
        new_size = (THUMB_MAX_WIDTH, max(1, round(img.height * ratio)))
        img = img.resize(new_size, Image.LANCZOS)
    # JPEG cannot hold alpha; flatten onto a neutral background.
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (30, 30, 34))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")
    img.save(dest_path, "JPEG", quality=82)


def _dir_bytes(path: str) -> int:
    """Total size in bytes of every regular file under ``path``."""
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            fp = os.path.join(dirpath, name)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


# ---------------------------------------------------------------------------
# MapRecord
# ---------------------------------------------------------------------------


@dataclass
class MapRecord:
    """A single library entry — the JSON shape returned by the API (Contract 4)."""

    id: str
    title: str
    prompt: str
    created_at: str
    cols: int
    rows: int
    px_per_square: int
    feet_per_square: int
    seed: int
    density: float
    mood: str
    biomes: list
    tags: list
    favorite: bool
    set_id: Optional[str]
    floor_index: int
    width_px: int
    height_px: int
    bytes: int
    warnings: list

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "title": self.title,
            "prompt": self.prompt,
            "created_at": self.created_at,
            "cols": self.cols,
            "rows": self.rows,
            "px_per_square": self.px_per_square,
            "feet_per_square": self.feet_per_square,
            "seed": self.seed,
            "density": self.density,
            "mood": self.mood,
            "biomes": list(self.biomes),
            "tags": list(self.tags),
            "favorite": bool(self.favorite),
            "set_id": self.set_id,
            "floor_index": self.floor_index,
            "width_px": self.width_px,
            "height_px": self.height_px,
            "bytes": self.bytes,
            "thumb_url": f"/api/maps/{self.id}/file/thumb",
            "warnings": list(self.warnings),
        }
        return d


_SCHEMA = """
CREATE TABLE IF NOT EXISTS maps (
    id TEXT PRIMARY KEY,
    title TEXT,
    prompt TEXT,
    created_at TEXT,
    cols INTEGER,
    rows INTEGER,
    px INTEGER,
    feet INTEGER,
    seed INTEGER,
    density REAL,
    mood TEXT,
    biomes TEXT,
    tags TEXT,
    favorite INTEGER,
    set_id TEXT,
    floor_index INTEGER,
    dir TEXT,
    bytes INTEGER
);
"""


# ---------------------------------------------------------------------------
# Library
# ---------------------------------------------------------------------------


class Library:
    """SQLite + on-disk storage for generated maps.

    Parameters
    ----------
    root:
        Directory that holds ``library.db`` and the ``maps/`` subtree. Created on
        demand. Defaults to ``library-data/`` in the current working directory.
    """

    def __init__(self, root: str = "library-data") -> None:
        self.root = os.path.abspath(root)
        self.maps_dir = os.path.join(self.root, "maps")
        self.db_path = os.path.join(self.root, "library.db")
        os.makedirs(self.maps_dir, exist_ok=True)
        self._init_db()

    # -- connection / schema ------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    # -- row <-> record -----------------------------------------------------

    def _row_to_record(self, row: sqlite3.Row) -> MapRecord:
        layout_dir = row["dir"]
        # width/height are derived from metadata.json when available; fall back to
        # cols*px / rows*px so a record is always well-formed.
        width_px = row["cols"] * row["px"]
        height_px = row["rows"] * row["px"]
        warnings: list = []
        meta_path = os.path.join(self.maps_dir, layout_dir, FILE_METADATA)
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as fh:
                    meta = json.load(fh)
                img = meta.get("image", {})
                width_px = img.get("width", width_px)
                height_px = img.get("height", height_px)
                warnings = meta.get("warnings", []) or []
            except (OSError, json.JSONDecodeError):
                pass
        return MapRecord(
            id=row["id"],
            title=row["title"],
            prompt=row["prompt"],
            created_at=row["created_at"],
            cols=row["cols"],
            rows=row["rows"],
            px_per_square=row["px"],
            feet_per_square=row["feet"],
            seed=row["seed"],
            density=row["density"],
            mood=row["mood"],
            biomes=json.loads(row["biomes"] or "[]"),
            tags=json.loads(row["tags"] or "[]"),
            favorite=bool(row["favorite"]),
            set_id=row["set_id"],
            floor_index=row["floor_index"],
            width_px=width_px,
            height_px=height_px,
            bytes=row["bytes"],
            warnings=warnings,
        )

    def map_dir(self, map_id: str) -> str:
        """Absolute path to the per-map directory (whether or not it exists)."""
        return os.path.join(self.maps_dir, map_id)

    # -- save ---------------------------------------------------------------

    def save(
        self,
        result: Any,
        *,
        map_id: Optional[str] = None,
        title: Optional[str] = None,
        prompt: Optional[str] = None,
        tags: Optional[list] = None,
        favorite: bool = False,
        set_id: Optional[str] = None,
        floor_index: int = 0,
        created_at: Optional[str] = None,
    ) -> MapRecord:
        """Persist an engine ``MapResult`` and return its ``MapRecord``.

        ``result`` must expose ``layout`` (Contract 2 dict), ``gridless`` and
        ``gridded`` PIL images, ``metadata`` (Contract 6 dict) and ``warnings``.
        The pristine ``layout.generated.json`` is written alongside ``layout.json``
        so :meth:`revert` can restore the untouched generated state later.
        """
        map_id = map_id or new_map_id()
        d = self.map_dir(map_id)
        os.makedirs(d, exist_ok=True)

        layout = result.layout
        metadata = dict(result.metadata) if result.metadata else {}
        warnings = list(getattr(result, "warnings", []) or [])
        # Keep the warnings inside metadata.json so records can recover them.
        metadata.setdefault("warnings", warnings)

        # Persist images.
        result.gridless.save(os.path.join(d, FILE_GRIDLESS), "PNG")
        result.gridded.save(os.path.join(d, FILE_GRIDDED), "PNG")
        _pil_save_thumb(result.gridded, os.path.join(d, FILE_THUMB))

        # Persist JSON: current layout + pristine generated copy + metadata.
        self._write_json(os.path.join(d, FILE_LAYOUT), layout)
        self._write_json(os.path.join(d, FILE_LAYOUT_GENERATED), layout)
        self._write_json(os.path.join(d, FILE_METADATA), metadata)

        # Derive scalar fields from layout / metadata.
        cols = layout.get("cols")
        rows = layout.get("rows")
        seed = layout.get("seed", 0)
        spec = layout.get("spec", {}) or {}
        px = spec.get("px_per_square") or metadata.get("grid", {}).get("px_per_square", 140)
        feet = spec.get("feet_per_square") or metadata.get("grid", {}).get("feet_per_square", 5)
        density = spec.get("density", metadata.get("density", 0.5))
        mood = spec.get("mood", metadata.get("mood", "neutral"))
        biomes = spec.get("biomes") or metadata.get("biomes") or []

        if title is None:
            title = spec.get("title") or metadata.get("title") or "Untitled Map"
        if prompt is None:
            prompt = spec.get("prompt") or metadata.get("prompt") or ""
        tags = list(tags or [])
        created_at = created_at or _now_iso()

        size_bytes = _dir_bytes(d)

        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO maps
                (id, title, prompt, created_at, cols, rows, px, feet, seed,
                 density, mood, biomes, tags, favorite, set_id, floor_index,
                 dir, bytes)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    map_id,
                    title,
                    prompt,
                    created_at,
                    cols,
                    rows,
                    px,
                    feet,
                    seed,
                    density,
                    mood,
                    json.dumps(list(biomes)),
                    json.dumps(tags),
                    1 if favorite else 0,
                    set_id,
                    floor_index,
                    map_id,  # dir is relative to maps_dir; equals the id
                    size_bytes,
                ),
            )
        return self.get(map_id)

    @staticmethod
    def _write_json(path: str, obj: Any) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)

    # -- read ---------------------------------------------------------------

    def get(self, map_id: str) -> MapRecord:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM maps WHERE id = ?", (map_id,)).fetchone()
        if row is None:
            raise MapNotFound(map_id)
        return self._row_to_record(row)

    def exists(self, map_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT 1 FROM maps WHERE id = ?", (map_id,)).fetchone()
        return row is not None

    def load_layout(self, map_id: str, generated: bool = False) -> dict:
        """Return the current (or pristine generated) layout dict for a map."""
        if not self.exists(map_id):
            raise MapNotFound(map_id)
        name = FILE_LAYOUT_GENERATED if generated else FILE_LAYOUT
        path = os.path.join(self.map_dir(map_id), name)
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def load_metadata(self, map_id: str) -> dict:
        if not self.exists(map_id):
            raise MapNotFound(map_id)
        path = os.path.join(self.map_dir(map_id), FILE_METADATA)
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def file_path(self, map_id: str, kind: str) -> str:
        """Absolute path to a stored file by API ``kind`` token; validates both."""
        if kind not in KIND_TO_FILE:
            raise LibraryError(f"unknown file kind: {kind}")
        if not self.exists(map_id):
            raise MapNotFound(map_id)
        return os.path.join(self.map_dir(map_id), KIND_TO_FILE[kind])

    def list(
        self,
        *,
        search: Optional[str] = None,
        tag: Optional[str] = None,
        biome: Optional[str] = None,
        favorite: Optional[bool] = None,
        sort: str = "newest",
        set_id: Optional[str] = None,
    ) -> dict:
        """Return ``{maps: [MapRecord], total_count, total_bytes}`` with filters.

        ``search`` matches a substring against title OR prompt (case-insensitive).
        ``tag`` / ``biome`` match an exact entry of the respective JSON list.
        ``sort`` is ``newest`` (default) or ``oldest`` by ``created_at``.
        """
        clauses: list[str] = []
        params: list[Any] = []

        if search:
            like = f"%{search.lower()}%"
            clauses.append("(LOWER(title) LIKE ? OR LOWER(prompt) LIKE ?)")
            params.extend([like, like])
        if favorite is not None:
            clauses.append("favorite = ?")
            params.append(1 if favorite else 0)
        if set_id is not None:
            clauses.append("set_id = ?")
            params.append(set_id)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        order = "ASC" if sort == "oldest" else "DESC"
        # Secondary key on floor_index keeps multi-level sets in floor order.
        sql = (
            f"SELECT * FROM maps {where} "
            f"ORDER BY created_at {order}, floor_index ASC"
        )

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        records = [self._row_to_record(r) for r in rows]

        # tag / biome filters operate on the decoded JSON lists (substring-free,
        # exact membership) — done in Python since the columns are JSON text.
        if tag:
            records = [r for r in records if tag in r.tags]
        if biome:
            records = [r for r in records if biome in r.biomes]

        total_bytes = sum(r.bytes for r in records)
        return {
            "maps": [r.to_dict() for r in records],
            "records": records,
            "total_count": len(records),
            "total_bytes": total_bytes,
        }

    # -- update -------------------------------------------------------------

    def update(
        self,
        map_id: str,
        *,
        title: Optional[str] = None,
        tags: Optional[list] = None,
        favorite: Optional[bool] = None,
    ) -> MapRecord:
        """Patch title / tags / favorite on a map (only provided fields change)."""
        if not self.exists(map_id):
            raise MapNotFound(map_id)
        sets: list[str] = []
        params: list[Any] = []
        if title is not None:
            sets.append("title = ?")
            params.append(title)
        if tags is not None:
            sets.append("tags = ?")
            params.append(json.dumps(list(tags)))
        if favorite is not None:
            sets.append("favorite = ?")
            params.append(1 if favorite else 0)
        if sets:
            params.append(map_id)
            with self._connect() as conn:
                conn.execute(
                    f"UPDATE maps SET {', '.join(sets)} WHERE id = ?", params
                )
        return self.get(map_id)

    def update_after_edit(self, result: Any, map_id: str) -> MapRecord:
        """Re-persist images + current layout + thumb after a manual edit.

        Does NOT touch ``layout.generated.json`` (the revert baseline) or the
        library metadata columns; only refreshes the rendered artifacts, current
        layout, metadata sidecar and recomputes on-disk byte size.
        """
        if not self.exists(map_id):
            raise MapNotFound(map_id)
        d = self.map_dir(map_id)
        result.gridless.save(os.path.join(d, FILE_GRIDLESS), "PNG")
        result.gridded.save(os.path.join(d, FILE_GRIDDED), "PNG")
        _pil_save_thumb(result.gridded, os.path.join(d, FILE_THUMB))
        self._write_json(os.path.join(d, FILE_LAYOUT), result.layout)
        if result.metadata:
            metadata = dict(result.metadata)
            metadata.setdefault("warnings", list(getattr(result, "warnings", []) or []))
            self._write_json(os.path.join(d, FILE_METADATA), metadata)
        self._refresh_bytes(map_id)
        return self.get(map_id)

    def revert(self, result: Any, map_id: str) -> MapRecord:
        """Restore ``layout.generated.json`` as the current layout after re-render.

        The caller re-composites the pristine generated layout into ``result`` and
        passes it here; we overwrite the current layout + rendered artifacts and
        refresh the thumb.
        """
        return self.update_after_edit(result, map_id)

    def _refresh_bytes(self, map_id: str) -> None:
        size_bytes = _dir_bytes(self.map_dir(map_id))
        with self._connect() as conn:
            conn.execute("UPDATE maps SET bytes = ? WHERE id = ?", (size_bytes, map_id))

    # -- delete -------------------------------------------------------------

    def delete(self, map_id: str) -> None:
        """Remove the DB row AND the map directory from disk (non-negotiable)."""
        if not self.exists(map_id):
            raise MapNotFound(map_id)
        with self._connect() as conn:
            conn.execute("DELETE FROM maps WHERE id = ?", (map_id,))
        d = self.map_dir(map_id)
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)

    # -- stats --------------------------------------------------------------

    def stats(self) -> dict:
        """Return ``{map_count, total_bytes, by_biome: {biome: count}}``."""
        with self._connect() as conn:
            rows = conn.execute("SELECT biomes, bytes FROM maps").fetchall()
        by_biome: dict[str, int] = {}
        total_bytes = 0
        for row in rows:
            total_bytes += row["bytes"] or 0
            for b in json.loads(row["biomes"] or "[]"):
                by_biome[b] = by_biome.get(b, 0) + 1
        return {
            "map_count": len(rows),
            "total_bytes": total_bytes,
            "by_biome": by_biome,
        }

    # -- backup / import ----------------------------------------------------

    def export_archive(self) -> bytes:
        """Return a single zip (bytes) containing the DB + every map directory."""
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            if os.path.exists(self.db_path):
                zf.write(self.db_path, arcname="library.db")
            for map_id in self._all_ids():
                d = self.map_dir(map_id)
                if not os.path.isdir(d):
                    continue
                for name in os.listdir(d):
                    fp = os.path.join(d, name)
                    if os.path.isfile(fp):
                        zf.write(fp, arcname=f"maps/{map_id}/{name}")
        buf.seek(0)
        return buf.getvalue()

    def import_archive(self, data: bytes) -> int:
        """Merge a backup archive into this library. Returns count imported.

        The archive is expected to contain ``library.db`` and ``maps/<id>/...``.
        Ids that collide with existing rows are regenerated (row + directory), so
        importing into a non-empty library never clobbers existing maps.
        """
        import tempfile
        import zipfile

        with tempfile.TemporaryDirectory() as tmp:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                zf.extractall(tmp)

            src_db = os.path.join(tmp, "library.db")
            if not os.path.exists(src_db):
                raise LibraryError("archive missing library.db")

            src_conn = sqlite3.connect(src_db)
            src_conn.row_factory = sqlite3.Row
            try:
                src_rows = src_conn.execute("SELECT * FROM maps").fetchall()
            finally:
                src_conn.close()

            imported = 0
            for row in src_rows:
                old_id = row["id"]
                new_id = old_id
                if self.exists(new_id):
                    new_id = new_map_id()
                    while self.exists(new_id) or os.path.isdir(self.map_dir(new_id)):
                        new_id = new_map_id()

                src_map_dir = os.path.join(tmp, "maps", old_id)
                dst_map_dir = self.map_dir(new_id)
                if os.path.isdir(src_map_dir):
                    shutil.copytree(src_map_dir, dst_map_dir, dirs_exist_ok=True)
                else:
                    os.makedirs(dst_map_dir, exist_ok=True)

                size_bytes = _dir_bytes(dst_map_dir)
                with self._connect() as conn:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO maps
                        (id, title, prompt, created_at, cols, rows, px, feet, seed,
                         density, mood, biomes, tags, favorite, set_id, floor_index,
                         dir, bytes)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            new_id,
                            row["title"],
                            row["prompt"],
                            row["created_at"],
                            row["cols"],
                            row["rows"],
                            row["px"],
                            row["feet"],
                            row["seed"],
                            row["density"],
                            row["mood"],
                            row["biomes"],
                            row["tags"],
                            row["favorite"],
                            row["set_id"],
                            row["floor_index"],
                            new_id,
                            size_bytes,
                        ),
                    )
                imported += 1
            return imported

    # -- internal -----------------------------------------------------------

    def _all_ids(self) -> Iterable[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT id FROM maps").fetchall()
        return [r["id"] for r in rows]
