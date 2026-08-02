#!/usr/bin/env python3
"""
verify_scriptures.py
====================
Spot-checks scriptures.db against churchofjesuschrist.org.

Samples random verses (stratified across the five volumes), fetches each one
from the church website, and compares the text. The database holds the
public-domain editions, which can differ slightly from the current church
edition (punctuation, formerly-italicized words), so differences are reported
for review — they are not automatic failures.

    python verify_scriptures.py             # 20 verses, 4 per volume
    python verify_scriptures.py -n 10
    python verify_scriptures.py --seed 42   # reproducible sample

Requires: pip install requests beautifulsoup4 lxml  (same as the scraper)
"""

import argparse
import difflib
import random
import re
import sqlite3
import time
import unicodedata

import requests
from bs4 import BeautifulSoup

from build_scriptures_db import DB_PATH

BASE_URL = "https://www.churchofjesuschrist.org/study/scriptures"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
        "(personal-research scripture-db verifier)"
    )
}


def load_slugs(db):
    """book name -> (volume_lds_url, book_lds_url)."""
    return {
        name: (volume_url, book_url)
        for name, volume_url, book_url in db.execute(
            "SELECT b.name, vol.lds_url, b.lds_url "
            "FROM books b JOIN volumes vol ON vol.id = b.volume_id"
        )
    }


def sample_verses(db, per_volume):
    verses = []
    for (vid,) in db.execute("SELECT id FROM volumes ORDER BY id"):
        verses.extend(
            db.execute(
                "SELECT book, chapter, verse, text FROM v_verses "
                "WHERE volume_id = ? ORDER BY RANDOM() LIMIT ?",
                (vid, per_volume),
            )
        )
    return verses


def fetch_verse(session, volume_slug, book_slug, chapter, verse):
    url = f"{BASE_URL}/{volume_slug}/{book_slug}/{chapter}?lang=eng"
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "lxml")
    p = soup.find("p", id=f"p{verse}")
    if p is None:
        return url, None
    for tag in p.find_all(["sup", "span"], class_=["marker", "verse-number"]):
        tag.decompose()
    for sup in p.find_all("sup"):
        sup.decompose()
    # No separator: inline footnote anchors would otherwise split words from
    # their punctuation ("covenant ,"); real whitespace survives in text nodes.
    return url, p.get_text()


def normalize(text, punctuation=False):
    """Collapse whitespace; optionally neutralize punctuation-style variance."""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\s+", " ", text).strip()
    if punctuation:
        text = text.replace("‘", "'").replace("’", "'")
        text = text.replace("“", '"').replace("”", '"')
        text = text.replace("–", "-").replace("—", "-")
        text = text.replace("æ", "ae").replace("Æ", "Ae")
        text = re.sub(r"[^\w\s]", "", text).lower()
        text = re.sub(r"\s+", " ", text).strip()
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-n", type=int, default=20, help="total verses to check")
    parser.add_argument("--seed", type=int, help="RNG seed for a reproducible sample")
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    db = sqlite3.connect(DB_PATH)
    slugs = load_slugs(db)
    if args.seed is not None:
        # ORDER BY RANDOM() ignores Python's seed; emulate stratification here.
        db.create_function("RANDOM", 0, lambda: random.getrandbits(63))
    verses = sample_verses(db, max(1, args.n // 5))

    session = requests.Session()
    session.headers.update(HEADERS)

    exact = punct_only = differs = missing = 0
    for book, chapter, verse, db_text in verses:
        ref = f"{book} {chapter}:{verse}"
        volume_slug, book_slug = slugs[book]
        url, site_text = fetch_verse(session, volume_slug, book_slug, chapter, verse)
        time.sleep(args.delay)

        if site_text is None:
            missing += 1
            print(f"MISSING     {ref} — verse not found at {url}")
            continue

        if normalize(db_text) == normalize(site_text):
            exact += 1
            print(f"EXACT       {ref}")
        elif normalize(db_text, punctuation=True) == normalize(site_text, punctuation=True):
            punct_only += 1
            print(f"PUNCTUATION {ref} — wording matches, punctuation differs")
        else:
            differs += 1
            print(f"DIFFERS     {ref} — {url}")
            for line in difflib.unified_diff(
                [normalize(db_text)], [normalize(site_text)],
                fromfile="db", tofile="site", lineterm="",
            ):
                print(f"    {line}")

    total = len(verses)
    print(
        f"\n{total} checked: {exact} exact, {punct_only} punctuation-only, "
        f"{differs} differ, {missing} missing"
    )


if __name__ == "__main__":
    main()
