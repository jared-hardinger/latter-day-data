# Traps

A trap is a specific mistake a competent implementer will otherwise make, plus
the concrete damage it causes. Traps are the reason a handoff beats "read the
spec and build it."

## Rules

1. **Name the failure, not the topic.** "Be careful with state" is useless.
   "Wiring only the render path leaves buttons that die after an undo" tells
   the implementer what they'll see when they get it wrong.
2. **Earn each one.** Traps come from code you read this session, bugs prior
   rounds actually hit, or measurements you took. Never pad from this file's
   checklist — check *against* it, then verify the hazard is real for this
   round before writing it down.
3. **Cap at five.** Every handoff so far has 2–5. More than five means the
   round is too large; say so rather than writing a six-trap handoff.
4. **Cite the evidence when there is any.** "120s for `"the"*` vs 21ms for the
   spec's form." "Two ways to mutate the same state is how the counts drifted
   in round 3." Numbers and history make a trap land.
5. **Order by damage.** Data loss first, then silent wrongness, then broken UI.

## Where to look

- **Prior specs and `git log`** — a bug that bit one round often bites the next.
  The undo-wiring trap appeared in rounds 3, 5, *and* 6.
- **Code you read while designing** — the moment you thought "that's subtle" is
  a trap.
- **Anything you measured** — performance cliffs never survive as intuition.
- **The seam with the previous round** — most regressions live where this
  round's changes meet last round's.

## This repo's recurring hazards

Check every round against these. Include one only if it genuinely applies.

**Dual render paths.** Undo strips rebuild a block separately from the list
render. Wiring only the render path leaves controls that die after an undo. Bit
rounds 3, 5, and 6. The fix is always one shared `wire*` helper used by both.

**State that lives in three places.** A mutation must reach the Study tab data,
the Curate cache (`curateState.results` — `status_in_topic` *and* `entry_id`),
and any open panel's own rendering. Patching one and re-rendering is how counts
drift.

**Panels mount on `document.body`.** Both tabs re-render `#tab-content`
constantly; anything mounted inside it gets destroyed.

**`guide.db` is precious and singular.** It's hand-made curation with no second
copy. Before any migration: back it up outside the repo, then assert the new
row count matches the old and every note survived.

**FTS `MATCH` belongs in `WHERE`, never in `JOIN … ON`.** Correct-looking on
narrow queries, effectively hangs on broad ones — 120s for `"the"*` against
21ms for the correct form.

**Delete old routes, don't alias them.** Two ways to mutate the same state is
how the counts drifted in round 3. Update every call site in `index.html` and
`test_server.py` in the same change.

**Rebuild `scriptures.db`, then `scriptures_fts.db`, in that order.** The FTS
database is a copy, so it won't have new columns until rebuilt too. `guide.db`
stores bare verse IDs, so verse IDs must survive the rebuild — sha256 before and
after, and stop if they differ.

**Escape key stacking.** Panels, modals, and selections all bind Escape. A new
one must clear its own state without breaking the others' behavior.
