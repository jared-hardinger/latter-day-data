# Passages — curating a range of verses as one unit (round 6)

## What and why

The unit of curation is currently one verse, and that is the wrong unit for a
lot of scripture. 3 Nephi 18:15–16 is one thought about prayer; 18:18–23 is a
different, longer thought about the same subject. Today the guide can only
hold those as ten unrelated rows, each with its own note slot, each rendering
as its own block, with nothing recording that 15 and 16 belong together or
that 17 was deliberately left out.

This round makes **a passage the thing you add to a topic**. You select a
contiguous range of verses inside one chapter, and it becomes a single entry:
one block on the Study tab, one note, one Remove. A single verse is a passage
of length one, so nothing about the one-verse case gets worse.

The selection happens inside the round-5 chapter panel, which already puts the
whole chapter in front of you. Round 5's spec parked panel mutation as "the
obvious round 6, and the panel is where it goes." This is that round.

---

## Decisions (settled with Jared — do not reopen)

1. **Explicit ranges, not automatic clustering.** A passage exists because you
   selected it. Two passages from the same chapter stay two separate blocks —
   that distinction (15–16 vs. 18–23) is the entire point. The app never
   invents a grouping from verses that merely happen to share a chapter.
2. **The chapter panel is where you select.** Click a verse to anchor,
   shift-click another to set the far end, add the range to the topic. You are
   reading the actual text while choosing the boundaries.
3. **A passage is one thing when counting.** The Study header reads
   `8 passages · 17 verses`; the home-page card and the volume chips count
   passages. Chip counts then equal the number of blocks you can see.
4. **One note per passage.** The note describes the idea the passage carries.
   When entries merge, their notes concatenate rather than one winning.

---

## Judgment calls, flagged

Decided, with reasoning, but these are the softest points — push back now
rather than after it's built.

- **Ranges must be contiguous and within one book and chapter.** The data
  model could hold an arbitrary set of verse ids, but "3 Ne 18:15, 16, 20" has
  no reference notation, no sensible render order with gaps, and no clear
  meaning distinct from two passages. Contiguity is what makes the reference
  string `3 Nephi 18:15–16` honest.
- **Overlapping adds merge; adjacent adds do not.** Adding 16–18 when 15–16
  exists yields one entry, 15–18, because the schema only lets a verse live in
  one entry. Adding 17 when 15–16 exists yields two entries, because
  auto-joining on adjacency would quietly destroy exactly the boundary the
  feature exists to preserve.
- **Multi-verse entries are always approved.** Rejection is a Curate-tab
  tombstone on a single search hit; "half this passage is rejected" is not a
  state with a meaning. A new approved range absorbs and deletes any rejected
  singletons it covers, and `PATCH status=rejected` on a multi-verse entry is
  a 422 that tells you to remove it instead.
- **En dash in the UI, hyphen in the export.** `3 Nephi 18:15–16` on screen
  because that is how the reference is set in print; `3 Nephi 18:15-16` in
  `guide_export.json` because that file is a git artifact you grep and diff,
  and ASCII keeps it so.
- **Splitting and trimming a passage are out of scope.** To turn 15–18 into
  15–16, remove it and re-add. Removing a middle verse would fork one entry
  into two and force a decision about which half keeps the note; that deserves
  its own round if it turns out to be a real need.

---

## Part 1 — the data model

### The shape, and why it changes

Today `topic_verses` carries `status`, `note`, and `source` on the verse row.
Grouping verses under a shared note means those three columns describe the
group, not the verse. Two ways to get there:

- Add a nullable `group_id` to `topic_verses` and a table to hold the group
  note. Cheapest diff, but `status` stays per-verse — the schema then permits a
  passage that is half approved and half rejected — and a note can live in two
  different places depending on whether the entry is a singleton.
- Split the row in two: an **entry** carries status, note, and source; a
  **verse link** maps an entry to each verse it covers. Illegal states stop
  being representable, and a note has exactly one home.

Take the second. `guide.db` currently holds 5 topics and 18 links, so the
migration is cheap now and will never be cheaper.

### New schema

```sql
CREATE TABLE topic_entries (
    id        INTEGER PRIMARY KEY,
    topic_id  INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    status    TEXT NOT NULL CHECK (status IN ('approved', 'rejected')),
    note      TEXT NOT NULL DEFAULT '',
    source    TEXT NOT NULL CHECK (source IN ('exact','prefix','phrase','semantic','manual')),
    added_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE topic_verses (
    topic_id  INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    entry_id  INTEGER NOT NULL REFERENCES topic_entries(id) ON DELETE CASCADE,
    verse_id  INTEGER NOT NULL,
    PRIMARY KEY (topic_id, verse_id)
);

CREATE INDEX IF NOT EXISTS idx_topic_verses_entry ON topic_verses(entry_id);
CREATE INDEX IF NOT EXISTS idx_topic_entries_topic ON topic_entries(topic_id);
```

`topic_id` stays denormalised onto `topic_verses` on purpose: it keeps the
primary key `(topic_id, verse_id)`, which is what makes *a verse belongs to at
most one entry per topic* an invariant the database enforces rather than
something the application has to remember. The cascade from `topics` fires on
both tables; deleting an entry cascades to its verse links.

Invariants the application maintains and the tests assert:

- Every verse of an entry shares one book and chapter, and the verse ids are
  contiguous (they are sequential by volume/book/chapter/verse — see README
  *Verse ordering*).
- An entry with more than one verse always has `status = 'approved'`.
- Ordering everywhere is by the entry's lowest verse id.

### Migration

`init_guide_db()` currently only runs `CREATE TABLE IF NOT EXISTS`. It gains a
one-shot migration that runs when the old shape is detected:

```python
def needs_entry_migration(conn) -> bool:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(topic_verses)")}
    return bool(cols) and "status" in cols


def migrate_to_entries(conn):
    """Old shape: one topic_verses row per verse, carrying status/note/source.
    New shape: topic_entries holds those, topic_verses maps entries to verses.
    Every pre-existing row becomes a singleton entry — grouping is explicit, so
    the migration never infers a passage from verses that happen to adjoin."""
    conn.execute("PRAGMA foreign_keys = OFF")
    with conn:
        conn.execute("ALTER TABLE topic_verses RENAME TO topic_verses_old")
        conn.executescript(SCHEMA)
        for row in conn.execute(
            """SELECT topic_id, verse_id, status, note, source, added_at
               FROM topic_verses_old ORDER BY topic_id, verse_id"""
        ).fetchall():
            cur = conn.execute(
                """INSERT INTO topic_entries (topic_id, status, note, source, added_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (row["topic_id"], row["status"], row["note"], row["source"], row["added_at"]),
            )
            conn.execute(
                "INSERT INTO topic_verses (topic_id, entry_id, verse_id) VALUES (?, ?, ?)",
                (row["topic_id"], cur.lastrowid, row["verse_id"]),
            )
        conn.execute("DROP TABLE topic_verses_old")
    conn.execute("PRAGMA foreign_keys = ON")
```

Before running it against the committed database, copy `guide.db` to
`guide.db.bak` outside the repo, and afterwards assert that entry count equals
the old row count and that every note survived. `guide.db.bak` is not
committed.

---

## Part 2 — server

### Reference formatting

One helper, used by the API, the export, and the AI prompts:

```python
def entry_reference(fts_db, verse_ids, dash="–") -> str:
    """'3 Nephi 18:15' or '3 Nephi 18:15–16'. verse_ids must be one contiguous
    range within one chapter — the entry invariant guarantees it."""
    first = fts_db.execute(
        "SELECT book, chapter, verse FROM v_verses WHERE id = ?", (verse_ids[0],)
    ).fetchone()
    if first is None:
        return None
    base = f"{first['book']} {first['chapter']}:{first['verse']}"
    if len(verse_ids) == 1:
        return base
    last = fts_db.execute(
        "SELECT verse FROM v_verses WHERE id = ?", (verse_ids[-1],)
    ).fetchone()
    return f"{base}{dash}{last['verse']}"
```

`write_export` calls it with `dash="-"`; everything else takes the default.

### Range validation and merge

The single place that turns a requested range into an entry:

```python
def expand_range(fts_db, start_verse_id: int, end_verse_id: int) -> list[int]:
    """Validate a requested range and return its verse ids in order."""
    if end_verse_id < start_verse_id:
        start_verse_id, end_verse_id = end_verse_id, start_verse_id
    rows = fts_db.execute(
        "SELECT id, book_id, chapter FROM v_verses WHERE id IN (?, ?)",
        (start_verse_id, end_verse_id),
    ).fetchall()
    if len(rows) < (1 if start_verse_id == end_verse_id else 2):
        raise HTTPException(400, "Verse does not exist")
    if rows[0]["book_id"] != rows[-1]["book_id"] or rows[0]["chapter"] != rows[-1]["chapter"]:
        raise HTTPException(400, "A passage must stay within one chapter")
    ids = [
        r["id"] for r in fts_db.execute(
            "SELECT id FROM v_verses WHERE id BETWEEN ? AND ? ORDER BY id",
            (start_verse_id, end_verse_id),
        )
    ]
    if len(ids) > MAX_PASSAGE_VERSES:
        raise HTTPException(422, f"A passage is limited to {MAX_PASSAGE_VERSES} verses")
    return ids
```

`MAX_PASSAGE_VERSES = 40` — a guard against a fat-fingered selection swallowing
a whole chapter, not a theological position.

Absorption, run inside the POST's transaction once the range is known:

1. Find every existing entry in this topic that owns any verse in the range.
2. Delete those entries' verse links and the entries themselves.
3. Collect their verse ids — an overlapping **approved** entry contributes its
   verses even where they fall outside the requested range, so adding 16–18
   over an existing 15–16 produces 15–18, not 16–18 plus an orphaned 15.
   Re-validate the union with `expand_range` on its min and max.
4. Rejected entries are always singletons and contribute nothing beyond the
   overlapping verse itself; they are simply dropped.
5. The surviving note is every distinct non-empty note of the absorbed
   approved entries, in verse order, joined by `"\n\n"`, with the request's own
   `note` appended last if it supplied one.
6. `source` comes from the request. Panel selection sends `manual`; a Curate
   approve keeps sending the search mode, so the enum is unchanged.

### Endpoints

Entries replace verse links as the addressable resource. The old
`/verses/{verse_id}` routes are removed rather than kept as aliases — this app
has one user and one client, and two ways to mutate the same state is how the
counts drifted in round 3.

| Method | Path | Body | Notes |
| --- | --- | --- | --- |
| `POST` | `/api/topics/{topic_id}/entries` | `{start_verse_id, end_verse_id, status, source, note?}` | Creates or merges. Returns the resulting entry. `end_verse_id` defaults to `start_verse_id`. |
| `PATCH` | `/api/topics/{topic_id}/entries/{entry_id}` | `{status?, note?}` | 422 on `status='rejected'` for a multi-verse entry. |
| `DELETE` | `/api/topics/{topic_id}/entries/{entry_id}` | — | Cascades to verse links. 204. |

The entry object returned everywhere:

```json
{
  "entry_id": 12,
  "reference": "3 Nephi 18:15–16",
  "verse_ids": [31145, 31146],
  "verses": [{"verse_id": 31145, "verse": 15, "text": "…"}],
  "volume": "Book of Mormon",
  "volume_id": 3,
  "status": "approved",
  "source": "manual",
  "note": "…"
}
```

`GET /api/topics/{topic_id}` returns `entries` in place of `verses`, ordered by
lowest verse id, plus:

- `volume_counts[].count` — approved **entries** per volume.
- `passage_count`, `verse_count` — for the Study header.
- `rejected_count`, `note_count` — unchanged in meaning, now counted over
  entries.

`GET /api/search` gains `entry_id` and `entry_reference` on each result, so a
row whose verse sits inside a passage can say so instead of just "Approved".
`status_in_topic` keeps its current meaning and is read through the join.

`GET /api/chapter` gains an optional `topic_id`. When present, each verse in
the response carries `entry_id` (or null) so the panel can tint what is
already curated while you choose a range.

### AI helpers

- `fill_note` moves from `/verses/{verse_id}/note/fill` to
  `/entries/{entry_id}/note/fill`. The passage becomes the marked span in the
  prompt — every verse of the entry gets the `>>` marker, and the ±2 context
  window is measured from the entry's ends rather than from a single verse.
- `polish_description` iterates entries instead of verse links; its per-verse
  lines become per-entry lines using `entry_reference` and the concatenated
  text. Its 40-item cap now counts entries.

### Export

```json
{
  "name": "Prayer",
  "description": "…",
  "verses": [
    {"reference": "3 Nephi 18:15-16", "verse_count": 2,
     "status": "approved", "source": "manual", "note": "…"}
  ]
}
```

The `verses` key keeps its name so existing diffs stay readable; `verse_count`
is new. Ordering is by lowest verse id, as before.

---

## Part 3 — the chapter panel becomes a selector

The panel keeps its round-5 anatomy (mounted on `document.body`, one instance,
`chapterPanelToken` staling in-flight fetches) and gains selection plus an
action bar.

### Selection

- Every `.cv` row becomes a `<button class="cv">`; the whole row is the target,
  not just the number.
- **Click** sets the anchor and collapses the selection to that one verse.
  Clicking the currently-selected single verse clears the selection.
- **Shift-click** sets the far end; the selection is `min…max` of anchor and
  clicked verse.
- Selected rows get `.cv-selected`. Verses already in the topic get
  `.cv-curated` (a left border in the approved colour) regardless of selection,
  from the `entry_id` the chapter endpoint now returns.
- The round-5 `.cv-current` highlight stays — it marks the verse you clicked
  through from, which is not the same thing as what you have selected.

### Action bar

Sticky at the bottom of the panel, present only when something is selected:

```
3 Nephi 18:15–16 · 2 verses          [ Add to Prayer ]  [ Clear ]
```

Three states:

1. **Selection touches nothing curated** — `Add to Prayer`.
2. **Selection overlaps an existing entry** — `Merge into 3 Nephi 18:15–18`,
   labelled with the reference the merge will produce, so the button says what
   it will do before you press it.
3. **Selection is exactly an existing entry** — `Remove from Prayer`, plus the
   note shown read-only beneath. Editing the note stays on the Study tab; the
   panel does one job.

When nothing is selected the bar is replaced by the hint *Click a verse,
shift-click another to select a range.*

### After a successful add

The response entry has to reach two in-memory caches that the panel does not
own — `topic` (Study) and `curateState` (Curate) — and the panel's own tint:

```js
function applyEntryChange(entry, removedEntryIds) { … }
```

It removes any absorbed entries from `topic.entries`, splices the new entry in
at its lowest-verse-id position, recomputes `topic.volume_counts` from
`topic.entries` the way `patchStudySummary` already does, patches every
affected row in `curateState.results` with the new `status_in_topic` and
`entry_id`, re-renders whichever tab is live, and re-tints the open panel.

---

## Part 4 — Study tab

One block per entry:

```html
<div class="entry-block" data-entry-id="12">
  <button type="button" class="verse-ref ref-link" data-verse-id="31145">3 Nephi 18:15–16</button>
  <div class="verse-text">
    <span class="ev"><span class="ev-num">15</span>Verily, verily…</span>
    <span class="ev"><span class="ev-num">16</span>For verily…</span>
  </div>
  <div class="note-area" data-entry-id="12">…</div>
</div>
```

- Verse numbers render only when the entry has more than one verse; a
  singleton block is visually identical to today's.
- The reference opens the chapter panel on the entry's first verse.
- `Remove` removes the whole entry. The existing removed-strip and undo path
  carries over; undo re-POSTs the entry's range, note, and source. As in round
  5, the render path and the undo path must share one `wireEntryBlock` — a
  button wired only in the render path dies after an undo.
- Header: `In this topic — 8 passages · 17 verses`. Volume chips show passage
  counts and filter blocks, so a chip's number always equals the number of
  blocks it reveals.

## Part 5 — Curate tab

Search results are still per verse; that is correct, since a match is a verse.
What changes is what an already-curated row says:

- Verse is a singleton entry → `Approved` / `Rejected`, exactly as today.
- Verse is inside a multi-verse entry → badge reads `In 3 Nephi 18:15–16`, and
  the action reads `Remove passage` rather than `Undo`, because it deletes an
  entry larger than the row you are looking at.
- `Approve` on an uncurated row posts a one-verse range — the existing
  one-click flow is untouched.

Escape ordering in the single global `keydown` listener becomes: delete modal →
panel selection (clear it) → panel (close it) → topic edit mode. A selection is
more transient than the panel that holds it, so it goes first.

---

## Tests (`test_server.py`)

- Migration: build an old-shape database with notes on both an approved and a
  rejected row, run `init_guide_db`, assert one singleton entry per old row
  with every note, status, and source preserved.
- `expand_range`: cross-chapter → 400; reversed ends normalise; nonexistent
  verse → 400; over `MAX_PASSAGE_VERSES` → 422.
- Adding 15–16 then 16–18 yields one entry 15–18 with both notes joined.
- Adding 15–16 then 17 yields two entries.
- An approved range covering a rejected singleton deletes the rejected entry.
- `PATCH status='rejected'` on a multi-verse entry → 422.
- `DELETE` of an entry removes all of its verse links.
- `GET /api/topics/{id}` ordering by lowest verse id, and
  `passage_count` / `verse_count` / `volume_counts` arithmetic.
- `GET /api/chapter?topic_id=` marks curated verses with the right `entry_id`.
- Export: hyphen dash, `verse_count`, deterministic order.

Run the full suite, not just the new file — every endpoint touching
`topic_verses` changed.

## Docs to update (same commit as the code)

`README.md`: the topical-guide *Schema* block, *Editing and deleting topics*
(entry-level removal), *Verse ordering* (ordering key is now the entry's lowest
verse id), *Search* (the new result fields), *Verse context* (the panel now
selects and mutates), *What's committed vs. derived* (export shape), and a new
*Passages* section covering the contiguity rule, merge-on-overlap, and the
counting convention.

## Out of scope

- Splitting or trimming an existing passage.
- Non-contiguous passages, and passages crossing a chapter or book boundary.
- Automatic clustering of same-chapter entries under a shared heading — the
  third option from the grouping decision, worth revisiting once there are
  enough real passages to see whether the Study tab wants it.
- Per-verse notes inside a passage.
- Reordering entries by anything other than canonical order.
- Drag-selection and keyboard arrow navigation inside the panel; click and
  shift-click only.
