"""
test_server.py
===============
Tests for the topical guide's FastAPI backend, covering topics, entries
(passages), search, the chapter panel, and the JSON export.

Every test runs against a fresh temp-file guide.db, monkeypatched in per
test, so nothing here can touch the real topical-guide/guide.db.
"""

import json
import os
import sqlite3
import sys
import tempfile

import pytest

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _MODULE_DIR)

_FTS_DB_PATH = os.path.normpath(
    os.path.join(_MODULE_DIR, "..", "scriptures", "scriptures_fts.db")
)
if not os.path.exists(_FTS_DB_PATH):
    pytest.skip(
        "scriptures/scriptures_fts.db is missing — run "
        "`python scriptures/build_fts_db.py` first.",
        allow_module_level=True,
    )

# Point the DB paths at a throwaway bootstrap directory *before* the first
# `import server` below, so the module-level init_guide_db() call that runs
# at import time can never touch the real topical-guide/guide.db.
_BOOTSTRAP_DIR = tempfile.mkdtemp(prefix="topical_guide_test_bootstrap_")
os.environ["GUIDE_DB_PATH"] = os.path.join(_BOOTSTRAP_DIR, "guide.db")
os.environ["GUIDE_EXPORT_PATH"] = os.path.join(_BOOTSTRAP_DIR, "guide_export.json")
os.environ["AI_LOG_DB_PATH"] = os.path.join(_BOOTSTRAP_DIR, "ai_log.db")

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402


def _find_test_verse() -> sqlite3.Row:
    conn = sqlite3.connect(f"file:{_FTS_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, book, chapter, verse, text FROM v_verses "
            "WHERE book = 'Genesis' AND chapter = 1 AND verse = 10"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "expected test fixture verse Genesis 1:10 to exist"
    return row


_TEST_VERSE = _find_test_verse()


def _verse_id_for(book: str, chapter: int, verse: int) -> int:
    conn = sqlite3.connect(f"file:{_FTS_DB_PATH}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT id FROM v_verses WHERE book = ? AND chapter = ? AND verse = ?",
            (book, chapter, verse),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, f"expected {book} {chapter}:{verse} to exist"
    return row[0]


@pytest.fixture()
def paths(tmp_path, monkeypatch):
    guide_db_path = str(tmp_path / "guide.db")
    export_path = str(tmp_path / "guide_export.json")

    monkeypatch.setattr(server, "GUIDE_DB_PATH", guide_db_path)
    monkeypatch.setattr(server, "EXPORT_PATH", export_path)

    server.init_guide_db()

    return {"guide_db": guide_db_path, "export": export_path}


@pytest.fixture()
def client(paths):
    return TestClient(server.app)


def create_topic(client, name="Prayer", description=""):
    resp = client.post("/api/topics", json={"name": name, "description": description})
    assert resp.status_code == 201
    return resp.json()["id"]


def create_entry(
    client, topic_id, start_verse_id, end_verse_id=None, status="approved",
    source="manual", note=None,
):
    body = {
        "start_verse_id": start_verse_id,
        "end_verse_id": end_verse_id if end_verse_id is not None else start_verse_id,
        "status": status,
        "source": source,
    }
    if note is not None:
        body["note"] = note
    return client.post(f"/api/topics/{topic_id}/entries", json=body)


def topic_verses_count(paths, topic_id=None):
    conn = sqlite3.connect(paths["guide_db"])
    try:
        if topic_id is None:
            return conn.execute("SELECT COUNT(*) FROM topic_verses").fetchone()[0]
        return conn.execute(
            "SELECT COUNT(*) FROM topic_verses WHERE topic_id = ?", (topic_id,)
        ).fetchone()[0]
    finally:
        conn.close()


def topic_entries_count(paths, topic_id=None):
    conn = sqlite3.connect(paths["guide_db"])
    try:
        if topic_id is None:
            return conn.execute("SELECT COUNT(*) FROM topic_entries").fetchone()[0]
        return conn.execute(
            "SELECT COUNT(*) FROM topic_entries WHERE topic_id = ?", (topic_id,)
        ).fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. PATCH renaming a topic leaves topic_verses untouched
# ---------------------------------------------------------------------------


def test_patch_rename_leaves_topic_verses_untouched(client, paths):
    topic_id = create_topic(client, "Prayer", "General pattern of prayer.")
    create_entry(client, topic_id, _TEST_VERSE["id"])
    before = topic_verses_count(paths, topic_id)

    resp = client.patch(f"/api/topics/{topic_id}", json={"name": "Prayerfulness"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Prayerfulness"

    assert topic_verses_count(paths, topic_id) == before


# ---------------------------------------------------------------------------
# 2. PATCH to a taken name -> 409, original row unchanged
# ---------------------------------------------------------------------------


def test_patch_to_taken_name_returns_409_and_leaves_row_unchanged(client):
    create_topic(client, "Prayer", "General pattern of prayer.")
    topic_id = create_topic(client, "Adversity", "Enduring hard things.")

    resp = client.patch(f"/api/topics/{topic_id}", json={"name": "Prayer"})
    assert resp.status_code == 409

    row = client.get(f"/api/topics/{topic_id}").json()
    assert row["name"] == "Adversity"
    assert row["description"] == "Enduring hard things."


# ---------------------------------------------------------------------------
# 3. PATCH with only one field leaves the other alone, both directions
# ---------------------------------------------------------------------------


def test_patch_with_only_description_leaves_name_alone(client):
    topic_id = create_topic(client, "Prayer", "General pattern of prayer.")
    resp = client.patch(
        f"/api/topics/{topic_id}", json={"description": "Asking God for things."}
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Prayer"
    assert resp.json()["description"] == "Asking God for things."


def test_patch_with_only_name_leaves_description_alone(client):
    topic_id = create_topic(client, "Prayer", "General pattern of prayer.")
    resp = client.patch(f"/api/topics/{topic_id}", json={"name": "Prayerfulness"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Prayerfulness"
    assert resp.json()["description"] == "General pattern of prayer."


# ---------------------------------------------------------------------------
# 4. DELETE cascades topic_verses/topic_entries and removes the topic from
#    the export
# ---------------------------------------------------------------------------


def test_delete_cascades_and_updates_export(client, paths):
    topic_id = create_topic(client, "Prayer", "General pattern of prayer.")
    create_entry(client, topic_id, _TEST_VERSE["id"])
    assert topic_verses_count(paths, topic_id) == 1
    assert topic_entries_count(paths, topic_id) == 1

    resp = client.delete(f"/api/topics/{topic_id}")
    assert resp.status_code == 204

    assert topic_verses_count(paths, topic_id) == 0
    assert topic_entries_count(paths, topic_id) == 0
    assert client.get(f"/api/topics/{topic_id}").status_code == 404

    with open(paths["export"]) as f:
        export = json.load(f)
    assert all(t["name"] != "Prayer" for t in export)


# ---------------------------------------------------------------------------
# 5. note_count counts notes on rejected entries too, excludes empty strings
# ---------------------------------------------------------------------------


def test_note_count_includes_rejected_and_excludes_empty(client):
    topic_id = create_topic(client, "Prayer")

    # An approved entry with a note.
    create_entry(
        client, topic_id, _TEST_VERSE["id"],
        note="the counsel comes before the doing",
    )

    conn = sqlite3.connect(f"file:{_FTS_DB_PATH}?mode=ro", uri=True)
    try:
        second_verse_id = conn.execute(
            "SELECT id FROM v_verses WHERE book = 'Genesis' AND chapter = 1 AND verse = 11"
        ).fetchone()[0]
        third_verse_id = conn.execute(
            "SELECT id FROM v_verses WHERE book = 'Genesis' AND chapter = 1 AND verse = 12"
        ).fetchone()[0]
    finally:
        conn.close()

    # A rejected entry with a note — should still count.
    create_entry(
        client, topic_id, second_verse_id, status="rejected",
        note="close, but not quite the right link",
    )
    # An approved entry with an empty note — should not count.
    create_entry(client, topic_id, third_verse_id)

    topic = client.get(f"/api/topics/{topic_id}").json()
    assert topic["note_count"] == 2
    assert topic["rejected_count"] == 1


# ---------------------------------------------------------------------------
# 6. DELETE /topics/{id}/entries/{entry_id} — carried over from round 3's
#    verse-level delete, now entry-level
# ---------------------------------------------------------------------------


def _second_test_verse() -> sqlite3.Row:
    conn = sqlite3.connect(f"file:{_FTS_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, book, chapter, verse, text FROM v_verses "
            "WHERE book = 'Genesis' AND chapter = 1 AND verse = 11"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "expected test fixture verse Genesis 1:11 to exist"
    return row


_SECOND_TEST_VERSE = _second_test_verse()


def test_delete_entry_removes_row_and_updates_export(client, paths):
    topic_id = create_topic(client, "Prayer")
    create_entry(client, topic_id, _TEST_VERSE["id"])
    entry2 = create_entry(client, topic_id, _SECOND_TEST_VERSE["id"]).json()
    assert topic_verses_count(paths, topic_id) == 2

    resp = client.delete(f"/api/topics/{topic_id}/entries/{entry2['entry_id']}")
    assert resp.status_code == 204

    assert topic_verses_count(paths, topic_id) == 1

    with open(paths["export"]) as f:
        export = json.load(f)
    prayer = next(t for t in export if t["name"] == "Prayer")
    refs = [v["reference"] for v in prayer["verses"]]
    assert refs == [
        f"{_TEST_VERSE['book']} {_TEST_VERSE['chapter']}:{_TEST_VERSE['verse']}"
    ]


def test_delete_entry_is_idempotent(client):
    topic_id = create_topic(client, "Prayer")
    entry = create_entry(client, topic_id, _TEST_VERSE["id"]).json()

    first = client.delete(f"/api/topics/{topic_id}/entries/{entry['entry_id']}")
    second = client.delete(f"/api/topics/{topic_id}/entries/{entry['entry_id']}")
    assert first.status_code == 204
    assert second.status_code == 204


def test_delete_entry_scoped_to_one_topic(client, paths):
    topic_a = create_topic(client, "Prayer")
    topic_b = create_topic(client, "Adversity")
    entry_a = create_entry(client, topic_a, _TEST_VERSE["id"]).json()
    create_entry(client, topic_b, _TEST_VERSE["id"])

    resp = client.delete(f"/api/topics/{topic_a}/entries/{entry_a['entry_id']}")
    assert resp.status_code == 204

    assert topic_verses_count(paths, topic_a) == 0
    assert topic_verses_count(paths, topic_b) == 1


def test_undo_round_trip_preserves_note_and_source(client):
    topic_id = create_topic(client, "Prayer")
    entry = create_entry(
        client, topic_id, _TEST_VERSE["id"], source="phrase",
        note="the counsel comes before the doing",
    ).json()

    resp = client.delete(f"/api/topics/{topic_id}/entries/{entry['entry_id']}")
    assert resp.status_code == 204

    create_entry(
        client, topic_id, _TEST_VERSE["id"], source="phrase",
        note="the counsel comes before the doing",
    )

    topic = client.get(f"/api/topics/{topic_id}").json()
    restored = next(e for e in topic["entries"] if e["verse_ids"] == [_TEST_VERSE["id"]])
    assert restored["note"] == "the counsel comes before the doing"
    assert restored["source"] == "phrase"


def test_delete_then_repost_without_note_yields_empty_note(client):
    topic_id = create_topic(client, "Prayer")
    entry = create_entry(
        client, topic_id, _TEST_VERSE["id"],
        note="the counsel comes before the doing",
    ).json()

    resp = client.delete(f"/api/topics/{topic_id}/entries/{entry['entry_id']}")
    assert resp.status_code == 204

    create_entry(client, topic_id, _TEST_VERSE["id"])

    topic = client.get(f"/api/topics/{topic_id}").json()
    restored = next(e for e in topic["entries"] if e["verse_ids"] == [_TEST_VERSE["id"]])
    assert restored["note"] == ""


# ---------------------------------------------------------------------------
# 7. Volume summary and filter — round 4, now over entries
# ---------------------------------------------------------------------------

_CANONICAL_VOLUMES = [
    "Old Testament",
    "New Testament",
    "Book of Mormon",
    "Doctrine and Covenants",
    "Pearl of Great Price",
]


def test_topic_volume_counts_five_entries_one_nonzero(client):
    topic_id = create_topic(client, "Prayer")
    create_entry(client, topic_id, _TEST_VERSE["id"])

    topic = client.get(f"/api/topics/{topic_id}").json()
    counts = topic["volume_counts"]
    assert [c["volume"] for c in counts] == _CANONICAL_VOLUMES
    assert [c["volume_id"] for c in counts] == [1, 2, 3, 4, 5]
    assert [c["count"] for c in counts] == [1, 0, 0, 0, 0]


def test_topic_volume_counts_five_zeros_when_no_verses(client):
    topic_id = create_topic(client, "Prayer")

    topic = client.get(f"/api/topics/{topic_id}").json()
    counts = topic["volume_counts"]
    assert [c["volume"] for c in counts] == _CANONICAL_VOLUMES
    assert [c["count"] for c in counts] == [0, 0, 0, 0, 0]


def test_topic_volume_counts_excludes_rejected(client):
    topic_id = create_topic(client, "Prayer")
    create_entry(client, topic_id, _TEST_VERSE["id"], status="rejected")

    topic = client.get(f"/api/topics/{topic_id}").json()
    counts = topic["volume_counts"]
    assert [c["count"] for c in counts] == [0, 0, 0, 0, 0]


def test_topic_entries_carry_volume_fields(client):
    topic_id = create_topic(client, "Prayer")
    create_entry(client, topic_id, _TEST_VERSE["id"])

    topic = client.get(f"/api/topics/{topic_id}").json()
    entry = topic["entries"][0]
    assert entry["volume"] == "Old Testament"
    assert entry["volume_id"] == 1


def test_search_volume_counts_sum_to_total(client):
    resp = client.get("/api/search", params={"q": "money", "mode": "prefix"})
    assert resp.status_code == 200
    data = resp.json()
    counts = data["volume_counts"]
    assert [c["volume"] for c in counts] == _CANONICAL_VOLUMES
    assert sum(c["count"] for c in counts) == data["total"]
    by_volume = {c["volume"]: c["count"] for c in counts}
    assert by_volume == {
        "Old Testament": 101,
        "New Testament": 24,
        "Book of Mormon": 13,
        "Doctrine and Covenants": 38,
        "Pearl of Great Price": 1,
    }
    assert data["total"] == 177


def test_search_volume_id_filters_results_and_total(client):
    resp = client.get(
        "/api/search", params={"q": "money", "mode": "prefix", "volume_id": 1}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 101
    assert len(data["results"]) <= 50
    # Every returned result must actually belong to volume 1 (Old Testament).
    conn = sqlite3.connect(f"file:{_FTS_DB_PATH}?mode=ro", uri=True)
    try:
        for r in data["results"]:
            volume_id = conn.execute(
                "SELECT volume_id FROM v_verses WHERE id = ?", (r["verse_id"],)
            ).fetchone()[0]
            assert volume_id == 1
    finally:
        conn.close()


def test_search_results_in_canonical_order(client):
    # Verse IDs are sequential by volume, book, chapter, verse, so ascending
    # verse_id *is* canonical scripture order.
    for params in (
        {"q": "money", "mode": "prefix"},
        {"q": "money", "mode": "prefix", "volume_id": 3},
    ):
        data = client.get("/api/search", params=params).json()
        ids = [r["verse_id"] for r in data["results"]]
        assert len(ids) > 1
        assert ids == sorted(ids), params


def test_search_limit_truncates_from_front_of_canon(client):
    # The limit applies to canonical order, not relevance: a broad query fills
    # the page from the earliest volume and stops. The volume filter is the
    # only way to reach later volumes.
    data = client.get(
        "/api/search", params={"q": "money", "mode": "prefix", "limit": 5}
    ).json()
    assert data["total"] == 177
    assert [r["reference"] for r in data["results"]] == [
        "Genesis 17:12",
        "Genesis 17:13",
        "Genesis 17:23",
        "Genesis 17:27",
        "Genesis 23:9",
    ]


def test_search_volume_counts_identical_with_and_without_volume_id(client):
    unfiltered = client.get("/api/search", params={"q": "money", "mode": "prefix"}).json()
    filtered = client.get(
        "/api/search", params={"q": "money", "mode": "prefix", "volume_id": 3}
    ).json()
    assert filtered["volume_counts"] == unfiltered["volume_counts"]


def test_search_unknown_volume_id_404(client):
    resp = client.get(
        "/api/search", params={"q": "money", "mode": "prefix", "volume_id": 999}
    )
    assert resp.status_code == 404


def test_search_volume_id_with_topic_id_status_in_topic(client):
    topic_id = create_topic(client, "Prayer")
    # Genesis 17:12 — an Old Testament verse matching "money"*.
    conn = sqlite3.connect(f"file:{_FTS_DB_PATH}?mode=ro", uri=True)
    try:
        ot_money_verse_id = conn.execute(
            "SELECT id FROM v_verses WHERE book = 'Genesis' AND chapter = 17 AND verse = 12"
        ).fetchone()[0]
    finally:
        conn.close()
    create_entry(client, topic_id, ot_money_verse_id)

    resp = client.get(
        "/api/search",
        params={
            "q": "money",
            "mode": "prefix",
            "volume_id": 1,
            "topic_id": topic_id,
            "limit": 500,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    result = next(r for r in data["results"] if r["verse_id"] == ot_money_verse_id)
    assert result["status_in_topic"] == "approved"
    # A singleton entry's reference is identical to the verse's own — not
    # worth surfacing separately.
    assert result["entry_reference"] is None


# ---------------------------------------------------------------------------
# GET /api/chapter
# ---------------------------------------------------------------------------


def test_get_chapter_returns_whole_chapter_in_order(client):
    verse_id = _verse_id_for("Jacob", 2, 18)
    resp = client.get("/api/chapter", params={"verse_id": verse_id})
    assert resp.status_code == 200
    data = resp.json()
    assert data["reference"] == "Jacob 2"
    assert data["verse"] == 18
    assert data["verse_id"] == verse_id
    verses = data["verses"]
    assert [v["verse"] for v in verses] == sorted(v["verse"] for v in verses)
    assert verses[0]["verse"] == 1


def test_get_chapter_verse_count_matches_real_chapter(client):
    verse_id = _verse_id_for("Jacob", 2, 18)
    conn = sqlite3.connect(f"file:{_FTS_DB_PATH}?mode=ro", uri=True)
    try:
        expected = conn.execute(
            "SELECT COUNT(*) FROM v_verses WHERE book = 'Jacob' AND chapter = 2"
        ).fetchone()[0]
    finally:
        conn.close()

    resp = client.get("/api/chapter", params={"verse_id": verse_id})
    assert len(resp.json()["verses"]) == expected


def test_get_chapter_external_url_book_of_mormon(client):
    verse_id = _verse_id_for("Jacob", 2, 18)
    resp = client.get("/api/chapter", params={"verse_id": verse_id})
    assert resp.json()["external_url"] == (
        "https://www.churchofjesuschrist.org/study/scriptures/bofm/jacob/2"
        "?lang=eng&id=p18#p18"
    )


def test_get_chapter_external_url_doctrine_and_covenants(client):
    verse_id = _verse_id_for("Doctrine and Covenants", 4, 2)
    resp = client.get("/api/chapter", params={"verse_id": verse_id})
    assert resp.json()["external_url"] == (
        "https://www.churchofjesuschrist.org/study/scriptures/dc-testament/dc/4"
        "?lang=eng&id=p2#p2"
    )


def test_get_chapter_external_url_joseph_smith_history(client):
    verse_id = _verse_id_for("Joseph Smith--History", 1, 17)
    resp = client.get("/api/chapter", params={"verse_id": verse_id})
    assert resp.json()["external_url"] == (
        "https://www.churchofjesuschrist.org/study/scriptures/pgp/js-h/1"
        "?lang=eng&id=p17#p17"
    )


def test_get_chapter_unknown_verse_id_404(client):
    resp = client.get("/api/chapter", params={"verse_id": 999999})
    assert resp.status_code == 404


def test_get_chapter_missing_verse_id_422(client):
    resp = client.get("/api/chapter")
    assert resp.status_code == 422


def test_get_chapter_marks_curated_verses_with_entry_id(client):
    topic_id = create_topic(client, "Prayer")
    v15 = _verse_id_for("3 Nephi", 18, 15)
    v16 = _verse_id_for("3 Nephi", 18, 16)
    v18 = _verse_id_for("3 Nephi", 18, 18)
    entry = create_entry(client, topic_id, v15, v16).json()
    create_entry(client, topic_id, v18, v18, status="rejected")

    resp = client.get("/api/chapter", params={"verse_id": v15, "topic_id": topic_id})
    assert resp.status_code == 200
    by_id = {v["verse_id"]: v["entry_id"] for v in resp.json()["verses"]}
    assert by_id[v15] == entry["entry_id"]
    assert by_id[v16] == entry["entry_id"]
    # A rejected singleton is a tombstone, not something "in the topic" —
    # the panel doesn't tint it.
    assert by_id[v18] is None
    v17 = _verse_id_for("3 Nephi", 18, 17)
    assert by_id[v17] is None


def test_get_chapter_without_topic_id_omits_entry_id(client):
    verse_id = _verse_id_for("Jacob", 2, 18)
    resp = client.get("/api/chapter", params={"verse_id": verse_id})
    assert all(v["entry_id"] is None for v in resp.json()["verses"])


def test_books_and_volumes_all_have_lds_url_slugs():
    conn = sqlite3.connect(f"file:{_FTS_DB_PATH}?mode=ro", uri=True)
    try:
        missing_volumes = conn.execute(
            "SELECT COUNT(*) FROM volumes WHERE lds_url IS NULL OR lds_url = ''"
        ).fetchone()[0]
        missing_books = conn.execute(
            "SELECT COUNT(*) FROM books WHERE lds_url IS NULL OR lds_url = ''"
        ).fetchone()[0]
        volume_count = conn.execute("SELECT COUNT(*) FROM volumes").fetchone()[0]
        book_count = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    finally:
        conn.close()
    assert missing_volumes == 0
    assert missing_books == 0
    assert volume_count == 5
    assert book_count == 87


# ---------------------------------------------------------------------------
# 8. Migration — old shape to entries (round 6)
# ---------------------------------------------------------------------------


def test_migration_preserves_status_note_and_source(tmp_path, monkeypatch):
    guide_db_path = str(tmp_path / "old_guide.db")
    export_path = str(tmp_path / "old_guide_export.json")
    monkeypatch.setattr(server, "GUIDE_DB_PATH", guide_db_path)
    monkeypatch.setattr(server, "EXPORT_PATH", export_path)

    # Build an old-shape database by hand — the pre-round-6 schema.
    conn = sqlite3.connect(guide_db_path)
    conn.executescript(
        """
        CREATE TABLE topics (
            id          INTEGER PRIMARY KEY,
            name        TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE topic_verses (
            topic_id  INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
            verse_id  INTEGER NOT NULL,
            status    TEXT NOT NULL CHECK (status IN ('approved', 'rejected')),
            note      TEXT NOT NULL DEFAULT '',
            source    TEXT NOT NULL CHECK (source IN ('exact','prefix','phrase','semantic','manual')),
            added_at  TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (topic_id, verse_id)
        );
        """
    )
    conn.execute("INSERT INTO topics (id, name) VALUES (1, 'Prayer')")
    conn.execute(
        """INSERT INTO topic_verses (topic_id, verse_id, status, note, source)
           VALUES (1, ?, 'approved', 'a note on the approved row', 'manual')""",
        (_TEST_VERSE["id"],),
    )
    conn.execute(
        """INSERT INTO topic_verses (topic_id, verse_id, status, note, source)
           VALUES (1, ?, 'rejected', 'a note on the rejected row', 'prefix')""",
        (_SECOND_TEST_VERSE["id"],),
    )
    conn.commit()
    conn.close()

    server.init_guide_db()

    conn = sqlite3.connect(guide_db_path)
    conn.row_factory = sqlite3.Row
    entries = conn.execute(
        "SELECT status, note, source FROM topic_entries ORDER BY id"
    ).fetchall()
    assert len(entries) == 2
    assert {(e["status"], e["note"], e["source"]) for e in entries} == {
        ("approved", "a note on the approved row", "manual"),
        ("rejected", "a note on the rejected row", "prefix"),
    }
    link_count = conn.execute("SELECT COUNT(*) FROM topic_verses").fetchone()[0]
    assert link_count == 2
    assert server.needs_entry_migration(conn) is False
    conn.close()


# ---------------------------------------------------------------------------
# 9. expand_range edge cases (round 6)
# ---------------------------------------------------------------------------


def test_create_entry_cross_chapter_returns_400(client):
    topic_id = create_topic(client, "Prayer")
    v_end_of_18 = _verse_id_for("3 Nephi", 18, 39)
    v_start_of_19 = _verse_id_for("3 Nephi", 19, 1)
    resp = create_entry(client, topic_id, v_end_of_18, v_start_of_19)
    assert resp.status_code == 400


def test_create_entry_reversed_ends_normalizes(client):
    topic_id = create_topic(client, "Prayer")
    v15 = _verse_id_for("3 Nephi", 18, 15)
    v16 = _verse_id_for("3 Nephi", 18, 16)
    resp = create_entry(client, topic_id, v16, v15)
    assert resp.status_code == 201
    assert resp.json()["verse_ids"] == [v15, v16]


def test_create_entry_nonexistent_verse_returns_400(client):
    topic_id = create_topic(client, "Prayer")
    resp = create_entry(client, topic_id, 99999999, 99999999)
    assert resp.status_code == 400


def test_create_entry_over_max_passage_verses_returns_422(client):
    topic_id = create_topic(client, "Prayer")
    conn = sqlite3.connect(f"file:{_FTS_DB_PATH}?mode=ro", uri=True)
    try:
        ids = [
            r[0] for r in conn.execute(
                "SELECT id FROM v_verses WHERE book = 'Psalms' AND chapter = 119 "
                "ORDER BY verse LIMIT ?",
                (server.MAX_PASSAGE_VERSES + 1,),
            )
        ]
    finally:
        conn.close()
    assert len(ids) == server.MAX_PASSAGE_VERSES + 1
    resp = create_entry(client, topic_id, ids[0], ids[-1])
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 10. Absorption / merge (round 6)
# ---------------------------------------------------------------------------


def test_overlapping_add_merges_into_one_entry_with_joined_notes(client):
    topic_id = create_topic(client, "Prayer")
    v15 = _verse_id_for("3 Nephi", 18, 15)
    v16 = _verse_id_for("3 Nephi", 18, 16)
    v17 = _verse_id_for("3 Nephi", 18, 17)
    v18 = _verse_id_for("3 Nephi", 18, 18)

    first = create_entry(client, topic_id, v15, v16, note="note A").json()
    assert first["reference"] == "3 Nephi 18:15–16"

    second = create_entry(client, topic_id, v16, v18, note="note B").json()
    assert second["verse_ids"] == [v15, v16, v17, v18]
    assert second["reference"] == "3 Nephi 18:15–18"
    assert second["note"] == "note A\n\nnote B"

    topic = client.get(f"/api/topics/{topic_id}").json()
    assert topic["passage_count"] == 1
    assert topic["verse_count"] == 4
    assert [e["entry_id"] for e in topic["entries"]] == [second["entry_id"]]


def test_adjacent_add_does_not_merge(client):
    topic_id = create_topic(client, "Prayer")
    v15 = _verse_id_for("3 Nephi", 18, 15)
    v16 = _verse_id_for("3 Nephi", 18, 16)
    v17 = _verse_id_for("3 Nephi", 18, 17)

    create_entry(client, topic_id, v15, v16)
    create_entry(client, topic_id, v17, v17)

    topic = client.get(f"/api/topics/{topic_id}").json()
    assert topic["passage_count"] == 2
    refs = sorted(e["reference"] for e in topic["entries"])
    assert refs == ["3 Nephi 18:15–16", "3 Nephi 18:17"]


def test_approved_range_absorbs_and_deletes_rejected_singleton(client, paths):
    topic_id = create_topic(client, "Prayer")
    v15 = _verse_id_for("3 Nephi", 18, 15)
    v16 = _verse_id_for("3 Nephi", 18, 16)

    create_entry(client, topic_id, v16, v16, status="rejected")
    assert topic_entries_count(paths, topic_id) == 1

    merged = create_entry(client, topic_id, v15, v16).json()
    # The overlapping rejected singleton at v16 is absorbed (deleted, not
    # merged in beyond the verse already in the requested range) — the
    # result is exactly the requested 15-16, approved, and it's the only
    # entry left in the topic (the rejected singleton didn't survive
    # alongside it under a reused id or otherwise).
    assert merged["verse_ids"] == [v15, v16]
    assert merged["status"] == "approved"
    assert topic_entries_count(paths, topic_id) == 1


def test_multi_verse_reject_request_returns_422(client):
    topic_id = create_topic(client, "Prayer")
    v15 = _verse_id_for("3 Nephi", 18, 15)
    v16 = _verse_id_for("3 Nephi", 18, 16)
    resp = create_entry(client, topic_id, v15, v16, status="rejected")
    assert resp.status_code == 422


def test_patch_reject_on_multi_verse_entry_returns_422(client):
    topic_id = create_topic(client, "Prayer")
    v15 = _verse_id_for("3 Nephi", 18, 15)
    v16 = _verse_id_for("3 Nephi", 18, 16)
    entry = create_entry(client, topic_id, v15, v16).json()

    resp = client.patch(
        f"/api/topics/{topic_id}/entries/{entry['entry_id']}", json={"status": "rejected"}
    )
    assert resp.status_code == 422


def test_patch_reject_on_singleton_entry_succeeds(client):
    topic_id = create_topic(client, "Prayer")
    entry = create_entry(client, topic_id, _TEST_VERSE["id"]).json()

    resp = client.patch(
        f"/api/topics/{topic_id}/entries/{entry['entry_id']}", json={"status": "rejected"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


def test_delete_entry_removes_all_verse_links(client, paths):
    topic_id = create_topic(client, "Prayer")
    v15 = _verse_id_for("3 Nephi", 18, 15)
    v16 = _verse_id_for("3 Nephi", 18, 16)
    entry = create_entry(client, topic_id, v15, v16).json()
    assert topic_verses_count(paths, topic_id) == 2

    resp = client.delete(f"/api/topics/{topic_id}/entries/{entry['entry_id']}")
    assert resp.status_code == 204
    assert topic_verses_count(paths, topic_id) == 0


# ---------------------------------------------------------------------------
# 11. GET /api/topics/{id} ordering and passage_count/verse_count (round 6)
# ---------------------------------------------------------------------------


def test_get_topic_entries_ordered_by_lowest_verse_id(client):
    topic_id = create_topic(client, "Prayer")
    later = create_entry(client, topic_id, _SECOND_TEST_VERSE["id"]).json()
    earlier = create_entry(client, topic_id, _TEST_VERSE["id"]).json()

    topic = client.get(f"/api/topics/{topic_id}").json()
    assert [e["entry_id"] for e in topic["entries"]] == [
        earlier["entry_id"], later["entry_id"]
    ]


def test_passage_count_and_verse_count_arithmetic(client):
    topic_id = create_topic(client, "Prayer")
    v15 = _verse_id_for("3 Nephi", 18, 15)
    v16 = _verse_id_for("3 Nephi", 18, 16)
    v23 = _verse_id_for("3 Nephi", 18, 23)

    create_entry(client, topic_id, v15, v16)  # a 2-verse passage
    create_entry(client, topic_id, v23, v23)  # a singleton
    create_entry(client, topic_id, _TEST_VERSE["id"], status="rejected")  # excluded

    topic = client.get(f"/api/topics/{topic_id}").json()
    assert topic["passage_count"] == 2
    assert topic["verse_count"] == 3


# ---------------------------------------------------------------------------
# 12. Export — hyphen dash, verse_count, deterministic order (round 6)
# ---------------------------------------------------------------------------


def test_export_uses_hyphen_dash_and_verse_count(client, paths):
    topic_id = create_topic(client, "Prayer")
    v15 = _verse_id_for("3 Nephi", 18, 15)
    v16 = _verse_id_for("3 Nephi", 18, 16)
    create_entry(client, topic_id, v15, v16)

    with open(paths["export"]) as f:
        export = json.load(f)
    prayer = next(t for t in export if t["name"] == "Prayer")
    entry = prayer["verses"][0]
    assert entry["reference"] == "3 Nephi 18:15-16"
    assert "–" not in entry["reference"]
    assert entry["verse_count"] == 2


def test_export_order_is_deterministic_by_lowest_verse_id(client, paths):
    topic_id = create_topic(client, "Prayer")
    create_entry(client, topic_id, _SECOND_TEST_VERSE["id"])
    create_entry(client, topic_id, _TEST_VERSE["id"])

    with open(paths["export"]) as f:
        export = json.load(f)
    prayer = next(t for t in export if t["name"] == "Prayer")
    refs = [v["reference"] for v in prayer["verses"]]
    assert refs == [
        f"{_TEST_VERSE['book']} {_TEST_VERSE['chapter']}:{_TEST_VERSE['verse']}",
        f"{_SECOND_TEST_VERSE['book']} {_SECOND_TEST_VERSE['chapter']}:{_SECOND_TEST_VERSE['verse']}",
    ]
