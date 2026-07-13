"""Flask HTTP API for the TTRPG Grid Map Generator (Contract 4).

All routes live under ``/api`` (except ``/`` and static serving of ``web/``).
Errors are JSON ``{"error": "..."}`` with an appropriate status code; unknown
map ids return 404. Downloads carry a Content-Disposition name per Contract 6
(the ``<cols>x<rows>`` token is load-bearing for Owlbear auto-detection).

The generation engine (``server.engine``, Contract 3) is built by another leg and
imported lazily / guardedly so this app — and its test-suite — import cleanly even
when the engine package is absent. Tests inject a fake engine module.

Zero network calls at runtime. The dev server binds 127.0.0.1 only.
"""

from __future__ import annotations

import json
import os
import secrets
from typing import Any, Optional

from flask import Flask, Response, jsonify, request, send_file

from . import exports as exports_mod
from .library import Library, LibraryError, MapNotFound

# Repo root = parent of this file's directory (server/ -> repo root).
_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(_HERE)
WEB_DIR = os.path.join(REPO_ROOT, "web")
ASSETS_MANIFEST = os.path.join(REPO_ROOT, "assets", "manifest.json")

# 32-bit-ish positive seed space; server-side randomization uses secrets, not time.
_SEED_MAX = 2**31 - 1


# ---------------------------------------------------------------------------
# Engine access (Contract 3), guarded so tests can stub it.
# ---------------------------------------------------------------------------


def _get_engine():
    """Import and return the engine module, or raise a friendly error.

    Imported lazily inside request handlers so the module imports without the
    engine present, and so tests can monkeypatch ``server.app._get_engine``.
    """
    from . import engine  # noqa: WPS433  (intentional local import)

    return engine


class EngineUnavailable(RuntimeError):
    """Raised when a generation route is hit but the engine cannot be imported."""


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(library: Optional[Library] = None, library_root: str = "library-data") -> Flask:
    """Build the Flask app. ``library`` (or ``library_root``) is injectable for tests."""
    app = Flask(__name__, static_folder=None)
    lib = library if library is not None else Library(library_root)
    app.config["LIBRARY"] = lib

    # -- error handling -----------------------------------------------------

    @app.errorhandler(MapNotFound)
    def _handle_not_found(err: MapNotFound):  # noqa: WPS430
        return jsonify({"error": f"map not found: {err}"}), 404

    @app.errorhandler(LibraryError)
    def _handle_library_error(err: LibraryError):  # noqa: WPS430
        return jsonify({"error": str(err)}), 400

    @app.errorhandler(EngineUnavailable)
    def _handle_engine(err: EngineUnavailable):  # noqa: WPS430
        return jsonify({"error": str(err)}), 503

    def _bad_request(msg: str):
        return jsonify({"error": msg}), 400

    # -- generation ---------------------------------------------------------

    @app.post("/api/generate")
    def generate():  # noqa: WPS430
        payload = request.get_json(silent=True) or {}
        try:
            engine = _get_engine()
        except Exception as exc:  # pragma: no cover - exercised via stub in tests
            raise EngineUnavailable(f"generation engine unavailable: {exc}")

        prompt = payload.get("prompt", "") or ""
        # Overrides = every explicit control value the UI passed (minus prompt).
        override_keys = (
            "cols", "rows", "px_per_square", "feet_per_square", "seed",
            "density", "mood", "biomes", "structure", "multi_level", "levels",
            "palette_mode", "title", "features",
        )
        overrides = {k: payload[k] for k in override_keys if k in payload and payload[k] is not None}

        spec = engine.parse_prompt(prompt, overrides)

        # Randomize the seed server-side when absent/zero — using secrets, NOT time.
        if not getattr(spec, "seed", 0):
            spec.seed = secrets.randbelow(_SEED_MAX) + 1

        multi = bool(getattr(spec, "multi_level", False))
        if multi:
            results = engine.generate_level_set(spec)
            set_id = _new_set_id()
            records = []
            for idx, result in enumerate(results):
                rec = lib.save(
                    result,
                    title=_result_title(result, spec),
                    prompt=spec.prompt,
                    set_id=set_id,
                    floor_index=idx,
                )
                records.append(_augment_record(rec, result))
            return jsonify({"maps": records})

        result = engine.generate_map(spec)
        rec = lib.save(result, title=_result_title(result, spec), prompt=spec.prompt)
        return jsonify({"map": _augment_record(rec, result)})

    @app.post("/api/estimate")
    def estimate():  # noqa: WPS430
        payload = request.get_json(silent=True) or {}
        try:
            cols = int(payload["cols"])
            rows = int(payload["rows"])
            px = int(payload["px_per_square"])
        except (KeyError, TypeError, ValueError):
            return _bad_request("cols, rows and px_per_square are required integers")
        try:
            engine = _get_engine()
        except Exception as exc:
            raise EngineUnavailable(f"generation engine unavailable: {exc}")
        return jsonify(engine.estimate_size(cols, rows, px))

    # -- library CRUD -------------------------------------------------------

    @app.get("/api/maps")
    def list_maps():  # noqa: WPS430
        args = request.args
        favorite = None
        if args.get("favorite") in ("1", "true", "True"):
            favorite = True
        sort = args.get("sort", "newest")
        if sort not in ("newest", "oldest"):
            sort = "newest"
        result = lib.list(
            search=args.get("search") or None,
            tag=args.get("tag") or None,
            biome=args.get("biome") or None,
            favorite=favorite,
            sort=sort,
            set_id=args.get("set") or None,
        )
        return jsonify({
            "maps": result["maps"],
            "total_count": result["total_count"],
            "total_bytes": result["total_bytes"],
        })

    @app.get("/api/maps/<map_id>")
    def map_detail(map_id: str):  # noqa: WPS430
        record = lib.get(map_id)  # raises MapNotFound -> 404
        layout = lib.load_layout(map_id)
        return jsonify({"map": record.to_dict(), "layout": layout})

    @app.patch("/api/maps/<map_id>")
    def patch_map(map_id: str):  # noqa: WPS430
        if not lib.exists(map_id):
            raise MapNotFound(map_id)
        payload = request.get_json(silent=True) or {}
        record = lib.update(
            map_id,
            title=payload.get("title"),
            tags=payload.get("tags"),
            favorite=payload.get("favorite"),
        )
        return jsonify({"map": record.to_dict()})

    @app.delete("/api/maps/<map_id>")
    def delete_map(map_id: str):  # noqa: WPS430
        lib.delete(map_id)  # raises MapNotFound -> 404; removes row + directory
        return Response(status=204)

    @app.get("/api/maps/<map_id>/file/<kind>")
    def map_file(map_id: str, kind: str):  # noqa: WPS430
        if kind not in ("gridless", "gridded", "thumb", "metadata", "layout"):
            return _bad_request(f"unknown file kind: {kind}")
        record = lib.get(map_id)
        path = lib.file_path(map_id, kind)
        if not os.path.exists(path):
            raise MapNotFound(map_id)
        dl_name = exports_mod.download_name(record.title, record.cols, record.rows, kind)
        mime = {
            "gridless": "image/png",
            "gridded": "image/png",
            "thumb": "image/jpeg",
            "metadata": "application/json",
            "layout": "application/json",
        }[kind]
        return send_file(path, mimetype=mime, as_attachment=True, download_name=dl_name)

    # -- editing / revert ---------------------------------------------------

    @app.post("/api/maps/<map_id>/edit")
    def edit_map(map_id: str):  # noqa: WPS430
        record = lib.get(map_id)
        payload = request.get_json(silent=True) or {}
        layout = lib.load_layout(map_id)
        _apply_edits(layout, payload)

        try:
            engine = _get_engine()
        except Exception as exc:
            raise EngineUnavailable(f"generation engine unavailable: {exc}")
        palette_mode = _layout_palette_mode(layout)
        result = engine.assemble_from_layout(layout, palette_mode=palette_mode)
        updated = lib.update_after_edit(result, map_id)
        return jsonify({"map": updated.to_dict(), "layout": result.layout})

    @app.post("/api/maps/<map_id>/revert")
    def revert_map(map_id: str):  # noqa: WPS430
        record = lib.get(map_id)
        generated = lib.load_layout(map_id, generated=True)
        try:
            engine = _get_engine()
        except Exception as exc:
            raise EngineUnavailable(f"generation engine unavailable: {exc}")
        palette_mode = _layout_palette_mode(generated)
        result = engine.assemble_from_layout(generated, palette_mode=palette_mode)
        updated = lib.revert(result, map_id)
        return jsonify({"map": updated.to_dict(), "layout": result.layout})

    # -- print PDF ----------------------------------------------------------

    @app.get("/api/maps/<map_id>/print-pdf")
    def print_pdf_route(map_id: str):  # noqa: WPS430
        record = lib.get(map_id)
        paper = request.args.get("paper", "letter").lower()
        if paper not in ("letter", "a4"):
            return _bad_request("paper must be 'letter' or 'a4'")
        buf = exports_mod.print_pdf(lib, map_id, paper=paper)
        dl_name = exports_mod.download_name(record.title, record.cols, record.rows, "print-pdf")
        return send_file(
            buf,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=dl_name,
        )

    # -- bulk / module / library archive -----------------------------------

    @app.get("/api/export/bulk")
    def export_bulk():  # noqa: WPS430
        ids = _parse_ids(request.args.get("ids"))
        if not ids:
            return _bad_request("ids query parameter is required")
        missing = [i for i in ids if not lib.exists(i)]
        if missing:
            return jsonify({"error": f"unknown map ids: {', '.join(missing)}"}), 404
        buf = exports_mod.bulk_zip(lib, ids)
        return send_file(
            buf,
            mimetype="application/zip",
            as_attachment=True,
            download_name="ttrpg-maps.zip",
        )

    @app.get("/api/export/foundry-module")
    def export_foundry():  # noqa: WPS430
        ids = _parse_ids(request.args.get("ids"))
        if not ids:
            return _bad_request("ids query parameter is required")
        missing = [i for i in ids if not lib.exists(i)]
        if missing:
            return jsonify({"error": f"unknown map ids: {', '.join(missing)}"}), 404
        name = request.args.get("name", "ttrpg-grid-maps")
        buf = exports_mod.foundry_module_zip(lib, ids, name=name)
        module_id = exports_mod._module_id_slug(name)
        return send_file(
            buf,
            mimetype="application/zip",
            as_attachment=True,
            download_name=f"{module_id}.zip",
        )

    @app.get("/api/library/export")
    def library_export():  # noqa: WPS430
        buf = exports_mod.library_backup(lib)
        return send_file(
            buf,
            mimetype="application/zip",
            as_attachment=True,
            download_name="ttrpg-library-backup.zip",
        )

    @app.post("/api/library/import")
    def library_import():  # noqa: WPS430
        upload = request.files.get("archive") or request.files.get("file")
        if upload is None:
            data = request.get_data()
            if not data:
                return _bad_request("no archive uploaded")
        else:
            data = upload.read()
        try:
            imported = exports_mod.library_restore(lib, data)
        except LibraryError as exc:
            return _bad_request(str(exc))
        except Exception as exc:  # zip/db corruption
            return _bad_request(f"invalid archive: {exc}")
        return jsonify({"imported": imported})

    # -- stats / palette ----------------------------------------------------

    @app.get("/api/stats")
    def stats():  # noqa: WPS430
        return jsonify(lib.stats())

    @app.get("/api/assets/palette")
    def palette():  # noqa: WPS430
        return jsonify(_build_palette())

    # -- static SPA ---------------------------------------------------------

    @app.get("/")
    def index():  # noqa: WPS430
        idx = os.path.join(WEB_DIR, "index.html")
        if os.path.exists(idx):
            return send_file(idx)
        return jsonify({"error": "web/index.html not found (frontend not built yet)"}), 404

    @app.get("/static/<path:filename>")
    def static_files(filename: str):  # noqa: WPS430
        return _serve_web(filename)

    # Catch-all for other top-level web assets (app.js, style.css, favicon, ...).
    @app.get("/<path:filename>")
    def web_asset(filename: str):  # noqa: WPS430
        if filename.startswith("api/"):
            return jsonify({"error": "not found"}), 404
        return _serve_web(filename)

    def _serve_web(filename: str):
        # Prevent path traversal outside web/.
        safe = os.path.normpath(os.path.join(WEB_DIR, filename))
        if not safe.startswith(os.path.abspath(WEB_DIR) + os.sep) and safe != os.path.abspath(WEB_DIR):
            return jsonify({"error": "not found"}), 404
        if os.path.isfile(safe):
            return send_file(safe)
        return jsonify({"error": "not found"}), 404

    return app


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _new_set_id() -> str:
    import uuid

    return uuid.uuid4().hex[:12]


def _result_title(result: Any, spec: Any) -> Optional[str]:
    """Best title for a freshly generated map: spec.title, else the map metadata."""
    title = getattr(spec, "title", "") or ""
    if title:
        return title
    meta = getattr(result, "metadata", None) or {}
    return meta.get("title") or None


def _augment_record(record, result) -> dict:
    """Record dict + the engine warnings for this specific generation."""
    d = record.to_dict()
    warnings = list(getattr(result, "warnings", []) or [])
    if warnings:
        d["warnings"] = warnings
    return d


def _layout_palette_mode(layout: dict) -> str:
    spec = layout.get("spec", {}) or {}
    return spec.get("palette_mode", "standard")


def _parse_ids(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    return [x for x in (part.strip() for part in raw.split(",")) if x]


def _apply_edits(layout: dict, payload: dict) -> None:
    """Mutate ``layout`` in place per Contract 4 edit payload semantics.

    ``set_cells``    -> overwrite terrain at (x, y).
    ``props_remove`` -> drop props by id.
    ``props_update`` -> patch rot / variant / type of props by id.
    ``props_add``    -> append new prop dicts (id auto-assigned if missing).
    """
    cells = layout.get("cells")
    for change in payload.get("set_cells", []) or []:
        try:
            x, y, terrain = int(change["x"]), int(change["y"]), change["terrain"]
        except (KeyError, TypeError, ValueError):
            continue
        if cells and 0 <= y < len(cells) and 0 <= x < len(cells[y]):
            cells[y][x] = terrain

    props = layout.setdefault("props", [])

    remove_ids = set(payload.get("props_remove", []) or [])
    if remove_ids:
        props[:] = [p for p in props if p.get("id") not in remove_ids]

    updates = {u["id"]: u for u in (payload.get("props_update", []) or []) if "id" in u}
    if updates:
        for p in props:
            u = updates.get(p.get("id"))
            if not u:
                continue
            for key in ("rot", "variant", "type"):
                if key in u and u[key] is not None:
                    p[key] = u[key]

    existing_ids = {p.get("id") for p in props}
    next_n = len(props) + 1
    for add in payload.get("props_add", []) or []:
        prop = dict(add)
        if not prop.get("id"):
            new_id = f"p{next_n:03d}"
            while new_id in existing_ids:
                next_n += 1
                new_id = f"p{next_n:03d}"
            prop["id"] = new_id
            existing_ids.add(new_id)
            next_n += 1
        props.append(prop)


def _build_palette() -> dict:
    """Editor palette derived from ``assets/manifest.json`` (Contract 1).

    Returns terrains (with display + walkable/blocking/hazard/difficult/indoor
    flags) and props (with display, category, footprint, variant count). If the
    manifest is missing, returns empty lists rather than raising, so the editor
    degrades gracefully before the asset leg has landed.
    """
    if not os.path.exists(ASSETS_MANIFEST):
        return {"px_per_square": 140, "terrains": [], "props": []}
    with open(ASSETS_MANIFEST, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)

    terrains = []
    for tid, t in (manifest.get("terrains") or {}).items():
        terrains.append({
            "id": tid,
            "display": t.get("display", tid),
            "walkable": bool(t.get("walkable", True)),
            "blocking": bool(t.get("blocking", False)),
            "hazard": bool(t.get("hazard", False)),
            "difficult": bool(t.get("difficult", False)),
            "indoor": bool(t.get("indoor", False)),
        })

    props = []
    for pid, p in (manifest.get("props") or {}).items():
        variants = p.get("variants") or []
        props.append({
            "id": pid,
            "display": p.get("display", pid),
            "category": p.get("category", "misc"),
            "footprint": p.get("footprint", [1, 1]),
            "variant_count": len(variants),
            "rotatable": bool(p.get("rotatable", False)),
        })

    return {
        "px_per_square": manifest.get("px_per_square", 140),
        "terrains": terrains,
        "props": props,
    }
