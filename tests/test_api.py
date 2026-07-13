"""Tests for the Flask HTTP API (Contract 4) using the fake engine.

The ``client`` / ``app`` / ``library`` fixtures and the ``FakeEngine`` live in
``conftest.py``. ``app`` monkeypatches ``server.app._get_engine`` to the fake, so
every generation route runs without the real engine present.
"""

from __future__ import annotations

import io
import json
import zipfile


# ---------------------------------------------------------------------------
# generate -> list -> detail -> patch -> delete lifecycle
# ---------------------------------------------------------------------------


def test_generate_returns_map_record(client):
    resp = client.post("/api/generate", json={"prompt": "a dark crypt", "cols": 6, "rows": 4})
    assert resp.status_code == 200
    data = resp.get_json()
    assert "map" in data
    rec = data["map"]
    assert rec["id"]
    assert rec["cols"] == 6 and rec["rows"] == 4
    # engine warnings propagate
    assert rec["warnings"] == ["fake warning: procedural fallback in use"]


def test_generate_randomizes_seed_when_absent(client):
    r1 = client.post("/api/generate", json={"prompt": "cave"}).get_json()["map"]
    r2 = client.post("/api/generate", json={"prompt": "cave"}).get_json()["map"]
    assert r1["seed"] != 0 and r2["seed"] != 0
    # two randomized seeds essentially never collide
    assert r1["seed"] != r2["seed"]


def test_generate_honours_explicit_seed(client):
    rec = client.post("/api/generate", json={"prompt": "cave", "seed": 42}).get_json()["map"]
    assert rec["seed"] == 42


def test_full_lifecycle(client, library):
    # generate
    rec = client.post("/api/generate", json={"prompt": "forest ambush", "cols": 6, "rows": 4}).get_json()["map"]
    map_id = rec["id"]

    # list
    listing = client.get("/api/maps").get_json()
    assert listing["total_count"] == 1
    assert listing["maps"][0]["id"] == map_id

    # detail
    detail = client.get(f"/api/maps/{map_id}")
    assert detail.status_code == 200
    dj = detail.get_json()
    assert dj["map"]["id"] == map_id
    assert dj["layout"]["cols"] == 6

    # patch
    patched = client.patch(f"/api/maps/{map_id}", json={"title": "Session 4 - Ambush", "favorite": True})
    assert patched.status_code == 200
    assert patched.get_json()["map"]["title"] == "Session 4 - Ambush"
    assert patched.get_json()["map"]["favorite"] is True

    # delete -> 204 and directory gone
    import os
    d = library.map_dir(map_id)
    assert os.path.isdir(d)
    delete = client.delete(f"/api/maps/{map_id}")
    assert delete.status_code == 204
    assert not os.path.exists(d)
    # now 404
    assert client.get(f"/api/maps/{map_id}").status_code == 404


def test_multi_level_generate(client):
    resp = client.post("/api/generate", json={
        "prompt": "a wizard's tower", "multi_level": True, "levels": 3, "cols": 6, "rows": 4,
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert "maps" in data
    assert len(data["maps"]) == 3
    set_ids = {m["set_id"] for m in data["maps"]}
    assert len(set_ids) == 1 and None not in set_ids
    floor_indices = sorted(m["floor_index"] for m in data["maps"])
    assert floor_indices == [0, 1, 2]


# ---------------------------------------------------------------------------
# unknown id -> 404
# ---------------------------------------------------------------------------


def test_unknown_id_404(client):
    assert client.get("/api/maps/deadbeef0000").status_code == 404
    assert client.patch("/api/maps/deadbeef0000", json={"title": "x"}).status_code == 404
    assert client.delete("/api/maps/deadbeef0000").status_code == 404
    assert client.get("/api/maps/deadbeef0000/file/gridless").status_code == 404
    assert client.get("/api/maps/deadbeef0000/print-pdf").status_code == 404


def test_error_shape_is_json_error(client):
    resp = client.get("/api/maps/deadbeef0000")
    assert resp.status_code == 404
    assert "error" in resp.get_json()


# ---------------------------------------------------------------------------
# download filename correctness (Contract 6)
# ---------------------------------------------------------------------------


def _content_disposition(resp):
    return resp.headers.get("Content-Disposition", "")


def test_download_filenames(client):
    rec = client.post("/api/generate", json={
        "prompt": "Ruined Watchtower!!", "title": "Ruined Watchtower", "cols": 30, "rows": 20,
    }).get_json()["map"]
    map_id = rec["id"]

    gridless = client.get(f"/api/maps/{map_id}/file/gridless")
    assert gridless.status_code == 200
    assert "ruined-watchtower_30x20.png" in _content_disposition(gridless)

    gridded = client.get(f"/api/maps/{map_id}/file/gridded")
    assert "ruined-watchtower_30x20_gridded.png" in _content_disposition(gridded)

    metadata = client.get(f"/api/maps/{map_id}/file/metadata")
    assert "ruined-watchtower_30x20.json" in _content_disposition(metadata)

    thumb = client.get(f"/api/maps/{map_id}/file/thumb")
    assert thumb.status_code == 200
    assert thumb.mimetype == "image/jpeg"


def test_bad_file_kind_400(client):
    rec = client.post("/api/generate", json={"prompt": "cave"}).get_json()["map"]
    resp = client.get(f"/api/maps/{rec['id']}/file/bogus")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# search / filter / sort via HTTP
# ---------------------------------------------------------------------------


def _seed_library(client):
    a = client.post("/api/generate", json={
        "prompt": "ambush on a forest road", "title": "Forest Ambush", "biomes": ["forest"],
    }).get_json()["map"]
    b = client.post("/api/generate", json={
        "prompt": "a frozen dwarven crypt", "title": "Dark Crypt", "biomes": ["dungeon"],
    }).get_json()["map"]
    return a, b


def test_http_search_and_filters(client):
    a, b = _seed_library(client)

    r = client.get("/api/maps?search=crypt").get_json()
    assert [m["id"] for m in r["maps"]] == [b["id"]]

    r = client.get("/api/maps?biome=forest").get_json()
    assert [m["id"] for m in r["maps"]] == [a["id"]]

    # favorite filter
    client.patch(f"/api/maps/{b['id']}", json={"favorite": True})
    r = client.get("/api/maps?favorite=1").get_json()
    assert [m["id"] for m in r["maps"]] == [b["id"]]

    # tag filter
    client.patch(f"/api/maps/{a['id']}", json={"tags": ["campaign-a"]})
    r = client.get("/api/maps?tag=campaign-a").get_json()
    assert [m["id"] for m in r["maps"]] == [a["id"]]


def test_http_sort(client, library):
    from conftest import make_result, make_layout

    # Save with distinct timestamps directly so ordering is deterministic
    # (two API generates in the same second would tie on created_at).
    a = library.save(make_result(make_layout(title="Older")), title="Older",
                     created_at="2026-01-01T00:00:00+00:00")
    b = library.save(make_result(make_layout(title="Newer")), title="Newer",
                     created_at="2026-06-01T00:00:00+00:00")
    newest = [m["id"] for m in client.get("/api/maps?sort=newest").get_json()["maps"]]
    oldest = [m["id"] for m in client.get("/api/maps?sort=oldest").get_json()["maps"]]
    assert newest == [b.id, a.id]
    assert oldest == [a.id, b.id]


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


def test_stats_endpoint(client):
    _seed_library(client)
    s = client.get("/api/stats").get_json()
    assert s["map_count"] == 2
    assert s["total_bytes"] > 0
    assert s["by_biome"] == {"forest": 1, "dungeon": 1}


# ---------------------------------------------------------------------------
# estimate
# ---------------------------------------------------------------------------


def test_estimate(client):
    r = client.post("/api/estimate", json={"cols": 30, "rows": 20, "px_per_square": 140}).get_json()
    assert r["width"] == 4200 and r["height"] == 2800
    assert "megapixels" in r and "est_bytes" in r


def test_estimate_missing_args_400(client):
    assert client.post("/api/estimate", json={"cols": 30}).status_code == 400


# ---------------------------------------------------------------------------
# edit / revert
# ---------------------------------------------------------------------------


def test_edit_then_revert(client):
    rec = client.post("/api/generate", json={"prompt": "cave", "cols": 6, "rows": 4}).get_json()["map"]
    map_id = rec["id"]

    edit = client.post(f"/api/maps/{map_id}/edit", json={
        "set_cells": [{"x": 0, "y": 0, "terrain": "lava"}],
        "props_remove": ["p001"],
        "props_update": [{"id": "p002", "rot": 180}],
        "props_add": [{"type": "chest", "x": 3, "y": 1}],
    })
    assert edit.status_code == 200
    layout = edit.get_json()["layout"]
    assert layout["cells"][0][0] == "lava"
    ids = {p["id"] for p in layout["props"]}
    assert "p001" not in ids  # removed
    p002 = next(p for p in layout["props"] if p["id"] == "p002")
    assert p002["rot"] == 180
    assert any(p.get("type") == "chest" for p in layout["props"])  # added

    # revert restores generated
    rev = client.post(f"/api/maps/{map_id}/revert")
    assert rev.status_code == 200
    reverted = rev.get_json()["layout"]
    assert reverted["cells"][0][0] == "stone_wall"
    assert {p["id"] for p in reverted["props"]} == {"p001", "p002"}


# ---------------------------------------------------------------------------
# bulk zip
# ---------------------------------------------------------------------------


def test_bulk_zip_contents(client):
    a, b = _seed_library(client)
    resp = client.get(f"/api/export/bulk?ids={a['id']},{b['id']}")
    assert resp.status_code == 200
    assert resp.mimetype == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(resp.data))
    names = zf.namelist()
    # each map contributes its files under a folder
    assert any(n.endswith(".png") for n in names)
    assert any(n.endswith(".json") for n in names)
    # both maps present
    assert any(a["id"] in n for n in names)
    assert any(b["id"] in n for n in names)


def test_bulk_zip_missing_id_404(client):
    a, _ = _seed_library(client)
    resp = client.get(f"/api/export/bulk?ids={a['id']},nope00000000")
    assert resp.status_code == 404


def test_bulk_no_ids_400(client):
    assert client.get("/api/export/bulk").status_code == 400


# ---------------------------------------------------------------------------
# print PDF
# ---------------------------------------------------------------------------


def test_print_pdf_valid(client):
    rec = client.post("/api/generate", json={"prompt": "cave", "cols": 6, "rows": 4}).get_json()["map"]
    resp = client.get(f"/api/maps/{rec['id']}/print-pdf?paper=letter")
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    assert resp.data[:5] == b"%PDF-"  # PDF magic
    assert "print.pdf" in _content_disposition(resp)


def test_print_pdf_a4(client):
    rec = client.post("/api/generate", json={"prompt": "cave"}).get_json()["map"]
    resp = client.get(f"/api/maps/{rec['id']}/print-pdf?paper=a4")
    assert resp.status_code == 200
    assert resp.data[:5] == b"%PDF-"


def test_print_pdf_bad_paper_400(client):
    rec = client.post("/api/generate", json={"prompt": "cave"}).get_json()["map"]
    assert client.get(f"/api/maps/{rec['id']}/print-pdf?paper=folio").status_code == 400


# ---------------------------------------------------------------------------
# Foundry module export
# ---------------------------------------------------------------------------


def test_foundry_module_zip(client):
    a, b = _seed_library(client)
    resp = client.get(f"/api/export/foundry-module?ids={a['id']},{b['id']}&name=My Campaign Maps")
    assert resp.status_code == 200
    assert resp.mimetype == "application/zip"

    zf = zipfile.ZipFile(io.BytesIO(resp.data))
    names = zf.namelist()

    # module id folder derived from name
    module_json = [n for n in names if n.endswith("module.json")]
    assert len(module_json) == 1
    module_id = module_json[0].split("/")[0]
    assert module_id == "my-campaign-maps"

    # module.json parses and has required Foundry fields
    manifest = json.loads(zf.read(module_json[0]))
    for key in ("id", "title", "description", "version", "compatibility"):
        assert key in manifest, f"module.json missing {key}"
    assert manifest["id"] == module_id
    assert "verified" in manifest["compatibility"]
    assert isinstance(manifest["authors"], list)

    # maps organized under <module_id>/maps/
    assert any(n.startswith(f"{module_id}/maps/") and n.endswith(".png") for n in names)


def test_foundry_module_missing_id_404(client):
    a, _ = _seed_library(client)
    assert client.get(f"/api/export/foundry-module?ids={a['id']},missing00000").status_code == 404


# ---------------------------------------------------------------------------
# library backup export / import round-trip via HTTP
# ---------------------------------------------------------------------------


def test_library_export_import_roundtrip(client):
    a, b = _seed_library(client)

    export = client.get("/api/library/export")
    assert export.status_code == 200
    archive = export.data
    assert archive[:2] == b"PK"

    before = client.get("/api/maps").get_json()["total_count"]
    assert before == 2

    # import back into the same library -> ids regenerate, count doubles
    resp = client.post(
        "/api/library/import",
        data={"archive": (io.BytesIO(archive), "backup.zip")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    assert resp.get_json()["imported"] == 2

    after = client.get("/api/maps").get_json()["total_count"]
    assert after == 4


def test_library_import_bad_archive_400(client):
    resp = client.post(
        "/api/library/import",
        data={"archive": (io.BytesIO(b"not a zip"), "x.zip")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# palette
# ---------------------------------------------------------------------------


def test_palette_endpoint(client):
    resp = client.get("/api/assets/palette")
    assert resp.status_code == 200
    data = resp.get_json()
    # keys always present even if the manifest isn't built yet in this worktree
    assert "terrains" in data and "props" in data


# ---------------------------------------------------------------------------
# static SPA fallback
# ---------------------------------------------------------------------------


def test_index_missing_web_returns_404_json(client):
    # web/ is built by another leg and absent here -> graceful JSON 404
    resp = client.get("/")
    assert resp.status_code in (200, 404)
    if resp.status_code == 404:
        assert "error" in resp.get_json()
