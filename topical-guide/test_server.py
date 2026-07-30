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
