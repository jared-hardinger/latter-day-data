#!/usr/bin/env python3
"""
build_scriptures_db.py
======================
Builds scriptures.db — one row per verse of the LDS standard works — from the
public-domain LDS Documentation Project dataset (beandog/lds-scriptures).

The source CSV is downloaded once and cached under cache/. Re-running the
script rebuilds the database from the cached copy; use --refresh to force a
fresh download.

    python build_scriptures_db.py

ID stability guarantee: volume, book, and verse IDs are taken directly from
the source dataset, which numbers them sequentially in canonical order. A
rebuild from the same source always produces identical IDs, so other tools
may safely store verse IDs.

Note: Official Declarations 1 and 2 are prose, not versed scripture, and are
not part of the dataset — the Doctrine and Covenants here is Sections 1-138.
"""

import argparse
import csv
import os
import sqlite3
import sys
import urllib.request

SOURCE_URL = (
    "https://raw.githubusercontent.com/beandog/lds-scriptures"
    "/master/csv/lds-scriptures.csv"
)
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
CSV_PATH = os.path.join(CACHE_DIR, "lds-scriptures.csv")
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scriptures.db")

# Hard expectations — the build fails if the source doesn't match the canon.
EXPECTED = {
    # volume_id: (name, book count, verse count)
    1: ("Old Testament", 39, 23145),
    2: ("New Testament", 27, 7957),
    3: ("Book of Mormon", 15, 6604),
    4: ("Doctrine and Covenants", 1, 3654),
    5: ("Pearl of Great Price", 5, 635),
}
EXPECTED_TOTAL = 41995
EXPECTED_DC_SECTIONS = 138

SCHEMA = """
CREATE TABLE volumes (
    id      INTEGER PRIMARY KEY,
    name    TEXT NOT NULL UNIQUE,
    lds_url TEXT NOT NULL          -- churchofjesuschrist.org volume slug
);

CREATE TABLE books (
    id        INTEGER PRIMARY KEY,
    volume_id INTEGER NOT NULL REFERENCES volumes(id),
    name      TEXT NOT NULL UNIQUE,
    position  INTEGER NOT NULL,    -- canonical order within the volume
    lds_url   TEXT NOT NULL,       -- churchofjesuschrist.org book slug
    UNIQUE (volume_id, position)
);

CREATE TABLE verses (
    id      INTEGER PRIMARY KEY,
    book_id INTEGER NOT NULL REFERENCES books(id),
    chapter INTEGER NOT NULL,            -- D&C section number for the D&C
    verse   INTEGER NOT NULL,
    text    TEXT NOT NULL,
    UNIQUE (book_id, chapter, verse)
);

CREATE VIEW v_verses AS
    SELECT v.id, vol.id AS volume_id, vol.name AS volume,
           vol.lds_url AS volume_url,
           b.id AS book_id, b.name AS book, b.lds_url AS book_url,
           v.chapter, v.verse, v.text
    FROM verses v
    JOIN books b ON b.id = v.book_id
    JOIN volumes vol ON vol.id = b.volume_id;
"""


def fetch_csv(refresh=False):
    if os.path.exists(CSV_PATH) and not refresh:
        print(f"Using cached {CSV_PATH}")
        return
    os.makedirs(CACHE_DIR, exist_ok=True)
    print(f"Downloading {SOURCE_URL}")
    urllib.request.urlretrieve(SOURCE_URL, CSV_PATH)
    print(f"Saved to {CSV_PATH}")


def load_rows():
    with open(CSV_PATH, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def build(rows):
    tmp_path = DB_PATH + ".tmp"
    if os.path.exists(tmp_path):
        os.remove(tmp_path)

    db = sqlite3.connect(tmp_path)
    db.executescript(SCHEMA)

    volumes = {}   # id -> (name, lds_url)
    books = {}     # id -> (volume_id, name, position, lds_url)
    positions = {} # volume_id -> next position
    for r in rows:
        vid, bid = int(r["volume_id"]), int(r["book_id"])
        if vid not in volumes:
            volumes[vid] = (r["volume_title"], r["volume_lds_url"])
        if bid not in books:
            positions[vid] = positions.get(vid, 0) + 1
            books[bid] = (vid, r["book_title"], positions[vid], r["book_lds_url"])

    db.executemany(
        "INSERT INTO volumes (id, name, lds_url) VALUES (?, ?, ?)",
        [(vid, name, url) for vid, (name, url) in sorted(volumes.items())],
    )
    db.executemany(
        "INSERT INTO books (id, volume_id, name, position, lds_url) VALUES (?, ?, ?, ?, ?)",
        [(bid, vid, name, pos, url) for bid, (vid, name, pos, url) in sorted(books.items())],
    )
    db.executemany(
        "INSERT INTO verses (id, book_id, chapter, verse, text) VALUES (?, ?, ?, ?, ?)",
        [
            (
                int(r["verse_id"]),
                int(r["book_id"]),
                int(r["chapter_number"]),
                int(r["verse_number"]),
                r["scripture_text"],
            )
            for r in rows
        ],
    )
    db.commit()
    return db, tmp_path


def sanity_check(db):
    failures = []

    def expect(label, actual, wanted):
        if actual != wanted:
            failures.append(f"{label}: expected {wanted}, got {actual}")

    expect(
        "total verses",
        db.execute("SELECT COUNT(*) FROM verses").fetchone()[0],
        EXPECTED_TOTAL,
    )
    for vid, (name, book_count, verse_count) in EXPECTED.items():
        row = db.execute("SELECT name FROM volumes WHERE id = ?", (vid,)).fetchone()
        expect(f"volume {vid} name", row and row[0], name)
        expect(
            f"{name} book count",
            db.execute(
                "SELECT COUNT(*) FROM books WHERE volume_id = ?", (vid,)
            ).fetchone()[0],
            book_count,
        )
        expect(
            f"{name} verse count",
            db.execute(
                "SELECT COUNT(*) FROM v_verses WHERE volume_id = ?", (vid,)
            ).fetchone()[0],
            verse_count,
        )
    expect(
        "D&C section count",
        db.execute(
            "SELECT COUNT(DISTINCT chapter) FROM v_verses WHERE volume_id = 4"
        ).fetchone()[0],
        EXPECTED_DC_SECTIONS,
    )
    expect("volumes missing a slug",
           db.execute("SELECT COUNT(*) FROM volumes WHERE lds_url IS NULL OR lds_url = ''").fetchone()[0], 0)
    expect("books missing a slug",
           db.execute("SELECT COUNT(*) FROM books WHERE lds_url IS NULL OR lds_url = ''").fetchone()[0], 0)

    if failures:
        for f in failures:
            print(f"SANITY CHECK FAILED — {f}", file=sys.stderr)
        sys.exit(1)
    print("All sanity checks passed.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh", action="store_true", help="re-download the source CSV"
    )
    args = parser.parse_args()

    fetch_csv(refresh=args.refresh)
    rows = load_rows()
    print(f"Loaded {len(rows)} verses from source CSV")

    db, tmp_path = build(rows)
    sanity_check(db)
    db.close()

    os.replace(tmp_path, DB_PATH)
    size_mb = os.path.getsize(DB_PATH) / 1024 / 1024
    print(f"Wrote {DB_PATH} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
