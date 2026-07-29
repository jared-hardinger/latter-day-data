#!/usr/bin/env python3
"""
build_fts_db.py
===============
Builds scriptures_fts.db — a copy of scriptures.db with an FTS5 full-text
index over the verse text. The committed scriptures.db stays untouched; this
derived file is gitignored and can be deleted and rebuilt at any time.

    python build_fts_db.py

The index is an external-content FTS5 table (verses_fts) that reads its text
from the verses table, so the text is not stored twice. The tokenizer is
unicode61 with diacritics removed; there is no stemmer, because Porter does
not understand KJV forms like "believeth" — use a prefix query (believ*)
instead.

Query it like this:

    SELECT b.name, v.chapter, v.verse
    FROM verses_fts f
    JOIN verses v ON v.id = f.rowid
    JOIN books b ON b.id = v.book_id
    WHERE verses_fts MATCH '"still small voice"'
    ORDER BY rank;

MATCH is a query language, not a plain string. Raw user input with stray
quotes or dashes raises a syntax error — wrap each term in double quotes, or
catch sqlite3.OperationalError.
"""

import os
import shutil
import sqlite3
import sys

SRC_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scriptures.db")
DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "scriptures_fts.db"
)

EXPECTED_TOTAL = 41995

FTS_SCHEMA = """
CREATE VIRTUAL TABLE verses_fts USING fts5(
    text,
    content='verses',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
"""


def build():
    tmp_path = DB_PATH + ".tmp"
    if os.path.exists(tmp_path):
        os.remove(tmp_path)

    shutil.copy(SRC_PATH, tmp_path)
    db = sqlite3.connect(tmp_path)
    db.executescript(FTS_SCHEMA)
    db.execute("INSERT INTO verses_fts (rowid, text) SELECT id, text FROM verses")
    db.execute("INSERT INTO verses_fts (verses_fts) VALUES ('optimize')")
    db.commit()
    db.execute("VACUUM")
    return db, tmp_path


def sanity_check(db):
    failures = []

    def expect(label, actual, wanted):
        if actual != wanted:
            failures.append(f"{label}: expected {wanted}, got {actual}")

    expect(
        "indexed verse count",
        db.execute("SELECT COUNT(*) FROM verses_fts").fetchone()[0],
        EXPECTED_TOTAL,
    )
    # A known phrase must come back with its best-known verse ranked first.
    top = db.execute(
        """
        SELECT b.name, v.chapter, v.verse
        FROM verses_fts f
        JOIN verses v ON v.id = f.rowid
        JOIN books b ON b.id = v.book_id
        WHERE verses_fts MATCH '"still small voice"'
        ORDER BY rank LIMIT 1
        """
    ).fetchone()
    expect('top hit for "still small voice"', top, ("1 Kings", 19, 12))
    expect(
        "matches for charity",
        db.execute(
            "SELECT COUNT(*) FROM verses_fts WHERE verses_fts MATCH 'charity'"
        ).fetchone()[0]
        > 0,
        True,
    )

    if failures:
        for f in failures:
            print(f"SANITY CHECK FAILED — {f}", file=sys.stderr)
        sys.exit(1)
    print("All sanity checks passed.")


def main():
    if not os.path.exists(SRC_PATH):
        print(f"Source database not found: {SRC_PATH}", file=sys.stderr)
        sys.exit(1)

    db, tmp_path = build()
    sanity_check(db)
    db.close()

    os.replace(tmp_path, DB_PATH)
    size_mb = os.path.getsize(DB_PATH) / 1024 / 1024
    print(f"Wrote {DB_PATH} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
