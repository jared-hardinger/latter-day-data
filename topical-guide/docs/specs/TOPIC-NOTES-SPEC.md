# Topic notes — a long-form document per topic (round 7)

## What and why

You can curate a topic but you cannot *write* about one.

Six rounds have gone into deciding which verses belong to "Prayer" and what to
say about each of them in a sentence. What's missing is the sentence that isn't
about a single verse: *these eleven passages are really making three different
arguments, and the third one only makes sense if you already accept the first.*
That thought has nowhere to live. `topics.description` is a 400-character blurb
that shows under the title, and `topic_entries.note` is a single-line
`<input type="text">` attached to one passage — a caption. Neither is a place
to think out loud.

Today the only home for that writing is outside the app, which means the
curation and the conclusions drawn from it live in different places and drift.

This round adds a third kind of prose: **one long-form document per topic**,
written in markdown, read as a rendered document, on its own tab. Headings,
paragraphs, bullets, numbered lists, blockquotes, links.

`PLAN.md` decision 8 parked exactly this: *"Notes are in the schema from day
one (a text column per topic–verse link and a description per topic), even
though rich notes UI can come later."* This is later.

---

## Decisions (settled with Jared — do not reopen)

1. **One notes document per topic, not a list of notes.** A `notes` column on
   `topics`, not a `topic_notes` table. Structure inside a topic's writing comes
   from headings — that is what headings are for. A list of titled documents
   would add list management, ordering, and deletion UI to buy something
   `## A heading` already buys.

2. **Markdown is the source of truth; the reading view is rendered HTML.**
   Editing happens in a `<textarea>` with a formatting toolbar; reading happens
   in a rendered document. No WYSIWYG, no contenteditable, no vendored editor
   library. This keeps the stored value plain text — greppable, diffable, and
   consistent with every other artifact this project commits — and keeps the
   frontend free of a build step. The "document" feeling is delivered by the
   rendered view, which is where the time is actually spent.

3. **The supported markdown is document essentials, and nothing more.**
   `#`/`##`/`###` headings, `**bold**`, `*italic*`, `-` bullets with one level
   of nesting, `1.` numbered lists, `>` blockquote, `[text](url)` links, `---`
   rules, blank-line-separated paragraphs. No tables, no code fences, no inline
   code, no images. Every element above earns its place in scripture-study
   prose; tables are the fiddliest part of any markdown parser and would be the
   single largest source of renderer bugs for the least return.

4. **Its own tab: Study | Curate | Notes.** It matches the existing tab
   architecture exactly, gives the document the full 874 px column, and keeps
   the Study list uncluttered. The cost — notes are one click out of sight — is
   accepted.

5. **`guide_export.json` stores notes as an array of lines**, one array element
   per line, `[]` when empty. That file exists so git shows a readable diff of
   the curation over time; a multi-paragraph document JSON-encoded as one
   string would appear as a single 3,000-character line whose every edit is an
   unreadable whole-line change. An array of lines diffs the way prose should.

6. **`renderMarkdown` is extracted to `static/markdown.js` and tested with
   `node --test`.** The renderer is a hand-rolled parser and the densest logic
   in this round; it is also a pure `string → string` function, which makes it
   the cheapest possible thing to test. Node 26 is installed and its test runner
   is built in, so this adds **no npm packages, no `package.json`, no
   `node_modules`, and no build step**. The cost is that `static/` becomes three
   files instead of one.

7. **The round stays lean.** Scripture-reference auto-linking, an AI drafting
   helper, and a home-page notes indicator are all deliberately deferred — see
   *Out of scope*.

---

## Judgment calls, flagged

Decided, with reasoning, but these are the softest points — push back now
rather than after it's built.

- **`PATCH /api/topics/{id}` gains a `notes` field rather than getting a
  dedicated endpoint.** `TopicUpdate` already has exactly the right partial
  semantics: the header edit form sends `name`/`description` and leaves `notes`
  as `None` (preserved); the Notes tab sends only `notes`. One route, one
  `write_export` call, no new surface. The alternative — `PUT
  /api/topics/{id}/notes` — reads more explicitly but duplicates the
  fetch-existing / update / re-export dance for no behavioral gain.

- **`GET /api/topics` (the home list) does not return `notes`.** It selects
  columns explicitly today, and the home page has no use for every topic's full
  document. The consequence is that `topic_dict` stays untouched and `PATCH`
  does **not** echo the saved notes back — the Notes tab already holds the text
  it just sent. If a home-page indicator ever ships, this is the decision it
  reopens.

- **No `notes_updated_at` column.** Nothing would display it. The schema has
  stayed deliberately lean for six rounds and a timestamp nobody reads is how
  that stops being true. Easy to add later; impossible to backfill honestly,
  which is the real argument for adding it now if you want it.

- **`markdown.js` duplicates `escapeHtml` as a private `esc()`.** The file must
  be loadable by Node with no DOM and no globals from `index.html`, so it cannot
  reach the existing `escapeHtml`. Defining a *global* `escapeHtml` in
  `markdown.js` instead would collide with the identically-named function
  declaration in `index.html`'s inline script — same global scope, later
  declaration silently wins. Six duplicated lines is the cheaper problem.

- **The renderer escapes the entire source *first*, then applies markdown
  rules to the escaped text.** This is the security property the whole feature
  rests on: raw HTML typed into a note can never reach the DOM, because by the
  time any rule runs there are no `<` characters left. It also means block
  rules must match `&gt;` rather than `>` for blockquotes — see the trap in
  *Edge cases*.

- **The unsaved-changes guard uses the existing `.modal-backdrop` pattern, and
  `closeDeleteModal` is renamed to `closeModal`.** A second modal type sharing a
  function called `closeDeleteModal` is the kind of naming drift that makes the
  next round's reader hesitate. The rename touches four call sites, all in
  `index.html`. The alternative — a second close function for a second modal —
  is two ways to remove one `.modal-backdrop`, which is the shape of the bug
  this repo has hit before.

- **`beforeunload` fires the browser's own generic dialog**, not a styled one;
  browsers do not allow custom text. Accepted, because losing a half-written
  essay to a reflexive Cmd-R is worse than an ugly dialog.

- **The toolbar strips an existing line prefix before applying a new one.**
  Pressing H2 on a line that is already `# Heading` produces `## Heading`, not
  `## # Heading`. This means the toolbar cannot be used to write a literal
  leading `#` — type it by hand.

---

## Part 1 — data model (`server.py`)

### `SCHEMA`, at `server.py:66`

Add the column to the `topics` table so fresh databases get it directly:

```sql
CREATE TABLE IF NOT EXISTS topics (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    notes       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### The migration

**This is required, not optional.** `init_guide_db` runs
`conn.executescript(SCHEMA)`, and `CREATE TABLE IF NOT EXISTS` on an existing
table is a silent no-op — it does not add columns. Jared's real `guide.db`
already exists, so without an explicit `ALTER TABLE` the app would start
cleanly and then 500 on the first read of `topic["notes"]`.

Follow the existing `needs_entry_migration` / `migrate_to_entries` pattern at
`server.py:104`. Add directly beneath it:

```python
def needs_notes_migration(conn) -> bool:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(topics)")}
    return bool(cols) and "notes" not in cols


def migrate_add_notes(conn):
    """`CREATE TABLE IF NOT EXISTS` never alters an existing table, so a
    guide.db created before round 7 does not pick up the notes column from
    SCHEMA. Add it explicitly. `DEFAULT ''` means every existing topic comes
    out with empty notes and no existing row is rewritten."""
    with conn:
        conn.execute("ALTER TABLE topics ADD COLUMN notes TEXT NOT NULL DEFAULT ''")
```

Wire it into `init_guide_db` (`server.py:130`), **before** `executescript`:

```python
def init_guide_db():
    conn = sqlite3.connect(GUIDE_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if needs_entry_migration(conn):
        migrate_to_entries(conn)
    if needs_notes_migration(conn):
        migrate_add_notes(conn)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
```

The `bool(cols)` guard is what makes this correct on a fresh database:
`PRAGMA table_info` on a table that does not exist returns no rows, so
`needs_notes_migration` is `False` and `executescript` creates the table with
the column already in it.

### Normalization

Next to the other module-level helpers, above the topics section:

```python
def normalize_notes(text: str) -> str:
    """Textareas can submit CRLF line endings, and a stray \\r on the end of
    every line would poison the line-array export in guide_export.json —
    invisible in the browser, ugly and diff-noisy in git. Trailing blank lines
    go for the same reason: they accumulate as empty array entries every time
    you press return before saving."""
    return text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
```

---

## Part 2 — backend API (`server.py`)

### `TopicUpdate`, at `server.py:327`

```python
class TopicUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    notes: Optional[str] = None
```

No length cap. A description is capped at 400 in the UI because it is a blurb;
a document is not.

### `update_topic`, at `server.py:369`

Three edits — resolve `notes`, add it to the `UPDATE`, and nothing else:

```python
@app.patch("/api/topics/{topic_id}")
def update_topic(topic_id: int, body: TopicUpdate, guide_db=Depends(get_guide_db)):
    existing = guide_db.execute(
        "SELECT * FROM topics WHERE id = ?", (topic_id,)
    ).fetchone()
    if existing is None:
        raise HTTPException(404, "Topic not found")
    name = body.name if body.name is not None else existing["name"]
    description = (
        body.description if body.description is not None else existing["description"]
    )
    notes = (
        normalize_notes(body.notes) if body.notes is not None else existing["notes"]
    )
    try:
        guide_db.execute(
            "UPDATE topics SET name = ?, description = ?, notes = ? WHERE id = ?",
            (name, description, notes, topic_id),
        )
        guide_db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"Topic '{name}' already exists")
    write_export(guide_db)
    approved_count = guide_db.execute(
        "SELECT COUNT(*) FROM topic_entries WHERE topic_id = ? AND status = 'approved'",
        (topic_id,),
    ).fetchone()[0]
    row = guide_db.execute(
        "SELECT id, name, description FROM topics WHERE id = ?", (topic_id,)
    ).fetchone()
    return topic_dict(row, approved_count)
```

`topic_dict` is **unchanged** and the return value is **unchanged** — see the
judgment call. The existing tests
`test_patch_with_only_description_leaves_name_alone` and
`test_patch_with_only_name_leaves_description_alone` must still pass untouched;
they are the proof that the `is not None` pattern still holds with a third
field.

### `get_topic`, at `server.py:412`

It already does `SELECT * FROM topics`, so the column is in hand. Add one key
to the returned dict, after `description`:

```python
    return {
        "id": topic["id"],
        "name": topic["name"],
        "description": topic["description"],
        "notes": topic["notes"],
        "entries": entries,
        "volume_counts": list(volume_counts.values()),
        "passage_count": len(entries),
        "verse_count": verse_count,
        "rejected_count": rejected_count,
        "note_count": note_count,
    }
```

`note_count` is untouched and still means *how many passage notes exist*. The
two are different things with confusingly similar names; do not merge them.

### `list_topics`, at `server.py:337` — **no change**

It selects columns explicitly and must keep doing so.

---

## Part 3 — export (`server.py`, `write_export` at `server.py:261`)

Two edits. Widen the topics query:

```python
        topics = guide_db.execute(
            "SELECT id, name, description, notes FROM topics ORDER BY name"
        ).fetchall()
```

and add the key, placed before `verses` so the large array stays last:

```python
            export.append(
                {
                    "name": t["name"],
                    "description": t["description"],
                    "notes": t["notes"].split("\n") if t["notes"] else [],
                    "verses": verses,
                }
            )
```

The `if t["notes"]` guard matters: `"".split("\n")` returns `[""]`, not `[]`,
so without it every topic without notes would export a one-element array
containing an empty string.

Every topic gets the key. The first commit after this ships will therefore add
a `"notes": []` line to every topic in `guide_export.json` — a single readable
one-time diff, which is the right price for a consistent shape.

---

## Part 4 — `static/markdown.js` (new file)

The complete file. It is self-contained: no DOM access, no globals from
`index.html`.

```js
/*
 * markdown.js — the notes renderer.
 *
 * Loaded two ways, which is why the export guard at the bottom exists:
 *   - by index.html as a plain <script>, defining window.renderMarkdown
 *   - by markdown.test.js under `node --test`, as a CommonJS module
 *
 * It must therefore stay free of DOM access and of index.html's globals —
 * including escapeHtml, which is duplicated here as esc() rather than shared,
 * since a second global of that name would collide with index.html's own
 * function declaration.
 *
 * The security property this file rests on: the ENTIRE source is HTML-escaped
 * up front, before a single markdown rule runs. Raw HTML typed into a note can
 * never reach the DOM because by then there are no '<' characters left. The
 * consequence is that block rules must match the ESCAPED text — '&gt;' for a
 * blockquote marker, never '>'.
 */
(function (global) {
  "use strict";

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  const BULLET_RE = /^(\s*)-\s+(.*)$/;
  const NUMBER_RE = /^(\s*)\d+\.\s+(.*)$/;
  const QUOTE_RE = /^\s*&gt;\s?/;          // '>' post-escape
  const HEADING_RE = /^(#{1,3})\s+(.*)$/;
  const RULE_RE = /^\s*---+\s*$/;

  // Only http(s) and in-page anchors become links. javascript: and data: URLs
  // execute; everything else is rendered as literal text instead. The '<'
  // check catches an href that an earlier inline rule injected a tag into —
  // see the ordering note in inline().
  function safeHref(href) {
    const trimmed = href.trim();
    if (/[<>]/.test(trimmed)) return null;
    if (/^https?:\/\//i.test(trimmed)) return trimmed;
    if (/^#/.test(trimmed)) return trimmed;
    return null;
  }

  function inline(text) {
    // Order matters: bold before italic. Run the other way, the italic rule
    // consumes the inner asterisks of **bold** and leaves a stray pair behind.
    let out = text;
    out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    // The [^\s*] guard stops a lone asterisk pair separated by spaces
    // ("a * b * c") from italicising the text between them.
    out = out.replace(/(^|[^*])\*([^\s*][^*]*?)\*/g, "$1<em>$2</em>");
    out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, function (match, label, href) {
      const safe = safeHref(href);
      if (!safe) return match;
      return '<a href="' + safe + '" target="_blank" rel="noopener noreferrer">' + label + "</a>";
    });
    return out;
  }

  function parseItem(line) {
    let m = line.match(BULLET_RE);
    if (m) return { ordered: false, indent: m[1].length, text: m[2] };
    m = line.match(NUMBER_RE);
    if (m) return { ordered: true, indent: m[1].length, text: m[2] };
    return null;
  }

  function isBlockStart(line) {
    return RULE_RE.test(line)
      || HEADING_RE.test(line)
      || QUOTE_RE.test(line)
      || parseItem(line) !== null;
  }

  // One level of nesting, by decision. Two or more leading spaces nests;
  // anything deeper collapses to that same one level rather than being
  // dropped. Returns [html, indexOfFirstLineAfterTheList].
  function renderList(lines, start) {
    const first = parseItem(lines[start]);
    const tag = first.ordered ? "ol" : "ul";
    const parts = ["<" + tag + ">"];
    let liOpen = false;
    let nestedTag = null;
    let i = start;

    while (i < lines.length) {
      const item = parseItem(lines[i]);
      if (!item) break;
      // A top-level item of the other kind ends this list and starts a new
      // one, rather than silently joining a numbered item to a bulleted list.
      if (item.indent < 2 && item.ordered !== first.ordered) break;

      if (item.indent >= 2) {
        // A nested list belongs INSIDE the <li> above it, which is why that
        // <li> is left open until the nesting closes.
        if (!liOpen) { parts.push("<li>"); liOpen = true; }
        if (!nestedTag) {
          nestedTag = item.ordered ? "ol" : "ul";
          parts.push("<" + nestedTag + ">");
        }
        parts.push("<li>" + inline(item.text.trim()) + "</li>");
      } else {
        if (nestedTag) { parts.push("</" + nestedTag + ">"); nestedTag = null; }
        if (liOpen) { parts.push("</li>"); liOpen = false; }
        parts.push("<li>" + inline(item.text.trim()));
        liOpen = true;
      }
      i++;
    }

    if (nestedTag) parts.push("</" + nestedTag + ">");
    if (liOpen) parts.push("</li>");
    parts.push("</" + tag + ">");
    return [parts.join(""), i];
  }

  function renderMarkdown(src) {
    const normalized = String(src == null ? "" : src).replace(/\r\n?/g, "\n");
    const lines = esc(normalized).split("\n");
    const out = [];
    let i = 0;

    while (i < lines.length) {
      const line = lines[i];

      if (/^\s*$/.test(line)) { i++; continue; }

      if (RULE_RE.test(line)) { out.push("<hr>"); i++; continue; }

      const h = line.match(HEADING_RE);
      if (h) {
        const level = h[1].length;
        out.push("<h" + level + ">" + inline(h[2].trim()) + "</h" + level + ">");
        i++;
        continue;
      }

      if (QUOTE_RE.test(line)) {
        const buf = [];
        while (i < lines.length && QUOTE_RE.test(lines[i])) {
          buf.push(lines[i].replace(QUOTE_RE, ""));
          i++;
        }
        out.push("<blockquote>" + inline(buf.join(" ").trim()) + "</blockquote>");
        continue;
      }

      if (parseItem(line) !== null) {
        const result = renderList(lines, i);
        out.push(result[0]);
        i = result[1];
        continue;
      }

      // Paragraph: consecutive lines until a blank line or a block start.
      // Soft line breaks join with a space, the way markdown does it.
      const buf = [];
      while (i < lines.length && !/^\s*$/.test(lines[i]) && !isBlockStart(lines[i])) {
        buf.push(lines[i].trim());
        i++;
      }
      out.push("<p>" + inline(buf.join(" ")) + "</p>");
    }

    return out.join("\n");
  }

  global.renderMarkdown = renderMarkdown;
  // Inert in the browser, where `module` is undefined.
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { renderMarkdown: renderMarkdown };
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
```

---

## Part 5 — frontend (`static/index.html`)

### Loading the renderer

At `index.html:341`, immediately before the existing `<script>`:

```html
<script src="markdown.js"></script>
<script>
```

Plain script, not a module — no `type="module"`, no build step. It defines
`window.renderMarkdown` before the inline script runs.

### The tab

In `renderTopicPage` (`index.html:537`), add the button:

```html
    <div class="tabs">
      <button class="tab-btn" data-tab="study">Study</button>
      <button class="tab-btn" data-tab="curate">Curate</button>
      <button class="tab-btn" data-tab="notes">Notes</button>
    </div>
```

and widen the dispatch at the bottom of the same function:

```js
  const content = document.getElementById("tab-content");
  if (activeTab === "study") {
    renderStudyTab(content, topic);
  } else if (activeTab === "curate") {
    renderCurateTab(content, topic);
  } else {
    renderNotesTab(content, topic);
  }
```

The tab-button wiring in the same function gains the navigation guard:

```js
  tabButtons.forEach(btn => {
    btn.classList.toggle("active", btn.dataset.tab === activeTab);
    btn.addEventListener("click", () => {
      guardNotesNavigation(() => renderTopicPage(app, topicId, btn.dataset.tab), btn);
    });
  });
```

and the back link, immediately after `renderTopicHeaderView(topic)`:

```js
  const backLink = app.querySelector(".back-link");
  backLink.addEventListener("click", (e) => {
    if (!notesDirty()) return;        // let the anchor navigate normally
    e.preventDefault();
    guardNotesNavigation(() => { notesEditing = false; location.hash = "#/"; }, null);
  });
```

### Notes tab state and rendering

Add a new section after the Curate tab section and before the chapter-panel
section. Module-level state first — it lives at module level because the
dirty check is consulted from the tab buttons, the back link, `beforeunload`,
and the Escape chain, none of which hold a reference to the editor:

```js
// ---------------------------------------------------------------------------
// Notes tab — one long-form markdown document per topic. Markdown is the
// stored form; renderMarkdown (markdown.js) produces the reading view.
// ---------------------------------------------------------------------------

let notesEditing = false;
let notesOriginal = "";

function notesDirty() {
  if (!notesEditing) return false;
  const ta = document.getElementById("notes-textarea");
  return !!ta && ta.value !== notesOriginal;
}

function renderNotesTab(content, topic) {
  content.innerHTML = `<div id="notes-pane"></div>`;
  renderNotesView(topic);
}

function renderNotesView(topic) {
  notesEditing = false;
  notesOriginal = "";
  const pane = document.getElementById("notes-pane");
  const body = topic.notes
    ? `<div class="notes-doc">${renderMarkdown(topic.notes)}</div>`
    : `<p class="empty-state">No notes yet. Write what you're working out about this topic — headings, paragraphs, bullets.</p>`;
  pane.innerHTML = `
    <div class="notes-head">
      <button type="button" class="link-btn" id="notes-edit-btn">${topic.notes ? "Edit notes" : "Write notes"}</button>
    </div>
    ${body}
  `;
  document.getElementById("notes-edit-btn").addEventListener("click", () => renderNotesEditor(topic));
}
```

`renderMarkdown` output is inserted with `innerHTML` **by design** — it returns
HTML. That is safe only because the renderer escaped its input first; do not
"fix" this by escaping the renderer's output, which would display raw tags.

### The editor

```js
function renderNotesEditor(topic) {
  const pane = document.getElementById("notes-pane");
  pane.innerHTML = `
    <div class="notes-toolbar">
      <button type="button" data-md="h1" title="Heading 1">H1</button>
      <button type="button" data-md="h2" title="Heading 2">H2</button>
      <button type="button" data-md="h3" title="Heading 3">H3</button>
      <span class="notes-toolbar-gap"></span>
      <button type="button" data-md="bold" title="Bold (&#8984;B)"><strong>B</strong></button>
      <button type="button" data-md="italic" title="Italic (&#8984;I)"><em>I</em></button>
      <span class="notes-toolbar-gap"></span>
      <button type="button" data-md="bullet" title="Bulleted list">&#8226;</button>
      <button type="button" data-md="number" title="Numbered list">1.</button>
      <button type="button" data-md="quote" title="Blockquote">&rdquo;</button>
      <button type="button" data-md="link" title="Link">&#128279;</button>
    </div>
    <textarea id="notes-textarea" class="notes-textarea" spellcheck="true"></textarea>
    <div class="error-msg" id="notes-error"></div>
    <div class="notes-actions">
      <button type="button" class="primary" id="notes-save-btn">Save notes</button>
      <button type="button" id="notes-cancel-btn">Cancel</button>
    </div>
  `;

  const ta = document.getElementById("notes-textarea");
  // Assigned as a property, never interpolated into the template above: a
  // note containing the literal text </textarea> would otherwise close the
  // element early.
  ta.value = topic.notes;
  notesOriginal = topic.notes;
  notesEditing = true;

  pane.querySelectorAll(".notes-toolbar button").forEach(btn => {
    btn.addEventListener("click", () => applyNotesFormat(ta, btn.dataset.md));
  });

  ta.addEventListener("keydown", (e) => {
    if (!(e.metaKey || e.ctrlKey)) return;
    const key = e.key.toLowerCase();
    if (key === "b") { e.preventDefault(); applyNotesFormat(ta, "bold"); }
    else if (key === "i") { e.preventDefault(); applyNotesFormat(ta, "italic"); }
    else if (key === "s") { e.preventDefault(); saveNotes(topic, ta); }
  });

  document.getElementById("notes-save-btn").addEventListener("click", () => saveNotes(topic, ta));
  document.getElementById("notes-cancel-btn").addEventListener("click", () => cancelNotesEdit(topic));

  ta.focus();
}

async function saveNotes(topic, ta) {
  const errBox = document.getElementById("notes-error");
  const saveBtn = document.getElementById("notes-save-btn");
  errBox.textContent = "";
  saveBtn.disabled = true;
  try {
    await api(`/topics/${topic.id}`, "PATCH", { notes: ta.value });
    // Mirror the server's normalization locally. PATCH deliberately does not
    // echo notes back, so without this the in-memory topic and the stored row
    // disagree about trailing newlines and the next dirty check lies.
    topic.notes = ta.value.replace(/\r\n?/g, "\n").replace(/\n+$/, "");
    if (activeTopic && activeTopic.id === topic.id) activeTopic.notes = topic.notes;
    renderNotesView(topic);
  } catch (err) {
    errBox.textContent = err.message;
    saveBtn.disabled = false;
  }
}

function cancelNotesEdit(topic) {
  if (!notesDirty()) { renderNotesView(topic); return; }
  openUnsavedModal(() => renderNotesView(topic), document.getElementById("notes-cancel-btn"));
}
```

### The toolbar

```js
const NOTES_LINE_PREFIX = { h1: "# ", h2: "## ", h3: "### ", bullet: "- ", quote: "> " };

// Any prefix this toolbar can produce, so pressing H2 on an existing "# x"
// replaces the marker instead of stacking a second one onto it.
const NOTES_PREFIX_STRIP_RE = /^(\s*)(#{1,3}\s+|-\s+|\d+\.\s+|>\s?)/;

function applyNotesFormat(ta, kind) {
  const value = ta.value;
  const start = ta.selectionStart;
  const end = ta.selectionEnd;

  if (kind === "bold" || kind === "italic") {
    const marker = kind === "bold" ? "**" : "*";
    const selected = value.slice(start, end) || (kind === "bold" ? "bold text" : "italic text");
    const replacement = marker + selected + marker;
    ta.value = value.slice(0, start) + replacement + value.slice(end);
    ta.selectionStart = start + marker.length;
    ta.selectionEnd = start + marker.length + selected.length;
    ta.focus();
    return;
  }

  if (kind === "link") {
    const selected = value.slice(start, end) || "link text";
    const replacement = `[${selected}](https://)`;
    ta.value = value.slice(0, start) + replacement + value.slice(end);
    // Caret lands inside the parentheses, where the URL goes.
    const caret = start + replacement.length - 1;
    ta.selectionStart = caret;
    ta.selectionEnd = caret;
    ta.focus();
    return;
  }

  // Line-prefix kinds apply to every line the selection touches, so selecting
  // three lines and pressing the bullet button produces three bullets.
  const lineStart = value.lastIndexOf("\n", start - 1) + 1;
  let lineEnd = value.indexOf("\n", end);
  if (lineEnd === -1) lineEnd = value.length;

  const prefixed = value.slice(lineStart, lineEnd).split("\n").map((line, idx) => {
    const bare = line.replace(NOTES_PREFIX_STRIP_RE, "$1");
    return kind === "number" ? `${idx + 1}. ${bare}` : NOTES_LINE_PREFIX[kind] + bare;
  }).join("\n");

  ta.value = value.slice(0, lineStart) + prefixed + value.slice(lineEnd);
  ta.selectionStart = lineStart;
  ta.selectionEnd = lineStart + prefixed.length;
  ta.focus();
}
```

### The unsaved-changes guard

```js
function guardNotesNavigation(proceed, triggerBtn) {
  if (!notesDirty()) { proceed(); return; }
  openUnsavedModal(proceed, triggerBtn);
}

function openUnsavedModal(onDiscard, triggerBtn) {
  const modal = el(`
    <div class="modal-backdrop">
      <div class="modal" role="dialog" aria-modal="true">
        <h2>Discard unsaved notes?</h2>
        <p>Your edits to these notes haven't been saved. Leaving now loses them.</p>
        <div class="modal-actions">
          <button type="button" id="unsaved-modal-cancel">Keep editing</button>
          <button type="button" class="btn-danger" id="unsaved-modal-confirm">Discard</button>
        </div>
      </div>
    </div>
  `);
  modal._triggerBtn = triggerBtn;
  document.body.appendChild(modal);

  modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(); });
  document.getElementById("unsaved-modal-cancel").addEventListener("click", () => closeModal());
  document.getElementById("unsaved-modal-confirm").addEventListener("click", () => {
    closeModal();
    notesEditing = false;   // before onDiscard, so a nav guard can't re-trigger
    onDiscard();
  });
  document.getElementById("unsaved-modal-cancel").focus();
}
```

### Renaming `closeDeleteModal` → `closeModal`

The function at `index.html:729` removes whatever `.modal-backdrop` is present;
it is not delete-specific and now has two callers' worth of modals. Rename the
declaration and **all four call sites**:

| line | context |
|---|---|
| `index.html:412` | the Escape handler |
| `index.html:713` | delete modal, backdrop click |
| `index.html:715` | delete modal, cancel button |
| `index.html:729` | the declaration itself |

Leave no alias. Two names for one function is how the count-drift bug in round
3 started.

### Escape and `beforeunload`

Extend the existing single listener at `index.html:405`. The notes branch goes
**after** the chapter panel and **before** topic-header edit mode — same
"topmost thing first" rule the chain already encodes. The `.modal-backdrop`
check at the top already covers the unsaved modal, where Escape means "Keep
editing", which is the safe default:

```js
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (document.querySelector(".modal-backdrop")) {
    closeModal();
    return;
  }
  if (chapterPanel && panelSelection) {
    panelSelection = null;
    renderPanelActionBar();
    return;
  }
  if (chapterPanel) {
    closeChapterPanel();
    return;
  }
  if (notesEditing && activeTopic) {
    cancelNotesEdit(activeTopic);
    return;
  }
  if (editModeActive && cancelEditFn) {
    cancelEditFn();
  }
});
```

Alongside the existing `hashchange` / `DOMContentLoaded` listeners at
`index.html:401`:

```js
// The browser supplies its own generic dialog here; custom text is not
// allowed. Ugly, but losing a half-written document to a reflexive Cmd-R is
// worse.
window.addEventListener("beforeunload", (e) => {
  if (!notesDirty()) return;
  e.preventDefault();
  e.returnValue = "";
});
```

### CSS

Append after the `body.panel-open` block at the end of the `<style>` element:

```css
.notes-head { display: flex; justify-content: flex-end; margin-bottom: 0.6rem; }
.notes-toolbar {
  display: flex; align-items: center; gap: 0.35rem; flex-wrap: wrap;
  margin-bottom: 0.6rem;
}
.notes-toolbar button { padding: 0.35rem 0.6rem; font-size: 0.85rem; min-width: 2.3rem; }
.notes-toolbar-gap { width: 0.6rem; }
.notes-textarea {
  width: 100%;
  min-height: 60vh;
  line-height: 1.6;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.9rem;
  resize: vertical;
}
.notes-actions { display: flex; align-items: center; gap: 0.6rem; margin-top: 0.8rem; }

/* Everything below is scoped to .notes-doc on purpose. The global h1 rule
   (1.9rem, #fff) styles page titles; unscoped, a heading inside a note would
   render at the same weight as the topic name itself. */
.notes-doc { line-height: 1.7; color: #ddd; }
.notes-doc > * + * { margin-top: 0.9rem; }
.notes-doc h1 { font-size: 1.35rem; color: #fff; font-weight: 600; margin-top: 1.7rem; }
.notes-doc h2 { font-size: 1.15rem; color: #fff; font-weight: 600; margin-top: 1.5rem; }
.notes-doc h3 { font-size: 1rem; color: #cfd8e8; font-weight: 600; margin-top: 1.2rem; }
.notes-doc > :first-child { margin-top: 0; }
.notes-doc strong { color: #fff; font-weight: 600; }
.notes-doc em { font-style: italic; }
.notes-doc ul, .notes-doc ol { padding-left: 1.4rem; }
.notes-doc li { margin-bottom: 0.35rem; }
.notes-doc li > ul, .notes-doc li > ol { margin-top: 0.35rem; }
.notes-doc blockquote {
  padding: 0.5rem 0.9rem;
  background: #0f1626;
  border-left: 3px solid #4a90d9;
  color: #ccc;
  font-style: italic;
}
.notes-doc hr { border: none; border-top: 1px solid #2a3a5e; margin: 1.5rem 0; }
.notes-doc a { color: #4a90d9; }
```

---

## Edge cases to handle

- **A `guide.db` predating this round** — the `ALTER TABLE` migration. Without
  it the app starts fine and 500s on the first topic page. This is the single
  highest-damage failure in the round.

- **Blockquotes match `&gt;`, not `>`.** The source is escaped before block
  parsing, so a `QUOTE_RE` written as `/^\s*>\s?/` matches nothing and every
  blockquote silently renders as a paragraph beginning with a literal `&gt;`.

- **A note containing literal HTML** — `<script>alert(1)</script>` typed into
  the editor renders as visible text, never executes. The escape-first ordering
  is what guarantees it.

- **`[x](javascript:alert(1))`** — `safeHref` returns null and the whole
  construct renders as literal text, not an anchor.

- **A URL containing `*`** — a documented limitation. Inline emphasis runs
  before links, so `**` inside an href becomes a `<strong>` tag; `safeHref`'s
  `/[<>]/` check then rejects the link. `[a](https://x.com/**b**)` renders as
  `<p>[a](https://x.com/<strong>b</strong>)</p>` — visibly wrong, but no anchor
  and no injection, which is the property that matters. Verified output, not a
  prediction.

- **An unclosed `**`** — renders as literal asterisks. The italic rule cannot
  match it because `[^\s*]` fails on the second asterisk, so it does not
  swallow the rest of the document.

- **Empty notes** — the view shows the empty state and the button reads "Write
  notes" instead of "Edit notes"; the export shows `[]`.

- **Notes that are only whitespace** — `normalize_notes` strips trailing
  newlines but not a line of spaces. `renderMarkdown` skips whitespace-only
  lines, so the rendered output is empty while `topic.notes` is truthy: the
  view shows an empty `.notes-doc` rather than the empty state. Acceptable;
  do not add special-casing for it.

- **A nested list item as the very first line** (`  - x` with nothing above)
  — opens an empty `<li>` wrapping the nested list. Valid HTML, renders as an
  indented bullet.

- **A numbered item directly after a bulleted one at the same indent** — ends
  the `<ul>` and starts an `<ol>`. Two lists, not one mixed one.

- **Switching tabs mid-edit with unsaved changes** — the modal; "Keep editing"
  leaves the textarea untouched with its content intact.

- **Switching tabs mid-edit with *no* changes** — no modal, no friction.
  Retyping your way back to the original text also counts as clean.

- **Saving, then immediately switching tabs** — `saveNotes` calls
  `renderNotesView`, which sets `notesEditing = false`, so no modal appears.

- **A passage removed from the chapter panel while the Notes tab is showing** —
  `applyEntryChange` (`index.html:1482`) guards on
  `document.getElementById("study-verse-list")` and `"results"`, neither of
  which exists on the Notes tab, so it no-ops safely. No change needed; do not
  add a Notes branch to it.

- **`renderMarkdown` called before `markdown.js` loads** — impossible with a
  plain `<script src>` above the inline script, since classic scripts execute
  in document order. Do not add `defer` or `type="module"` to either tag, which
  would break exactly this.

---

## Tests

### `static/markdown.test.js` (new file)

Run with `node --test topical-guide/static/`. Uses only built-in `node:test`
and `node:assert`; **no dependencies are added to the repo.**

```js
const test = require("node:test");
const assert = require("node:assert");
const { renderMarkdown } = require("./markdown.js");
```

Cases, each asserting exact output:

| case | input | expected |
|---|---|---|
| empty | `""` | `""` |
| whitespace only | `"   \n\n  "` | `""` |
| paragraph | `"one two"` | `<p>one two</p>` |
| soft wrap joins | `"one\ntwo"` | `<p>one two</p>` |
| two paragraphs | `"a\n\nb"` | `<p>a</p>\n<p>b</p>` |
| h1/h2/h3 | `"# a\n## b\n### c"` | three heading elements at the right levels |
| four hashes is not a heading | `"#### a"` | a `<p>`, not an `<h4>` |
| bold | `"**x**"` | `<p><strong>x</strong></p>` |
| italic | `"*x*"` | `<p><em>x</em></p>` |
| bold inside heading | `"## a **b**"` | `<h2>a <strong>b</strong></h2>` |
| unclosed bold | `"**x"` | literal `**x`, no `<strong>` |
| spaced asterisks | `"a * b * c"` | no `<em>` in the output |
| bullets | `"- a\n- b"` | `<ul><li>a</li><li>b</li></ul>` |
| nested bullets | `"- a\n  - b\n- c"` | `<ul><li>a<ul><li>b</li></ul></li><li>c</li></ul>` |
| deeper nesting collapses | `"- a\n    - b"` | same as one level of nesting |
| numbered | `"1. a\n2. b"` | `<ol><li>a</li><li>b</li></ol>` |
| numbered not starting at 1 | `"4. a\n5. b"` | still one `<ol>` with two items |
| bullet then number | `"- a\n1. b"` | a `<ul>` followed by a separate `<ol>` |
| blockquote | `"> a"` | `<blockquote>a</blockquote>` |
| multi-line blockquote | `"> a\n> b"` | one `<blockquote>` containing `a b` |
| blockquote split by blank | `"> a\n\n> b"` | two `<blockquote>` elements |
| rule | `"---"` | `<hr>` |
| rule after text | `"a\n---"` | `<p>a</p>\n<hr>` |
| http link | `"[a](https://x.com)"` | anchor with `target="_blank" rel="noopener noreferrer"` |
| anchor link | `"[a](#b)"` | anchor with `href="#b"` |
| javascript: link | `"[a](javascript:alert(1))"` | literal text, **no** `<a`, no `javascript:` in output |
| data: link | `"[a](data:text/html,x)"` | literal text, no `<a` |
| script tag | `"<script>alert(1)</script>"` | output contains `&lt;script&gt;` and **not** `<script>` |
| ampersand | `"a & b"` | `&amp;` |
| quotes | `"say \"hi\""` | `&quot;` |
| CRLF | `"a\r\nb"` | identical to the `"a\nb"` output |
| null / undefined | `null`, `undefined` | `""`, no throw |

The `javascript:`, `data:`, and `<script>` cases are the ones that must never
regress; keep them together under a clearly-named group.

**Every expected value in that table was produced by running the Part 4 code
under Node 26 during design, not predicted.** All 29 exact-match cases passed
as written. If a case fails during implementation, the transcription of the
renderer is wrong — fix that rather than relaxing the assertion.

### `test_server.py`

Backend cases, following the existing `client` / `paths` fixture pattern:

- `test_get_topic_returns_notes` — a topic created without notes returns
  `"notes": ""`; after a `PATCH`, returns exactly what was stored.
- `test_list_topics_omits_notes` — `GET /api/topics` responses have no `notes`
  key. Pins the judgment call above.
- `test_patch_with_only_notes_leaves_name_and_description_alone` — mirrors the
  two existing `test_patch_with_only_*` tests.
- `test_patch_with_only_name_leaves_notes_alone` — the other direction; this is
  what proves the header edit form can't wipe a document.
- `test_notes_crlf_normalized_to_lf` — `PATCH` `"a\r\nb"`, read back `"a\nb"`.
- `test_notes_trailing_blank_lines_stripped` — `PATCH` `"a\n\n\n"`, read back
  `"a"`.
- `test_export_notes_is_line_array` — a three-line document exports as a
  three-element array in `guide_export.json`.
- `test_export_notes_empty_is_empty_list` — a topic with no notes exports
  `[]`, **not** `[""]`.
- `test_notes_migration_adds_column_and_preserves_rows` — the important one.
  Build a `guide.db` by hand with a pre-round-7 `topics` table (no `notes`
  column) plus the current `topic_entries` / `topic_verses` shape, insert a
  topic, point `server.GUIDE_DB_PATH` at it with `monkeypatch`, call
  `server.init_guide_db()`, then assert: `notes` is in
  `PRAGMA table_info(topics)`, the pre-existing row still has its original
  name and description, and its `notes` is `""`. Use the current shape for the
  entry tables so this test exercises the notes migration alone rather than
  both migrations at once.

**Run the full suite, not just the new tests.** This round modifies
`update_topic`, `get_topic`, and `write_export` — three functions that nearly
every existing test touches. Both commands:

```bash
pytest topical-guide/
node --test topical-guide/static/
```

---

## Manual acceptance checklist

The DOM work has no automated coverage; walk it.

1. Start the server, open a topic, click **Notes**. Empty state reads "No notes
   yet…", button reads **Write notes**.
2. Click it. Toolbar, empty textarea, **Save notes** / **Cancel** appear.
3. Type a document using every supported element — all three heading levels,
   a paragraph, bold, italic, a bulleted list with one nested item, a numbered
   list, a blockquote, a link, and a `---` rule.
4. Save. The reading view renders all of it. Confirm the note's `# heading` is
   visibly *smaller* than the topic title above it — that is the scoped-CSS
   check.
5. Reload the page and return to Notes. Everything survived.
6. Click **Edit notes**, change one word, then click the **Study** tab →
   the discard modal appears. **Keep editing** returns you to the textarea with
   the change intact.
7. Repeat, choosing **Discard** → lands on Study, and returning to Notes shows
   the *saved* text without your change.
8. Edit, change something, press Escape → the discard modal. Escape again →
   "Keep editing" (modal closes, textarea intact).
9. Edit, change something, press Cmd-R → the browser's leave-site dialog.
10. Edit, change nothing, switch tabs → no modal at all.
11. Select three lines, press the bullet button → three bullets. Press H2 on a
    line that already starts with `#` → it becomes `##`, not `## #`.
12. Select a word, Cmd-B → wrapped in `**`, with the word still selected.
13. Type `<script>alert(1)</script>` into a note and save → it displays as
    text and no alert fires.
14. Check `guide_export.json` in `git diff` — the notes appear as an array of
    lines, one per line, and the diff of a small edit is small.

---

## Docs to update (same commit as the code)

- **`README.md` → Topical Guide**. A new `### Notes` subsection after
  `### Verse context`: one markdown document per topic on its own tab, the
  supported markdown subset, the explicit-save-with-guard behavior, and the
  note that per-passage notes remain plain single-line text.
- **`README.md` → Topical Guide → What's committed vs. derived**. The
  `guide_export.json` bullet gains the `notes` line-array field and why it is
  an array rather than a string.
- **`README.md` → Topical Guide → Run it**. Add `node --test
  topical-guide/static/` beside the pytest instructions, noting it needs no
  install.
- **`topical-guide/docs/PLAN.md` → `## Files`** (line 68). The `static/` block
  gains `markdown.js` and `markdown.test.js`.
- **`topical-guide/docs/PLAN.md` → `## Schema (guide.db)`** (line 84). Add the
  `notes` column to the `topics` DDL. Note while you are there that the
  `topic_verses` DDL shown in this section is the **pre-round-6** shape and no
  longer matches `server.py`; strike it as stale rather than deleting it, so
  the record stays honest.
- **`topical-guide/docs/PLAN.md` → `### Export`** (line 146). Mention the
  `notes` array.
- **`topical-guide/docs/PLAN.md`** — add a `## Topic notes (round 7 — shipped)`
  section after the round-3 section. Rounds 4, 5, and 6 shipped without one;
  do not extend that drift, and mention in the commit message that the gap for
  4–6 remains.
- **`topical-guide/docs/PLAN.md` → decision 8** (line 42). It says rich notes
  UI "can come later" — amend to note that round 7 delivered the topic-level
  half, while per-passage notes remain plain text by decision.

---

## Out of scope

- **Scripture references auto-linking inside notes.** Typing `Alma 34:17` in a
  note and having it open the existing chapter panel is the obvious next round —
  the panel infrastructure from round 5 already exists and would need almost
  nothing new. It is deferred because reference *parsing* is its own problem
  (`1 Nephi`, `D&C`, `Joseph Smith—History`, ranges, abbreviations, and false
  positives like a bare `3:16`), and getting it wrong makes notes worse, not
  better. **This is the recommended round 8.**
- **An AI drafting helper for notes.** It would follow the existing three
  helpers exactly, but a helper that drafts a *document* needs a different
  prompt design than one that drafts a sentence, and that design conversation
  hasn't happened.
- **A notes indicator on the home page.** Requires `notes` in the list
  endpoint's response, which is a decision deliberately taken the other way
  above. Revisit together.
- **Multiple notes per topic.** Decision 1. If headings turn out to be
  insufficient after real use, that is the evidence that would justify a
  `topic_notes` table.
- **Rich text for per-passage notes.** They are captions on a single passage;
  a caption that needs headings is really a topic note.
- **Tables, code fences, inline code, images.** Decision 3.
- **Live side-by-side preview while editing.** Considered and set aside: the
  topic column is 874 px, which is too narrow to split without both halves
  feeling cramped. Toggling between edit and read is the better fit at this
  width.
- **Search across note text.** The FTS index covers scripture, not curation.
  Searching your own writing is a real want and a real round of its own.
- **Autosave or draft recovery.** Explicit save with a navigation guard matches
  every other mutation in the app.
