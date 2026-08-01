"""
test_server.py
===============
Tests for PATCH/DELETE on topics and the note_count field on GET
/api/topics/{id} — none of these had tests before round 2. No AI mocking
needed; these endpoints never touch ai.py.

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


# ---------------------------------------------------------------------------
# 1. PATCH renaming a topic leaves topic_verses untouched
# ---------------------------------------------------------------------------


def test_patch_rename_leaves_topic_verses_untouched(client, paths):
    topic_id = create_topic(client, "Prayer", "General pattern of prayer.")
    client.post(
        f"/api/topics/{topic_id}/verses",
        json={"verse_id": _TEST_VERSE["id"], "status": "approved", "source": "manual"},
    )
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
# 4. DELETE cascades topic_verses and removes the topic from the export
# ---------------------------------------------------------------------------


def test_delete_cascades_and_updates_export(client, paths):
    topic_id = create_topic(client, "Prayer", "General pattern of prayer.")
    client.post(
        f"/api/topics/{topic_id}/verses",
        json={"verse_id": _TEST_VERSE["id"], "status": "approved", "source": "manual"},
    )
    assert topic_verses_count(paths, topic_id) == 1

    resp = client.delete(f"/api/topics/{topic_id}")
    assert resp.status_code == 204

    assert topic_verses_count(paths, topic_id) == 0
    assert client.get(f"/api/topics/{topic_id}").status_code == 404

    with open(paths["export"]) as f:
        export = json.load(f)
    assert all(t["name"] != "Prayer" for t in export)


# ---------------------------------------------------------------------------
# 5. note_count counts notes on rejected links too, excludes empty strings
# ---------------------------------------------------------------------------


def test_note_count_includes_rejected_and_excludes_empty(client):
    topic_id = create_topic(client, "Prayer")
    verse_ids = [_TEST_VERSE["id"]]

    # An approved link with a note.
    client.post(
        f"/api/topics/{topic_id}/verses",
        json={
            "verse_id": verse_ids[0],
            "status": "approved",
            "source": "manual",
            "note": "the counsel comes before the doing",
        },
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

    # A rejected link with a note — should still count.
    client.post(
        f"/api/topics/{topic_id}/verses",
        json={
            "verse_id": second_verse_id,
            "status": "rejected",
            "source": "manual",
            "note": "close, but not quite the right link",
        },
    )
    # An approved link with an empty note — should not count.
    client.post(
        f"/api/topics/{topic_id}/verses",
        json={"verse_id": third_verse_id, "status": "approved", "source": "manual"},
    )

    topic = client.get(f"/api/topics/{topic_id}").json()
    assert topic["note_count"] == 2
    assert topic["rejected_count"] == 1


# ---------------------------------------------------------------------------
# 6. DELETE /topics/{id}/verses/{verse_id} — round 3
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


def test_delete_verse_removes_row_and_updates_export(client, paths):
    topic_id = create_topic(client, "Prayer")
    client.post(
        f"/api/topics/{topic_id}/verses",
        json={"verse_id": _TEST_VERSE["id"], "status": "approved", "source": "manual"},
    )
    client.post(
        f"/api/topics/{topic_id}/verses",
        json={"verse_id": _SECOND_TEST_VERSE["id"], "status": "approved", "source": "manual"},
    )
    assert topic_verses_count(paths, topic_id) == 2

    resp = client.delete(f"/api/topics/{topic_id}/verses/{_TEST_VERSE['id']}")
    assert resp.status_code == 204

    assert topic_verses_count(paths, topic_id) == 1

    with open(paths["export"]) as f:
        export = json.load(f)
    prayer = next(t for t in export if t["name"] == "Prayer")
    refs = [v["reference"] for v in prayer["verses"]]
    assert refs == [
        f"{_SECOND_TEST_VERSE['book']} {_SECOND_TEST_VERSE['chapter']}:{_SECOND_TEST_VERSE['verse']}"
    ]


def test_delete_verse_is_idempotent(client):
    topic_id = create_topic(client, "Prayer")
    client.post(
        f"/api/topics/{topic_id}/verses",
        json={"verse_id": _TEST_VERSE["id"], "status": "approved", "source": "manual"},
    )

    first = client.delete(f"/api/topics/{topic_id}/verses/{_TEST_VERSE['id']}")
    second = client.delete(f"/api/topics/{topic_id}/verses/{_TEST_VERSE['id']}")
    assert first.status_code == 204
    assert second.status_code == 204


def test_delete_verse_scoped_to_one_topic(client, paths):
    topic_a = create_topic(client, "Prayer")
    topic_b = create_topic(client, "Adversity")
    for topic_id in (topic_a, topic_b):
        client.post(
            f"/api/topics/{topic_id}/verses",
            json={"verse_id": _TEST_VERSE["id"], "status": "approved", "source": "manual"},
        )

    resp = client.delete(f"/api/topics/{topic_a}/verses/{_TEST_VERSE['id']}")
    assert resp.status_code == 204

    assert topic_verses_count(paths, topic_a) == 0
    assert topic_verses_count(paths, topic_b) == 1


def test_undo_round_trip_preserves_note_and_source(client):
    topic_id = create_topic(client, "Prayer")
    client.post(
        f"/api/topics/{topic_id}/verses",
        json={
            "verse_id": _TEST_VERSE["id"],
            "status": "approved",
            "source": "phrase",
            "note": "the counsel comes before the doing",
        },
    )

    resp = client.delete(f"/api/topics/{topic_id}/verses/{_TEST_VERSE['id']}")
    assert resp.status_code == 204

    client.post(
        f"/api/topics/{topic_id}/verses",
        json={
            "verse_id": _TEST_VERSE["id"],
            "status": "approved",
            "source": "phrase",
            "note": "the counsel comes before the doing",
        },
    )

    topic = client.get(f"/api/topics/{topic_id}").json()
    verse = next(v for v in topic["verses"] if v["verse_id"] == _TEST_VERSE["id"])
    assert verse["note"] == "the counsel comes before the doing"
    assert verse["source"] == "phrase"


def test_delete_then_repost_without_note_yields_empty_note(client):
    topic_id = create_topic(client, "Prayer")
    client.post(
        f"/api/topics/{topic_id}/verses",
        json={
            "verse_id": _TEST_VERSE["id"],
            "status": "approved",
            "source": "manual",
            "note": "the counsel comes before the doing",
        },
    )

    resp = client.delete(f"/api/topics/{topic_id}/verses/{_TEST_VERSE['id']}")
    assert resp.status_code == 204

    client.post(
        f"/api/topics/{topic_id}/verses",
        json={"verse_id": _TEST_VERSE["id"], "status": "approved", "source": "manual"},
    )

    topic = client.get(f"/api/topics/{topic_id}").json()
    verse = next(v for v in topic["verses"] if v["verse_id"] == _TEST_VERSE["id"])
    assert verse["note"] == ""


# ---------------------------------------------------------------------------
# 7. Volume summary and filter — round 4
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
    client.post(
        f"/api/topics/{topic_id}/verses",
        json={"verse_id": _TEST_VERSE["id"], "status": "approved", "source": "manual"},
    )

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
    client.post(
        f"/api/topics/{topic_id}/verses",
        json={"verse_id": _TEST_VERSE["id"], "status": "rejected", "source": "manual"},
    )

    topic = client.get(f"/api/topics/{topic_id}").json()
    counts = topic["volume_counts"]
    assert [c["count"] for c in counts] == [0, 0, 0, 0, 0]


def test_topic_verses_carry_volume_fields(client):
    topic_id = create_topic(client, "Prayer")
    client.post(
        f"/api/topics/{topic_id}/verses",
        json={"verse_id": _TEST_VERSE["id"], "status": "approved", "source": "manual"},
    )

    topic = client.get(f"/api/topics/{topic_id}").json()
    verse = topic["verses"][0]
    assert verse["volume"] == "Old Testament"
    assert verse["volume_id"] == 1


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
    client.post(
        f"/api/topics/{topic_id}/verses",
        json={"verse_id": ot_money_verse_id, "status": "approved", "source": "manual"},
    )

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
