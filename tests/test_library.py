"""Tests for the on-disk + SQLite library layer (Contract 5)."""

from __future__ import annotations

import json
import os

import pytest

from server.library import (
    FILE_GRIDDED,
    FILE_GRIDLESS,
    FILE_LAYOUT,
    FILE_LAYOUT_GENERATED,
    FILE_METADATA,
    FILE_THUMB,
    Library,
    MapNotFound,
)

from conftest import make_layout, make_result


# ---------------------------------------------------------------------------
# save / structure
# ---------------------------------------------------------------------------


def test_save_creates_db_dir_and_files(library, library_root):
    result = make_result(make_layout(title="Ruined Watchtower"))
    rec = library.save(result, title="Ruined Watchtower", prompt="a ruined watchtower")

    assert os.path.exists(os.path.join(library_root, "library.db"))
    d = library.map_dir(rec.id)
    for name in (FILE_GRIDLESS, FILE_GRIDDED, FILE_THUMB, FILE_LAYOUT,
                 FILE_LAYOUT_GENERATED, FILE_METADATA):
        assert os.path.isfile(os.path.join(d, name)), f"missing {name}"

    assert rec.id and len(rec.id) == 12
    assert rec.title == "Ruined Watchtower"
    assert rec.bytes > 0
    assert rec.to_dict()["thumb_url"] == f"/api/maps/{rec.id}/file/thumb"


def test_thumb_is_within_400px(library):
    # Build an oversized gridded image to force a downscale.
    from PIL import Image

    result = make_result(make_layout())
    result.gridded = Image.new("RGB", (1200, 800), (10, 10, 10))
    rec = library.save(result)
    thumb_path = os.path.join(library.map_dir(rec.id), FILE_THUMB)
    with Image.open(thumb_path) as im:
        assert im.width <= 400


def test_pristine_generated_copy_written(library):
    layout = make_layout(title="Pristine")
    rec = library.save(make_result(layout))
    gen_path = os.path.join(library.map_dir(rec.id), FILE_LAYOUT_GENERATED)
    with open(gen_path) as fh:
        pristine = json.load(fh)
    assert pristine["cols"] == layout["cols"]
    assert pristine["cells"] == layout["cells"]


# ---------------------------------------------------------------------------
# get / not found
# ---------------------------------------------------------------------------


def test_get_unknown_raises(library):
    with pytest.raises(MapNotFound):
        library.get("deadbeef0000")


def test_exists(library):
    rec = library.save(make_result())
    assert library.exists(rec.id)
    assert not library.exists("nope00000000")


# ---------------------------------------------------------------------------
# list: search / filter / sort
# ---------------------------------------------------------------------------


def _save_sample(library):
    forest = library.save(
        make_result(make_layout(title="Forest Ambush", prompt="ambush on a forest road", biomes=["forest"])),
        title="Forest Ambush", prompt="ambush on a forest road",
        tags=["campaign-a"],
    )
    dungeon = library.save(
        make_result(make_layout(title="Dark Crypt", prompt="a frozen dwarven crypt", biomes=["dungeon"])),
        title="Dark Crypt", prompt="a frozen dwarven crypt",
        tags=["campaign-b"], favorite=True,
    )
    return forest, dungeon


def test_list_search_matches_title_and_prompt(library):
    forest, dungeon = _save_sample(library)
    # title match
    res = library.list(search="crypt")
    ids = [m["id"] for m in res["maps"]]
    assert dungeon.id in ids and forest.id not in ids
    # prompt match
    res = library.list(search="ambush")
    ids = [m["id"] for m in res["maps"]]
    assert forest.id in ids and dungeon.id not in ids


def test_list_filter_by_biome_and_tag_and_favorite(library):
    forest, dungeon = _save_sample(library)

    res = library.list(biome="forest")
    assert [m["id"] for m in res["maps"]] == [forest.id]

    res = library.list(tag="campaign-b")
    assert [m["id"] for m in res["maps"]] == [dungeon.id]

    res = library.list(favorite=True)
    assert [m["id"] for m in res["maps"]] == [dungeon.id]


def test_list_sort_newest_oldest(library):
    a = library.save(make_result(make_layout(title="A")), title="A", created_at="2026-01-01T00:00:00+00:00")
    b = library.save(make_result(make_layout(title="B")), title="B", created_at="2026-06-01T00:00:00+00:00")

    newest = [m["id"] for m in library.list(sort="newest")["maps"]]
    oldest = [m["id"] for m in library.list(sort="oldest")["maps"]]
    assert newest[0] == b.id
    assert oldest[0] == a.id


def test_list_totals(library):
    _save_sample(library)
    res = library.list()
    assert res["total_count"] == 2
    assert res["total_bytes"] == sum(m["bytes"] for m in res["maps"])


def test_list_set_grouping(library):
    s = "set123456789"
    f0 = library.save(make_result(make_layout(title="Tower G")), set_id=s, floor_index=0, title="Tower G")
    f1 = library.save(make_result(make_layout(title="Tower 1")), set_id=s, floor_index=1, title="Tower 1")
    library.save(make_result(make_layout(title="Unrelated")), title="Unrelated")

    res = library.list(set_id=s)
    ids = [m["id"] for m in res["maps"]]
    assert set(ids) == {f0.id, f1.id}


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


def test_update_title_tags_favorite(library):
    rec = library.save(make_result(), title="Old")
    updated = library.update(rec.id, title="New Name", tags=["session-4"], favorite=True)
    assert updated.title == "New Name"
    assert updated.tags == ["session-4"]
    assert updated.favorite is True


def test_update_partial_leaves_others(library):
    rec = library.save(make_result(), title="Keep", tags=["x"])
    updated = library.update(rec.id, favorite=True)
    assert updated.title == "Keep"
    assert updated.tags == ["x"]
    assert updated.favorite is True


def test_update_unknown_raises(library):
    with pytest.raises(MapNotFound):
        library.update("missing00000", title="x")


# ---------------------------------------------------------------------------
# revert / edit
# ---------------------------------------------------------------------------


def test_update_after_edit_keeps_pristine(library):
    rec = library.save(make_result(make_layout(title="Edit Me")))
    # Simulate an edited layout with a changed cell.
    edited_layout = library.load_layout(rec.id)
    edited_layout["cells"][0][0] = "lava"
    edited_result = make_result(edited_layout)
    library.update_after_edit(edited_result, rec.id)

    # current layout changed
    assert library.load_layout(rec.id)["cells"][0][0] == "lava"
    # pristine generated copy unchanged
    assert library.load_layout(rec.id, generated=True)["cells"][0][0] == "stone_wall"


def test_revert_restores_generated(library):
    rec = library.save(make_result(make_layout(title="Revert Me")))
    edited = library.load_layout(rec.id)
    edited["cells"][0][0] = "lava"
    library.update_after_edit(make_result(edited), rec.id)

    generated = library.load_layout(rec.id, generated=True)
    library.revert(make_result(generated), rec.id)
    assert library.load_layout(rec.id)["cells"][0][0] == "stone_wall"


# ---------------------------------------------------------------------------
# delete — non-negotiable spec item: row AND directory removed
# ---------------------------------------------------------------------------


def test_delete_removes_row_and_directory(library):
    rec = library.save(make_result())
    d = library.map_dir(rec.id)
    assert os.path.isdir(d)

    library.delete(rec.id)

    assert not library.exists(rec.id)
    assert not os.path.exists(d), "map directory must be removed from disk"


def test_delete_unknown_raises(library):
    with pytest.raises(MapNotFound):
        library.delete("missing00000")


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


def test_stats(library):
    _save_sample(library)
    s = library.stats()
    assert s["map_count"] == 2
    assert s["total_bytes"] > 0
    assert s["by_biome"] == {"forest": 1, "dungeon": 1}


# ---------------------------------------------------------------------------
# backup export / import round-trip
# ---------------------------------------------------------------------------


def test_backup_export_import_roundtrip(library, tmp_path):
    forest, dungeon = _save_sample(library)
    archive = library.export_archive()
    assert archive[:2] == b"PK"  # zip magic

    # Import into a brand-new empty library.
    dest = Library(str(tmp_path / "restored"))
    imported = dest.import_archive(archive)
    assert imported == 2

    got_titles = sorted(m["title"] for m in dest.list()["maps"])
    assert got_titles == ["Dark Crypt", "Forest Ambush"]
    # files came across
    for m in dest.list()["maps"]:
        d = dest.map_dir(m["id"])
        assert os.path.isfile(os.path.join(d, FILE_GRIDLESS))


def test_import_regenerates_ids_on_collision(library):
    _save_sample(library)
    archive = library.export_archive()
    before = library.list()["total_count"]

    # Importing back into the SAME library must not clobber; ids regenerate.
    imported = library.import_archive(archive)
    assert imported == 2
    after = library.list()["total_count"]
    assert after == before + 2

    # No id appears twice and every dir exists.
    ids = [m["id"] for m in library.list()["maps"]]
    assert len(ids) == len(set(ids))
    for i in ids:
        assert os.path.isdir(library.map_dir(i))
