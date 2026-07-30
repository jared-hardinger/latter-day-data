"""
ai.py
=====
AI writing helpers for the topical guide: a shared Claude client, call
logging, the house style / per-feature prompt constants, and the feature
registry that makes adding a new helper a four-edit job (see the bottom of
this file).

Call logs live in their own gitignored database, ai_log.db — never guide.db.
guide.db is the committed hand-made artifact; AI call logs must not bloat it
or its export. Nothing in this module writes to guide.db; it only drafts
fields for server.py's endpoints to return unsaved.
"""

import hashlib
import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Optional, Tuple

import anthropic
from dotenv import load_dotenv
from fastapi import HTTPException
from pydantic import BaseModel

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

_logger = logging.getLogger(__name__)

AI_SERVICE_ERROR = "Could not reach the writing assistant. Try again."
AI_NO_KEY_ERROR = (
    "No ANTHROPIC_API_KEY found. Put one in topical-guide/.env to use the AI helpers."
)

# ---------------------------------------------------------------------------
# The log database
# ---------------------------------------------------------------------------

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


def init_ai_log_db():
    conn = sqlite3.connect(AI_LOG_DB_PATH)
    conn.executescript(LOG_SCHEMA)
    conn.commit()
    conn.close()


def _write_call_row(**fields: Any) -> Optional[int]:
    """Insert one ai_calls row on its own connection. Never raises — a
    logging failure must never break a fill."""
    try:
        conn = sqlite3.connect(AI_LOG_DB_PATH)
        try:
            columns = list(fields.keys())
            column_list = ", ".join(columns)
            placeholders = ", ".join("?" for _ in columns)
            cur = conn.execute(
                f"INSERT INTO ai_calls ({column_list}) VALUES ({placeholders})",
                [fields[c] for c in columns],
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()
    except Exception:
        _logger.exception("ai._write_call_row: failed to log call row")
        return None


def _int_field(value: Any) -> Optional[int]:
    return value if isinstance(value, int) else None


def _str_field(value: Any) -> Optional[str]:
    return value if isinstance(value, str) else None


def _serialize_output(parsed: Any) -> Optional[str]:
    if parsed is None:
        return None
    if isinstance(parsed, BaseModel):
        return json.dumps(parsed.model_dump(mode="json"))
    return json.dumps(parsed)


# ---------------------------------------------------------------------------
# call_claude
# ---------------------------------------------------------------------------


def prompt_hash(*parts: str) -> str:
    """sha256 of the static instruction text.

    Hash the instruction constants only, never the rendered system text — the
    rendered text embeds the live topic list, and hashing that would shatter
    the comparison bucket every time a topic is added.
    """
    return hashlib.sha256("\n\n".join(parts).encode()).hexdigest()


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
) -> Tuple[Any, Optional[int]]:
    """Call Claude, log the call, return (parsed_output, call_id)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        # No call was made — nothing to log.
        raise HTTPException(503, AI_NO_KEY_ERROR)

    client = anthropic.Anthropic(api_key=api_key)
    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "system": [
            {"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}
        ],
        "messages": [{"role": "user", "content": user_text}],
        "output_format": output_format,
    }
    if timeout is not None:
        kwargs["timeout"] = timeout

    start = time.monotonic()
    try:
        response = client.messages.parse(**kwargs)
    except anthropic.APIError as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        _write_call_row(
            feature=feature,
            model=model,
            prompt_hash=prompt_hash,
            system_prompt=system_text,
            user_prompt=user_text,
            response_json=None,
            status="api_error",
            error=str(exc),
            stop_reason=None,
            request_id=None,
            input_tokens=None,
            output_tokens=None,
            cache_read_input_tokens=None,
            cache_creation_input_tokens=None,
            latency_ms=latency_ms,
        )
        raise HTTPException(502, AI_SERVICE_ERROR) from exc

    latency_ms = int((time.monotonic() - start) * 1000)
    parsed = response.parsed_output
    usage = response.usage

    call_id = _write_call_row(
        feature=feature,
        model=model,
        prompt_hash=prompt_hash,
        system_prompt=system_text,
        user_prompt=user_text,
        response_json=_serialize_output(parsed),
        status="ok",
        error=None,
        stop_reason=_str_field(getattr(response, "stop_reason", None)),
        request_id=_str_field(getattr(response, "_request_id", None)),
        input_tokens=_int_field(getattr(usage, "input_tokens", None)),
        output_tokens=_int_field(getattr(usage, "output_tokens", None)),
        cache_read_input_tokens=_int_field(getattr(usage, "cache_read_input_tokens", None)),
        cache_creation_input_tokens=_int_field(
            getattr(usage, "cache_creation_input_tokens", None)
        ),
        latency_ms=latency_ms,
    )
    return parsed, call_id


# ---------------------------------------------------------------------------
# The house style — shared by every helper, now and later
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# fill_topic prompt constants
# ---------------------------------------------------------------------------

# Shared with polish_description below — how a topic description should read
# does not depend on whether it was drafted or revised.
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

# ---------------------------------------------------------------------------
# fill_note prompt constants
# ---------------------------------------------------------------------------

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
    "When the curator supplied words steering the rewrite, follow them.\n"
    "Occasionally the approved verses show a topic has drifted from its name — it "
    "was named for one thing and has grown into another. When that happens, you "
    "may also suggest a replacement name. This is rare: most of the time the "
    "current name still fits and you should leave it alone."
)

_POLISH_RUBRIC = (
    "Rules:\n"
    "- You are primarily writing one field: the description. Only set "
    "suggested_name when the current name is actually a poor fit for what the "
    "approved verses show this topic has become — not merely improvable. Most "
    "polishes leave it null.\n"
    "- When you do set suggested_name: short, Title Case, two or three words at "
    "most, matching the breadth and style of the other topics in the guide — same "
    "rule as naming a brand-new topic.\n"
    "- This is a revision, not a fresh draft. Keep what the current description "
    "already gets right. If it is already correct and in convention, return it "
    "unchanged and say so in reason.\n"
    "- The boundary clause names a real topic from the list you were given, "
    "spelled exactly as it appears there. If no existing topic is near enough to "
    "be confused with this one, leave the boundary clause off rather than "
    "inventing a topic.\n"
    "- The notes on the approved verses are the curator's own reasoning. Use them "
    "to find the link this topic actually holds.\n"
    "- reason: one line saying what you changed and why, in the same plain voice — "
    "covering a renamed suggestion too, when you make one."
)

# ---------------------------------------------------------------------------
# Output models — the model's raw output, coerced before it leaves the endpoint
# ---------------------------------------------------------------------------


class _TopicFill(BaseModel):
    name: str
    description: str
    duplicate_of: Optional[str]
    reason: str


class _NoteFill(BaseModel):
    note: str
    reason: str


class _DescriptionPolish(BaseModel):
    description: str
    reason: str
    suggested_name: Optional[str] = None


# ---------------------------------------------------------------------------
# The feature registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AiFeature:
    name: str
    model: str
    max_tokens: int
    max_prompt_chars: int
    instructions: str
    convention: str
    rubric: str

    def prompt_hash(self) -> str:
        # Static parts only — never the rendered context.
        return prompt_hash(self.instructions, _HOUSE_STYLE, self.convention, self.rubric)

    def system_text(self, *context_blocks: str) -> str:
        parts = [self.instructions, _HOUSE_STYLE, self.convention, self.rubric]
        parts.extend(b for b in context_blocks if b)
        return "\n\n".join(parts)


FEATURES: dict = {
    "fill_topic": AiFeature(
        name="fill_topic",
        model="claude-haiku-4-5",
        max_tokens=2048,  # output is a name, a short description, a reason
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
    "polish_description": AiFeature(
        name="polish_description",
        model="claude-haiku-4-5",
        max_tokens=2048,
        max_prompt_chars=1000,  # steering words, not a draft
        instructions=_POLISH_INSTRUCTIONS,
        convention=_TOPIC_DESCRIPTION_CONVENTION,
        rubric=_POLISH_RUBRIC,
    ),
}


# ---------------------------------------------------------------------------
# How to add a third AI helper
# ---------------------------------------------------------------------------
#
# Adding one is four edits and no new plumbing:
#
# 1. Add three prompt constants (_X_INSTRUCTIONS, _X_CONVENTION, _X_RUBRIC)
#    next to the others in this file. Reuse _HOUSE_STYLE — do not restate
#    voice rules in your rubric. A convention constant can be reused as-is
#    across features when the same convention applies to both (see how
#    polish_description shares _TOPIC_DESCRIPTION_CONVENTION with fill_topic).
# 2. Add an output model (class _XFill(BaseModel)).
# 3. Add one FEATURES["fill_x"] = AiFeature(...) entry with the model and caps.
# 4. Add a thin endpoint in server.py: validate the prompt against
#    max_prompt_chars, build the context block, call call_claude, coerce every
#    field, return. Roughly 25 lines.
#
# Logging, the key check, the 502/503 mapping, and the prompt-hash bucketing
# all come for free.
