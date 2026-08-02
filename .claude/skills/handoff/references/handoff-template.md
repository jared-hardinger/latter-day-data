# Handoff template

30–70 lines. This is what Jared pastes into a fresh session, so every line
competes for the implementing model's attention. The spec holds the detail;
the handoff holds orientation, order, hazards, and proof.

## Shape

````markdown
# Handoff prompt — <feature> (round N)

**Recommended model: <model>** — <one line of reasoning>

Paste the block below into a fresh session started in the repo root.

---

<One or two sentences: what to implement, in domain terms.>

`topical-guide/docs/specs/<FEATURE>-SPEC.md` is the complete spec, settled with
Jared. Read it fully first, along with <the 2–4 files this round touches, and
any earlier spec whose behavior this round must not break>. Don't reopen the
"Decisions" section.

Work in this order:

1. <...>
2. <...>

<N> traps, all detailed in the spec:

1. **<Bold statement of the mistake.>** <The concrete failure it causes.>
2. ...

Verify: add the spec's tests to `test_server.py` and run the **full** suite.
Then run the app and check by hand — <specific actions with specific expected
results and real numbers>.

Update `README.md` per the spec's "Docs to update" and commit it with the code.

<Explicit instruction about guide.db / guide_export.json.>
````

The model recommendation sits **above the `---`**, outside the paste block —
Jared picks the model before pasting, and the implementing session shouldn't
read a recommendation about itself.

## Model recommendation

| Model | When |
|---|---|
| **Opus 5** | Schema change or migration; `guide.db` data-loss risk; ≥4 files with interdependent state; or the spec still leaves real design judgment (a merge rule, an absorption rule) |
| **Sonnet 5** | Literal code for nearly every change, ≤3 files, additive (new endpoint + new UI), no migration, tests enumerated |
| **Haiku 4.5** | Mechanical only: doc updates, tests against a settled API, a rename across files. No design left |

One line, with the actual reason:

> **Recommended model: Opus 5** — schema migration on the only copy of
> `guide.db`, plus absorption logic the spec describes but doesn't fully code.

Judge the round in front of you. Past rounds: 3 and 5 and 6 were Opus work
(state patched in several places, a migration, real judgment); round 4 was
close to Sonnet territory but for the FTS performance trap.

## "Work in this order"

Include when the round has 3+ parts or when order actually matters — schema
before endpoints, endpoints before UI, rebuild `scriptures.db` before
`scriptures_fts.db`. Skip it for a two-file round; it's noise there.

## Traps

The highest-value part of the handoff. See `traps.md`.

## Verify

Two halves, both required:

- **Automated** — add the spec's tests, run the **full** suite. Say "full"
  explicitly; rounds touching shared endpoints break things the new tests
  don't cover.
- **By hand** — specific actions, specific expected results. Real numbers you
  computed, not placeholders. Include the check most likely to fail: the
  interaction between this round and the previous one.

## The last line

Every handoff ends with an explicit statement about `guide.db` and
`guide_export.json`. The answer differs per round:

- Round 4 and 5: *"Leave `guide.db` / `guide_export.json` out — they already had
  unrelated uncommitted changes before this work started."*
- Round 6: *"**Unlike previous rounds, `guide.db` and `guide_export.json` must be
  in this commit** — the migration rewrites the schema, so an uncommitted
  database would leave the repo's curated artifact unreadable by the new code."*

Run `git status` and work out the right answer for this round. If uncommitted
curation changes will get swept into the commit, say so and tell the
implementing session to report what got swept.
