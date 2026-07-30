# AI writing helpers for the Topical Guide — round 1

Read `topical-guide/PLAN.md` first (especially **Decisions already made** — this
round does not reopen any of them). Then skim
`/Users/hardingerfamily/Documents/dev/family-finance-hub/server/app/ai/service.py`
and the `fill_category` endpoint in that repo's
`server/app/transactions/router.py`. **This round ports that pattern** to a
plainer stack: raw `sqlite3` instead of SQLAlchemy, vanilla JS instead of React.

This spec is the whole scope. Do not add features beyond it.

## What and why

Two hand-written fields in the topical guide are the ones that matter most and
get written worst:

1. **A topic's `name` + `description`**, typed on the New Topic form.
2. **A note on a topic–verse link** — the one sentence saying *why this verse
   belongs to this topic*.

Both are read by a person studying. Both are also, per `PLAN.md`, destined to
feed later machine features: decision 8 says "notes will eventually feed
semantic search and LLM features," and Phase 3 plans "suggest more like this
topic" seeded by the topic's approved verses **and notes**. So a vague
description ("stuff about faith") costs twice — once now for the reader, and
again later when it's the input to a retrieval or suggestion step.

This round adds two helpers. You type a plain sentence (or nothing, for a
note), Claude drafts the field, **you review and save**. The AI never writes to
`guide.db`. It fills a form; the existing endpoints stay the only write path.

The point of the round is the **house style** — one shared voice constant that
steers every AI helper in this app, now and later.

## Decisions (settled with Jared — do not reopen)

- **Two helpers only.** Topic fill and note fill. Not "polish an existing
  description," not "suggest candidate verses" (that stays parked behind
  semantic search in PLAN.md Phase 3/4).
- **Haiku 4.5 to start.** `claude-haiku-4-5`, a per-feature tuning knob. Jared
  wants to see how it does on this prose before paying for more. Haiku does not
  accept `effort` — do not add an effort knob.
- **The topic helper reflects what you typed.** You supply a natural-language
  prompt; the model turns it into a name and description. It sharpens your
  meaning. It does not substitute a better idea of its own.
- **The note helper generates from context.** Press the button with an empty
  note box and it drafts a note from the verse text and the topic's name and
  description. No prompt required.
- **Call logs live in their own gitignored database.** `topical-guide/ai_log.db`,
  never `guide.db`. `guide.db` is the committed hand-made artifact; AI call
  logs must not bloat it or its export.
- **Fill, then review, then save.** Both endpoints return unsaved values.
  Nothing reaches `guide.db` until Jared clicks a save control. `guide_export.json`
  therefore never changes as a result of a fill.
- **Local-only, key in server env.** `ANTHROPIC_API_KEY` in
  `topical-guide/.env`, read server-side, never sent to the browser. `.env` is
  gitignored.
- **No frontend framework, no build step.** Same vanilla JS in
  `static/index.html`, same dark palette and existing CSS classes.

### One judgment call, flagged

Jared chose "generate only" for the note helper, and separately said he wants
output that "reflect[s] the meaning of what I type in." Those pull in opposite
directions for the note box specifically. Resolution: `POST .../note/fill`
takes an **optional** `prompt`. Empty (the default path, and the one Jared
picked) → generate from verse + topic context. Non-empty → treat the text as
Jared's own rough thought and sharpen it without changing its point. The button
works with nothing typed either way. If Jared would rather the note helper be
strictly context-only, delete the `prompt` field and the fidelity branch — it's
one request field, one rubric paragraph, and three lines of JS.

### One honest note about prompt caching

The system text carries `cache_control: {"type": "ephemeral"}`, copied from the
finance hub. **Do not expect cache hits at this size.** Haiku 4.5's minimum
cacheable prefix is 4096 tokens; these prompts plus a small topic list are well
under that, so caching will silently no-op (`cache_creation_input_tokens: 0`).
Keep the breakpoint anyway — it costs nothing and starts working on its own once
the topic list grows or the model changes. Do not claim a cost saving from it.

---

## The house style (the heart of this round)

One constant, shared by both features and every helper added later. Jared's
brief: plain language, faithful to what he typed, Hemingway.

```python
_HOUSE_STYLE = (
    "How to write. This voice is fixed — every field you draft for this app uses it:\n"
    "- Short declarative sentences. Plain words. Concrete nouns.\n"
    "- One idea per sentence. State the thing, then stop.\n"
    "- Cut adverbs and intensifiers. No 'truly', 'deeply', 'powerfully', "
    "'beautifully', 'profoundly'.\n"
    "- No preamble. Do not announce what you are about to say, and do not "
    "summarize what you just said.\n"
    "- Hemingway, not a hymn. Do not reach for devotional register or poetic "
    "cadence. Sincerity reads plainer than ornament.\n"
    "- Reflect the meaning of what the user wrote. Sharpen their words. Do not "
    "replace their idea with a better one of your own, and do not add a point "
    "they did not make.\n"
    "- Never assert doctrine, interpretation, or historical claim that the user "
    "did not supply and the given scripture text does not support. When unsure, "
    "say less.\n"
    "- The reader is a Latter-day Saint studying the standard works. They know "
    "the books of scripture. Do not explain them.\n"
)
```

**`_HOUSE_STYLE` is shared and hashed.** It is part of every feature's
`prompt_hash`, so changing it re-buckets every feature's logged calls at once —
which is correct, because the voice did change.

### The topic description convention

```python
_TOPIC_DESCRIPTION_CONVENTION = (
    "How to write the description. A person reads it on the topic list, and a "
    "later machine step will read it to decide whether a verse belongs here — so "
    "it has to be concrete enough to sort a verse in or out:\n"
    "- Say what the topic covers: the kinds of verses that belong in it.\n"
    "- Be specific enough that a borderline verse can be judged against it. "
    "'Faith' is not a description; 'trusting God before the outcome is visible' is.\n"
    "- End with a boundary: a 'Not ...' clause naming the nearest existing topic "
    "a verse could be confused with, so the two can be told apart.\n"
    "Two to four sentences, under 400 characters. Prose, not a keyword list."
)
```

Worked example, for the prompt *"verses about praying when you don't feel like
it"*:

> **Reluctant Prayer** — Verses where someone prays under duress, in
> discouragement, or against their own reluctance. Covers wrestling in prayer,
> praying without desire, and being commanded to pray anyway. Not Prayer, which
> holds the general pattern and promises of prayer.

The "Not Prayer" clause is the load-bearing part, and writing it requires seeing
the existing topic list — which the model does.

### The note convention

```python
_NOTE_CONVENTION = (
    "How to write the note. It sits under the verse in the study view, in the "
    "reader's own guide:\n"
    "- Say why this verse belongs to this topic. The specific link, not a "
    "restatement of the verse.\n"
    "- Name the words in the verse that carry the link when it helps.\n"
    "- Do not quote the verse back. The reader has it directly above the note.\n"
    "- Do not cite other verses. You cannot know what else is in this guide.\n"
    "One or two sentences, under 300 characters."
)
```

---

## Files

```
topical-guide/
  ai.py                 # NEW — client, logging, feature registry, prompt constants
  server.py             # + two endpoints, + env-overridable paths
  ai_log.db             # NEW — derived, gitignored, created on first run
  .env                  # NEW — ANTHROPIC_API_KEY; gitignored, never committed
  requirements.txt      # + anthropic, python-dotenv
  requirements-dev.txt  # NEW — pytest, httpx (keeps runtime deps minimal)
  test_ai.py            # NEW — backend tests, SDK mocked
  static/index.html     # + fill UI on the New Topic form and the note row
```

---

## Step 1 — Config, deps, gitignore

**`topical-guide/requirements.txt`** — append:

```
anthropic
python-dotenv
```

**`topical-guide/requirements-dev.txt`** — new:

```
pytest
httpx
```

**`.gitignore`** (repo root) — append. There is currently **no `.env` entry
anywhere in this repo**; add it before writing any key to disk:

```
topical-guide/.env
topical-guide/ai_log.db
```

**`server.py`** — make the three DB paths env-overridable so tests can point at
temp files. Current values stay the defaults; nothing changes for a normal run.

```python
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GUIDE_DB_PATH = os.environ.get("GUIDE_DB_PATH", os.path.join(BASE_DIR, "guide.db"))
EXPORT_PATH = os.environ.get("GUIDE_EXPORT_PATH", os.path.join(BASE_DIR, "guide_export.json"))
FTS_DB_PATH = os.environ.get(
    "FTS_DB_PATH",
    os.path.normpath(os.path.join(BASE_DIR, "..", "scriptures", "scriptures_fts.db")),
)
```

Load `.env` at the top of `ai.py` (not `server.py` — the key belongs to the AI
module):

```python
from dotenv import load_dotenv
load_dotenv(os.path.join(BASE_DIR, ".env"))
```

`load_dotenv` never overrides an already-set environment variable, so an
exported `ANTHROPIC_API_KEY` still wins. A missing `.env` is not an error.

---

## Step 2 — `topical-guide/ai.py`

One module. Four parts: the log database, `call_claude`, the prompt constants
(above), and the feature registry.

### 2a. The log database

Mirrors the finance hub's `ai_calls` table, minus SQLAlchemy. **Own connection
per write. Never raises** — a logging failure must never break a fill.

```python
AI_LOG_DB_PATH = os.environ.get("AI_LOG_DB_PATH", os.path.join(BASE_DIR, "ai_log.db"))

LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_calls (
    id                        INTEGER PRIMARY KEY,
    created_at                TEXT NOT NULL DEFAULT (datetime('now')),
    feature                   TEXT NOT NULL,
    model                     TEXT NOT NULL,
    prompt_hash               TEXT NOT NULL,
    system_prompt             TEXT NOT NULL,
    user_prompt               TEXT NOT NULL,
    response_json             TEXT,
    status                    TEXT NOT NULL,
    error                     TEXT,
    stop_reason               TEXT,
    request_id                TEXT,
    input_tokens              INTEGER,
    output_tokens             INTEGER,
    cache_read_input_tokens   INTEGER,
    cache_creation_input_tokens INTEGER,
    latency_ms                INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_calls_prompt_hash ON ai_calls(prompt_hash);
"""
```

`init_ai_log_db()` runs `executescript(LOG_SCHEMA)` and is called from
`server.py` startup alongside `init_guide_db()`.

`_write_call_row(**fields) -> Optional[int]` opens its own connection, inserts,
commits, returns `lastrowid`; wraps everything in `try/except Exception` and
logs to `logging` on failure, returning `None`. Same contract as the finance
hub's `_write_call_row`.

### 2b. `call_claude`

Port `family-finance-hub/server/app/ai/service.py::call_claude` nearly verbatim.
Differences: no `effort` argument (Haiku rejects it), no `guidance` column, and
the log write goes through `_write_call_row` above.

```python
AI_SERVICE_ERROR = "Could not reach the writing assistant. Try again."
AI_NO_KEY_ERROR = (
    "No ANTHROPIC_API_KEY found. Put one in topical-guide/.env to use the AI helpers."
)


def call_claude(
    *,
    feature: str,
    model: str,
    prompt_hash: str,
    system_text: str,
    user_text: str,
    output_format: type,
    max_tokens: int,
    timeout: Optional[float] = None,
) -> tuple[Any, Optional[int]]:
    """Call Claude, log the call, return (parsed_output, call_id)."""
```

Behavior, in order:

1. No `ANTHROPIC_API_KEY` → `HTTPException(503, AI_NO_KEY_ERROR)`. **No call was
   made, so nothing is logged.**
2. Build the request and call `client.messages.parse(**kwargs)`:
   ```python
   kwargs = {
       "model": model,
       "max_tokens": max_tokens,
       "system": [
           {"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}
       ],
       "messages": [{"role": "user", "content": user_text}],
       "output_format": output_format,
   }
   ```
   `messages.parse()` with a Pydantic `output_format` is the current structured-
   outputs path and is supported on Haiku 4.5. Do **not** use the deprecated
   top-level `output_format` on `messages.create()`.
3. `anthropic.APIError` → log a row with `status="api_error"` and the exception
   string, then `HTTPException(502, AI_SERVICE_ERROR)`.
4. Success → log `status="ok"` with `response.parsed_output` serialized, plus
   `stop_reason`, `getattr(response, "_request_id", None)`, and the four usage
   counters read defensively with `getattr`. Return
   `(response.parsed_output, call_id)`.

Also port `prompt_hash(*parts)` — sha256 of the joined **static instruction
constants only**. Never hash the rendered system text; that embeds the live
topic list and would shatter the comparison bucket every time Jared adds a topic.

### 2c. The feature registry

This is what makes a third helper cheap. One frozen dataclass holding a
feature's tuning knobs and its prompt constants:

```python
@dataclass(frozen=True)
class AiFeature:
    name: str                 # goes in ai_calls.feature
    model: str
    max_tokens: int
    max_prompt_chars: int
    instructions: str
    convention: str
    rubric: str

    def prompt_hash(self) -> str:
        # static parts only — never the rendered context
        return prompt_hash(self.instructions, _HOUSE_STYLE, self.convention, self.rubric)

    def system_text(self, *context_blocks: str) -> str:
        parts = [self.instructions, _HOUSE_STYLE, self.convention, self.rubric]
        parts.extend(b for b in context_blocks if b)
        return "\n\n".join(parts)


FEATURES: dict[str, AiFeature] = {
    "fill_topic": AiFeature(
        name="fill_topic",
        model="claude-haiku-4-5",
        max_tokens=2048,          # output is a name, a short description, a reason
        max_prompt_chars=2000,
        instructions=_TOPIC_INSTRUCTIONS,
        convention=_TOPIC_DESCRIPTION_CONVENTION,
        rubric=_TOPIC_RUBRIC,
    ),
    "fill_note": AiFeature(
        name="fill_note",
        model="claude-haiku-4-5",
        max_tokens=1024,
        max_prompt_chars=1000,
        instructions=_NOTE_INSTRUCTIONS,
        convention=_NOTE_CONVENTION,
        rubric=_NOTE_RUBRIC,
    ),
}
```

**Every tuning knob for both helpers lives in that dict.** Changing a model or a
cap means editing one line, nothing else — the same "one tuning-knob block"
property the finance hub endpoints have.

### 2d. The four remaining prompt constants

```python
_TOPIC_INSTRUCTIONS = (
    "You help set up one topic in a personal topical guide to the scriptures. The "
    "user describes, in plain language, a topic they want to create. Turn that into "
    "the topic's two fields: a name and a description. You are also given the topics "
    "that already exist — match their naming style, use them to write an accurate "
    "boundary in the description, and use them to flag a likely duplicate."
)

_TOPIC_RUBRIC = (
    "Rules:\n"
    "- Name: short, Title Case, two or three words at most. Match the breadth and "
    "style of the existing topics. Prefer 'Adversity' over 'Enduring Hard Things "
    "Faithfully'.\n"
    "- The name is what the user will scan a list for. Make it the plainest word "
    "that covers the topic.\n"
    "- If the described topic clearly duplicates one that already exists, still fill "
    "both fields as asked, but set duplicate_of to that existing topic's exact name. "
    "Otherwise set duplicate_of to null.\n"
    "- reason: one line explaining your choice, in the same plain voice."
)

_NOTE_INSTRUCTIONS = (
    "You help write one note in a personal topical guide to the scriptures. You are "
    "given a topic, its description, one verse with its surrounding verses for "
    "context, and sometimes the curator's own rough words. Write the note that "
    "belongs on this verse in this topic.\n"
    "When the curator supplied rough words, that is the note — your job is to say "
    "the same thing more clearly, in their voice, without adding a point they did "
    "not make. When they supplied nothing, draft the note yourself from the verse "
    "and the topic."
)

_NOTE_RUBRIC = (
    "Rules:\n"
    "- The surrounding verses are context for you only. The note is about the one "
    "verse named as the subject.\n"
    "- If the verse's link to this topic is weak, say what the actual link is rather "
    "than forcing a strong claim.\n"
    "- reason: one line explaining what link you drew, in the same plain voice."
)
```

### 2e. Output models

Underscore-prefixed — these are the model's raw output, coerced before they
leave the endpoint.

```python
class _TopicFill(BaseModel):
    name: str
    description: str
    duplicate_of: Optional[str]   # exact name of an existing topic, or null
    reason: str


class _NoteFill(BaseModel):
    note: str
    reason: str
```

---

## Step 3 — Endpoint: fill a topic

`POST /api/ai/topics/fill` in `server.py`.

**Request** `{"prompt": str}` → **Response**
`{"name", "description", "duplicate_of", "reason"}`.

```python
class TopicFillRequest(BaseModel):
    prompt: str
```

Body, in order:

1. `prompt = body.prompt.strip()`. Empty → `HTTPException(400, "Describe the topic you want.")`.
   Longer than `feature.max_prompt_chars` → `HTTPException(422, "That description is too long.")`.
   **Both checks run before any SDK call.**
2. Read every topic: `SELECT name, description FROM topics ORDER BY name`. Build
   `existing_by_casefold = {name.casefold(): name}`.
3. Build the context block — a plain list the model can read:
   ```
   Existing topics:
   - Prayer: Verses on asking God for ... Not Worship, which ...
   - Adversity: (no description)
   ```
   With no topics yet, pass `"Existing topics: none yet — this is the first one."`
   so the model does not invent a boundary clause against nothing.
4. `call_claude(feature=..., model=..., prompt_hash=feature.prompt_hash(), system_text=feature.system_text(topic_block), user_text="The topic to create:\n" + prompt, output_format=ai._TopicFill, max_tokens=feature.max_tokens)`.
5. `ai is None` → `HTTPException(502, AI_SERVICE_ERROR)`. Same guard the finance
   hub uses for a run that produced no parsed output.
6. **Coerce, do not trust:**
   - `name = ai.name.strip()`; empty → `HTTPException(502, AI_SERVICE_ERROR)`.
   - `description = ai.description.strip()[:400]`.
   - `duplicate_of = existing_by_casefold.get(ai.duplicate_of.strip().casefold())`
     when `ai.duplicate_of` is truthy, else `None`. A name that matches no real
     topic becomes `None`. **The frontend must never receive a duplicate name
     that is not a real topic.**
7. Return the four fields. **Write nothing.**

---

## Step 4 — Endpoint: fill a verse note

`POST /api/ai/topics/{topic_id}/verses/{verse_id}/note/fill`.

**Request** `{"prompt": str | null}` → **Response** `{"note", "reason"}`.

```python
class NoteFillRequest(BaseModel):
    prompt: Optional[str] = None
```

Body, in order:

1. Topic missing → `HTTPException(404, "Topic not found")`. Verse not in
   `v_verses` → `HTTPException(400, f"Verse {verse_id} does not exist")`. Same
   shapes `upsert_verse` already uses. **The topic–verse link is not required** —
   only that both ends exist.
2. `prompt = (body.prompt or "").strip()`. Over `max_prompt_chars` → 422. Empty
   is fine and is the normal path.
3. Read the verse and its neighbours from the read-only FTS DB:
   ```sql
   SELECT book, chapter, verse, text FROM v_verses WHERE id = ?
   SELECT verse, text FROM v_verses
    WHERE book = ? AND chapter = ? AND verse BETWEEN ? AND ?
    ORDER BY verse
   ```
   Neighbour window is `verse - 2 .. verse + 2`. A verse in isolation is often
   cryptic ("And he said unto them...") and the note quality depends on knowing
   what "he" said. Mark the subject verse in the block so the model cannot
   confuse it:
   ```
   Topic: Reluctant Prayer
   Topic description: Verses where someone prays under duress...

   Passage (the subject verse is marked >>):
     Alma 34:16  ...
   >> Alma 34:17  Therefore may God grant unto you...
     Alma 34:18  ...
   ```
4. `user_text` is `"The curator's own words for this note:\n" + prompt` when a
   prompt was supplied, otherwise `"The curator supplied no words. Draft the note."`
5. `call_claude(...)` with `output_format=ai._NoteFill`.
6. Coerce: `note = ai.note.strip()[:300]`; empty → `HTTPException(502, AI_SERVICE_ERROR)`.
7. Return `{note, reason}`. **Write nothing** — `topic_verses.note` is only
   written by the existing `PATCH /api/topics/{id}/verses/{verse_id}`.

---

## Step 5 — Frontend (`static/index.html`)

Vanilla JS, existing classes, existing dark palette. Three changes.

### 5a. A fill row on the New Topic form

`renderHome()`. Above the existing name/description inputs, inside the same
card:

```html
<div class="ai-fill">
  <input type="text" id="fill-topic-prompt"
         placeholder="Describe the topic… e.g. verses about praying when you don't feel like it">
  <button type="button" id="fill-topic-btn">&#10022; Fill with AI</button>
</div>
<div class="error-msg" id="fill-topic-error"></div>
<div class="ai-note" id="fill-topic-reason"></div>
<div class="ai-warn" id="fill-topic-duplicate"></div>
```

- The description input becomes `<textarea id="new-topic-desc" rows="3" maxlength="400">`.
  It is a single-line `<input>` today and a 400-character description does not
  fit in one.
- On click: disable the button, set its text to `Filling…`, `POST /ai/topics/fill`
  with `{prompt}` through the existing `api()` helper.
- On success: set `#new-topic-name.value` and `#new-topic-desc.value` from the
  result. Show `reason` in `#fill-topic-reason` (muted). When `duplicate_of` is
  set, show `Looks like your existing "Prayer". Add it anyway, or cancel.` in
  `#fill-topic-duplicate` (amber). **A duplicate never blocks the fill and never
  disables Add Topic** — Jared decides at save, and `POST /api/topics` already
  returns 409 with a clean message if he goes ahead and it really is a duplicate.
- On error: message into `#fill-topic-error`. **Do not clear the form.**
- **Do not clear the prompt box** after a run — Jared may want to tweak the
  sentence and fill again.
- Add Topic still posts to `POST /api/topics`, unchanged.

### 5b. AI fill on the note row, with an explicit save

`renderResultRow()` — the approved branch, which today renders
`<input class="note-input">` that saves on `change`.

Add a `&#10022;` button beside it. On click, `POST` to the note-fill path with
whatever is currently in the input as `prompt` (empty string when blank — the
generate path).

**On success, populate the input but do NOT save.** Add a `Save note` button
that appears only once the box is dirty; clicking it calls the existing
`saveNote()` (`PATCH /api/topics/{id}/verses/{verse_id}`) and hides itself
again.

Two reasons this matters:

- `PLAN.md`'s vision is "the human is always the final say," and the finance hub
  spec's hard rule is that AI never touches the database. An immediate
  auto-save would put unreviewed AI prose straight into the committed `guide.db`
  and into `guide_export.json`'s git history.
- **Mechanically, auto-save cannot be skipped by accident here.** Setting
  `input.value` from JS does not mark the field dirty, so the existing `change`
  listener will *not* fire on blur. Without an explicit save control a filled
  note silently never persists. Whichever way this goes, the fill path needs its
  own save call — make it a button Jared presses.

Keep the existing `change` listener for hand-typed edits.

### 5c. CSS

Three small additions in the existing `<style>` block, matching the palette:

```css
.ai-fill { display: flex; gap: 0.6rem; flex-wrap: wrap; }
.ai-fill input[type="text"] { flex: 1; min-width: 200px; }
.ai-note { color: #999; font-size: 0.85rem; margin-top: 0.3rem; }
.ai-warn { color: #d9a44a; font-size: 0.88rem; margin-top: 0.3rem; }
button:disabled { opacity: 0.5; cursor: not-allowed; }
```

### Not in this round

- No fill on the Study tab. Notes are not editable there today, and making them
  editable is a separate change.
- No topic **edit** form, and so no fill on one. `PATCH /api/topics/{id}` exists
  but the UI has never called it. Fill is for creating.
- No streaming, no spinner beyond the button's `Filling…` label.

---

## Step 6 — Tests (`topical-guide/test_ai.py`)

`pip install -r topical-guide/requirements-dev.txt`, then
`pytest topical-guide/test_ai.py`.

Point `GUIDE_DB_PATH`, `GUIDE_EXPORT_PATH`, and `AI_LOG_DB_PATH` at `tmp_path`
files via `monkeypatch.setenv` **before importing `server`**, so no test can
touch the real `guide.db`. `FTS_DB_PATH` uses the real
`scriptures/scriptures_fts.db`; `pytest.skip` the module with a clear message if
it is missing, since `server.py` exits at import without it.

Mock the SDK — never make a network call. A helper that patches
`ai.anthropic.Anthropic` with a stub whose `messages.parse(**kwargs)` records
the kwargs and returns an object carrying `parsed_output`, `usage`, and
`stop_reason`.

1. **Topic fill returns unsaved fields.** Valid `_TopicFill` from the stub →
   response carries name, description, reason. `SELECT COUNT(*) FROM topics` is
   unchanged, and `guide_export.json` is byte-identical before and after.
2. **Description is capped.** A 600-character description comes back ≤ 400.
3. **Duplicate is validated.** Seed topic `Prayer`. Model returns
   `duplicate_of: "prayer"` → response `"Prayer"`. Model returns
   `duplicate_of: "Nope"` → response `null`.
4. **Empty prompt → 400 and the SDK is never called.** Over-length prompt → 422,
   SDK never called.
5. **Existing topics and the house style reach the model.** Seed two topics; the
   captured `system_text` contains both names, the `Not ...` boundary language
   from `_TOPIC_DESCRIPTION_CONVENTION`, and a distinctive phrase from
   `_HOUSE_STYLE` ("Hemingway, not a hymn").
6. **The call is logged.** After a successful fill, `ai_log.db` holds exactly one
   `ai_calls` row with `feature = "fill_topic"`, `model = "claude-haiku-4-5"`,
   `status = "ok"`, and a non-null `latency_ms`.
7. **Missing key → 503 and nothing is logged.** `APIError` → 502 **and one row
   with `status = "api_error"`**. `parsed_output is None` → 502.
8. **Note fill generates with no prompt.** Seed a topic and approve a real verse.
   `POST` with `{}` → 200 with a note; the captured `system_text` contains the
   topic name and description, and the captured `user_text` says no words were
   supplied. `SELECT note FROM topic_verses` is still `''` — **the endpoint wrote
   nothing.**
9. **Note fill passes the surrounding verses and marks the subject.** The
   captured prompt contains the neighbouring verses' text and exactly one `>>`
   marker.
10. **Note is capped at 300, and a blank note → 502.**
11. **Bad ids.** Unknown `topic_id` → 404. Unknown `verse_id` → 400. SDK never
    called in either case.
12. **A logging failure does not break a fill.** Point `AI_LOG_DB_PATH` at an
    unwritable path; the fill still returns 200.

---

## How to add a third AI helper

Write this down at the bottom of `ai.py` as a comment, because "easy to expand"
is half the point of the round. Adding one is four edits and no new plumbing:

1. Add three prompt constants (`_X_INSTRUCTIONS`, `_X_CONVENTION`, `_X_RUBRIC`)
   next to the others in `ai.py`. Reuse `_HOUSE_STYLE` — do not restate voice
   rules in your rubric.
2. Add an output model (`class _XFill(BaseModel)`).
3. Add one `FEATURES["fill_x"] = AiFeature(...)` entry with the model and caps.
4. Add a thin endpoint in `server.py`: validate the prompt against
   `max_prompt_chars`, build the context block, call `call_claude`, coerce every
   field, return. Roughly 25 lines.

Logging, the key check, the 502/503 mapping, and the prompt-hash bucketing all
come for free.

---

## Hard checks

- **Neither endpoint writes to `guide.db`.** Prove it in tests (row counts
  unchanged) and confirm `guide_export.json` is untouched by a fill — a fill is
  not a mutation and must not trigger `write_export`.
- **Every field is coerced.** A description over 400 chars is cut, a note over
  300 is cut, a `duplicate_of` naming no real topic becomes `null`, an empty
  name or note becomes a 502.
- **`ANTHROPIC_API_KEY` never reaches the browser.** Grep `static/index.html`
  for `ANTHROPIC` and `sk-ant` before finishing; both must be absent.
- **`.env` and `ai_log.db` are gitignored** before any key is written. Run
  `git status` and confirm neither appears.
- Missing key → 503; API error → 502 **and a logged `api_error` row**;
  `parsed_output is None` → 502.
- **The prompt hash covers only the static constants.** Adding a topic must not
  change `prompt_hash` for `fill_topic`. Editing `_HOUSE_STYLE` must change it
  for both features.

## Manual acceptance checklist

Verify each by actually doing it:

- [ ] With no `.env`, clicking **Fill with AI** shows the "No ANTHROPIC_API_KEY"
      message in the UI, not a stack trace, and the form still works by hand.
- [ ] With a key set, `Describe the topic… verses about praying when you don't
      feel like it` fills a plausible Title Case name and a description ending in
      a `Not ...` clause naming a real existing topic.
- [ ] The description reads like the worked example above — short sentences,
      plain words, no "profoundly."
- [ ] Filling twice with the same prompt does not create anything; only Add
      Topic does.
- [ ] Describing a topic you already have shows the amber duplicate note and
      still lets you press Add Topic (which then 409s cleanly).
- [ ] On the Curate tab, approve a verse, press ✦ with the note box empty → a
      drafted note appears in the box, **`Save note` appears, and nothing is
      saved until you press it**.
- [ ] Type a rough thought in the note box, press ✦ → the result says the same
      thing more plainly and does not add a new claim.
- [ ] Save the note; it shows on the Study tab and appears in
      `guide_export.json`.
- [ ] `sqlite3 topical-guide/ai_log.db "SELECT feature, model, status, latency_ms,
      input_tokens, output_tokens FROM ai_calls"` shows one row per press.
- [ ] `git status` shows no `.env` and no `ai_log.db`.
- [ ] Restart the server: everything persists, `ai_log.db` is appended to, not
      recreated.

## Docs to update in the same commit

Per Jared's standing rule: docs first, then code, one commit.

- **`topical-guide/PLAN.md`** — add an "AI writing helpers" section recording the
  decisions above (Haiku to start, the house style, logs in a separate gitignored
  DB, fill-then-review, no write path). Under **Later phases**, note that Phase
  4's "LLM-assisted candidate suggestion" is still ahead and that this round
  deliberately shipped only the two writing helpers.
- **Top-level `README.md`** — in the **Topical Guide** section: the two helpers
  and what each fills; `ANTHROPIC_API_KEY` in `topical-guide/.env`, server-side
  only; `pip install -r topical-guide/requirements.txt` now pulls `anthropic`;
  and under **What's committed vs. derived**, add `ai_log.db` and `.env` to the
  derived/gitignored list with one line on why call logs stay out of `guide.db`.
  Add one clause to the bullet in the repo intro list.
- **This file** — correct it if the implementation deviates.

Then report what changed and stop. **Do not commit. Wait for Jared to review.**
