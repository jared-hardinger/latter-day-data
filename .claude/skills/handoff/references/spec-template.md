# Spec template

The spec is the committed record of a round. It is long (300–750 lines) on
purpose: the implementing session has no other context, and the spec outlives
the handoff.

Sections in this order. Skip one only if it would be empty, and say why.

---

## `# <Feature> — <short gloss> (round N)`

Examples that work:

- `# Passages — curating a range of verses as one unit (round 6)`
- `# Verse context — chapter panel and official-site link (round 5)`

## `## What and why`

The problem in concrete domain terms, not abstract ones. The round-6 spec opens
with *"3 Nephi 18:15–16 is one thought about prayer; 18:18–23 is a different,
longer thought about the same subject"* — a reader who knows scripture but not
the codebase understands the need immediately.

Say what's wrong today before saying what you'll build. If a previous round
deliberately parked this work, cite it.

## `## Decisions (settled with Jared — do not reopen)`

Numbered. Each starts with a **bold claim**, then the reasoning.

This section exists to stop the implementing session from re-litigating.
Everything in it must be something Jared actually agreed to — see Phase 0.

Four to six items is typical. If you have ten, some are judgment calls.

## `## Judgment calls, flagged`

Open with the standard invitation:

> Decided, with reasoning, but these are the softest points — push back now
> rather than after it's built.

Bulleted, **bold lead**, then why you chose this way *and* what the alternative
cost. This is the section Jared reads most carefully and changes most often.
Anything you assumed rather than confirmed goes here.

## `## Part N — <area>` or `## Step N — <area>`

The body. Use `Part` when the round splits by layer (data model / server /
frontend), `Step` when it's a sequence.

**Write the literal code.** Round 5's handoff tells the implementer *"The spec
has the exact code for every change; follow it rather than improvising"* — that
promise has to be true. Full function bodies, real SQL, actual CSS.

Anchor every change to a location: `server.py:272`, `renderStudyTab` at
`index.html:622`. Line numbers drift, so pair them with the symbol name.

## `## Edge cases to handle`

Enumerated, each with the expected behavior. Empty states, boundaries,
concurrent edits, what happens when a thing is already in the state requested.

## `## Tests`

Name each case and the assertion. Say which file (`test_server.py`,
`test_ai.py`). If existing tests will break, say which and why that's correct.

End with whether the full suite must run — for any round touching shared
endpoints, it must.

## `## Hard checks` / `## Manual acceptance checklist`

For rounds with data-loss risk or heavy UI. Concrete steps with concrete
expected results, the same values that will go in the handoff's Verify block.

## `## Docs to update (same commit as the code)`

Name **specific sections**, not just files:

- `README.md` — which sections, and what changes in each
- `topical-guide/docs/PLAN.md` — a new `## <Feature> (round N — shipped)`
  section. Rounds 4 and 5 shipped without one; don't add to that drift.
- Any earlier spec that this round invalidates — say to strike the stale claim
  rather than delete it, so the record stays honest.

## `## Out of scope` / `## Not in this round`

Each item with **the reason it's deferred**. "Splitting a passage would fork one
entry into two and force a decision about which half keeps the note; that
deserves its own round" tells the next designer something. A bare list doesn't.

Name the obvious next round if there is one.
