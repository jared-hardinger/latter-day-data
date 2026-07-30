# Latter-day Data

Datasets and tools around the scriptures and General Conference talks of the
Church of Jesus Christ of Latter-day Saints.

- **[Scripture database](#scripture-database)** — every verse of the standard
  works in a SQLite file (`scriptures/scriptures.db`), a foundation for other
  tools.
- **[Conference talk corpus](#general-conference-talk-corpus-builder)** — full
  text of General Conference talks scraped into JSON, with static HTML
  explorers (`index.html`, `word_frequency.html`, `talk_analyzer.html`).
- **[Topical guide](#topical-guide)** — a local app (`topical-guide/`) for
  curating your own personal topical guide: search the scripture database,
  approve or reject candidate verses, and study the result by topic. Two
  Claude-backed helpers can draft a topic's name/description or a verse's
  note for you to review and save.

## Scripture Database

`scriptures/scriptures.db` holds all 41,995 verses of the LDS canon — Old
Testament, New Testament, Book of Mormon, Doctrine and Covenants, and Pearl of
Great Price — loaded from the public-domain
[LDS Documentation Project](https://github.com/beandog/lds-scriptures) dataset.
The database is committed to the repo, so tools can use it directly with no
build step.

### Schema

Three tables plus a convenience view:

| table | columns | notes |
|---|---|---|
| `volumes` | `id`, `name` | 1=Old Testament, 2=New Testament, 3=Book of Mormon, 4=Doctrine and Covenants, 5=Pearl of Great Price |
| `books` | `id`, `volume_id`, `name`, `position` | 87 books; `position` = canonical order within the volume |
| `verses` | `id`, `book_id`, `chapter`, `verse`, `text` | one row per verse; D&C section numbers live in `chapter` |
| `v_verses` (view) | `id`, `volume_id`, `volume`, `book_id`, `book`, `chapter`, `verse`, `text` | flat join for easy querying |

```sql
sqlite3 scriptures/scriptures.db \
  "SELECT text FROM v_verses WHERE book = '1 Nephi' AND chapter = 3 AND verse = 7"
```

**ID stability guarantee:** IDs come from the source dataset, which numbers
volumes, books, and verses sequentially in canonical order. Rebuilding from
the same source produces a byte-identical database, so other tools may safely
store verse IDs.

**Caveats:** The text is the public-domain editions, which differ from the
current church edition only in typography (straight vs. curly apostrophes,
`æ` ligatures, omitted KJV `¶` paragraph marks) — a 240-verse spot-check
against churchofjesuschrist.org found zero wording differences. Official
Declarations 1 and 2 are prose rather than versed scripture and are not
included; the D&C here is Sections 1–138.

### Full-text search

`scriptures.db` carries no search index. That keeps it small and its rebuilds
byte-identical. To search the text, build the derived database:

```bash
python scriptures/build_fts_db.py     # writes scriptures/scriptures_fts.db (~11 MB)
```

It copies `scriptures.db` and adds `verses_fts`, an FTS5 index over the verse
text. The file is gitignored — delete it any time; the rebuild takes under a
second.

Query with `MATCH` and sort by `rank` (BM25 relevance, best match first):

```sql
SELECT b.name, v.chapter, v.verse
FROM verses_fts f
JOIN verses v ON v.id = f.rowid
JOIN books b ON b.id = v.book_id
WHERE verses_fts MATCH '"still small voice"'
ORDER BY rank;
```

`MATCH` takes words (`charity`), phrases (`"still small voice"`), prefixes
(`believ*`), booleans (`repentance NOT baptism`), and proximity
(`NEAR(faith works, 5)`). The `snippet()` function returns each hit with
surrounding context.

Two cautions. `MATCH` is a query language, not a plain string — wrap raw user
input in double quotes or catch the syntax error it throws. And there is no
stemmer: KJV forms like `believeth` are their own words. Use a prefix query;
`believ*` catches them all.

### Scripts

```bash
# Rebuild the database (source CSV is cached under scriptures/cache/)
python scriptures/build_scriptures_db.py            # --refresh re-downloads

# Build the full-text search database (gitignored, derived from scriptures.db)
python scriptures/build_fts_db.py

# Spot-check random verses against churchofjesuschrist.org
python scriptures/verify_scriptures.py              # -n 20 --seed 42 --delay 1.0
```

The build fails loudly if the loaded data doesn't match the expected canon
(5 volumes, 39/27/15/1/5 books, 41,995 verses, 138 D&C sections). The verify
script reports per-verse `EXACT` / `PUNCTUATION` / `DIFFERS` / `MISSING` and
needs `requests beautifulsoup4 lxml` (same as the scraper below).

## Topical Guide

`topical-guide/` is a local web app for curating your own personal topical
guide to the scriptures: search `scriptures/scriptures.db` for candidate
verses on a topic, then approve or reject each one — you're always the final
say. The approved collection becomes a browsable, interactive guide inside
the same app. It's a separate tool from the conference-talk scripts above,
but reads the shared scripture database.

### Run it

```bash
pip install -r topical-guide/requirements.txt
python scriptures/build_fts_db.py    # only if scriptures_fts.db doesn't exist yet
python topical-guide/server.py       # binds to 127.0.0.1:8000
```

Backend is FastAPI, frontend is a single vanilla-JS page (`static/index.html`)
with hash routing — no build step, matching the repo's other static-HTML
tools. There's no auth or deployment; it's meant to run locally on your own
machine. `pip install -r topical-guide/requirements.txt` now also pulls in
`anthropic` and `python-dotenv` for the AI helpers below.

### AI writing helpers

Two small Claude-backed helpers draft the fields that are hardest to write
well — you review and save, the AI never writes to the database:

- **Fill a topic** — on the New Topic form, describe the topic in plain
  language (e.g. "verses about praying when you don't feel like it") and
  press **✦ Fill with AI** to draft a name and description.
- **Fill a note** — on the Curate tab, press **✦** beside an approved verse's
  note box to draft a note from the verse and topic context (or, with rough
  words already typed, to sharpen them without changing the point). A
  **Save note** button appears once a fill is ready — nothing is saved until
  you press it.

Both need `ANTHROPIC_API_KEY` in `topical-guide/.env` (gitignored), read
server-side only — it's never sent to the browser. Without a key, the
buttons show a plain "no API key" message and the rest of the app still
works normally.

### What's committed vs. derived

- `topical-guide/guide.db` — your curated topics and verse approvals, in its
  own SQLite database (verses are referenced by their stable IDs into
  `scriptures.db`). Created automatically on first run if missing. This is
  the hand-made, precious artifact — **it's committed to the repo**, same as
  `scriptures.db`.
- `topical-guide/guide_export.json` — a plain-text mirror of `guide.db`,
  rewritten after every change with deterministic ordering so git history
  shows a readable diff of your curation over time. Also **committed**.
- `scriptures/scriptures_fts.db` — same derived, gitignored full-text index
  described above. The server refuses to start without it.
- `topical-guide/ai_log.db` and `topical-guide/.env` — gitignored. Call logs
  (`ai_log.db`) are a debugging/cost record for the AI helpers above, not
  curation data, so they stay out of `guide.db` and never appear in
  `guide_export.json`; `.env` holds the API key and must never be committed.

### Search

Search uses the same FTS5 index as above, in three modes: **word forms**
(the default — `pray` matches `prayer`, `prayeth`, etc. via prefix queries,
since there's no stemmer), **exact** (whole-word match), and **phrase**
(the whole query as one quoted phrase). Rejecting a verse in a topic is
remembered, so the same near-misses don't resurface as fresh candidates on
the next search.

## General Conference Talk Corpus Builder

Pulls the full text of General Conference talks from the official source
(`churchofjesuschrist.org`) into a structured JSON database for personal study
and analysis.

> The talks are © Intellectual Reserve, Inc. This tool fetches the freely
> published text to your own machine for personal use — it does not redistribute
> a text dump. Re-run it after each conference to stay current.

## Setup

```bash
pip install requests beautifulsoup4 lxml
```

## Run

```bash
# Everything, 1971 → current year (this is the big one: ~3,900 talks)
python download_conference_talks.py --out talks.json

# A range
python download_conference_talks.py --start 2015 --end 2026 --out recent.json

# JSON Lines instead of one big array (better for streaming / large analysis)
python download_conference_talks.py --out talks.jsonl --format jsonl

# Drop sustainings / reports / prayers, keep only sermons
python download_conference_talks.py --talks-only --out sermons.json

# Skip footnotes
python download_conference_talks.py --no-footnotes --out talks.json
```

Results are cached per-conference under `conference_cache/`, so re-runs only
fetch what's new. Use `--overwrite` to force a refresh. `--delay` (default 1.0s)
throttles requests to be polite to the server.

## Schema (one record per talk)

| field | meaning |
|---|---|
| `conference` | e.g. `"2026-04"` |
| `year`, `month` | 1971+, month is 4 or 10 |
| `date` | best-effort calendar date (Saturday vs Sunday derived from session) |
| `session` | e.g. `"Saturday Morning Session"` |
| `session_order` | nth session of the conference (1, 2, 3…) |
| `order_in_session` | **1 = first speaker in that session** |
| `order_in_conference` | 1 = first speaker overall |
| `speaker` | e.g. `"David A. Bednar"` |
| `speaker_role` | e.g. `"Of the Quorum of the Twelve Apostles"` |
| `title` | talk title |
| `type` | `talk` \| `business` \| `report` \| `prayer` (heuristic, for filtering) |
| `url` | canonical source URL |
| `word_count` | words in `full_text` |
| `full_text` | the talk body, paragraphs joined by blank lines |
| `footnotes` | list of footnote/reference strings |

`april_2026_sample.json` shows the exact shape using real April 2026 metadata,
with `full_text` as a placeholder the scraper fills in on a live run.

## Caveats

- Body/author selectors target the current site (v4.48, 2026). If the Church
  redesigns the site, the `extract_*` functions are where you'd adjust.
- Pre-1990s conferences sometimes used extra sessions (Welfare, Priesthood,
  General Women's). The scraper handles them generically — spot-check a couple
  of older conferences after your first full run.
- Missing conferences (e.g. Oct 1957 was cancelled) 404 and are skipped cleanly.
