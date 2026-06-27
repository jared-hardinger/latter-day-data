# General Conference Talk Corpus Builder

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
