---
name: handoff
description: Turn a settled design conversation into a spec doc and a handoff prompt for a fresh implementation session. Use when Jared says /handoff, "write the spec", "write the handoff", or otherwise asks to hand a designed-but-unbuilt feature to a new session. Assumes the design discussion already happened in this conversation.
---

# Handoff

Jared designs features with a strong model, then pastes a short prompt into a
fresh session to implement them. This skill writes the two artifacts that
transfer: a **spec** (the committed record, exhaustive) and a **handoff prompt**
(scratch, ~30–70 lines, the thing he actually pastes).

You are the designing model. The implementing session gets no context except
what you write down. Everything you know and don't record is lost.

## Layout

```
topical-guide/docs/
├── PLAN.md                        # roadmap; each shipped round gets a section
├── specs/<FEATURE>-SPEC.md        # committed with the implementation
└── handoffs/<FEATURE>-HANDOFF.md  # gitignored, scratch
```

`<FEATURE>` is SCREAMING-KEBAB, one or two words, matching the existing set:
`AI-HELPERS`, `TOPIC-EDIT`, `VERSE-REMOVE`, `VOLUME-SUMMARY`, `VERSE-CONTEXT`,
`PASSAGE`.

**Round number:** the highest `(round N)` across `docs/specs/*.md` titles, plus
one. A specced-but-unbuilt round still counts as taken. State the number you
picked and let Jared correct it.

## Phase 0 — is the design actually settled?

This skill is invoked *after* the design conversation. Its main failure mode is
being called too early and inventing decisions, then attributing them to Jared
in a section headed "settled with Jared — do not reopen."

Before writing, confirm all three:

1. **Decisions Jared actually assented to.** Not options you presented and he
   didn't reject. Silence is not assent.
2. **No unresolved forks.** Any "we could go either way" still open in the
   conversation is a hole in the spec.
3. **Enough code read to write literal code.** Specs contain the actual diff,
   not a description of one. If you haven't opened `server.py`,
   `static/index.html`, and whatever else this round touches, you can't.

If something is missing, say what specifically is unsettled and offer to
proceed anyway with your best reading — flag those as assumptions in the
spec's "Judgment calls" section. Don't hard-stop on a small gap; do refuse to
silently manufacture decisions.

## Phase 1 — write the spec, then stop

Read `references/spec-template.md` for the section order and per-section rules.

Write `docs/specs/<FEATURE>-SPEC.md`. Then **stop and report**:

- the Decisions list, verbatim
- the Judgment calls list — these are what Jared most often changes
- the traps you plan to put in the handoff
- the model recommendation and why
- the round number you assigned

Do not write the handoff yet. Jared reviews and edits the spec first; a handoff
written against a spec he's about to change is wasted.

## Phase 2 — write the handoff

Triggered by Jared approving, editing, or invoking `/handoff` again.

Re-read the spec from disk first — he may have edited it. Then read
`references/handoff-template.md` and write
`docs/handoffs/<FEATURE>-HANDOFF.md`.

Two rules carry the whole artifact:

**Verification numbers must be real.** Run the queries. A handoff that says
`OT 101, NT 24, BoM 13, D&C 38, PoGP 1, total 177 (verified against the real
database)` gives the implementing session something it cannot fake. A handoff
that says "confirm the counts look right" gives it nothing. If a number can't
be computed before the feature exists, say so rather than inventing one.

**Traps must name a concrete failure.** See `references/traps.md` for the
derivation rules and this repo's recurring hazards. "Be careful with state" is
not a trap. "Wiring only the render path leaves buttons that die after an undo"
is.

## Commit conventions

- The spec is committed **with the implementation**, not before.
- Handoffs are gitignored. Never commit one, never suggest it.
- Run `git status` before writing the handoff and state accurately whether
  `guide.db` / `guide_export.json` had pre-existing uncommitted changes, and
  whether this round's commit should include them. Every handoff so far ends
  with an explicit instruction on this; get it right rather than copying the
  previous round's answer.
