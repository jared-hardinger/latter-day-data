# Verse context — chapter panel and official-site link (round 5)

## What and why

Every place the app shows a verse, it shows exactly one verse. That is the
wrong unit for the question you actually have while curating: *is this verse
saying what I think it's saying?* A verse pulled out of Alma 39 by a keyword
search reads very differently once you can see the two verses on either side
of it, and today the only way to find that out is to leave the app.

This round makes **the reference itself the way in**. Clicking `Jacob 2:18`
on either tab slides open a right-side panel with the whole of Jacob 2,
verse 18 highlighted and scrolled to. The panel's header carries a link to
the same chapter on churchofjesuschrist.org, opening in a new tab, for when
you want footnotes and cross-references the local database does not have.

The two halves answer different needs. The panel is the fast one — no
network round trip to a heavy site, no lost place, no tab. The link is the
authoritative one. Neither replaces the other, which is why both ship.

---

## Decisions (settled with Jared — do not reopen)

1. **Both the panel and the external link.** The panel is the primary
   experience; the church link lives in the panel header, not on the verse
   rows. Reaching the official site costs two clicks, and that is the right
   price — the rows stay uncluttered and the panel usually ends the question
   before you get there.
2. **The reference is the click target**, on both tabs. Not a separate icon.
   The reference is already where the eye goes and already rendered in link
   blue; it gains a hover underline and a pointer cursor.
3. **The panel is read-only.** No approve, reject, or note editing inside it.
   A second place that mutates topic state means a second place that can
   drift out of sync with the Study list, the Curate results, and the volume
   summary counts. Deliberately deferred — see *Out of scope*.
4. **The external link opens in a new tab**, `target="_blank"` with
   `rel="noopener noreferrer"`.
5. **The church-site slugs come from the database, not a map in `server.py`.**
   The source CSV already carries `volume_lds_url` and `book_lds_url` for all
   five volumes and all 87 books; `build_scriptures_db.py` currently discards
   them. Store them instead. The cost is a rebuild of the committed 7.6 MB
   `scriptures.db` binary; the alternative is hand-maintaining 92 slugs in
   application code, forever, next to a source that already has them right.

---

## Judgment calls, flagged

Decided, with reasoning, but these are the softest points.

- **The panel mounts on `document.body`, outside `#app`.** Both tabs
  re-render `#tab-content` freely — Study on every volume-filter click,
  Curate on every approve/reject — and a panel living inside it would be
  destroyed mid-read. Outside, it simply persists.
- **On wide viewports the page content shifts left rather than being
  covered**, via `body.panel-open { padding-right: … }`. `#app` is a centered
  874 px column, so a 440 px drawer would otherwise sit on top of it at
  typical laptop widths. Below 900 px the drawer goes full-width and overlays,
  because there is nowhere to shift to.
- **Escape closes the panel, ordered after the delete modal and before topic
  edit mode.** The existing single global listener (`index.html:343`) already
  encodes this "topmost thing first" rule; the panel slots in between.
- **Removing the verse a panel was opened from leaves the panel open.** It is
  read-only context, not a view of the link that was just deleted, and
  auto-closing would yank the chapter out from under someone mid-sentence.
- **The references are `<button>`, not `<a>`.** They open a panel; they do not
  navigate. The one real anchor in this feature is the external link, which
  really does navigate.

---

## Part 1 — slugs into `scriptures.db`

### `scriptures/build_scriptures_db.py`

Add one column to each of two tables and two columns to the view:

```sql
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

CREATE VIEW v_verses AS
    SELECT v.id, vol.id AS volume_id, vol.name AS volume,
           vol.lds_url AS volume_url,
           b.id AS book_id, b.name AS book, b.lds_url AS book_url,
           v.chapter, v.verse, v.text
    FROM verses v
    JOIN books b ON b.id = v.book_id
    JOIN volumes vol ON vol.id = b.volume_id;
```

In `build()` (line 106), widen the two accumulators and the two inserts:

```python
volumes = {}   # id -> (name, lds_url)
books = {}     # id -> (volume_id, name, position, lds_url)
...
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
```

In `sanity_check()` (line 142), add two expectations — a slug that is empty or
missing is the one failure mode that would silently produce broken links:

```python
expect("volumes missing a slug",
       db.execute("SELECT COUNT(*) FROM volumes WHERE lds_url IS NULL OR lds_url = ''").fetchone()[0], 0)
expect("books missing a slug",
       db.execute("SELECT COUNT(*) FROM books WHERE lds_url IS NULL OR lds_url = ''").fetchone()[0], 0)
```

`build_fts_db.py` needs **no change** — it `shutil.copy`s `scriptures.db` and
adds the FTS table, so it inherits the new columns and view for free.

### Rebuilding

The source CSV is already cached at `scriptures/cache/lds-scriptures.csv`, so
this is offline. Do **not** pass `--refresh`.

```bash
python scriptures/build_scriptures_db.py     # rewrites scriptures.db
python scriptures/build_fts_db.py            # rewrites scriptures_fts.db
```

**Verse IDs must come out identical.** `guide.db` stores bare verse IDs, so a
renumbering would silently repoint every curated verse in the guide. The
build's ID-stability guarantee says this holds; prove it rather than trusting
it. Run this **before** rebuilding, save the hash, run it again after, and
confirm the two match:

```bash
python3 - <<'EOF'
import hashlib, sqlite3
h = hashlib.sha256()
db = sqlite3.connect("scriptures/scriptures.db")
for row in db.execute("SELECT id, book_id, chapter, verse, text FROM verses ORDER BY id"):
    h.update(repr(row).encode())
print(h.hexdigest())
EOF
```

If the hashes differ, **stop** and report it — do not continue, and do not
commit the rebuilt database.

### `scriptures/verify_scriptures.py`

`load_slugs()` (line 45) reads the slugs out of the cached CSV. Now that the
database has them, read them from there so there is one source of truth:

```python
def load_slugs(db):
    """book name -> (volume_lds_url, book_lds_url)."""
    return {
        name: (volume_url, book_url)
        for name, volume_url, book_url in db.execute(
            "SELECT b.name, vol.lds_url, b.lds_url "
            "FROM books b JOIN volumes vol ON vol.id = b.volume_id"
        )
    }
```

In `main()`, this moves to *after* `db = sqlite3.connect(DB_PATH)`. Drop the
now-unused `import csv` and narrow the import at line 32 to
`from build_scriptures_db import DB_PATH`.

---

## Part 2 — backend: `GET /api/chapter`

In `server.py`, next to the other module constants:

```python
CHURCH_BASE_URL = "https://www.churchofjesuschrist.org/study/scriptures"


def external_url(volume_url: str, book_url: str, chapter: int, verse: int) -> str:
    """Deep link to one verse on churchofjesuschrist.org. The verse anchor is
    `p{verse}` both as the `id` query param (which the site scrolls to) and as
    the fragment."""
    return (
        f"{CHURCH_BASE_URL}/{volume_url}/{book_url}/{chapter}"
        f"?lang=eng&id=p{verse}#p{verse}"
    )
```

All three URL shapes were checked live and return 200:

| verse | URL |
|---|---|
| Jacob 2:18 | `…/bofm/jacob/2?lang=eng&id=p18#p18` |
| D&C 4:2 | `…/dc-testament/dc/4?lang=eng&id=p2#p2` |
| Joseph Smith—History 1:17 | `…/pgp/js-h/1?lang=eng&id=p17#p17` |

The D&C case is the one worth noting: its section number lives in `chapter`,
and `dc-testament/dc/4` is exactly the right URL for section 4. No special
casing anywhere.

### The endpoint

Read-only. It touches `scriptures_fts.db` only — no `get_guide_db` dependency,
no `write_export`. Place it after the search section.

```python
@app.get("/api/chapter")
def get_chapter(verse_id: int, fts_db=Depends(get_fts_db)):
    subject = fts_db.execute(
        """SELECT book_id, book, chapter, verse, volume, volume_url, book_url
           FROM v_verses WHERE id = ?""",
        (verse_id,),
    ).fetchone()
    if subject is None:
        raise HTTPException(404, "Verse not found")
    rows = fts_db.execute(
        "SELECT id, verse, text FROM v_verses WHERE book_id = ? AND chapter = ? ORDER BY verse",
        (subject["book_id"], subject["chapter"]),
    ).fetchall()
    return {
        "reference": f"{subject['book']} {subject['chapter']}",
        "book": subject["book"],
        "chapter": subject["chapter"],
        "volume": subject["volume"],
        "verse_id": verse_id,
        "verse": subject["verse"],
        "external_url": external_url(
            subject["volume_url"], subject["book_url"],
            subject["chapter"], subject["verse"],
        ),
        "verses": [
            {"verse_id": r["id"], "verse": r["verse"], "text": r["text"]}
            for r in rows
        ],
    }
```

`GET /api/topics/{id}` and `GET /api/search` are **unchanged**. The panel
fetches its own data, so no existing response shape moves — which also means
nothing in round 4's volume-summary work can regress here.

---

## Part 3 — frontend (`static/index.html`)

### Making the references clickable

**Study** — `studyVerseInnerHtml` (line 741). The ref div becomes a button,
keeping its existing class so the styling carries over:

```js
<button type="button" class="verse-ref ref-link" data-verse-id="${v.verse_id}">${escapeHtml(v.reference)}</button>
```

The wiring has a trap. `renderStudyVerseList` wires `.note-area` per block,
and `undoStudyRemove` (line 837) *separately* rebuilds one block's inner HTML
and re-wires only its note area. Two call sites, and a reference button wired
in just one of them is a button that silently stops working after an undo.
Collapse them into one helper and use it in both places:

```js
function wireStudyVerseBlock(block, topic, verseId) {
  wireStudyNoteArea(block.querySelector(".note-area"), topic, verseId);
  const refBtn = block.querySelector(".ref-link");
  refBtn.addEventListener("click", () => openChapterPanel(verseId, refBtn));
}
```

- In `renderStudyVerseList` (line 721), iterate `.verse-block` instead of
  `.note-area` and call `wireStudyVerseBlock`.
- In `undoStudyRemove`, replace the `wireStudyNoteArea(...)` call with
  `wireStudyVerseBlock(block, topic, verse.verse_id)`.

`openStudyNoteEditor`'s `exitToView` only ever replaces `.note-area`, so it
leaves the reference button alone. No change there.

**Curate** — `renderResultRow` (line 1012):

```js
<button type="button" class="result-ref ref-link" data-action="context" data-verse-id="${r.verse_id}">${escapeHtml(r.reference)}</button>
```

`renderResults` already loops `[data-action]` and rewires everything on every
re-render, so this needs one more branch alongside `approve`/`reject`/`undo`:

```js
} else if (action === "context") {
  node.addEventListener("click", () => openChapterPanel(verseId, node));
}
```

### The panel

Module-level state, following the pattern of `curateState`:

```js
let chapterPanel = null;      // the DOM element, or null when closed
let chapterPanelToken = 0;    // guards against out-of-order fetches
```

`openChapterPanel(verseId, triggerBtn)`:

1. Create the panel element if it does not exist, append it to
   `document.body`, and add `panel-open` to `<body>`. Stash `triggerBtn` on
   the element (the `modal._triggerBtn` pattern at line 619).
2. Render the shell immediately with `Loading…` in the body — the panel must
   appear on the click, not after the round trip.
3. `const token = ++chapterPanelToken;` then
   `await api("/chapter?verse_id=" + verseId)`. When the response lands,
   **bail if `token !== chapterPanelToken`** — clicking two references quickly
   must not let the slower first response overwrite the second.
4. On error, show the message in the panel body with the panel still open.
5. Focus the close button.

Panel markup:

```html
<div class="chapter-panel" role="complementary" aria-label="Chapter context">
  <div class="chapter-panel-head">
    <div class="chapter-panel-title">Jacob 2</div>
    <a class="chapter-panel-ext" href="…" target="_blank" rel="noopener noreferrer">churchofjesuschrist.org &#8599;</a>
    <button type="button" class="chapter-panel-close" aria-label="Close">&#10005;</button>
  </div>
  <div class="chapter-panel-body">…</div>
</div>
```

Each verse in the body:

```html
<div class="cv"><span class="cv-num">18</span><span class="cv-text">…</span></div>
```

The subject verse also gets `cv-current`, and after render:
`panel.querySelector(".cv-current").scrollIntoView({ block: "center" })`.

`closeChapterPanel()` removes the element, drops `panel-open` from `<body>`,
nulls `chapterPanel`, and returns focus to the stored trigger **guarded by
`isConnected`** — a Study re-render or a Curate approve may have replaced that
button with a new one while the panel was open.

### Escape

Extend the existing listener (line 343). Order matters: modal, then panel,
then edit mode.

```js
if (document.querySelector(".modal-backdrop")) { closeDeleteModal(); return; }
if (chapterPanel) { closeChapterPanel(); return; }
if (editModeActive && cancelEditFn) { cancelEditFn(); }
```

### CSS

Add near the existing `.modal` rules. `z-index: 9` keeps the panel under the
delete modal's `10`.

```css
.ref-link {
  background: none; border: none; padding: 0;
  font: inherit; font-weight: 600; color: #4a90d9;
  cursor: pointer; text-align: left;
}
.ref-link:hover { text-decoration: underline; color: #6fa8e5; background: none; }
.verse-ref.ref-link { font-size: 0.95rem; margin-bottom: 0.3rem; display: block; }
.result-ref.ref-link { font-size: 0.9rem; margin-bottom: 0.3rem; display: block; }

.chapter-panel {
  position: fixed; top: 0; right: 0; bottom: 0;
  width: min(440px, 100vw);
  background: #16213e; border-left: 1px solid #2a3a5e;
  z-index: 9; display: flex; flex-direction: column;
}
.chapter-panel-head {
  display: flex; align-items: baseline; gap: 0.8rem;
  padding: 1.1rem 1.25rem; border-bottom: 1px solid #2a3a5e;
}
.chapter-panel-title { font-size: 1.05rem; color: #fff; font-weight: 600; flex: 1; }
.chapter-panel-ext { font-size: 0.8rem; text-decoration: none; }
.chapter-panel-ext:hover { text-decoration: underline; }
.chapter-panel-close { background: none; border: none; color: #999; padding: 0.2rem 0.4rem; }
.chapter-panel-close:hover { color: #fff; background: none; }
.chapter-panel-body { overflow-y: auto; padding: 1rem 1.25rem 3rem; }
.cv { display: flex; gap: 0.6rem; padding: 0.35rem 0.5rem; line-height: 1.6; }
.cv-num { color: #666; font-size: 0.8rem; min-width: 1.6rem; text-align: right; padding-top: 0.2rem; }
.cv-current {
  background: #1a2a4e; border-left: 3px solid #4a90d9;
  border-radius: 0 6px 6px 0; padding-left: 0.5rem;
}
.cv-current .cv-num { color: #4a90d9; }

body.panel-open { padding-right: calc(min(440px, 100vw) + 2rem); }
@media (max-width: 900px) {
  body.panel-open { padding-right: 2rem; }
}
```

---

## Edge cases to handle

- **Two references clicked in quick succession** — the token check in step 3
  above. Without it the first response can land last and show the wrong
  chapter under the right title.
- **Clicking a reference while the panel is already open** — swaps the content
  in place. Do not close and reopen; do not stack two panels.
- **Single-verse chapters** (Obadiah 1, Jude 1, several Psalms) — the panel
  shows one verse, which is correct, not an error state.
- **Long chapters** (Psalm 119, 176 verses) — the body scrolls on its own and
  `scrollIntoView` lands the subject verse mid-panel.
- **Removing the verse whose chapter is open** — the panel stays, by decision.
  It also must not throw: it holds no reference to `topic.verses`.
- **Panel open across a tab switch** — `renderTopicPage` replaces `#app`, not
  `document.body`, so the panel survives. That is intended; the chapter is
  still worth reading on the other tab.
- **`/api/chapter` failure** — message inside the panel body, panel stays open
  and closable. Do not fall back to the app-level error path, which would
  replace the entire page.

---

## Tests

`test_server.py` — backend only; the frontend has no test harness in this repo.

Add a helper alongside `_find_test_verse` (line 46) that resolves a reference
to a verse id, so the URL tests can name real verses:

```python
def _verse_id_for(book: str, chapter: int, verse: int) -> int:
```

Cases:

- `GET /api/chapter?verse_id=…` for Jacob 2:18 returns every verse of Jacob 2,
  in ascending verse order, starting at verse 1, with `reference == "Jacob 2"`
  and `verse == 18`.
- The `verses` array length equals the chapter's real verse count from
  `scriptures_fts.db` — not a hardcoded number.
- `external_url` is exactly
  `https://www.churchofjesuschrist.org/study/scriptures/bofm/jacob/2?lang=eng&id=p18#p18`.
- The same assertion for D&C 4:2 (`dc-testament/dc/4`) and
  Joseph Smith—History 1:17 (`pgp/js-h/1`) — the two shapes most likely to be
  special-cased wrongly.
- `GET /api/chapter?verse_id=999999` → 404.
- `GET /api/chapter` with no `verse_id` → 422 (FastAPI's own validation).
- A data test: no row in `books` or `volumes` has a null or empty `lds_url`,
  and there are 87 and 5 of them respectively.

Run the full suite, not just the new file — round 4's volume-summary tests
exercise the same view this round modifies.

---

## Docs to update in the same commit

Per the repo's workflow rule, docs land with the code.

- `README.md` → **Scripture Database / Schema** (line 27). Add `lds_url` to
  the `volumes` and `books` rows of the table, and `volume_url` / `book_url`
  to the `v_verses` row, with a one-line note that these are the
  churchofjesuschrist.org slugs used to build deep links.
- `README.md` → **Topical Guide**. A new `### Verse context` subsection after
  `### Search` (line 206): clicking a reference on either tab opens the
  chapter panel; the panel links out to the official page in a new tab;
  `GET /api/chapter?verse_id=N` is the endpoint behind it.

Leave `guide.db` and `guide_export.json` out of the commit — they carry
unrelated uncommitted changes that predate this work.

---

## Out of scope

- **Any mutation from inside the panel** — approve, reject, notes, removal.
  Decision 3. This is the obvious round 6, and the panel is where it goes.
- Previous/next chapter navigation inside the panel.
- Footnotes, cross-references, or chapter headings — the local database holds
  verse text only. That gap is exactly what the external link is for.
- Caching fetched chapters client-side. A chapter is a few KB from a local
  SQLite file; there is nothing to optimize yet.
- A per-verse external link on the rows. Decision 1.
- Resizing or docking the panel, or remembering its state across reloads.
- Any change to `guide_export.json`, `guide.db`, or the curation data model.
  This round adds no curation data — it only reads scripture.
