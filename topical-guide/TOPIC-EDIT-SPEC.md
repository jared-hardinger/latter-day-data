# Editing topics in the UI — round 2

## What and why

Today a topic can be created and filled with verses, but never changed. There is
no way to fix a typo in a name, sharpen a description after the topic has grown,
or get rid of a topic that turned out to be a bad idea. `PATCH /api/topics/{id}`
and `DELETE /api/topics/{id}` have existed since Phase 1 and the UI has never
called either one.

This round makes a topic editable from its own page:

1. **Edit the name and description** in an inline form on the topic page.
2. **Polish the description with AI** — a third helper that reads the topic's
   *approved verses* and rewrites the description to the house convention. This
   is the one genuinely new capability: the round-1 `fill_topic` helper drafts a
   topic from a plain-language prompt and knows nothing about what the topic has
   since become.
3. **Delete the topic**, behind a confirmation modal that names exactly what
   dies.
4. **Edit a verse's note from the Study tab**, so notes are no longer reachable
   only by re-running a Curate search that happens to surface the verse.

Nothing about the schema changes. Both mutating endpoints already exist, already
call `write_export`, and are already correct.

---

## Decisions (settled with Jared — do not reopen)

1. **Edit and delete live on the topic page only** (`#/topic/{id}`). An `Edit`
   button beside the title swaps the header block into a form. `Delete topic`
   lives inside that form, not on the page chrome. The home list at `#/` stays a
   clean, read-only browse list.
2. **The AI helper is a new `polish_description` feature**, not a reuse of
   `fill_topic`. It takes the current name, the current description, the topic's
   approved verses *with their notes*, and the other topic names — and returns a
   revised description. It is the third entry in `ai.py`'s `FEATURES` registry
   and follows the existing four-edit recipe.
3. **Delete is guarded by a custom in-page modal** that names the topic and the
   counts it will take down (approved verses, rejections, notes), and says
   plainly that git history of `guide_export.json` is the only way back. No
   `window.confirm()`. No type-the-name-to-confirm friction.
4. **Notes become editable in the Study tab**, reusing the note row already
   built for the Curate tab (input + `✦` AI fill + Save).
5. **Delete is a hard delete.** No archive, no soft-delete flag, no trash. The
   committed `guide_export.json` plus git history is the undo, and the modal
   says so. `ON DELETE CASCADE` already removes the links.
6. **The AI never writes.** `polish_description` returns an unsaved string. It
   reaches `guide.db` only when the user presses **Save changes**, which calls
   the existing `PATCH /api/topics/{id}`. A polish must not trigger
   `write_export` — same hard rule as round 1.
7. **No new tables, no new columns, no migration.**

### Judgment calls, flagged

- **Polish returns the description only — never the name.** *(Superseded —
  see below.)* On a topic that already has curated verses, the name is the
  stable handle the curator scans a list for and the thing the boundary
  clauses of *other* topics point at. Renaming is a deliberate act; the text
  input is right there. If this turns out to be wrong, the cheap fix is
  adding an optional `suggested_name` to the output model and surfacing it
  only when it differs from the current name.
  **Update:** Jared asked for this after the round shipped. `polish_description`
  now may also set `suggested_name` on `_DescriptionPolish` — only when the
  approved verses show the current name is a poor fit, per the rubric — and
  the endpoint nulls it out unless it differs (case-insensitively) from the
  topic's current name. The frontend auto-fills the name input alongside the
  description textarea when present; nothing is saved until **Save changes**,
  same review-before-save contract as the description already has.
- **The shared note row always shows its `Save note` button.** In the Curate tab
  today the button is hidden until an AI fill happens, and a blur-triggered
  `change` handler does the real saving. Making one shared row means picking one
  behavior; an always-visible Save is the honest one. Save-on-blur stays as
  well, so nothing that works today stops working.
- **Cancel discards without warning.** Local single-user app, small forms.
- **Verse context for polish is capped at 40 verses.** Deterministic — the first
  40 by `verse_id` — with a trailing line naming how many were omitted, so the
  model knows it is seeing a sample.

---

## Files

```
topical-guide/
  TOPIC-EDIT-SPEC.md   # this file
  ai.py                # + 2 prompt constants, + 1 output model, + 1 FEATURES entry
  server.py            # + note_count on GET /api/topics/{id}, + 1 polish endpoint
  static/index.html    # + edit mode, + delete modal, + shared/editable note row, + CSS
  test_ai.py           # + polish endpoint tests
  test_server.py       # NEW (recommended, severable) — tests for PATCH/DELETE/counts
```

---

## Step 1 — Backend: counts for the delete modal

The modal needs three numbers. `GET /api/topics/{topic_id}` already returns the
approved verses (so `verses.length` is the approved count) and `rejected_count`.
Add one field:

```python
note_count = guide_db.execute(
    "SELECT COUNT(*) FROM topic_verses WHERE topic_id = ? AND note != ''",
    (topic_id,),
).fetchone()[0]
```

Return it as `note_count` alongside `rejected_count`. Purely additive — no
existing field changes shape, so nothing else in the frontend needs touching.

Notes on rejected verses count here too. That is correct: they are curation work
the delete destroys, and the modal should say so.

---

## Step 2 — Backend: the polish helper

### 2a. `ai.py` — prompt constants

Add next to the `fill_note` constants. **Reuse `_TOPIC_DESCRIPTION_CONVENTION`
unchanged** — how a topic description should read does not depend on whether it
was drafted or revised. This means the constant is now shared by two features
and editing it re-buckets both prompt hashes. That is correct and worth a
one-line comment above it.

```python
# ---------------------------------------------------------------------------
# polish_description prompt constants — the convention is shared with
# fill_topic; only the framing and the rules differ.
# ---------------------------------------------------------------------------

_POLISH_INSTRUCTIONS = (
    "You sharpen the description of one topic that already exists in a personal "
    "topical guide to the scriptures. You are given the topic's name, its current "
    "description, the verses the curator has already approved into it, and the "
    "other topics in the guide. Rewrite the description so it matches the "
    "convention below.\n"
    "The approved verses are the evidence of what this topic has actually become. "
    "Where the current description and the verses disagree, follow the verses — "
    "but describe only what is there. Do not widen the topic to cover verses the "
    "curator has not approved.\n"
    "When the curator supplied words steering the rewrite, follow them."
)

_POLISH_RUBRIC = (
    "Rules:\n"
    "- Do not change the topic's name. You are writing one field: the description.\n"
    "- This is a revision, not a fresh draft. Keep what the current description "
    "already gets right. If it is already correct and in convention, return it "
    "unchanged and say so in reason.\n"
    "- The boundary clause names a real topic from the list you were given, "
    "spelled exactly as it appears there. If no existing topic is near enough to "
    "be confused with this one, leave the boundary clause off rather than "
    "inventing a topic.\n"
    "- The notes on the approved verses are the curator's own reasoning. Use them "
    "to find the link this topic actually holds.\n"
    "- reason: one line saying what you changed and why, in the same plain voice."
)
```

### 2b. `ai.py` — output model

```python
class _DescriptionPolish(BaseModel):
    description: str
    reason: str
```

### 2c. `ai.py` — registry entry

```python
"polish_description": AiFeature(
    name="polish_description",
    model="claude-haiku-4-5",
    max_tokens=2048,
    max_prompt_chars=1000,   # steering words, not a draft
    instructions=_POLISH_INSTRUCTIONS,
    convention=_TOPIC_DESCRIPTION_CONVENTION,
    rubric=_POLISH_RUBRIC,
),
```

### 2d. `server.py` — the endpoint

`POST /api/ai/topics/{topic_id}/description/polish`

| | |
|---|---|
| Body | `{"prompt": str \| null}` — optional steering words |
| Returns | `{"description": str, "reason": str}` — **unsaved** |
| 404 | unknown `topic_id`, SDK never called |
| 422 | prompt over `max_prompt_chars`, SDK never called |
| 502 / 503 | inherited from `ai.call_claude` — API error / missing key |

Three context blocks, in this order:

**Block 1 — the other topics** (for the boundary clause). Every topic *except*
this one, `name: description`, ordered by name. When there are none:
`"Other topics: none — this is the only topic in the guide."`

**Block 2 — this topic.**

```
Topic name: Reluctant Prayer
Current description: prayer when you don't want to
```

Use `(no description yet)` when the description is empty.

**Block 3 — the approved verses.** Joined from `scriptures_fts.db` in
`verse_id` order, capped at 40:

```
Approved verses in this topic (12 of 12):
Alma 37:37  Counsel with the Lord in all thy doings…
  note: the counsel comes before the doing, not after it
Enos 1:4  And my soul hungered…
```

Include the curator's note under a verse when one exists, indented as shown.
When the topic has more than 40 approved verses, show the first 40 and append
`… and 17 more approved verses not shown.` When the topic has none:
`"Approved verses in this topic: none yet."` — the model must then work from the
name and current description alone, and the rubric's "describe only what is
there" still applies.

`user_text` mirrors `fill_note`: the curator's words when supplied, otherwise
`"The curator supplied no words. Polish the description as it stands."`

**Coercion, before anything leaves the endpoint:** `description` is stripped and
cut to 400 chars; empty after stripping → 502 with `ai.AI_SERVICE_ERROR`.

Roughly 40 lines. No new plumbing — logging, the key check, and the 502/503
mapping all come from `call_claude`.

---

## Step 3 — Frontend: edit mode on the topic header

### Structure

`renderTopicPage` currently rebuilds the whole page on every tab switch. Wrap
the title and description in their own container so edit mode can swap **only**
that container:

```html
<a class="back-link" href="#/">← All topics</a>
<div id="topic-header"></div>
<div class="tabs">…</div>
<div id="tab-content"></div>
```

Two render functions write into `#topic-header`:

- `renderTopicHeaderView(topic)` — the `<h1>`, the description paragraph, and an
  `Edit` button in a `.header-actions` row.
- `renderTopicHeaderEdit(topic)` — the form below.

**Entering or leaving edit mode must not re-render `#tab-content`.** A curator
who has a page of search results open, clicks Edit, and clicks Cancel must find
those results still there. This is the reason for the container split; do not
"simplify" it back into a full `renderTopicPage()` call.

### The form

```
[ Reluctant Prayer                                    ]   ← input, required
[ Verses where someone prays under duress, in         ]   ← textarea rows=3
[ discouragement, or against their own reluctance.    ]      maxlength=400
[ Optional: how to change it…      ] [ ✦ Polish with AI ]  ← reuses .ai-fill
  Sharpened to name the boundary against Prayer.          ← .ai-note (reason)
  <error line>                                            ← .error-msg
[Save changes]  [Cancel]                    Delete topic  ← .btn-danger, right
```

Behavior:

- **Save changes** → `PATCH /api/topics/{id}` with `{name, description}`. On
  success, re-render the header in view mode from the response. The `<h1>`
  updates in place; no reload, no route change.
- **Empty name** → blocked client-side with `A topic needs a name.` in the error
  line. Do not send the request.
- **409** (name taken) → show the server's message, stay in edit mode with the
  user's text intact.
- **Cancel** and **Escape** → discard, return to view mode.
- **✦ Polish with AI** → `POST /api/ai/topics/{id}/description/polish` with the
  steering input's value. While in flight, disable the button and label it
  `Polishing…` (matching the round-1 `Filling…` pattern). On success, replace the
  **textarea's** value with the returned description and show `reason` in the
  `.ai-note` line. Errors go to the error line. **The polish result is not
  saved** — the user still has to press Save changes, and the spec's hard checks
  test exactly this.

---

## Step 4 — Frontend: the delete modal

Triggered by `Delete topic` in the edit form. Built entirely from the already
loaded `topic` object — no extra fetch.

```
┌──────────────────────────────────────────────┐
│ Delete "Reluctant Prayer"?                   │
│                                              │
│ This also deletes 12 approved verses,        │
│ 4 rejections, and 7 notes.                   │
│                                              │
│ This cannot be undone from the app. The only │
│ way back is git history for                  │
│ guide_export.json.                           │
│                                              │
│                    [Cancel]  [Delete topic]  │
└──────────────────────────────────────────────┘
```

Copy rules:

- Build the counts sentence from the non-zero counts only, comma-joined with a
  final "and": `12 approved verses`, `4 rejections`, `7 notes`. Singularize at 1
  (`1 approved verse`, `1 rejection`, `1 note`).
- When all three are zero, replace that sentence with
  `This topic has no verses yet.`
- The git sentence is always shown.

Interaction:

- Backdrop covers the page; the modal is centered.
- `Cancel` receives focus on open. `Escape` and a backdrop click both cancel.
- `Delete topic` is `.btn-danger` and never autofocused.
- Confirm → `DELETE /api/topics/{id}` → `location.hash = "#/"` (the hashchange
  re-renders the home list, which will no longer contain the topic).
- A failed delete shows the error inside the modal and leaves it open.
- Only ever one modal in the DOM; remove the node on close and restore focus to
  the `Delete topic` button that opened it.

---

## Step 5 — Frontend: editable notes in the Study tab

### Extract the note row

The Curate tab's note row markup and wiring become two shared functions:

```js
function noteRowHtml(verseId, note)          // input + ✦ fill + Save note + error line
function wireNoteRow(root, topicId, onSaved) // fill, save-on-click, save-on-blur
```

`renderResults` (Curate) and the new Study renderer both call them. Changes to
the Curate row, both deliberate:

- `Save note` is always visible, not revealed only after an AI fill.
- After a successful save the button reads `Saved` for ~1.5s, then returns to
  `Save note`.

Save-on-`change` (blur) stays, so the current muscle memory still works.

### Study tab

Each verse block gains a note affordance under the verse text:

- **Note present** → today's `.verse-note` block, plus an `Edit note` link-button.
- **No note** → an `Add note` link-button.
- Clicking either swaps that verse's note area for `noteRowHtml(...)`, with the
  AI `✦` button wired to the existing
  `POST /api/ai/topics/{id}/verses/{verse_id}/note/fill` endpoint — no backend
  change needed.
- On save: update `topic.verses[i].note` in memory, exit back to display state,
  and show the new note. On Cancel/Escape: exit without saving.
- Only one note editor open at a time is not required, but re-rendering the
  whole Study tab on every save is: keep the update local to the verse block so
  the reader's scroll position is preserved.

Deleting a note is `Save` with an empty field — `PATCH` with `note: ""` already
does the right thing. Do not add a separate delete-note control.

---

## Step 6 — CSS

Additions to the existing `<style>` block, in the established palette
(`#16213e` surfaces, `#2a3a5e` borders, `#4a90d9` accent, `#d94a4a` danger):

```css
.header-actions { display: flex; align-items: baseline; gap: 0.8rem; }
.edit-form { display: flex; flex-direction: column; gap: 0.6rem; margin-bottom: 1.5rem; }
.edit-actions { display: flex; align-items: center; gap: 0.6rem; }
.edit-actions .spacer { flex: 1; }
button.btn-danger { border-color: #9e2a2a; color: #d94a4a; }
button.btn-danger:hover { background: #4e1a1a; border-color: #d94a4a; color: #fff; }
.modal-backdrop {
  position: fixed; inset: 0; background: rgba(10, 14, 26, 0.75);
  display: flex; align-items: center; justify-content: center; padding: 1rem; z-index: 10;
}
.modal {
  background: #16213e; border: 1px solid #2a3a5e; border-radius: 10px;
  padding: 1.5rem; max-width: 460px; width: 100%;
}
.modal h2 { font-size: 1.15rem; color: #fff; margin-bottom: 0.8rem; }
.modal p { color: #ccc; line-height: 1.5; margin-bottom: 0.8rem; font-size: 0.92rem; }
.modal-actions { display: flex; justify-content: flex-end; gap: 0.6rem; margin-top: 1.2rem; }
.note-actions { margin-top: 0.4rem; }
```

Reuse `.ai-fill`, `.ai-note`, `.error-msg`, `.note-row`, `.card` as they are.

---

## Step 7 — Tests

### `test_ai.py` — polish endpoint

Follow the existing fixtures (`client`, `paths`, `mock_anthropic_returning`,
`all_log_rows`, `topic_count`). Nine tests:

1. **Returns an unsaved description and writes nothing.** `guide.db` row counts
   unchanged; `guide_export.json` byte-identical before and after.
2. **Description is capped at 400 chars.**
3. **Blank description → 502.**
4. **Unknown `topic_id` → 404, SDK never called.**
5. **Overlong prompt → 422, SDK never called.**
6. **Context reaches the model:** assert the system text contains the topic's
   name, its current description, an approved verse's reference *and* its note,
   and another topic's name — and does **not** contain the topic's own name in
   the "other topics" block.
7. **The 40-verse cap:** with 45 approved verses, exactly 40 references appear
   and the text contains `5 more approved verses not shown`.
8. **A topic with no approved verses** still returns 200 and the system text
   says `none yet`.
9. **The call is logged** with `feature = 'polish_description'`, and the
   `prompt_hash` differs from `fill_topic`'s.

### `test_server.py` — NEW, recommended but severable

`PATCH`/`DELETE` on topics have never had tests, and this round is the first
thing to call them. Four tests, no AI mocking needed:

1. `PATCH` renaming a topic returns the new name and leaves `topic_verses`
   untouched (count before == count after).
2. `PATCH` to a name another topic already holds → 409, and the original row is
   unchanged.
3. `PATCH` with only `description` leaves the name alone (and vice versa).
4. `DELETE` cascades: `topic_verses` rows for that topic are gone, and the topic
   no longer appears in `guide_export.json`.
5. `GET /api/topics/{id}` reports `note_count` counting notes on **rejected**
   links too, and not counting empty-string notes.

If this file is cut for time, say so explicitly rather than quietly skipping it.

---

## Hard checks

- **A polish never writes.** Prove it: row counts unchanged *and*
  `guide_export.json` unmodified after a polish. Only `Save changes` writes.
- **Renaming does not touch verses.** `topic_verses` count identical before and
  after a rename, including rejections and notes.
- **No `window.confirm`, `window.alert`, or `window.prompt`** anywhere in
  `static/index.html`. Grep for them.
- **Escape works twice over:** closes the delete modal, and exits edit mode.
- **Editing does not disturb the Curate tab.** Manually: search, click Edit,
  Cancel, and confirm the results list is still on screen.
- **A no-op save produces no diff.** Open Edit, change nothing, press Save →
  `git diff guide_export.json` is empty.
- **`ANTHROPIC_API_KEY` still never reaches the browser.** Grep
  `static/index.html` for `ANTHROPIC` and `sk-ant`; both absent.
- **`ai.py`'s "How to add a third AI helper" comment is now describable as
  done.** Update it only if the recipe changed — it should now mention that a
  convention constant can be shared between features.

---

## Manual acceptance checklist

Verify each by actually doing it:

- [ ] Open a topic → `Edit` → rename it → Save. The `<h1>` updates, and the home
      list shows the new name in its new alphabetical position.
- [ ] Edit a description, Save, reload the page: it persisted.
- [ ] Rename a topic to a name another topic already has → a clear "already
      exists" message, the form stays open, nothing is lost.
- [ ] Save with the name field emptied → blocked with a message, no request sent.
- [ ] `✦ Polish with AI` on a topic with a dozen approved verses returns a
      description that names a boundary against a real, existing topic. The
      textarea fills, the reason line appears, and **nothing is saved** until you
      press Save changes.
- [ ] Polish, then Cancel → `git diff guide_export.json` is empty.
- [ ] Type steering words ("shorter, one sentence") and polish → the result
      obeys them.
- [ ] Delete a topic with verses → the modal names the topic and the right
      counts; Cancel leaves everything alone.
- [ ] Escape and a backdrop click both dismiss the modal.
- [ ] Confirm the delete → you land on the home list and the topic is gone from
      both `guide.db` and `guide_export.json`.
- [ ] Delete a brand-new empty topic → the modal reads "This topic has no verses
      yet."
- [ ] Study tab: add a note to a verse that had none, save, and see it under the
      verse. Reload: still there.
- [ ] Study tab: `✦` on a note field drafts one; Save persists it.
- [ ] Study tab: clear a note and save → the note block disappears.
- [ ] Curate tab notes still work exactly as before (including save-on-blur).
- [ ] `pytest topical-guide/` is green.

---

## Docs to update in the same commit

- **`topical-guide/PLAN.md`** — add a short "Topic editing (round 2 — shipped)"
  section after the AI-helpers one, pointing at this file. Fix the round-1
  section's line "no topic edit form" — it is history, so mark it superseded
  rather than deleting it.
- **`README.md`** — the Topical Guide section: topics can be edited and deleted
  from their page; deletion is permanent and takes its verses and notes with it;
  the third AI helper polishes a description against the topic's approved
  verses.
- **`topical-guide/AI-HELPERS-SPEC.md`** — leave it as the round-1 record. Add
  one line under "Not in this round" noting that the topic edit form and the
  polish helper landed in `TOPIC-EDIT-SPEC.md`.

---

## Not in this round

- **No rejected-verse management.** The Study tab still shows only a
  `rejected_count`; there is no list of rejections and no un-reject outside a
  Curate search. Worth its own round.
- ~~No remove/reject control on approved verses in the Study tab.~~ — shipped
  as a `Remove` control (no `Reject`) in round 3; see `VERSE-REMOVE-SPEC.md`.
- **No edit or delete controls on the home list.**
- **No bulk operations** — no multi-select, no delete-all-rejections.
- **No merge or split of topics.** Moving verses between topics is a Phase 4
  ontology concern, not an edit-form one.
- **No undo, no archive, no trash.** Git is the safety net, on purpose.
- ~~No `suggested_name` from the polish helper~~ — shipped after the round;
  see the flagged judgment call above.
