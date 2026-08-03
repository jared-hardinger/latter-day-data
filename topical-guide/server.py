#!/usr/bin/env python3
"""
server.py
=========
FastAPI backend for the personal topical guide. Serves the JSON API under
/api and the static vanilla-JS UI at /.

Curated data lives in guide.db (committed — it's the hand-made artifact),
referencing verses by their stable IDs in scriptures/scriptures.db. Full
verse text and full-text search both come from scriptures/scriptures_fts.db
(a superset copy of scriptures.db with an FTS5 index — see
scriptures/build_fts_db.py), opened read-only so this app can never mutate
the shared scripture data.

Run with:

    python server.py

which binds to 127.0.0.1:8000. Requires scriptures/scriptures_fts.db to
exist; build it first with `python scriptures/build_fts_db.py` if missing.
"""

import json
import os
import sqlite3
import sys
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import ai

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GUIDE_DB_PATH = os.environ.get("GUIDE_DB_PATH", os.path.join(BASE_DIR, "guide.db"))
EXPORT_PATH = os.environ.get(
    "GUIDE_EXPORT_PATH", os.path.join(BASE_DIR, "guide_export.json")
)
FTS_DB_PATH = os.environ.get(
    "FTS_DB_PATH",
    os.path.normpath(os.path.join(BASE_DIR, "..", "scriptures", "scriptures_fts.db")),
)
STATIC_DIR = os.path.join(BASE_DIR, "static")

VALID_STATUSES = ("approved", "rejected")
VALID_SOURCES = ("exact", "prefix", "phrase", "semantic", "manual")

# A guard against a fat-fingered selection swallowing a whole chapter, not a
# theological position.
MAX_PASSAGE_VERSES = 40

CHURCH_BASE_URL = "https://www.churchofjesuschrist.org/study/scriptures"


def external_url(volume_url: str, book_url: str, chapter: int, verse: int) -> str:
    """Deep link to one verse on churchofjesuschrist.org. The verse anchor is
    `p{verse}` both as the `id` query param (which the site scrolls to) and as
    the fragment."""
    return (
        f"{CHURCH_BASE_URL}/{volume_url}/{book_url}/{chapter}"
        f"?lang=eng&id=p{verse}#p{verse}"
    )

SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    notes       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS topic_entries (
    id        INTEGER PRIMARY KEY,
    topic_id  INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    status    TEXT NOT NULL CHECK (status IN ('approved', 'rejected')),
    note      TEXT NOT NULL DEFAULT '',
    source    TEXT NOT NULL CHECK (source IN ('exact','prefix','phrase','semantic','manual')),
    added_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS topic_verses (
    topic_id  INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    entry_id  INTEGER NOT NULL REFERENCES topic_entries(id) ON DELETE CASCADE,
    verse_id  INTEGER NOT NULL,
    PRIMARY KEY (topic_id, verse_id)
);

CREATE INDEX IF NOT EXISTS idx_topic_verses_entry ON topic_verses(entry_id);
CREATE INDEX IF NOT EXISTS idx_topic_entries_topic ON topic_entries(topic_id);
"""


def check_fts_db():
    if not os.path.exists(FTS_DB_PATH):
        print(
            "scriptures_fts.db not found. Build it first:\n"
            "    python scriptures/build_fts_db.py",
            file=sys.stderr,
        )
        sys.exit(1)


def needs_entry_migration(conn) -> bool:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(topic_verses)")}
    return bool(cols) and "status" in cols


def migrate_to_entries(conn):
    """Old shape: one topic_verses row per verse, carrying status/note/source.
    New shape: topic_entries holds those, topic_verses maps entries to verses.
    Every pre-existing row becomes a singleton entry — grouping is explicit, so
    the migration never infers a passage from verses that happen to adjoin."""
    conn.execute("PRAGMA foreign_keys = OFF")
    with conn:
        conn.execute("ALTER TABLE topic_verses RENAME TO topic_verses_old")
        conn.executescript(SCHEMA)
        for row in conn.execute(
            """SELECT topic_id, verse_id, status, note, source, added_at
               FROM topic_verses_old ORDER BY topic_id, verse_id"""
        ).fetchall():
            cur = conn.execute(
                """INSERT INTO topic_entries (topic_id, status, note, source, added_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (row["topic_id"], row["status"], row["note"], row["source"], row["added_at"]),
            )
            conn.execute(
                "INSERT INTO topic_verses (topic_id, entry_id, verse_id) VALUES (?, ?, ?)",
                (row["topic_id"], cur.lastrowid, row["verse_id"]),
            )
        conn.execute("DROP TABLE topic_verses_old")
    conn.execute("PRAGMA foreign_keys = ON")


def needs_notes_migration(conn) -> bool:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(topics)")}
    return bool(cols) and "notes" not in cols


def migrate_add_notes(conn):
    """`CREATE TABLE IF NOT EXISTS` never alters an existing table, so a
    guide.db created before round 7 does not pick up the notes column from
    SCHEMA. Add it explicitly. `DEFAULT ''` means every existing topic comes
    out with empty notes and no existing row is rewritten."""
    with conn:
        conn.execute("ALTER TABLE topics ADD COLUMN notes TEXT NOT NULL DEFAULT ''")


def init_guide_db():
    conn = sqlite3.connect(GUIDE_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if needs_entry_migration(conn):
        migrate_to_entries(conn)
    if needs_notes_migration(conn):
        migrate_add_notes(conn)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


check_fts_db()
init_guide_db()
ai.init_ai_log_db()

app = FastAPI(title="Topical Guide")


def get_guide_db():
    # check_same_thread=False: FastAPI dispatches sync dependencies and sync
    # endpoint functions to the threadpool independently, so the connection
    # opened here can be handed off to a different worker thread than the one
    # that runs the endpoint body. It's still only ever used sequentially
    # within a single request, never shared across concurrent requests.
    conn = sqlite3.connect(GUIDE_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def get_fts_db():
    conn = sqlite3.connect(
        f"file:{FTS_DB_PATH}?mode=ro", uri=True, check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def reference_for(fts_db: sqlite3.Connection, verse_id: int) -> Optional[str]:
    row = fts_db.execute(
        "SELECT book, chapter, verse FROM v_verses WHERE id = ?", (verse_id,)
    ).fetchone()
    if row is None:
        return None
    return f"{row['book']} {row['chapter']}:{row['verse']}"


def entry_reference(fts_db: sqlite3.Connection, verse_ids: list, dash="–") -> Optional[str]:
    """'3 Nephi 18:15' or '3 Nephi 18:15–16'. verse_ids must be one contiguous
    range within one chapter — the entry invariant guarantees it."""
    first = fts_db.execute(
        "SELECT book, chapter, verse FROM v_verses WHERE id = ?", (verse_ids[0],)
    ).fetchone()
    if first is None:
        return None
    base = f"{first['book']} {first['chapter']}:{first['verse']}"
    if len(verse_ids) == 1:
        return base
    last = fts_db.execute(
        "SELECT verse FROM v_verses WHERE id = ?", (verse_ids[-1],)
    ).fetchone()
    return f"{base}{dash}{last['verse']}"


def expand_range(fts_db: sqlite3.Connection, start_verse_id: int, end_verse_id: int) -> list:
    """Validate a requested range and return its verse ids in order."""
    if end_verse_id < start_verse_id:
        start_verse_id, end_verse_id = end_verse_id, start_verse_id
    rows = fts_db.execute(
        "SELECT id, book_id, chapter FROM v_verses WHERE id IN (?, ?)",
        (start_verse_id, end_verse_id),
    ).fetchall()
    if len(rows) < (1 if start_verse_id == end_verse_id else 2):
        raise HTTPException(400, "Verse does not exist")
    if rows[0]["book_id"] != rows[-1]["book_id"] or rows[0]["chapter"] != rows[-1]["chapter"]:
        raise HTTPException(400, "A passage must stay within one chapter")
    ids = [
        r["id"] for r in fts_db.execute(
            "SELECT id FROM v_verses WHERE id BETWEEN ? AND ? ORDER BY id",
            (start_verse_id, end_verse_id),
        )
    ]
    if len(ids) > MAX_PASSAGE_VERSES:
        raise HTTPException(422, f"A passage is limited to {MAX_PASSAGE_VERSES} verses")
    return ids


def _entry_verse_ids(guide_db: sqlite3.Connection, entry_id: int) -> list:
    return [
        r[0] for r in guide_db.execute(
            "SELECT verse_id FROM topic_verses WHERE entry_id = ? ORDER BY verse_id",
            (entry_id,),
        )
    ]


def entry_dict(guide_db: sqlite3.Connection, fts_db: sqlite3.Connection, entry_id: int) -> dict:
    entry = guide_db.execute(
        "SELECT id, status, note, source FROM topic_entries WHERE id = ?", (entry_id,)
    ).fetchone()
    verse_ids = _entry_verse_ids(guide_db, entry_id)
    verses = []
    volume = volume_id = None
    for vid in verse_ids:
        row = fts_db.execute(
            "SELECT volume, volume_id, verse, text FROM v_verses WHERE id = ?", (vid,)
        ).fetchone()
        verses.append({"verse_id": vid, "verse": row["verse"], "text": row["text"]})
        volume, volume_id = row["volume"], row["volume_id"]
    return {
        "entry_id": entry["id"],
        "reference": entry_reference(fts_db, verse_ids),
        "verse_ids": verse_ids,
        "verses": verses,
        "volume": volume,
        "volume_id": volume_id,
        "status": entry["status"],
        "source": entry["source"],
        "note": entry["note"],
    }


def write_export(guide_db: sqlite3.Connection):
    """Rewrite guide_export.json with deterministic ordering so git history
    shows readable diffs. Reference text (not full verse text) is looked up
    from scriptures_fts.db; the export is a curation record, not a scripture
    copy."""
    fts_db = sqlite3.connect(
        f"file:{FTS_DB_PATH}?mode=ro", uri=True, check_same_thread=False
    )
    fts_db.row_factory = sqlite3.Row
    try:
        topics = guide_db.execute(
            "SELECT id, name, description, notes FROM topics ORDER BY name"
        ).fetchall()
        export = []
        for t in topics:
            entries = guide_db.execute(
                """SELECT te.id AS entry_id, te.status, te.source, te.note,
                          (SELECT MIN(verse_id) FROM topic_verses WHERE entry_id = te.id) AS min_verse_id
                   FROM topic_entries te
                   WHERE te.topic_id = ?
                   ORDER BY min_verse_id""",
                (t["id"],),
            ).fetchall()
            verses = []
            for e in entries:
                verse_ids = _entry_verse_ids(guide_db, e["entry_id"])
                verses.append(
                    {
                        "reference": entry_reference(fts_db, verse_ids, dash="-"),
                        "verse_count": len(verse_ids),
                        "status": e["status"],
                        "source": e["source"],
                        "note": e["note"],
                    }
                )
            export.append(
                {
                    "name": t["name"],
                    "description": t["description"],
                    # A line array, not one long string: a multi-paragraph
                    # document JSON-encoded as a single value would diff as one
                    # unreadable whole-line change on every edit. The guard
                    # matters — "".split("\n") is [""], not [].
                    "notes": t["notes"].split("\n") if t["notes"] else [],
                    "verses": verses,
                }
            )
    finally:
        fts_db.close()
    with open(EXPORT_PATH, "w") as f:
        json.dump(export, f, indent=2)
        f.write("\n")


def normalize_notes(text: str) -> str:
    """Textareas can submit CRLF line endings, and a stray \\r on the end of
    every line would poison the line-array export in guide_export.json —
    invisible in the browser, ugly and diff-noisy in git. Trailing blank lines
    go for the same reason: they accumulate as empty array entries every time
    you press return before saving."""
    return text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")


# ---------------------------------------------------------------------------
# Topics
# ---------------------------------------------------------------------------


class TopicCreate(BaseModel):
    name: str
    description: str = ""


class TopicUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    # No length cap: a description is a blurb, a notes document is not.
    notes: Optional[str] = None


def topic_dict(row, approved_count: int) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "approved_count": approved_count,
    }


@app.get("/api/topics")
def list_topics(guide_db=Depends(get_guide_db)):
    rows = guide_db.execute(
        """
        SELECT t.id, t.name, t.description,
               COUNT(CASE WHEN te.status = 'approved' THEN 1 END) AS approved_count
        FROM topics t
        LEFT JOIN topic_entries te ON te.topic_id = t.id
        GROUP BY t.id
        ORDER BY t.name
        """
    ).fetchall()
    return [topic_dict(r, r["approved_count"]) for r in rows]


@app.post("/api/topics", status_code=201)
def create_topic(body: TopicCreate, guide_db=Depends(get_guide_db)):
    try:
        cur = guide_db.execute(
            "INSERT INTO topics (name, description) VALUES (?, ?)",
            (body.name, body.description),
        )
        guide_db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"Topic '{body.name}' already exists")
    write_export(guide_db)
    row = guide_db.execute(
        "SELECT id, name, description FROM topics WHERE id = ?", (cur.lastrowid,)
    ).fetchone()
    return topic_dict(row, 0)


@app.patch("/api/topics/{topic_id}")
def update_topic(topic_id: int, body: TopicUpdate, guide_db=Depends(get_guide_db)):
    existing = guide_db.execute(
        "SELECT * FROM topics WHERE id = ?", (topic_id,)
    ).fetchone()
    if existing is None:
        raise HTTPException(404, "Topic not found")
    name = body.name if body.name is not None else existing["name"]
    description = (
        body.description if body.description is not None else existing["description"]
    )
    # The `is not None` fallback is what stops the topic header's Edit form —
    # which sends name and description and no notes — from silently blanking a
    # whole document every time a topic is renamed.
    notes = (
        normalize_notes(body.notes) if body.notes is not None else existing["notes"]
    )
    try:
        guide_db.execute(
            "UPDATE topics SET name = ?, description = ?, notes = ? WHERE id = ?",
            (name, description, notes, topic_id),
        )
        guide_db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"Topic '{name}' already exists")
    write_export(guide_db)
    approved_count = guide_db.execute(
        "SELECT COUNT(*) FROM topic_entries WHERE topic_id = ? AND status = 'approved'",
        (topic_id,),
    ).fetchone()[0]
    row = guide_db.execute(
        "SELECT id, name, description FROM topics WHERE id = ?", (topic_id,)
    ).fetchone()
    return topic_dict(row, approved_count)


@app.delete("/api/topics/{topic_id}", status_code=204)
def delete_topic(topic_id: int, guide_db=Depends(get_guide_db)):
    existing = guide_db.execute(
        "SELECT id FROM topics WHERE id = ?", (topic_id,)
    ).fetchone()
    if existing is None:
        raise HTTPException(404, "Topic not found")
    guide_db.execute("DELETE FROM topics WHERE id = ?", (topic_id,))
    guide_db.commit()
    write_export(guide_db)
    return Response(status_code=204)


@app.get("/api/topics/{topic_id}")
def get_topic(
    topic_id: int, guide_db=Depends(get_guide_db), fts_db=Depends(get_fts_db)
):
    topic = guide_db.execute(
        "SELECT * FROM topics WHERE id = ?", (topic_id,)
    ).fetchone()
    if topic is None:
        raise HTTPException(404, "Topic not found")
    approved_entry_ids = [
        r[0] for r in guide_db.execute(
            """SELECT te.id
               FROM topic_entries te
               WHERE te.topic_id = ? AND te.status = 'approved'
               ORDER BY (SELECT MIN(verse_id) FROM topic_verses WHERE entry_id = te.id)""",
            (topic_id,),
        )
    ]
    rejected_count = guide_db.execute(
        "SELECT COUNT(*) FROM topic_entries WHERE topic_id = ? AND status = 'rejected'",
        (topic_id,),
    ).fetchone()[0]
    note_count = guide_db.execute(
        "SELECT COUNT(*) FROM topic_entries WHERE topic_id = ? AND note != ''",
        (topic_id,),
    ).fetchone()[0]
    volume_counts = {
        v["id"]: {"volume_id": v["id"], "volume": v["name"], "count": 0}
        for v in fts_db.execute("SELECT id, name FROM volumes ORDER BY id").fetchall()
    }
    entries = []
    verse_count = 0
    for entry_id in approved_entry_ids:
        entry = entry_dict(guide_db, fts_db, entry_id)
        entries.append(entry)
        verse_count += len(entry["verse_ids"])
        volume_counts[entry["volume_id"]]["count"] += 1
    return {
        "id": topic["id"],
        "name": topic["name"],
        "description": topic["description"],
        "notes": topic["notes"],
        "entries": entries,
        "volume_counts": list(volume_counts.values()),
        "passage_count": len(entries),
        "verse_count": verse_count,
        "rejected_count": rejected_count,
        "note_count": note_count,
    }


# ---------------------------------------------------------------------------
# Topic <-> verse links
# ---------------------------------------------------------------------------


class EntryCreate(BaseModel):
    start_verse_id: int
    end_verse_id: Optional[int] = None
    status: Literal["approved", "rejected"]
    source: Literal["exact", "prefix", "phrase", "semantic", "manual"]
    note: Optional[str] = None


class EntryUpdate(BaseModel):
    status: Optional[Literal["approved", "rejected"]] = None
    note: Optional[str] = None


@app.post("/api/topics/{topic_id}/entries", status_code=201)
def create_entry(
    topic_id: int,
    body: EntryCreate,
    guide_db=Depends(get_guide_db),
    fts_db=Depends(get_fts_db),
):
    topic = guide_db.execute(
        "SELECT id FROM topics WHERE id = ?", (topic_id,)
    ).fetchone()
    if topic is None:
        raise HTTPException(404, "Topic not found")

    end_verse_id = body.end_verse_id if body.end_verse_id is not None else body.start_verse_id
    requested_ids = expand_range(fts_db, body.start_verse_id, end_verse_id)

    placeholders = ",".join("?" * len(requested_ids))
    overlapping_entry_ids = [
        r[0] for r in guide_db.execute(
            f"""SELECT DISTINCT entry_id FROM topic_verses
                WHERE topic_id = ? AND verse_id IN ({placeholders})""",
            (topic_id, *requested_ids),
        )
    ]

    # Overlapping approved entries contribute all their verses — even where
    # they fall outside the requested range — so adding 16-18 over an
    # existing 15-16 produces one entry 15-18, not 16-18 plus an orphaned 15.
    # Overlapping rejected entries are always singletons (see the status
    # invariant on PATCH below) and contribute nothing beyond the verse
    # already in the requested range, so they are simply dropped.
    union_ids = set(requested_ids)
    absorbed_notes = []  # (min_verse_id, note) for ordering absorbed notes
    for entry_id in overlapping_entry_ids:
        entry = guide_db.execute(
            "SELECT status, note FROM topic_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        entry_verse_ids = _entry_verse_ids(guide_db, entry_id)
        if entry["status"] == "approved":
            union_ids.update(entry_verse_ids)
            if entry["note"]:
                absorbed_notes.append((entry_verse_ids[0], entry["note"]))

    final_ids = expand_range(fts_db, min(union_ids), max(union_ids))

    status = body.status
    if len(final_ids) > 1 and status == "rejected":
        raise HTTPException(422, "A passage of more than one verse must be approved.")

    seen_notes = set()
    notes_in_order = []
    for _, note in sorted(absorbed_notes, key=lambda pair: pair[0]):
        if note and note not in seen_notes:
            seen_notes.add(note)
            notes_in_order.append(note)
    if body.note:
        notes_in_order.append(body.note)
    merged_note = "\n\n".join(notes_in_order)

    for entry_id in overlapping_entry_ids:
        guide_db.execute("DELETE FROM topic_entries WHERE id = ?", (entry_id,))

    cur = guide_db.execute(
        "INSERT INTO topic_entries (topic_id, status, note, source) VALUES (?, ?, ?, ?)",
        (topic_id, status, merged_note, body.source),
    )
    new_entry_id = cur.lastrowid
    guide_db.executemany(
        "INSERT INTO topic_verses (topic_id, entry_id, verse_id) VALUES (?, ?, ?)",
        [(topic_id, new_entry_id, vid) for vid in final_ids],
    )
    guide_db.commit()
    write_export(guide_db)
    return entry_dict(guide_db, fts_db, new_entry_id)


@app.patch("/api/topics/{topic_id}/entries/{entry_id}")
def update_entry(
    topic_id: int,
    entry_id: int,
    body: EntryUpdate,
    guide_db=Depends(get_guide_db),
    fts_db=Depends(get_fts_db),
):
    existing = guide_db.execute(
        "SELECT * FROM topic_entries WHERE id = ? AND topic_id = ?", (entry_id, topic_id)
    ).fetchone()
    if existing is None:
        raise HTTPException(404, "Entry not found")
    status = body.status if body.status is not None else existing["status"]
    note = body.note if body.note is not None else existing["note"]
    if status == "rejected":
        verse_count = guide_db.execute(
            "SELECT COUNT(*) FROM topic_verses WHERE entry_id = ?", (entry_id,)
        ).fetchone()[0]
        if verse_count > 1:
            raise HTTPException(
                422, "A multi-verse passage can't be rejected; remove it instead."
            )
    guide_db.execute(
        "UPDATE topic_entries SET status = ?, note = ? WHERE id = ?",
        (status, note, entry_id),
    )
    guide_db.commit()
    write_export(guide_db)
    return entry_dict(guide_db, fts_db, entry_id)


@app.delete("/api/topics/{topic_id}/entries/{entry_id}", status_code=204)
def delete_entry(topic_id: int, entry_id: int, guide_db=Depends(get_guide_db)):
    guide_db.execute(
        "DELETE FROM topic_entries WHERE id = ? AND topic_id = ?", (entry_id, topic_id)
    )
    guide_db.commit()
    write_export(guide_db)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def build_match_query(q: str, mode: str) -> str:
    terms = q.split()
    if not terms:
        raise HTTPException(400, "Query must not be empty")

    def esc(t: str) -> str:
        return t.replace('"', '""')

    if mode == "phrase":
        return f'"{esc(q)}"'
    if mode == "exact":
        return " ".join(f'"{esc(t)}"' for t in terms)
    if mode == "prefix":
        return " ".join(f'"{esc(t)}"*' for t in terms)
    raise HTTPException(400, f"Unknown search mode: {mode}")


@app.get("/api/search")
def search(
    q: str,
    mode: Literal["prefix", "exact", "phrase"] = "prefix",
    topic_id: Optional[int] = None,
    volume_id: Optional[int] = None,
    limit: int = Query(50, ge=1, le=500),
    guide_db=Depends(get_guide_db),
    fts_db=Depends(get_fts_db),
):
    match_query = build_match_query(q, mode)

    if topic_id is not None:
        topic = guide_db.execute(
            "SELECT id FROM topics WHERE id = ?", (topic_id,)
        ).fetchone()
        if topic is None:
            raise HTTPException(404, "Topic not found")

    if volume_id is not None:
        volume = fts_db.execute(
            "SELECT id FROM volumes WHERE id = ?", (volume_id,)
        ).fetchone()
        if volume is None:
            raise HTTPException(404, "Volume not found")

    # Results come back in canonical scripture order, not BM25 rank: verse IDs
    # are sequential by volume, book, chapter, verse. Broad queries therefore
    # fill the limit from the front of the canon — the volume filter is how you
    # reach the rest.
    try:
        if volume_id is not None:
            rows = fts_db.execute(
                """
                SELECT f.rowid AS verse_id,
                       highlight(verses_fts, 0, '<mark>', '</mark>') AS highlighted
                FROM verses_fts f
                JOIN v_verses vv ON vv.id = f.rowid
                WHERE verses_fts MATCH ? AND vv.volume_id = ?
                ORDER BY f.rowid
                LIMIT ?
                """,
                (match_query, volume_id, limit),
            ).fetchall()
        else:
            rows = fts_db.execute(
                """
                SELECT f.rowid AS verse_id,
                       highlight(verses_fts, 0, '<mark>', '</mark>') AS highlighted
                FROM verses_fts f
                WHERE verses_fts MATCH ?
                ORDER BY f.rowid
                LIMIT ?
                """,
                (match_query, limit),
            ).fetchall()

        # MATCH must sit in a WHERE clause so FTS5 drives the query — the
        # equivalent LEFT JOIN ... ON verses_fts MATCH ? form is correct but
        # measured over 120s (vs 21ms here) on broad queries.
        volume_match_rows = fts_db.execute(
            """
            SELECT vv.volume_id, vv.volume, COUNT(*) AS count
            FROM verses_fts f JOIN v_verses vv ON vv.id = f.rowid
            WHERE verses_fts MATCH ?
            GROUP BY vv.volume_id ORDER BY vv.volume_id
            """,
            (match_query,),
        ).fetchall()
    except sqlite3.OperationalError:
        raise HTTPException(400, "Invalid search query")

    counts_by_volume = {r["volume_id"]: r["count"] for r in volume_match_rows}
    volume_counts = [
        {"volume_id": v["id"], "volume": v["name"], "count": counts_by_volume.get(v["id"], 0)}
        for v in fts_db.execute("SELECT id, name FROM volumes ORDER BY id").fetchall()
    ]
    total = counts_by_volume.get(volume_id, 0) if volume_id is not None else sum(counts_by_volume.values())

    status_map = {}
    entry_map = {}
    if topic_id is not None and rows:
        verse_ids = [r["verse_id"] for r in rows]
        placeholders = ",".join("?" * len(verse_ids))
        link_rows = guide_db.execute(
            f"""SELECT tv.verse_id, te.id AS entry_id, te.status
                FROM topic_verses tv JOIN topic_entries te ON te.id = tv.entry_id
                WHERE tv.topic_id = ? AND tv.verse_id IN ({placeholders})""",
            (topic_id, *verse_ids),
        ).fetchall()
        for lr in link_rows:
            status_map[lr["verse_id"]] = lr["status"]
            entry_map[lr["verse_id"]] = lr["entry_id"]

    results = []
    for row in rows:
        entry_id = entry_map.get(row["verse_id"])
        entry_ref = None
        if entry_id is not None:
            # Only worth surfacing for an actual passage — a singleton entry's
            # reference is identical to the verse's own, so the badge already
            # says everything it would.
            entry_verse_ids = _entry_verse_ids(guide_db, entry_id)
            if len(entry_verse_ids) > 1:
                entry_ref = entry_reference(fts_db, entry_verse_ids)
        results.append(
            {
                "verse_id": row["verse_id"],
                "reference": reference_for(fts_db, row["verse_id"]),
                "highlighted": row["highlighted"],
                "status_in_topic": status_map.get(row["verse_id"]),
                "entry_id": entry_id,
                "entry_reference": entry_ref,
            }
        )

    return {"total": total, "results": results, "volume_counts": volume_counts}


@app.get("/api/chapter")
def get_chapter(
    verse_id: int,
    topic_id: Optional[int] = None,
    guide_db=Depends(get_guide_db),
    fts_db=Depends(get_fts_db),
):
    subject = fts_db.execute(
        """SELECT book_id, book, chapter, verse, volume, volume_url, book_url
           FROM v_verses WHERE id = ?""",
        (verse_id,),
    ).fetchone()
    if subject is None:
        raise HTTPException(404, "Verse not found")
    rows = fts_db.execute(
        "SELECT id, verse, text FROM v_verses WHERE book_id = ? AND chapter = ? ORDER BY verse",
        (subject["book_id"], subject["chapter"]),
    ).fetchall()

    entry_by_verse = {}
    if topic_id is not None:
        chapter_verse_ids = [r["id"] for r in rows]
        placeholders = ",".join("?" * len(chapter_verse_ids))
        link_rows = guide_db.execute(
            f"""SELECT tv.verse_id, tv.entry_id
                FROM topic_verses tv JOIN topic_entries te ON te.id = tv.entry_id
                WHERE tv.topic_id = ? AND te.status = 'approved'
                AND tv.verse_id IN ({placeholders})""",
            (topic_id, *chapter_verse_ids),
        ).fetchall()
        entry_by_verse = {r["verse_id"]: r["entry_id"] for r in link_rows}

    return {
        "reference": f"{subject['book']} {subject['chapter']}",
        "book": subject["book"],
        "chapter": subject["chapter"],
        "volume": subject["volume"],
        "verse_id": verse_id,
        "verse": subject["verse"],
        "external_url": external_url(
            subject["volume_url"], subject["book_url"],
            subject["chapter"], subject["verse"],
        ),
        "verses": [
            {
                "verse_id": r["id"],
                "verse": r["verse"],
                "text": r["text"],
                "entry_id": entry_by_verse.get(r["id"]),
            }
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# AI writing helpers — fill only, never write to guide.db or trigger
# write_export. Both endpoints return unsaved values for the user to review
# and save through the existing endpoints above.
# ---------------------------------------------------------------------------


class TopicFillRequest(BaseModel):
    prompt: str


@app.post("/api/ai/topics/fill")
def fill_topic(body: TopicFillRequest, guide_db=Depends(get_guide_db)):
    feature = ai.FEATURES["fill_topic"]

    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(400, "Describe the topic you want.")
    if len(prompt) > feature.max_prompt_chars:
        raise HTTPException(422, "That description is too long.")

    topics = guide_db.execute(
        "SELECT name, description FROM topics ORDER BY name"
    ).fetchall()
    existing_by_casefold = {t["name"].casefold(): t["name"] for t in topics}

    if topics:
        lines = [
            f"- {t['name']}: {t['description'].strip() or '(no description)'}"
            for t in topics
        ]
        topic_block = "Existing topics:\n" + "\n".join(lines)
    else:
        topic_block = "Existing topics: none yet — this is the first one."

    parsed, _call_id = ai.call_claude(
        feature=feature.name,
        model=feature.model,
        prompt_hash=feature.prompt_hash(),
        system_text=feature.system_text(topic_block),
        user_text="The topic to create:\n" + prompt,
        output_format=ai._TopicFill,
        max_tokens=feature.max_tokens,
    )
    if parsed is None:
        raise HTTPException(502, ai.AI_SERVICE_ERROR)

    name = parsed.name.strip()
    if not name:
        raise HTTPException(502, ai.AI_SERVICE_ERROR)
    description = parsed.description.strip()[:400]
    duplicate_of = None
    if parsed.duplicate_of:
        duplicate_of = existing_by_casefold.get(parsed.duplicate_of.strip().casefold())

    return {
        "name": name,
        "description": description,
        "duplicate_of": duplicate_of,
        "reason": parsed.reason,
    }


class NoteFillRequest(BaseModel):
    prompt: Optional[str] = None


@app.post("/api/ai/topics/{topic_id}/entries/{entry_id}/note/fill")
def fill_note(
    topic_id: int,
    entry_id: int,
    body: NoteFillRequest,
    guide_db=Depends(get_guide_db),
    fts_db=Depends(get_fts_db),
):
    feature = ai.FEATURES["fill_note"]

    topic = guide_db.execute(
        "SELECT id, name, description FROM topics WHERE id = ?", (topic_id,)
    ).fetchone()
    if topic is None:
        raise HTTPException(404, "Topic not found")

    entry = guide_db.execute(
        "SELECT id FROM topic_entries WHERE id = ? AND topic_id = ?", (entry_id, topic_id)
    ).fetchone()
    if entry is None:
        raise HTTPException(400, f"Entry {entry_id} does not exist")

    entry_verse_ids = _entry_verse_ids(guide_db, entry_id)
    first_verse = fts_db.execute(
        "SELECT book, chapter, verse FROM v_verses WHERE id = ?", (entry_verse_ids[0],)
    ).fetchone()
    last_verse = fts_db.execute(
        "SELECT verse FROM v_verses WHERE id = ?", (entry_verse_ids[-1],)
    ).fetchone()

    prompt = (body.prompt or "").strip()
    if len(prompt) > feature.max_prompt_chars:
        raise HTTPException(422, "That note is too long.")

    neighbours = fts_db.execute(
        """SELECT verse, text FROM v_verses
           WHERE book = ? AND chapter = ? AND verse BETWEEN ? AND ?
           ORDER BY verse""",
        (
            first_verse["book"], first_verse["chapter"],
            first_verse["verse"] - 2, last_verse["verse"] + 2,
        ),
    ).fetchall()

    passage_lines = []
    for n in neighbours:
        marker = ">>" if first_verse["verse"] <= n["verse"] <= last_verse["verse"] else "  "
        passage_lines.append(
            f"{marker} {first_verse['book']} {first_verse['chapter']}:{n['verse']}  {n['text']}"
        )
    passage_block = "Passage (the subject verses are marked >>):\n" + "\n".join(
        passage_lines
    )

    context_block = (
        f"Topic: {topic['name']}\n"
        f"Topic description: {topic['description']}\n\n"
        f"{passage_block}"
    )

    if prompt:
        user_text = "The curator's own words for this note:\n" + prompt
    else:
        user_text = "The curator supplied no words. Draft the note."

    parsed, _call_id = ai.call_claude(
        feature=feature.name,
        model=feature.model,
        prompt_hash=feature.prompt_hash(),
        system_text=feature.system_text(context_block),
        user_text=user_text,
        output_format=ai._NoteFill,
        max_tokens=feature.max_tokens,
    )
    if parsed is None:
        raise HTTPException(502, ai.AI_SERVICE_ERROR)

    note = parsed.note.strip()[:300]
    if not note:
        raise HTTPException(502, ai.AI_SERVICE_ERROR)

    return {"note": note, "reason": parsed.reason}


class DescriptionPolishRequest(BaseModel):
    prompt: Optional[str] = None


@app.post("/api/ai/topics/{topic_id}/description/polish")
def polish_description(
    topic_id: int,
    body: DescriptionPolishRequest,
    guide_db=Depends(get_guide_db),
    fts_db=Depends(get_fts_db),
):
    feature = ai.FEATURES["polish_description"]

    topic = guide_db.execute(
        "SELECT id, name, description FROM topics WHERE id = ?", (topic_id,)
    ).fetchone()
    if topic is None:
        raise HTTPException(404, "Topic not found")

    prompt = (body.prompt or "").strip()
    if len(prompt) > feature.max_prompt_chars:
        raise HTTPException(422, "That's too long.")

    other_topics = guide_db.execute(
        "SELECT name, description FROM topics WHERE id != ? ORDER BY name",
        (topic_id,),
    ).fetchall()
    if other_topics:
        lines = [
            f"- {t['name']}: {t['description'].strip() or '(no description)'}"
            for t in other_topics
        ]
        other_block = "Other topics:\n" + "\n".join(lines)
    else:
        other_block = "Other topics: none — this is the only topic in the guide."

    desc_line = topic["description"].strip() or "(no description yet)"
    this_block = f"Topic name: {topic['name']}\nCurrent description: {desc_line}"

    approved_count = guide_db.execute(
        "SELECT COUNT(*) FROM topic_entries WHERE topic_id = ? AND status = 'approved'",
        (topic_id,),
    ).fetchone()[0]
    entries = guide_db.execute(
        """SELECT te.id AS entry_id, te.note
           FROM topic_entries te
           WHERE te.topic_id = ? AND te.status = 'approved'
           ORDER BY (SELECT MIN(verse_id) FROM topic_verses WHERE entry_id = te.id)
           LIMIT 40""",
        (topic_id,),
    ).fetchall()
    if entries:
        lines = []
        for entry in entries:
            entry_verse_ids = _entry_verse_ids(guide_db, entry["entry_id"])
            texts = [
                fts_db.execute(
                    "SELECT text FROM v_verses WHERE id = ?", (vid,)
                ).fetchone()["text"]
                for vid in entry_verse_ids
            ]
            ref = entry_reference(fts_db, entry_verse_ids)
            lines.append(f"{ref}  {' '.join(texts)}")
            if entry["note"]:
                lines.append(f"  note: {entry['note']}")
        verses_block = (
            f"Approved verses in this topic ({len(entries)} of {approved_count}):\n"
            + "\n".join(lines)
        )
        if approved_count > len(entries):
            verses_block += (
                f"\n… and {approved_count - len(entries)} more approved verses not shown."
            )
    else:
        verses_block = "Approved verses in this topic: none yet."

    if prompt:
        user_text = "The curator's own words for how to change it:\n" + prompt
    else:
        user_text = "The curator supplied no words. Polish the description as it stands."

    parsed, _call_id = ai.call_claude(
        feature=feature.name,
        model=feature.model,
        prompt_hash=feature.prompt_hash(),
        system_text=feature.system_text(other_block, this_block, verses_block),
        user_text=user_text,
        output_format=ai._DescriptionPolish,
        max_tokens=feature.max_tokens,
    )
    if parsed is None:
        raise HTTPException(502, ai.AI_SERVICE_ERROR)

    description = parsed.description.strip()[:400]
    if not description:
        raise HTTPException(502, ai.AI_SERVICE_ERROR)

    suggested_name = None
    if parsed.suggested_name:
        candidate = parsed.suggested_name.strip()
        if candidate and candidate.casefold() != topic["name"].casefold():
            suggested_name = candidate

    return {
        "description": description,
        "reason": parsed.reason,
        "suggested_name": suggested_name,
    }


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


def main():
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
