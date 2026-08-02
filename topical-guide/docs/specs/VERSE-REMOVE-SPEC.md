# Removing a verse from a topic — round 3

## What and why

Today the only way to drop a verse from a topic is the Curate tab's **Undo**
button, which appears next to a verse *only when a search happens to surface
it*. So getting rid of a verse you're reading in the Study tab means leaving the
page, guessing a query that will find it again, and clicking Undo on the result
row. `TOPIC-EDIT-SPEC.md` deferred this on purpose ("No remove/reject control on
approved verses in the Study tab") because round 2 was already adding one editing
surface to Study. This is that deferred round.

The whole feature is one button per verse in the Study tab, plus an undo strip.

`DELETE /api/topics/{topic_id}/verses/{verse_id}` already exists, already
cascades nothing (it is a single-row delete), and already calls `write_export`.
**No backend change at all.** This is a frontend round with tests backfilled for
an endpoint that has never had any.

---

## Decisions (settled with Jared — do not reopen)

1. **Remove means delete the link.** `DELETE /api/topics/{id}/verses/{verse_id}`.
   The row leaves `topic_verses` entirely: the verse goes back to being unmarked,
   so a later Curate search shows it with fresh **Approve** / **Reject** buttons,
   and its note is destroyed. Removing is *not* rejecting — `rejected_count`
   stays reserved for "I looked at this while curating and said no," and does not
   become a graveyard for verses dropped later.
2. **One control, not two.** No `Reject` button in the Study tab beside `Remove`.
   Rejecting is a curation act and stays in the Curate tab.
3. **The guard is an inline undo strip, not a modal.** The verse block collapses
   in place into `Removed Alma 37:37.  Undo`. No `window.confirm`, no dialog —
   dropping four verses while reading should cost four clicks, not four
   dismissals. The strip is session-only: it vanishes on tab switch, navigation,
   or reload.
4. **Undo restores status, source, and note.** It re-POSTs the link from the
   verse object still held in memory. The one thing it cannot restore is
   `added_at`, which becomes the time of the undo.
5. **The Curate tab's cached results are patched in place** so the two tabs stop
   disagreeing — not thrown away. Preserving an in-progress search across a
   round trip through the other tab is the same property round 2's
   `#topic-header` / `#tab-content` split exists to protect.
6. **The removal is not optimistic.** The `DELETE` is awaited; only a 2xx swaps
   in the strip. A failure leaves the verse on screen with the error under it.
7. **No new tables, no new columns, no migration, no new endpoint.**

### Judgment calls, flagged

- **The `Remove` button lives inside `.note-area`, in the `.note-actions` row**,
  next to `Edit note` / `Add note`. This means it is *hidden while a note editor
  is open on that verse* — you cannot remove a verse mid-note-edit, you Cancel
  first. That reads as correct rather than as a bug, and it comes free from the
  existing structure: `openStudyNoteEditor` already swaps the whole `.note-area`,
  and `exitToView` already re-renders and re-wires it. The alternative — a
  separate `.verse-actions` row outside `.note-area` — means two action rows per
  verse and a second wiring path, for no gain.
- **No live empty-state management.** Remove the last approved verse and the
  Study tab shows one undo strip and nothing else — not the "No approved verses
  yet" hint, which reappears on the next real render. Keeping both on screen
  would mean the tab claims to be empty while still holding a strip. The strip
  *is* the state.
- **The strip does not survive a reload, and does not say so.** Consistent with
  every other transient in this app; the copy stays short.
- **`DELETE` stays idempotent.** It does not 404 on an unknown link or an unknown
  topic today, and this round deliberately does not "fix" that — a double-click
  on `Remove` firing two `DELETE`s should be silent, not an error line. Locked in
  with a test so a future tidy-up doesn't break the UI.
- **A double-click is also blocked at the button**, which is disabled in flight.
  Belt and braces, since the idempotence above is behavior we now depend on.

---

## Files

```
topical-guide/
  docs/specs/VERSE-REMOVE-SPEC.md   # this file
  static/index.html      # + Remove button, + undo strip, + cache patch, + CSS
  test_server.py         # + 5 tests for DELETE /topics/{id}/verses/{verse_id}
```

`server.py` and `ai.py` are untouched. If a change to either turns out to be
needed, that is a signal the spec is wrong — stop and say so.

---

## Step 1 — Frontend: restructure the Study verse block

Three small changes to what already exists.

### 1a. Give each verse block its identity and an error line

`renderStudyTab` currently emits the ref, text, and `.note-area`. Add
`data-verse-id` on the block itself and pull the inner markup into a helper, so
Undo can rebuild exactly what was there:

```js
function studyVerseInnerHtml(v) {
  return `
    <div class="verse-ref">${escapeHtml(v.reference)}</div>
    <div class="verse-text">${escapeHtml(v.text)}</div>
    <div class="note-area" data-verse-id="${v.verse_id}">${studyNoteDisplayHtml(v)}</div>
  `;
}
```

`renderStudyTab` becomes `topic.verses.map(v => '<div class="verse-block"
data-verse-id="…">' + studyVerseInnerHtml(v) + '</div>')`, then wires each
`.note-area` exactly as it does now.

### 1b. Add the button and an error line to `studyNoteDisplayHtml`

```js
function studyNoteDisplayHtml(v) {
  return `
    ${v.note ? `<div class="verse-note">${escapeHtml(v.note)}</div>` : ""}
    <div class="note-actions">
      <button type="button" class="link-btn note-toggle-btn">${v.note ? "Edit note" : "Add note"}</button>
      <button type="button" class="link-btn verse-remove-btn">Remove</button>
    </div>
    <div class="error-msg verse-error"></div>
  `;
}
```

```
Alma 37:37
  Counsel with the Lord in all thy doings, and he will direct thee for good…
  ▸ the counsel comes before the doing, not after it
  Edit note   Remove
```

### 1c. Rename `wireStudyNoteToggle` → `wireStudyNoteArea`

It now wires two buttons, not one. Both existing call sites (`renderStudyTab` and
`exitToView` inside `openStudyNoteEditor`) already run at exactly the right
moments, so the re-wiring after a note save or cancel comes free:

```js
function wireStudyNoteArea(area, topic, verseId) {
  area.querySelector(".note-toggle-btn").addEventListener("click", () => {
    openStudyNoteEditor(area, topic, verseId);
  });
  area.querySelector(".verse-remove-btn").addEventListener("click", (e) => {
    removeStudyVerse(area.closest(".verse-block"), topic, verseId, e.currentTarget);
  });
}
```

---

## Step 2 — Frontend: remove, and the undo strip

### The remove handler

```js
async function removeStudyVerse(block, topic, verseId, btn) {
  const errBox = block.querySelector(".verse-error");
  const verse = topic.verses.find(v => v.verse_id === verseId);
  errBox.textContent = "";
  btn.disabled = true;
  try {
    await api(`/topics/${topic.id}/verses/${verseId}`, "DELETE");
  } catch (err) {
    errBox.textContent = err.message;
    btn.disabled = false;
    return;
  }
  forgetVerseLocally(topic, verse);
  renderStudyRemovedStrip(block, topic, verse);
}
```

`forgetVerseLocally` keeps the in-memory `topic` honest — it is the object the
delete-topic modal counts from, so a stale `verses.length` there would misreport
what deleting the topic destroys:

```js
function forgetVerseLocally(topic, verse) {
  topic.verses = topic.verses.filter(v => v.verse_id !== verse.verse_id);
  if (verse.note) topic.note_count -= 1;
  patchCuratedStatus(verse.verse_id, null, "");
}
```

### The strip

Replaces the block's *inner* HTML, so the block stays put in the document and
Undo restores in place with no scroll jump:

```js
function renderStudyRemovedStrip(block, topic, verse) {
  block.innerHTML = `
    <div class="removed-strip" role="status">
      <span>Removed ${escapeHtml(verse.reference)}.</span>
      <button type="button" class="link-btn undo-remove-btn">Undo</button>
      <span class="error-msg undo-error"></span>
    </div>
  `;
  const undoBtn = block.querySelector(".undo-remove-btn");
  undoBtn.focus();
  undoBtn.addEventListener("click", () => undoStudyRemove(block, topic, verse, undoBtn));
}
```

`role="status"` so the removal is announced rather than silently swallowed, and
`.focus()` so a keyboard user lands on Undo instead of being dumped back to the
top of the page.

### Undo

```js
async function undoStudyRemove(block, topic, verse, btn) {
  const errBox = block.querySelector(".undo-error");
  errBox.textContent = "";
  btn.disabled = true;
  try {
    await api(`/topics/${topic.id}/verses`, "POST", {
      verse_id: verse.verse_id,
      status: "approved",
      source: verse.source,
      note: verse.note,
    });
  } catch (err) {
    errBox.textContent = err.message;
    btn.disabled = false;
    return;
  }
  restoreVerseLocally(topic, verse);
  block.innerHTML = studyVerseInnerHtml(verse);
  wireStudyNoteArea(block.querySelector(".note-area"), topic, verse.verse_id);
  block.querySelector(".verse-remove-btn").focus();
}
```

Two details that matter:

- **`note` is sent explicitly.** `upsert_verse` only preserves an existing note
  when the caller omits the field — and there is no existing row here, so an
  omitted note would come back as `""`. Passing it is what makes undo lossless.
- **`source` is round-tripped from `GET /api/topics/{id}`**, which already
  returns it per verse. Do not hardcode `"manual"`; that would quietly rewrite
  the provenance of a verse the curator only ever mis-clicked.

`restoreVerseLocally` is the mirror of the forget, re-inserting at the
`verse_id`-sorted position the server would have returned it in:

```js
function restoreVerseLocally(topic, verse) {
  const at = topic.verses.findIndex(v => v.verse_id > verse.verse_id);
  topic.verses.splice(at === -1 ? topic.verses.length : at, 0, verse);
  if (verse.note) topic.note_count += 1;
  patchCuratedStatus(verse.verse_id, "approved", verse.note);
}
```

---

## Step 3 — Frontend: patch the Curate cache

One helper, called from both directions above. The Curate tab is not on screen,
so this touches data only — no re-render:

```js
function patchCuratedStatus(verseId, status, note) {
  if (!curateState) return;
  const r = curateState.results.find(x => x.verse_id === verseId);
  if (!r) return;
  r.status_in_topic = status;
  r.note = note;
}
```

The `curateState.topicId` guard is unnecessary here — `curateState` is reset by
`renderCurateTab` whenever the topic id differs, so any cache alive while a topic
page is open belongs to that topic. Assert it anyway if it reads clearer; it is
free.

---

## Step 4 — CSS

Two rules, in the established palette:

```css
.removed-strip {
  display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap;
  padding: 0.6rem 0.8rem; margin-bottom: 1.5rem;
  background: #0f1626; border: 1px dashed #2a3a5e; border-radius: 6px;
  color: #999; font-size: 0.9rem;
}
.note-actions { display: flex; align-items: center; gap: 0.4rem; }
```

`.note-actions` already exists with `margin-top: 0.4rem` — add the flex row to
it rather than duplicating the selector, since it now holds two buttons. Reuse
`.link-btn`, `.error-msg`, `.verse-block` unchanged.

---

## Step 5 — Tests

`test_server.py` gains five tests. `DELETE /api/topics/{id}/verses/{verse_id}`
has existed since Phase 1 with no coverage at all, and this round makes it a
first-class control.

1. **Removing a link deletes the row and updates the export.** Approve two
   verses, delete one: `topic_verses` for that topic drops to 1, and the topic's
   `verses` array in `guide_export.json` holds only the survivor's reference.
2. **`DELETE` is idempotent.** Deleting the same link twice returns 204 both
   times. This is the behavior the undo strip's double-click tolerance rests on;
   the test exists to stop a future 404 "fix" from breaking it silently.
3. **`DELETE` is scoped to one `(topic_id, verse_id)` pair.** The same verse
   approved into a second topic is untouched.
4. **The undo round-trip is lossless.** Approve with a note and
   `source="phrase"`, `DELETE`, then re-POST with `status="approved"` and the
   same `source` and `note` → `GET /api/topics/{id}` returns the verse with its
   note and source intact.
5. **The note really is destroyed.** After a `DELETE`, a re-POST that *omits*
   `note` yields `note == ""` — proving the row is gone rather than being
   resurrected by `upsert_verse`'s note-preserving branch, which is the whole
   reason Step 2 sends `note` explicitly.

Reuse the existing `paths` / `client` / `create_topic` / `topic_verses_count`
fixtures. No AI mocking; these endpoints never reach `ai.py`.

---

## Hard checks

- **Removing writes exactly once.** `guide_export.json` reflects the removal
  immediately after the `DELETE` (the endpoint's own `write_export`), and undo
  restores it to byte-identical content — the reference, status, source, and note
  are all that the export records, and all four round-trip.
- **`source` is not rewritten by an undo.** Approve a verse from a `phrase`
  search, remove it, undo, and confirm the export still says `phrase`.
- **Scroll position survives.** Remove a verse from the middle of a
  thirty-verse topic; nothing above it moves and the page does not jump.
- **Cross-tab agreement.** Search in Curate for a verse that is approved, switch
  to Study, remove it, switch back to Curate — the cached row now shows
  Approve / Reject, not the stale "Approved" badge.
- **The Curate tab's own Undo still works** and is not refactored into this
  path. It operates on `curateState`, not on `topic.verses`, and re-renders the
  results list; leave it alone.
- **The delete-topic modal counts stay right.** Remove two of five approved
  verses in Study, then open Edit → Delete topic: the modal says three approved
  verses, and one fewer note if a removed verse carried one.
- **No `window.confirm`, `window.alert`, or `window.prompt`** anywhere in
  `static/index.html`. Grep for them.
- **A note editor open on one verse does not stop another verse from being
  removed.** Only the editing verse's own `Remove` is out of reach.
- **`server.py` and `ai.py` show no diff for this round.**

---

## Manual acceptance checklist

Verify each by actually doing it:

- [ ] Study tab: a verse with a note shows `Edit note` and `Remove`; one without
      shows `Add note` and `Remove`.
- [ ] Click `Remove` → the verse collapses to `Removed <ref>.  Undo` in place,
      and the verses around it do not move.
- [ ] Click `Undo` → the verse comes back with its note and in its original
      position in the list.
- [ ] Remove, then reload without pressing Undo → the verse is gone for good and
      no strip is shown.
- [ ] Remove, then Undo, then reload → the verse is still there, note intact.
- [ ] Remove a verse whose note you wrote, Undo, and confirm
      `git diff guide_export.json` is empty.
- [ ] Remove the only approved verse in a topic → one strip, no verses; switch
      tabs and back → the "No approved verses yet" hint appears.
- [ ] Open a note editor on a verse → its `Remove` is not on screen; Cancel →
      `Remove` is back.
- [ ] Keyboard only: Tab to `Remove`, Enter, and confirm focus lands on `Undo`;
      Enter again and focus returns to `Remove`.
- [ ] Home list count drops by one after a removal (navigate back to `#/`).
- [ ] Curate tab: search a term matching a removed verse → it offers
      `Approve` / `Reject`, with no note carried over.
- [ ] `pytest topical-guide/` is green.

---

## Docs to update in the same commit

- **`README.md`** — the "Editing and deleting topics" section: a verse can be
  removed from a topic straight from the Study tab, with an inline Undo;
  removing deletes the link and its note outright rather than marking the verse
  rejected.
- **`topical-guide/docs/PLAN.md`** — a short "Verse removal (round 3 — shipped)"
  section after the round-2 one, pointing at this file.
- **`topical-guide/docs/specs/TOPIC-EDIT-SPEC.md`** — strike through the "No remove/reject
  control on approved verses in the Study tab" bullet under *Not in this round*
  and point it here, matching how the `suggested_name` bullet was superseded.

---

## Not in this round

- **No rejected-verse management.** Still no list of rejections in Study, and no
  un-rejecting outside a Curate search. Unchanged from round 2, still worth its
  own round.
- **No `Reject` control in the Study tab** — decision 2.
- **No bulk removal**, no multi-select, no "remove all with no note."
- **No persistent undo.** The strip is session-only; git history of
  `guide_export.json` remains the durable safety net.
- **No move-to-another-topic.** Removing here and approving there is the manual
  path; a real move belongs with the Phase 4 ontology work.
- **No change to `DELETE`'s idempotent, 404-free behavior** — flagged above as
  deliberate.
