# Topical Guide — Implementation Plan

## Vision

A local web app for curating a **personal topical guide** to the scriptures.
The user creates topics ("Prayer", "Adversity"), searches the scripture
database for candidate verses, and approves or rejects each candidate — the
human is always the final say. The approved collection becomes a browsable,
interactive topical guide inside the same app.

Search is the tool; the curated guide is the product. Search arrives in
tiers across phases: literal/lexical first (low-hanging fruit), semantic
similarity later.

This lives in `topical-guide/`, separate from the existing conference-talk
tools in the repo, but reads the shared scripture database in `scriptures/`.

## Decisions already made (do not re-litigate)

1. **Local-first.** Runs on the user's Mac via `python server.py`. No
   deployment, no auth, bind to 127.0.0.1.
2. **Backend is FastAPI (Python), frontend is a single vanilla-JS static
   page.** No frontend frameworks, no build step — matches the repo's
   existing `index.html` / `word_frequency.html` style.
3. **Curated data lives in its own database, `topical-guide/guide.db`**,
   referencing verses by their stable IDs from `scriptures/scriptures.db`
   (the README's ID-stability guarantee exists for exactly this purpose).
   `guide.db` is committed to the repo — it is the hand-made, precious
   artifact. Derived artifacts (FTS index, embeddings) stay gitignored.
4. **A plain-text export accompanies the binary DB.** After every mutation
   the server rewrites `topical-guide/guide_export.json` with deterministic
   ordering, so git history shows readable diffs of the curation. Also
   committed.
5. **Rejections are remembered.** Rejected verses stay in the DB so the
   same near-misses aren't re-litigated on every search. Search results are
   annotated with their existing status in the current topic.
6. **Provenance is recorded** on every topic–verse link: which kind of
   search produced it (`exact`, `prefix`, `phrase`, `semantic`, `manual`).
7. **Topics are flat for now.** No `parent_id` on topics — ever. Future
   relationships (see-also, broader/narrower) will be a separate
   `topic_links` edge table in a later phase.
8. **Notes are in the schema from day one** (a text column per topic–verse
   link and a description per topic), even though rich notes UI can come
   later. Notes will eventually feed semantic search and LLM features.
9. **Verse order within a topic is canonical scripture order** — verse IDs
   are sequential in canonical order, so `ORDER BY verse_id` suffices.

## Prerequisites

- `scriptures/scriptures.db` — committed, always present.
- `scriptures/scriptures_fts.db` — derived FTS5 index, built with
  `python scriptures/build_fts_db.py`. **The server must check for this at
  startup and exit with a clear message telling the user to run that script
  if it is missing.** Open it read-only
  (`sqlite3.connect("file:...?mode=ro", uris=True)`).
- Python deps: `fastapi`, `uvicorn`. Add `topical-guide/requirements.txt`.

### FTS5 quirks (read `scriptures/build_fts_db.py`'s docstring)

- The tokenizer is unicode61, **no stemmer** (Porter doesn't understand KJV
  forms like "believeth"). Word-family matching is done with prefix
  queries: `pray*` matches pray, prayer, prayers, praying, prayeth.
- `MATCH` takes a query language, not a plain string. Raw user input with
  stray quotes/dashes raises `sqlite3.OperationalError`. Always wrap terms
  in double quotes (escape embedded `"` by doubling), and catch
  `OperationalError` → HTTP 400.

## Files

```
topical-guide/
  PLAN.md              # this file
  requirements.txt     # fastapi, uvicorn
  server.py            # FastAPI app: serves static UI + JSON API
  guide.db             # curated data (committed; created by server on first run)
  guide_export.json    # deterministic text export (committed; rewritten on mutation)
  static/
    index.html         # the whole UI: vanilla JS, hash routing
```

## Schema (`guide.db`)

Created by `server.py` on startup if missing (`CREATE TABLE IF NOT EXISTS`).
Enable `PRAGMA foreign_keys = ON` on every connection.

```sql
CREATE TABLE IF NOT EXISTS topics (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS topic_verses (
    topic_id  INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    verse_id  INTEGER NOT NULL,  -- verses.id in scriptures.db (stable IDs)
    status    TEXT NOT NULL CHECK (status IN ('approved', 'rejected')),
    note      TEXT NOT NULL DEFAULT '',
    source    TEXT NOT NULL CHECK (source IN ('exact','prefix','phrase','semantic','manual')),
    added_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (topic_id, verse_id)
);
```

`verse_id` cannot be a real foreign key (different database file); instead
the server validates on insert that the ID exists in the scriptures DB and
returns 400 if not.

## API (Phase 1)

All endpoints under `/api`, JSON in/out. Static UI served at `/`.

| Method & path | Body / params | Returns |
|---|---|---|
| `GET /api/topics` | — | `[{id, name, description, approved_count}]`, ordered by name |
| `POST /api/topics` | `{name, description?}` | created topic; 409 if name exists |
| `PATCH /api/topics/{id}` | `{name?, description?}` | updated topic |
| `DELETE /api/topics/{id}` | — | 204; cascades topic_verses |
| `GET /api/topics/{id}` | — | topic + `verses`: approved links joined with full verse text/reference from the scriptures DB, ordered by `verse_id`; plus `rejected_count` |
| `POST /api/topics/{id}/verses` | `{verse_id, status, source, note?}` | upsert the link (idempotent; changing status via re-post is fine) |
| `PATCH /api/topics/{id}/verses/{verse_id}` | `{status?, note?}` | updated link |
| `DELETE /api/topics/{id}/verses/{verse_id}` | — | 204; fully forgets the link (including a rejection) |
| `GET /api/search` | `q`, `mode` = `prefix` (default) \| `exact` \| `phrase`, optional `topic_id`, `limit` (default 50) | see below |

### Search behavior

- **prefix** (default; labeled "word forms" in the UI): each whitespace
  term becomes `"term"*`, terms AND-ed. Catches KJV families (pray →
  prayeth).
- **exact**: each term becomes `"term"`, AND-ed.
- **phrase**: the whole query as one quoted phrase.
- Results ordered by FTS5 `rank`, joined with book/chapter/verse/reference.
  Use FTS5 `highlight()` to return text with `<mark>…</mark>` around
  matches. Include total match count alongside the limited results.
- When `topic_id` is given, each result carries
  `status_in_topic: 'approved' | 'rejected' | null` (look up the topic's
  links in Python; no cross-database JOIN needed).

### Export

After every mutating request, rewrite `guide_export.json`: topics ordered
by name; within each topic, verses ordered by `verse_id`; each entry as
`{reference: "Alma 37:37", status, source, note}`. Stable key order and
`indent=2` so diffs are clean. Do not include full verse text (it lives in
scriptures.db; the export is a curation record, not a scripture copy).

## Frontend (Phase 1)

One `static/index.html`, vanilla JS, hash routing, `fetch` to the API.
Simple clean styling consistent with the repo's existing pages. Views:

1. **`#/` — Guide home.** List of topics with approved-verse counts;
   inline form to add a topic. Click a topic → topic page.
2. **`#/topic/{id}` — Topic page**, with two tabs:
   - **Study tab** (default): topic name, description, then the approved
     verses in canonical order — reference ("Alma 37:37") + full verse
     text, with the note shown under a verse when present. Readable,
     uncluttered; this *is* the topical guide.
   - **Curate tab** (the workbench): search box + mode selector
     (word forms / exact / phrase) + match count. Each result shows
     reference, highlighted text, and Approve / Reject buttons — or a
     badge if already approved/rejected in this topic (with an undo).
     Approving/rejecting updates in place without losing the result list.
     Inline note editing on approved verses.

Verse references format as `Book Chapter:Verse` (the D&C's `chapter`
column is its section number, so "D&C 4:2" still reads correctly).

## Phase 1 acceptance checklist

Verify each of these by actually doing it:

- [ ] Fresh start: with `scriptures_fts.db` missing, `python server.py`
      exits with a message naming the build command.
- [ ] After building the FTS DB, the server starts and creates `guide.db`.
- [ ] Create topic "Prayer"; it appears on the home page.
- [ ] Search `pray` in word-forms mode → results include verses containing
      "prayer" and "prayeth"; match count shown.
- [ ] Search input with a stray quote (`pray"er`) returns a clean 400 /
      UI error, not a 500.
- [ ] Approve two verses, reject one. Study tab shows the two approved in
      canonical order with full text.
- [ ] Re-run the same search: results show approved/rejected badges
      instead of fresh Approve/Reject buttons.
- [ ] Add a note to an approved verse; it persists and shows in Study tab.
- [ ] Restart the server: everything persists.
- [ ] `guide_export.json` exists, reflects the state, and re-saving
      without changes produces no diff.
- [ ] README updated (see below) — same commit as the code.

## Repo conventions

- Update the top-level `README.md`: add a **Topical Guide** section
  (what it is, how to run it, what's committed vs derived) and add the
  tool to the repo's intro list. Docs and code go in the same commit.
- Add `topical-guide/guide.db.tmp`-style scratch patterns and any derived
  files to `.gitignore` if created; `guide.db` and `guide_export.json` are
  committed.
- Keep code style consistent with existing scripts (`build_fts_db.py`):
  plain Python, docstrings explaining the why, no heavy dependencies.

## AI writing helpers (round 1 — shipped)

Two Claude-backed drafting helpers, added after Phase 1: **fill a topic's
name/description** from a plain-language prompt, and **fill a topic–verse
note** from the verse text and topic context (optionally sharpening the
curator's own rough words). Full design lives in `AI-HELPERS-SPEC.md`.

- **Haiku 4.5 to start** (`claude-haiku-4-5`), a per-feature tuning knob in
  `ai.py`'s `FEATURES` registry — cheap to swap or tune per helper later.
- **One shared house style constant** (`_HOUSE_STYLE` in `ai.py`) steers every
  AI helper's prose, now and later: plain language, faithful to what the user
  typed, Hemingway rather than devotional register.
- **Call logs live in their own gitignored database**, `topical-guide/ai_log.db`
  — never `guide.db`. `guide.db` is the committed hand-made artifact; AI call
  logs must not bloat it or its export.
- **Fill, then review, then save.** Both endpoints
  (`POST /api/ai/topics/fill`, `POST /api/ai/topics/{id}/verses/{verse_id}/note/fill`)
  return unsaved values only. Nothing reaches `guide.db` until the existing
  `POST /api/topics` or `PATCH .../verses/{verse_id}` endpoints are called —
  the AI never writes, and a fill never triggers `write_export`.
- **Local-only, key in server env.** `ANTHROPIC_API_KEY` lives in
  `topical-guide/.env` (gitignored), read server-side only, never sent to the
  browser.

This round deliberately shipped only these two writing helpers — no verse
suggestion, no polish-existing-description, ~~no topic edit form~~
*(superseded — see below)*. Phase 4's "LLM-assisted candidate suggestion"
(below) is still ahead; it will build on these same prompt-constant and
logging conventions once semantic search (Phase 3) exists to seed it.

## Topic editing (round 2 — shipped)

Topics can now be renamed, re-described, and deleted from their own page
(`#/topic/{id}`), and a verse's note is editable from the Study tab as well
as Curate. Full design lives in `TOPIC-EDIT-SPEC.md`.

- **Edit and delete live on the topic page only.** An `Edit` button swaps the
  header into a form (name, description, `Delete topic`); the home list stays
  a read-only browse view.
- **A third AI helper, `polish_description`**, rewrites a topic's description
  from its approved verses (with their notes) and the other topic names —
  the round-1 `fill_topic` helper drafts from a prompt and knows nothing
  about what the topic has since become. It can also suggest a replacement
  *name*, but only when the approved verses show the current name is a poor
  fit; that suggestion, like the description, is unsaved until **Save
  changes** is pressed.
- **Delete is a hard delete**, behind a custom in-page confirmation modal
  that names the topic and the counts it destroys (approved verses,
  rejections, notes). No `window.confirm`, no archive, no trash — git history
  of `guide_export.json` is the only way back, and the modal says so.
  `ON DELETE CASCADE` removes the `topic_verses` links.
- **The AI never writes**, same hard rule as round 1: `polish_description`
  returns unsaved values only, and never triggers `write_export`.
- **No schema change.** Both endpoints (`PATCH`/`DELETE /api/topics/{id}`)
  existed since Phase 1; this round is the first thing to call them from the
  UI, and the first to test them.

## Later phases (do NOT build now — context only)

- **Phase 2 — review-loop polish:** saved searches per topic, provenance
  display/filtering, markdown export of the guide, richer notes UI.
- **Phase 3 — semantic search:** first a throwaway experiment script to
  test embedding quality on KJV text (embed all verses with a local
  sentence-transformers model; eyeball results for ~10 topic queries —
  e.g. does "prayer" surface Alma 37:37 "counsel with the Lord"?). If
  quality passes: `build_embeddings.py` (derived, gitignored), a semantic
  search mode, and "suggest more like this topic" seeded by the topic's
  approved verses + notes. Sources gain `'semantic'` (already in the
  schema CHECK).
- **Phase 4 — ontology & LLM features:** `topic_links` typed-edge table
  (see-also etc.), LLM-assisted candidate suggestion using notes.
