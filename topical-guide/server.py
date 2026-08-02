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
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS topic_verses (
    topic_id  INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    verse_id  INTEGER NOT NULL,
    status    TEXT NOT NULL CHECK (status IN ('approved', 'rejected')),
    note      TEXT NOT NULL DEFAULT '',
    source    TEXT NOT NULL CHECK (source IN ('exact','prefix','phrase','semantic','manual')),
    added_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (topic_id, verse_id)
);
"""


def check_fts_db():
    if not os.path.exists(FTS_DB_PATH):
        print(
            "scriptures_fts.db not found. Build it first:\n"
            "    python scriptures/build_fts_db.py",
            file=sys.stderr,
        )
        sys.exit(1)


def init_guide_db():
    conn = sqlite3.connect(GUIDE_DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
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
            "SELECT id, name, description FROM topics ORDER BY name"
        ).fetchall()
        export = []
        for t in topics:
            links = guide_db.execute(
                """SELECT verse_id, status, source, note FROM topic_verses
                   WHERE topic_id = ? ORDER BY verse_id""",
                (t["id"],),
            ).fetchall()
            verses = [
                {
                    "reference": reference_for(fts_db, link["verse_id"]),
                    "status": link["status"],
                    "source": link["source"],
                    "note": link["note"],
                }
                for link in links
            ]
            export.append(
                {
                    "name": t["name"],
                    "description": t["description"],
                    "verses": verses,
                }
            )
    finally:
        fts_db.close()
    with open(EXPORT_PATH, "w") as f:
        json.dump(export, f, indent=2)
        f.write("\n")


# ---------------------------------------------------------------------------
# Topics
# ---------------------------------------------------------------------------


class TopicCreate(BaseModel):
    name: str
    description: str = ""


class TopicUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


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
               COUNT(CASE WHEN tv.status = 'approved' THEN 1 END) AS approved_count
        FROM topics t
        LEFT JOIN topic_verses tv ON tv.topic_id = t.id
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
    try:
        guide_db.execute(
            "UPDATE topics SET name = ?, description = ? WHERE id = ?",
            (name, description, topic_id),
        )
        guide_db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"Topic '{name}' already exists")
    write_export(guide_db)
    approved_count = guide_db.execute(
        "SELECT COUNT(*) FROM topic_verses WHERE topic_id = ? AND status = 'approved'",
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
    approved = guide_db.execute(
        """SELECT verse_id, note, source FROM topic_verses
           WHERE topic_id = ? AND status = 'approved' ORDER BY verse_id""",
        (topic_id,),
    ).fetchall()
    rejected_count = guide_db.execute(
        "SELECT COUNT(*) FROM topic_verses WHERE topic_id = ? AND status = 'rejected'",
        (topic_id,),
    ).fetchone()[0]
    note_count = guide_db.execute(
        "SELECT COUNT(*) FROM topic_verses WHERE topic_id = ? AND note != ''",
        (topic_id,),
    ).fetchone()[0]
    volume_counts = {
        v["id"]: {"volume_id": v["id"], "volume": v["name"], "count": 0}
        for v in fts_db.execute("SELECT id, name FROM volumes ORDER BY id").fetchall()
    }
    verses = []
    for link in approved:
        row = fts_db.execute(
            "SELECT volume, volume_id, book, chapter, verse, text FROM v_verses WHERE id = ?",
            (link["verse_id"],),
        ).fetchone()
        verses.append(
            {
                "verse_id": link["verse_id"],
                "volume": row["volume"],
                "volume_id": row["volume_id"],
                "reference": f"{row['book']} {row['chapter']}:{row['verse']}",
                "text": row["text"],
                "note": link["note"],
                "source": link["source"],
            }
        )
        volume_counts[row["volume_id"]]["count"] += 1
    return {
        "id": topic["id"],
        "name": topic["name"],
        "description": topic["description"],
        "verses": verses,
        "volume_counts": list(volume_counts.values()),
        "rejected_count": rejected_count,
        "note_count": note_count,
    }


# ---------------------------------------------------------------------------
# Topic <-> verse links
# ---------------------------------------------------------------------------


class VerseUpsert(BaseModel):
    verse_id: int
    status: Literal["approved", "rejected"]
    source: Literal["exact", "prefix", "phrase", "semantic", "manual"]
    note: Optional[str] = None


class VerseUpdate(BaseModel):
    status: Optional[Literal["approved", "rejected"]] = None
    note: Optional[str] = None


@app.post("/api/topics/{topic_id}/verses")
def upsert_verse(
    topic_id: int,
    body: VerseUpsert,
    guide_db=Depends(get_guide_db),
    fts_db=Depends(get_fts_db),
):
    topic = guide_db.execute(
        "SELECT id FROM topics WHERE id = ?", (topic_id,)
    ).fetchone()
    if topic is None:
        raise HTTPException(404, "Topic not found")
    verse = fts_db.execute(
        "SELECT id FROM verses WHERE id = ?", (body.verse_id,)
    ).fetchone()
    if verse is None:
        raise HTTPException(400, f"Verse {body.verse_id} does not exist")

    # Re-posting to change status (e.g. reject -> approve) must not silently
    # blow away a note the user already wrote, so only overwrite it when the
    # caller explicitly supplied one.
    existing = guide_db.execute(
        "SELECT note FROM topic_verses WHERE topic_id = ? AND verse_id = ?",
        (topic_id, body.verse_id),
    ).fetchone()
    note = body.note if body.note is not None else (existing["note"] if existing else "")

    guide_db.execute(
        """
        INSERT INTO topic_verses (topic_id, verse_id, status, source, note)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (topic_id, verse_id) DO UPDATE SET
            status = excluded.status,
            source = excluded.source,
            note = excluded.note
        """,
        (topic_id, body.verse_id, body.status, body.source, note),
    )
    guide_db.commit()
    write_export(guide_db)
    row = guide_db.execute(
        "SELECT * FROM topic_verses WHERE topic_id = ? AND verse_id = ?",
        (topic_id, body.verse_id),
    ).fetchone()
    return dict(row)


@app.patch("/api/topics/{topic_id}/verses/{verse_id}")
def update_verse(
    topic_id: int, verse_id: int, body: VerseUpdate, guide_db=Depends(get_guide_db)
):
    existing = guide_db.execute(
        "SELECT * FROM topic_verses WHERE topic_id = ? AND verse_id = ?",
        (topic_id, verse_id),
    ).fetchone()
    if existing is None:
        raise HTTPException(404, "Link not found")
    status = body.status if body.status is not None else existing["status"]
    note = body.note if body.note is not None else existing["note"]
    guide_db.execute(
        "UPDATE topic_verses SET status = ?, note = ? WHERE topic_id = ? AND verse_id = ?",
        (status, note, topic_id, verse_id),
    )
    guide_db.commit()
    write_export(guide_db)
    row = guide_db.execute(
        "SELECT * FROM topic_verses WHERE topic_id = ? AND verse_id = ?",
        (topic_id, verse_id),
    ).fetchone()
    return dict(row)


@app.delete("/api/topics/{topic_id}/verses/{verse_id}", status_code=204)
def delete_verse(topic_id: int, verse_id: int, guide_db=Depends(get_guide_db)):
    guide_db.execute(
        "DELETE FROM topic_verses WHERE topic_id = ? AND verse_id = ?",
        (topic_id, verse_id),
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

    try:
        if volume_id is not None:
            rows = fts_db.execute(
                """
                SELECT f.rowid AS verse_id,
                       highlight(verses_fts, 0, '<mark>', '</mark>') AS highlighted
                FROM verses_fts f
                JOIN v_verses vv ON vv.id = f.rowid
                WHERE verses_fts MATCH ? AND vv.volume_id = ?
                ORDER BY rank
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
                ORDER BY rank
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
    if topic_id is not None and rows:
        verse_ids = [r["verse_id"] for r in rows]
        placeholders = ",".join("?" * len(verse_ids))
        status_rows = guide_db.execute(
            f"""SELECT verse_id, status FROM topic_verses
                WHERE topic_id = ? AND verse_id IN ({placeholders})""",
            (topic_id, *verse_ids),
        ).fetchall()
        status_map = {r["verse_id"]: r["status"] for r in status_rows}

    results = []
    for row in rows:
        results.append(
            {
                "verse_id": row["verse_id"],
                "reference": reference_for(fts_db, row["verse_id"]),
                "highlighted": row["highlighted"],
                "status_in_topic": status_map.get(row["verse_id"]),
            }
        )

    return {"total": total, "results": results, "volume_counts": volume_counts}


@app.get("/api/chapter")
def get_chapter(verse_id: int, fts_db=Depends(get_fts_db)):
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
            {"verse_id": r["id"], "verse": r["verse"], "text": r["text"]}
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


@app.post("/api/ai/topics/{topic_id}/verses/{verse_id}/note/fill")
def fill_note(
    topic_id: int,
    verse_id: int,
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

    verse = fts_db.execute(
        "SELECT book, chapter, verse, text FROM v_verses WHERE id = ?", (verse_id,)
    ).fetchone()
    if verse is None:
        raise HTTPException(400, f"Verse {verse_id} does not exist")

    prompt = (body.prompt or "").strip()
    if len(prompt) > feature.max_prompt_chars:
        raise HTTPException(422, "That note is too long.")

    neighbours = fts_db.execute(
        """SELECT verse, text FROM v_verses
           WHERE book = ? AND chapter = ? AND verse BETWEEN ? AND ?
           ORDER BY verse""",
        (verse["book"], verse["chapter"], verse["verse"] - 2, verse["verse"] + 2),
    ).fetchall()

    passage_lines = []
    for n in neighbours:
        marker = ">>" if n["verse"] == verse["verse"] else "  "
        passage_lines.append(
            f"{marker} {verse['book']} {verse['chapter']}:{n['verse']}  {n['text']}"
        )
    passage_block = "Passage (the subject verse is marked >>):\n" + "\n".join(
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
        "SELECT COUNT(*) FROM topic_verses WHERE topic_id = ? AND status = 'approved'",
        (topic_id,),
    ).fetchone()[0]
    links = guide_db.execute(
        """SELECT verse_id, note FROM topic_verses
           WHERE topic_id = ? AND status = 'approved' ORDER BY verse_id LIMIT 40""",
        (topic_id,),
    ).fetchall()
    if links:
        lines = []
        for link in links:
            row = fts_db.execute(
                "SELECT book, chapter, verse, text FROM v_verses WHERE id = ?",
                (link["verse_id"],),
            ).fetchone()
            ref = f"{row['book']} {row['chapter']}:{row['verse']}"
            lines.append(f"{ref}  {row['text']}")
            if link["note"]:
                lines.append(f"  note: {link['note']}")
        verses_block = (
            f"Approved verses in this topic ({len(links)} of {approved_count}):\n"
            + "\n".join(lines)
        )
        if approved_count > len(links):
            verses_block += (
                f"\n… and {approved_count - len(links)} more approved verses not shown."
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
