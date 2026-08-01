# Volume summary and filter — round 4

## What and why

Both tabs on a topic page show a flat list with no sense of shape. The Study
tab shows every approved verse in verse-ID order; the Curate tab shows the
first 50 of however many matched. Neither answers the question you actually
have while curating: **where in the canon does this topic live, and where is
it thin?** A topic with 52 Old Testament verses and 0 New Testament verses is
not finished, and today nothing on the screen says so.

This round adds a **volume summary strip** to both tabs — all five volumes,
always, zeros included — and makes each volume clickable as a filter.

The two strips look identical and share a renderer, but they count different
things, and that difference is the whole design:

| | Study tab | Curate tab |
|---|---|---|
| Counts | approved verses in this topic | matches for the current search |
| Changes when | you remove or undo a verse | you run a new search |
| Does *not* change when | you search | you approve or reject |
| Filtering is | client-side (list is fully in memory) | server-side (re-query) |
| When empty | strip is hidden entirely | strip is hidden until first search |

---

## Decisions (settled with Jared — do not reopen)

1. **Both tabs get a strip.** Study's counts approved verses; Curate's counts
   search matches. Same component, two data sources.
2. **All five volumes always render, in canonical order, including zeros.**
   `Old Testament, New Testament, Book of Mormon, Doctrine and Covenants,
   Pearl of Great Price` — the order of `volumes.id` in `scriptures_fts.db`.
   The zeros are the entire point: "New Testament: 0" is the signal being
   acted on, so hiding it defeats the feature. Fixed layout also means the
   strip never reflows between searches.
3. **The Study strip is hidden entirely when the topic has no approved
   verses.** The existing "No approved verses yet" empty state stands alone —
   five zeros above it would be noise.
4. **Clicking a volume filters the list below it; a Clear filter control
   returns to everything.** The active volume is visibly selected.
5. **Curate filtering is server-side**, via a new `volume_id` query param on
   `/api/search`. Filtering the 50 already-loaded rows client-side would be a
   lie: clicking "Old Testament 101" would show only the OT verses that
   happened to land in the top 50, and the other ~70 would be unreachable.
6. **The Curate strip's counts always describe the *unfiltered* query.** They
   stay stable while you click between volumes, so the strip stays a
   navigation control rather than collapsing to a single non-zero row the
   moment you use it.
7. **No new tables, no new columns, no migration.** Volume is already
   available through `v_verses` in the read-only FTS database.

---

## Judgment calls, flagged

These are decided, with reasoning, but they are the softest points in the spec.

- **The Curate volume filter resets on every new search.** Counts are
  per-query; carrying an Old Testament filter into a query with no Old
  Testament hits would render an empty list for a reason that is one scroll
  off screen. Resetting costs one extra click in the rarer case.
- **The Study filter is session-only** — it clears on tab switch, navigation,
  and reload, and is *not* stored in a persistent state object. This follows
  the precedent set by round 3's undo strip (`VERSE-REMOVE-SPEC.md`, decision
  3) rather than the precedent set by `curateState`. Curate keeps its state
  across tab switches because an in-progress *search* is expensive to
  recreate; a filter click is not.
- **When the Curate list is filtered, `total` reports the filtered count and
  the strip's counts do not.** So the screen can read "101 matches (showing
  50)" under a strip whose Old Testament chip reads 101 and whose other chips
  still show their unfiltered numbers. This is the correct pairing — the
  match line describes what is listed, the strip describes the query — but it
  is the most likely thing to be misread, so the strip needs a label that
  says what it is counting (see *Labeling*, below).
- **Removing a verse while a Study filter is active leaves the filter on**,
  even if it takes that volume to zero. Auto-clearing would re-render the
  list and destroy every visible undo strip. The user clears it themselves.

---

## Backend

### `GET /api/topics/{topic_id}` — `server.py:272`

Two additions:

1. Each entry in `verses` gains `volume` and `volume_id`. The per-verse
   lookup at `server.py:296` already selects from `v_verses`; widen it:

   ```python
   "SELECT volume, volume_id, book, chapter, verse, text FROM v_verses WHERE id = ?"
   ```

2. The response gains `volume_counts`: all five volumes in canonical order,
   counting **approved** verses only (the loop already only walks approved
   links).

   Seed from `SELECT id, name FROM volumes ORDER BY id` against `fts_db`, then
   tally in Python from the rows already fetched. Do **not** write a new
   cross-database aggregate — the approved verse IDs live in `guide.db` and
   the volumes live in `scriptures_fts.db`, so there is no join to write, and
   the data is already in hand.

   > This leaves the existing per-verse N+1 query in place. That is
   > pre-existing and out of scope for this round; do not refactor it.

Response shape:

```json
{
  "id": 3,
  "name": "Fellowship",
  "verses": [
    { "verse_id": 31102, "volume": "New Testament", "volume_id": 2,
      "reference": "Acts 2:42", "text": "...", "note": "", "source": "prefix" }
  ],
  "volume_counts": [
    { "volume_id": 1, "volume": "Old Testament",        "count": 52 },
    { "volume_id": 2, "volume": "New Testament",        "count": 0  },
    { "volume_id": 3, "volume": "Book of Mormon",       "count": 11 },
    { "volume_id": 4, "volume": "Doctrine and Covenants","count": 2  },
    { "volume_id": 5, "volume": "Pearl of Great Price", "count": 0  }
  ],
  "rejected_count": 8,
  "note_count": 4
}
```

### `GET /api/search` — `server.py:441`

New optional param `volume_id: Optional[int] = None`, validated against the
`volumes` table (404 on unknown, matching how `topic_id` is handled).

**The aggregate query — use exactly this form:**

```sql
SELECT vv.volume_id, vv.volume, COUNT(*) AS count
FROM verses_fts f JOIN v_verses vv ON vv.id = f.rowid
WHERE verses_fts MATCH ?
GROUP BY vv.volume_id ORDER BY vv.volume_id
```

Then seed the missing volumes to zero in Python from
`SELECT id, name FROM volumes ORDER BY id`.

> **Do not** try to get the zeros from SQL with
> `LEFT JOIN verses_fts f ON f.rowid = vv.id AND verses_fts MATCH ?`. It
> returns correct results on narrow queries and is pathologically slow on
> broad ones — measured at **over 120 seconds** (killed, never finished) for
> `"the"*`, where the form above returns in **21 ms**. The `MATCH` must sit in
> a `WHERE` clause so FTS5 drives the query; in a `LEFT JOIN … ON` it does
> not.

This aggregate **replaces** the standalone
`SELECT COUNT(*) FROM verses_fts WHERE verses_fts MATCH ?` at `server.py:471`
when unfiltered — `total` is the sum of the counts. No net new query.

The counts are computed on the **unfiltered** match set even when `volume_id`
is supplied (decision 6). When `volume_id` is supplied:

- the row query gains `JOIN v_verses vv ON vv.id = f.rowid` and
  `AND vv.volume_id = ?`, keeping `ORDER BY rank LIMIT ?` (verified working
  and fast);
- `total` becomes that volume's count, so `total` and the returned rows agree.

Response shape adds `volume_counts` (same array shape as above) alongside the
existing `total` and `results`. `status_in_topic` behavior is unchanged.

**Verified numbers** for the manual check below — `money`, word-forms mode:
OT 101, NT 24, BoM 13, D&C 38, PoGP 1, total 177.

---

## Frontend — `static/index.html`

### Shared component

Follow the existing `noteRowHtml` / `wireNoteRow` split (`index.html:917`) —
a pure HTML builder plus a wiring function:

```js
function volumeSummaryHtml(counts, activeVolumeId, label)  // -> string
function wireVolumeSummary(root, onSelect)                 // onSelect(volumeId|null)
```

`volumeSummaryHtml` renders every entry it is given, in the given order —
it does **not** know the volume names. Both callers get the full five-entry
array from the API, so there is no hardcoded canon list in the JS.

### Labeling

The strip needs a label naming what it counts, because the two tabs' numbers
mean different things and a bare row of numbers under "177 matches" invites
the misreading called out in *Judgment calls*:

- Study: `In this topic — 65 verses`
- Curate: `177 matches by volume`

### Study tab — `renderStudyTab` at `index.html:622`

- Render the strip above the verse list when `topic.verses.length > 0`;
  the existing early-return empty state is untouched (decision 3).
- Hold the active filter in a module-level `let studyVolumeFilter = null;`
  reset wherever the topic page renders and on tab switch (decision: it is
  session-only, and deliberately *not* part of a persisted state object).
- Filter at render: `topic.verses.filter(v => !f || v.volume_id === f)`.

**The one real integration wrinkle.** `removeStudyVerse` (`index.html:665`)
deliberately never re-renders the list — it swaps a single `.verse-block` for
an undo strip in place, so that other verses' undo strips survive. A full
re-render would destroy them. So the summary counts must be patched *without*
re-rendering the list.

`forgetVerseLocally` (`index.html:681`) and `restoreVerseLocally`
(`index.html:722`) are already exactly the "local state changed" hooks — both
already call `patchCuratedStatus` for the same reason. Add a
`patchStudySummary(topic)` call to both; it recomputes counts from
`topic.verses` and replaces only the strip's inner HTML.

Note that `undoStudyRemove` re-POSTs from the in-memory `verse` object, which
still carries `volume_id` — so restore works with no extra fetch.

### Curate tab — `renderCurateTab` at `index.html:776`

- `curateState` gains `volumeId: null` and `volumeCounts: []`.
- Render the strip between `#match-count` and `#results` — this is the
  position in the screenshot, below the match line and above the first result.
- Clicking a volume re-runs `doSearch` with `volume_id`; **Clear filter**
  re-runs it with none.
- `doSearch` resets `volumeId` to `null` whenever the query or mode changes
  (judgment call 1), and stores `data.volume_counts`.
- Approving or rejecting must **not** touch the counts — they describe the
  match set, not the topic. `patchCuratedStatus` (`index.html:729`) stays as
  is; verify it does not accidentally trigger a strip re-render.

---

## Edge cases to handle

- Filter active, then every verse in that volume is removed → empty list with
  the filter still on. Show a "No {volume} verses in this topic." message
  rather than blank space, with the Clear filter control still reachable.
- Curate: clicking a volume whose count is 0 — either make zero-count chips
  non-interactive, or let them filter to an empty list with the same message.
  Prefer non-interactive; a zero chip is information, not a destination.
- Search error path (`doSearch` catch, `index.html:822`) must clear
  `volumeCounts` along with `results`/`total`, or a stale strip outlives its
  query.
- Topic page loaded directly by hash URL while a filter would otherwise be
  remembered — filters start null on every page render.

---

## Tests

`test_server.py` — backend only; the frontend has no test harness in this repo.

- `GET /api/topics/{id}` returns exactly five `volume_counts` in canonical
  order, zeros included, on a topic with verses in only one volume.
- The same, on a topic with **no** verses — still five entries, all zero.
  (The frontend hides the strip; the API stays uniform.)
- `volume_counts` counts approved only — a rejected verse does not appear.
- Each verse in `verses` carries `volume` and `volume_id`.
- `GET /api/search` returns five `volume_counts` summing to `total`.
- `GET /api/search?volume_id=N` returns only verses from that volume, and
  `total` equals that volume's count from the unfiltered strip.
- `volume_counts` is **identical** with and without `volume_id` for the same
  query — this is decision 6 and the easiest thing to regress.
- Unknown `volume_id` → 404.
- `volume_id` combined with `topic_id` still returns correct
  `status_in_topic` values.

---

## Docs to update in the same commit

Per the repo's workflow rule, docs land with the code:

- `README.md` → **Topical Guide** section. The `### Search` subsection
  (line 195) gains the `volume_id` filter; a short paragraph near the Study
  tab description (lines 142–152) covers the summary strip on both tabs and
  the difference in what each counts.

---

## Out of scope

- Book-level breakdown (Genesis: 4, Isaiah: 9). Volume-level first; book-level
  is a natural round 5 and the shared component is the place it would go.
- Multi-select filtering (OT **and** BoM at once). Single volume or all.
- Persisting either filter across reloads.
- Sorting or grouping the Study list by volume — this round filters, it does
  not reorder. Verse-ID order is preserved within a filter.
- Any change to `guide_export.json`. Volume is derivable from the reference
  and this round adds no curation data.
- Fixing the pre-existing N+1 verse lookup in `get_topic`.
