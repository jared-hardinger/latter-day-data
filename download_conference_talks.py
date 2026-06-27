#!/usr/bin/env python3
"""
download_conference_talks.py
============================

Builds a structured corpus of General Conference talks from
The Church of Jesus Christ of Latter-day Saints, pulled directly from the
official source (churchofjesuschrist.org) for personal study and analysis.

The talks are copyrighted by Intellectual Reserve, Inc. This script does NOT
redistribute their text; it fetches the freely-available published text from
the official site to your own machine, the same way the BYU corpus tooling and
other research scrapers do. Keep the output for personal use.

Output: one JSON (or JSONL) file with one record per talk:

    {
      "conference":          "2026-04",
      "year":                2026,
      "month":               4,
      "date":                "2026-04-04",   # best-effort session date
      "session":             "Saturday Morning Session",
      "session_order":       1,              # nth session of the conference
      "order_in_session":    1,              # 1 = first speaker in the session
      "order_in_conference": 1,              # 1 = first speaker overall
      "speaker":             "Dallin H. Oaks",
      "speaker_role":        "President of the Church",
      "title":               "Introduction",
      "type":                "talk",         # talk | business | report | prayer
      "url":                 "https://www.churchofjesuschrist.org/study/...",
      "word_count":          1234,
      "full_text":           "....",
      "footnotes":           ["1. ...", "2. ..."]
    }

Usage
-----
    pip install requests beautifulsoup4 lxml

    # Everything from 1971 to the current year:
    python download_conference_talks.py --out talks.json

    # Just a range:
    python download_conference_talks.py --start 2015 --end 2026 --out recent.json

    # JSON Lines (nicer for large-scale analysis / streaming):
    python download_conference_talks.py --out talks.jsonl --format jsonl

    # Re-run after a new conference (cached conferences are skipped):
    python download_conference_talks.py --out talks.json

Notes
-----
* Per-conference results are cached under --cache-dir so re-runs are cheap and
  resumable. Use --overwrite to force a re-fetch.
* --delay throttles requests to be polite to the server (default 1.0s).
* Procedural items (sustainings, auditing/statistical reports, solemn assembly)
  are tagged via the "type" field so you can filter them out in analysis;
  use --talks-only to drop them at scrape time.
"""

import argparse
import datetime
import json
import os
import re
import sys
import time
from dataclasses import dataclass, asdict, field
from typing import Optional

import requests
from bs4 import BeautifulSoup

BASE = "https://www.churchofjesuschrist.org"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
        "(personal-research conference-corpus builder)"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

SESSION_ORDER_HINT = [
    "saturday morning",
    "saturday afternoon",
    "saturday evening",   # discontinued after Oct 2025, present historically
    "priesthood",
    "general women",
    "women",
    "sunday morning",
    "sunday afternoon",
]


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass
class Talk:
    conference: str
    year: int
    month: int
    date: Optional[str]
    session: str
    session_order: int
    order_in_session: int
    order_in_conference: int
    speaker: str
    speaker_role: str
    title: str
    type: str
    url: str
    word_count: int
    full_text: str
    footnotes: list = field(default_factory=list)


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def fetch(session: requests.Session, url: str, retries: int = 4,
          delay: float = 1.0) -> Optional[str]:
    """GET a URL with retry + backoff. Returns text or None."""
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code == 200:
                resp.encoding = "utf-8"
                return resp.text.replace("\ufeff", "")
            if resp.status_code == 404:
                return None
            print(f"    [{resp.status_code}] {url} (attempt {attempt})",
                  file=sys.stderr)
        except requests.RequestException as exc:
            print(f"    [error] {exc} (attempt {attempt})", file=sys.stderr)
        time.sleep(delay * attempt)
    return None


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def conference_index_url(year: int, month: int) -> str:
    return f"{BASE}/study/general-conference/{year}/{month:02d}?lang=eng"


def parse_index(html: str, year: int, month: int):
    """
    Walk the conference table of contents in document order and return an
    ordered list of (session_name, session_order, order_in_session, talk_url).
    """
    soup = BeautifulSoup(html, "lxml")
    talk_re = re.compile(
        rf"/study/general-conference/{year}/{month:02d}/([^/?#]+)")
    session_re = re.compile(r"-session$")

    current_session = None
    session_order = 0
    order_in_session = 0
    seen = set()
    rows = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = talk_re.search(href)
        if not m:
            continue
        slug = m.group(1)

        # Session header link, e.g. ".../2026/04/saturday-morning-session"
        if session_re.search(slug):
            name = a.get_text(" ", strip=True)
            # Strip trailing summary text the site appends to the tile link
            name = re.split(r"\s{2,}|  ", name)[0].strip()
            name = clean_session_name(name)
            if name and name != current_session:
                current_session = name
                session_order += 1
                order_in_session = 0
            continue

        # A real talk link (skip the bare "Contents" root and dupes)
        if slug in seen or current_session is None:
            continue
        seen.add(slug)
        order_in_session += 1
        url = f"{BASE}/study/general-conference/{year}/{month:02d}/{slug}?lang=eng"
        rows.append((current_session, session_order, order_in_session, url))

    return rows


def clean_session_name(name: str) -> str:
    """Normalize a session label to e.g. 'Saturday Morning Session'."""
    name = re.sub(r"\s+", " ", name).strip()
    # Some tiles repeat the name twice ("Saturday Morning SessionSaturday...")
    half = len(name) // 2
    if name[:half].strip() and name[:half].strip() == name[half:].strip():
        name = name[:half].strip()
    return name


def extract_text_block(soup: BeautifulSoup) -> str:
    """Pull the talk body, dropping footnote superscripts."""
    container = soup.select_one("div.body-block")
    if container is None:
        # Fallbacks for older/newer markup variants
        container = (soup.select_one('div[class*="body-block"]')
                     or soup.select_one("article")
                     or soup.find("main"))
    if container is None:
        return ""
    for sup in container.find_all("sup"):
        sup.decompose()
    paras = [p.get_text(" ", strip=True) for p in container.find_all("p")]
    paras = [p for p in paras if p]
    if not paras:  # last resort: whole-container text
        return re.sub(r"\s+\n", "\n", container.get_text("\n", strip=True))
    return "\n\n".join(paras)


def extract_speaker(soup: BeautifulSoup):
    def _txt(sel):
        el = soup.select_one(sel)
        return el.get_text(" ", strip=True) if el else ""

    speaker = _txt("p.author-name") or _txt('[class*="author-name"]')
    speaker = re.sub(r"^(By |Presented by )", "", speaker).strip()
    role = _txt("p.author-role") or _txt('[class*="author-role"]')
    if not role and speaker.startswith("President "):
        role = "President of the Church"
    return speaker, role


def extract_title(soup: BeautifulSoup) -> str:
    h1 = soup.select_one("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(" ", strip=True)
    t = soup.select_one("title")
    if t:
        return re.split(r"\s*\|\s*", t.get_text(strip=True))[0].strip()
    return ""


def extract_footnotes(soup: BeautifulSoup):
    return [li.get_text(" ", strip=True)
            for li in soup.select("footer.notes li")
            if li.get_text(strip=True)]


def classify(title: str) -> str:
    t = title.lower()
    if "sustaining" in t or "solemn assembly" in t:
        return "business"
    if "auditing" in t or "statistical report" in t:
        return "report"
    if t.strip() in ("opening prayer", "closing prayer", "invocation",
                     "benediction"):
        return "prayer"
    return "talk"


def session_date(year: int, month: int, session: str,
                 day1: Optional[datetime.date]) -> Optional[str]:
    """Best-effort calendar date for a talk based on its session."""
    if day1 is None:
        return None
    is_sunday = "sunday" in session.lower()
    d = day1 + datetime.timedelta(days=1) if is_sunday else day1
    return d.isoformat()


def parse_conference_day1(html: str, year: int, month: int) -> Optional[datetime.date]:
    """Find the Saturday (day 1) date, e.g. from 'held on April 4-5, 2026'."""
    m = re.search(r"([A-Z][a-z]+)\s+(\d{1,2})\s*[\u2013\u2014-]\s*\d{1,2},\s*(\d{4})",
                  html)
    if m:
        try:
            return datetime.datetime.strptime(
                f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y").date()
        except ValueError:
            pass
    # Fallback: first weekend (first Saturday) of the conference month.
    d = datetime.date(year, month, 1)
    while d.weekday() != 5:          # 5 = Saturday
        d += datetime.timedelta(days=1)
    return d


# --------------------------------------------------------------------------- #
# Per-conference scrape
# --------------------------------------------------------------------------- #
def scrape_conference(session, year, month, delay, want_footnotes):
    idx_url = conference_index_url(year, month)
    html = fetch(session, idx_url, delay=delay)
    if not html:
        return None  # conference doesn't exist (e.g. future, or Oct 1957)

    day1 = parse_conference_day1(html, year, month)
    rows = parse_index(html, year, month)
    if not rows:
        print(f"    no talks parsed for {year}-{month:02d}", file=sys.stderr)
        return []

    talks = []
    order_in_conf = 0
    for session_name, session_order, order_in_session, url in rows:
        order_in_conf += 1
        time.sleep(delay)
        page = fetch(session, url, delay=delay)
        if not page:
            print(f"    skip (no page): {url}", file=sys.stderr)
            continue
        soup = BeautifulSoup(page, "lxml")
        title = extract_title(soup)
        speaker, role = extract_speaker(soup)
        body = extract_text_block(soup)
        footnotes = extract_footnotes(soup) if want_footnotes else []
        talk = Talk(
            conference=f"{year}-{month:02d}",
            year=year, month=month,
            date=session_date(year, month, session_name, day1),
            session=session_name,
            session_order=session_order,
            order_in_session=order_in_session,
            order_in_conference=order_in_conf,
            speaker=speaker,
            speaker_role=role,
            title=title,
            type=classify(title),
            url=url,
            word_count=len(body.split()),
            full_text=body,
            footnotes=footnotes,
        )
        talks.append(talk)
        print(f"    [{order_in_conf:>2}] {speaker or '???'} — "
              f"\"{title}\" ({talk.word_count} words)")
    return talks


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=int, default=1971,
                    help="first year (default 1971)")
    ap.add_argument("--end", type=int, default=datetime.date.today().year,
                    help="last year (default current year)")
    ap.add_argument("--out", default="talks.json", help="output file")
    ap.add_argument("--format", choices=["json", "jsonl"], default="json")
    ap.add_argument("--cache-dir", default="conference_cache")
    ap.add_argument("--delay", type=float, default=1.0,
                    help="seconds between requests (default 1.0)")
    ap.add_argument("--no-footnotes", action="store_true",
                    help="omit footnotes/references")
    ap.add_argument("--talks-only", action="store_true",
                    help="drop sustainings, reports, and prayers")
    ap.add_argument("--overwrite", action="store_true",
                    help="re-fetch conferences even if cached")
    args = ap.parse_args()

    os.makedirs(args.cache_dir, exist_ok=True)
    session = make_session()
    today = datetime.date.today()
    all_talks = []

    for year in range(args.start, args.end + 1):
        for month in (4, 10):
            if datetime.date(year, month, 1) > today:
                continue
            cache_path = os.path.join(args.cache_dir, f"{year}-{month:02d}.json")
            if os.path.exists(cache_path) and not args.overwrite:
                with open(cache_path) as fh:
                    conf_talks = json.load(fh)
                print(f"=== {year}-{month:02d}: cached "
                      f"({len(conf_talks)} talks)")
            else:
                print(f"=== {year}-{month:02d}: fetching")
                talks = scrape_conference(session, year, month, args.delay,
                                          not args.no_footnotes)
                if talks is None:
                    print(f"    (no conference found for {year}-{month:02d})")
                    continue
                conf_talks = [asdict(t) for t in talks]
                with open(cache_path, "w") as fh:
                    json.dump(conf_talks, fh, ensure_ascii=False, indent=2)
            all_talks.extend(conf_talks)

    if args.talks_only:
        all_talks = [t for t in all_talks if t["type"] == "talk"]

    with open(args.out, "w", encoding="utf-8") as fh:
        if args.format == "jsonl":
            for t in all_talks:
                fh.write(json.dumps(t, ensure_ascii=False) + "\n")
        else:
            json.dump(all_talks, fh, ensure_ascii=False, indent=2)

    print(f"\nWrote {len(all_talks)} talks to {args.out}")


if __name__ == "__main__":
    main()
