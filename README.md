# Latter-day Data

Datasets and tools around the scriptures and General Conference talks of the
Church of Jesus Christ of Latter-day Saints.

- **[Scripture database](#scripture-database)** — every verse of the standard
  works in a SQLite file (`scriptures/scriptures.db`), a foundation for other
  tools.
- **[Conference talk corpus](#general-conference-talk-corpus-builder)** — full
  text of General Conference talks scraped into JSON, with static HTML
  explorers (`index.html`, `word_frequency.html`, `talk_analyzer.html`).

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
`æ` ligatures) — a 40-verse spot-check against churchofjesuschrist.org found
zero wording differences. Official Declarations 1 and 2 are prose rather than
versed scripture and are not included; the D&C here is Sections 1–138.

### Scripts

```bash
# Rebuild the database (source CSV is cached under scriptures/cache/)
python scriptures/build_scriptures_db.py            # --refresh re-downloads

# Spot-check random verses against churchofjesuschrist.org
python scriptures/verify_scriptures.py              # -n 20 --seed 42 --delay 1.0
```

The build fails loudly if the loaded data doesn't match the expected canon
(5 volumes, 39/27/15/1/5 books, 41,995 verses, 138 D&C sections). The verify
script reports per-verse `EXACT` / `PUNCTUATION` / `DIFFERS` / `MISSING` and
needs `requests beautifulsoup4 lxml` (same as the scraper below).

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
