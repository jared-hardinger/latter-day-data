"""
test_ai.py
==========
Tests for the two AI writing-helper endpoints. The Anthropic SDK is always
mocked — no network call is ever made. Every test runs against a fresh
temp-file guide.db and ai_log.db, monkeypatched in per test, so nothing here
can touch the real topical-guide/guide.db or ai_log.db.

FTS_DB_PATH is left at its default (the real scriptures/scriptures_fts.db) —
note-fill needs real verse text and neighbours. The whole module is skipped
if that file is missing.
"""

import os
import re
import sqlite3
import sys
import tempfile
from unittest.mock import MagicMock

import pytest

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _MODULE_DIR)

_FTS_DB_PATH = os.path.normpath(
    os.path.join(_MODULE_DIR, "..", "scriptures", "scriptures_fts.db")
)
if not os.path.exists(_FTS_DB_PATH):
    pytest.skip(
        "scriptures/scriptures_fts.db is missing — run "
        "`python scriptures/build_fts_db.py` first.",
        allow_module_level=True,
    )

# Point the three DB paths at a throwaway bootstrap directory *before* the
# first `import server` below, so the module-level init_guide_db() /
# ai.init_ai_log_db() calls that run at import time can never touch the real
# topical-guide/guide.db or ai_log.db.
_BOOTSTRAP_DIR = tempfile.mkdtemp(prefix="topical_guide_test_bootstrap_")
os.environ["GUIDE_DB_PATH"] = os.path.join(_BOOTSTRAP_DIR, "guide.db")
os.environ["GUIDE_EXPORT_PATH"] = os.path.join(_BOOTSTRAP_DIR, "guide_export.json")
os.environ["AI_LOG_DB_PATH"] = os.path.join(_BOOTSTRAP_DIR, "ai_log.db")

import anthropic  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import ai  # noqa: E402
import server  # noqa: E402


def _find_test_verse() -> sqlite3.Row:
    """A verse safely inside a chapter (not near either edge), so the
    verse-2..verse+2 neighbour window never runs off the chapter boundary."""
    conn = sqlite3.connect(f"file:{_FTS_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, book, chapter, verse, text FROM v_verses "
            "WHERE book = 'Genesis' AND chapter = 1 AND verse = 10"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "expected test fixture verse Genesis 1:10 to exist"
    return row


def _neighbour_texts(verse_row: sqlite3.Row) -> list:
    conn = sqlite3.connect(f"file:{_FTS_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT verse, text FROM v_verses "
            "WHERE book = ? AND chapter = ? AND verse BETWEEN ? AND ? ORDER BY verse",
            (
                verse_row["book"],
                verse_row["chapter"],
                verse_row["verse"] - 2,
                verse_row["verse"] + 2,
            ),
        ).fetchall()
    finally:
        conn.close()
    return [r["text"] for r in rows]


_TEST_VERSE = _find_test_verse()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def paths(tmp_path, monkeypatch):
    guide_db_path = str(tmp_path / "guide.db")
    export_path = str(tmp_path / "guide_export.json")
    ai_log_path = str(tmp_path / "ai_log.db")

    monkeypatch.setattr(server, "GUIDE_DB_PATH", guide_db_path)
    monkeypatch.setattr(server, "EXPORT_PATH", export_path)
    monkeypatch.setattr(ai, "AI_LOG_DB_PATH", ai_log_path)

    server.init_guide_db()
    ai.init_ai_log_db()

    return {"guide_db": guide_db_path, "export": export_path, "ai_log_db": ai_log_path}


@pytest.fixture()
def client(paths, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    return TestClient(server.app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def mock_anthropic_returning(monkeypatch, parsed_output):
    mock_client = MagicMock()
    mock_client.messages.parse.return_value = MagicMock(
        parsed_output=parsed_output,
        usage=MagicMock(
            input_tokens=100,
            output_tokens=50,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
        stop_reason="end_turn",
        _request_id="req_test123",
    )
    monkeypatch.setattr(ai.anthropic, "Anthropic", lambda **kwargs: mock_client)
    return mock_client


def mock_anthropic_raising(monkeypatch, exc):
    mock_client = MagicMock()
    mock_client.messages.parse.side_effect = exc
    monkeypatch.setattr(ai.anthropic, "Anthropic", lambda **kwargs: mock_client)
    return mock_client


def mock_anthropic_parsed_none(monkeypatch):
    mock_client = MagicMock()
    mock_client.messages.parse.return_value = MagicMock(
        parsed_output=None,
        usage=MagicMock(
            input_tokens=10,
            output_tokens=0,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
        stop_reason="max_tokens",
        _request_id="req_test456",
    )
    monkeypatch.setattr(ai.anthropic, "Anthropic", lambda **kwargs: mock_client)
    return mock_client


def create_topic(client, name="Prayer", description=""):
    resp = client.post("/api/topics", json={"name": name, "description": description})
    assert resp.status_code == 201
    return resp.json()["id"]


def all_log_rows(paths):
    conn = sqlite3.connect(paths["ai_log_db"])
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM ai_calls").fetchall()]
    finally:
        conn.close()


def topic_count(client) -> int:
    return len(client.get("/api/topics").json())


DEFAULT_TOPIC_FILL = ai._TopicFill(
    name="Reluctant Prayer",
    description=(
        "Verses where someone prays under duress or against their own "
        "reluctance. Not Prayer, which holds the general pattern of prayer."
    ),
    duplicate_of=None,
    reason="Covers reluctant prayer specifically, distinct from prayer in general.",
)

DEFAULT_NOTE_FILL = ai._NoteFill(
    note="This verse links faith to action, not belief alone.",
    reason="The verse pairs 'faith' with 'works' directly.",
)


# ---------------------------------------------------------------------------
# 1. Topic fill returns unsaved fields
# ---------------------------------------------------------------------------


def test_topic_fill_returns_unsaved_fields_and_writes_nothing(client, paths, monkeypatch):
    create_topic(client, "Adversity", "Enduring hard things.")
    before_count = topic_count(client)
    with open(paths["export"]) as f:
        export_before = f.read()

    mock_anthropic_returning(monkeypatch, DEFAULT_TOPIC_FILL)
    resp = client.post(
        "/api/ai/topics/fill",
        json={"prompt": "verses about praying when you don't feel like it"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Reluctant Prayer"
    assert body["description"] == DEFAULT_TOPIC_FILL.description
    assert body["reason"] == DEFAULT_TOPIC_FILL.reason
    assert body["duplicate_of"] is None

    assert topic_count(client) == before_count
    with open(paths["export"]) as f:
        export_after = f.read()
    assert export_after == export_before


# ---------------------------------------------------------------------------
# 2. Description is capped
# ---------------------------------------------------------------------------


def test_topic_description_is_capped_at_400_chars(client, monkeypatch):
    fill = ai._TopicFill(**{**DEFAULT_TOPIC_FILL.model_dump(), "description": "x" * 600})
    mock_anthropic_returning(monkeypatch, fill)

    resp = client.post("/api/ai/topics/fill", json={"prompt": "a topic"})
    assert resp.status_code == 200
    assert len(resp.json()["description"]) <= 400


# ---------------------------------------------------------------------------
# 3. Duplicate is validated
# ---------------------------------------------------------------------------


def test_topic_duplicate_is_validated_against_real_topics(client, monkeypatch):
    create_topic(client, "Prayer", "General pattern of prayer.")

    fill = ai._TopicFill(**{**DEFAULT_TOPIC_FILL.model_dump(), "duplicate_of": "prayer"})
    mock_anthropic_returning(monkeypatch, fill)
    resp = client.post("/api/ai/topics/fill", json={"prompt": "reluctant prayer"})
    assert resp.status_code == 200
    assert resp.json()["duplicate_of"] == "Prayer"

    fill = ai._TopicFill(**{**DEFAULT_TOPIC_FILL.model_dump(), "duplicate_of": "Nope"})
    mock_anthropic_returning(monkeypatch, fill)
    resp = client.post("/api/ai/topics/fill", json={"prompt": "reluctant prayer"})
    assert resp.status_code == 200
    assert resp.json()["duplicate_of"] is None


# ---------------------------------------------------------------------------
# 4. Empty / over-length prompt -> SDK never called
# ---------------------------------------------------------------------------


def test_topic_fill_empty_prompt_returns_400_without_calling_sdk(client, monkeypatch):
    mock_client = mock_anthropic_returning(monkeypatch, DEFAULT_TOPIC_FILL)
    resp = client.post("/api/ai/topics/fill", json={"prompt": "   "})
    assert resp.status_code == 400
    mock_client.messages.parse.assert_not_called()


def test_topic_fill_overlong_prompt_returns_422_without_calling_sdk(client, monkeypatch):
    mock_client = mock_anthropic_returning(monkeypatch, DEFAULT_TOPIC_FILL)
    max_chars = ai.FEATURES["fill_topic"].max_prompt_chars
    resp = client.post("/api/ai/topics/fill", json={"prompt": "x" * (max_chars + 1)})
    assert resp.status_code == 422
    mock_client.messages.parse.assert_not_called()


# ---------------------------------------------------------------------------
# 5. Existing topics and the house style reach the model
# ---------------------------------------------------------------------------


def test_existing_topics_and_house_style_reach_the_model(client, monkeypatch):
    create_topic(client, "Prayer", "General pattern of prayer.")
    create_topic(client, "Adversity", "Enduring hard things.")
    mock_client = mock_anthropic_returning(monkeypatch, DEFAULT_TOPIC_FILL)

    resp = client.post("/api/ai/topics/fill", json={"prompt": "reluctant prayer"})
    assert resp.status_code == 200

    system_text = mock_client.messages.parse.call_args.kwargs["system"][0]["text"]
    assert "Prayer" in system_text
    assert "Adversity" in system_text
    assert "Not ..." in system_text
    assert "Hemingway, not a hymn" in system_text


# ---------------------------------------------------------------------------
# 6. The call is logged
# ---------------------------------------------------------------------------


def test_topic_fill_call_is_logged(client, paths, monkeypatch):
    mock_anthropic_returning(monkeypatch, DEFAULT_TOPIC_FILL)
    resp = client.post("/api/ai/topics/fill", json={"prompt": "reluctant prayer"})
    assert resp.status_code == 200

    rows = all_log_rows(paths)
    assert len(rows) == 1
    assert rows[0]["feature"] == "fill_topic"
    assert rows[0]["model"] == "claude-haiku-4-5"
    assert rows[0]["status"] == "ok"
    assert rows[0]["latency_ms"] is not None


# ---------------------------------------------------------------------------
# 7. Missing key / API error / parsed_output is None
# ---------------------------------------------------------------------------


def test_missing_key_returns_503_and_nothing_is_logged(client, paths, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    mock_client = mock_anthropic_returning(monkeypatch, DEFAULT_TOPIC_FILL)

    resp = client.post("/api/ai/topics/fill", json={"prompt": "reluctant prayer"})
    assert resp.status_code == 503
    mock_client.messages.parse.assert_not_called()
    assert all_log_rows(paths) == []


def test_api_error_returns_502_and_logs_an_api_error_row(client, paths, monkeypatch):
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    mock_anthropic_raising(monkeypatch, anthropic.APIConnectionError(request=request))

    resp = client.post("/api/ai/topics/fill", json={"prompt": "reluctant prayer"})
    assert resp.status_code == 502

    rows = all_log_rows(paths)
    assert len(rows) == 1
    assert rows[0]["status"] == "api_error"


def test_parsed_output_none_returns_502(client, monkeypatch):
    mock_anthropic_parsed_none(monkeypatch)
    resp = client.post("/api/ai/topics/fill", json={"prompt": "reluctant prayer"})
    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# 8. Note fill generates with no prompt, and writes nothing
# ---------------------------------------------------------------------------


def test_note_fill_generates_with_no_prompt_and_writes_nothing(client, monkeypatch):
    topic_id = create_topic(client, "Faith", "Trusting God before the outcome is visible.")
    verse_id = _TEST_VERSE["id"]
    approve_resp = client.post(
        f"/api/topics/{topic_id}/verses",
        json={"verse_id": verse_id, "status": "approved", "source": "manual"},
    )
    assert approve_resp.status_code == 200

    mock_client = mock_anthropic_returning(monkeypatch, DEFAULT_NOTE_FILL)
    resp = client.post(
        f"/api/ai/topics/{topic_id}/verses/{verse_id}/note/fill", json={}
    )
    assert resp.status_code == 200
    assert resp.json()["note"] == DEFAULT_NOTE_FILL.note

    system_text = mock_client.messages.parse.call_args.kwargs["system"][0]["text"]
    assert "Faith" in system_text
    assert "Trusting God before the outcome is visible." in system_text
    user_text = mock_client.messages.parse.call_args.kwargs["messages"][0]["content"]
    assert "no words" in user_text.lower()

    topic = client.get(f"/api/topics/{topic_id}").json()
    linked_verse = next(v for v in topic["verses"] if v["verse_id"] == verse_id)
    assert linked_verse["note"] == ""


# ---------------------------------------------------------------------------
# 9. Note fill passes the surrounding verses and marks the subject
# ---------------------------------------------------------------------------


def test_note_fill_passes_neighbours_and_marks_one_subject(client, monkeypatch):
    topic_id = create_topic(client, "Faith")
    verse_id = _TEST_VERSE["id"]
    client.post(
        f"/api/topics/{topic_id}/verses",
        json={"verse_id": verse_id, "status": "approved", "source": "manual"},
    )

    mock_client = mock_anthropic_returning(monkeypatch, DEFAULT_NOTE_FILL)
    resp = client.post(f"/api/ai/topics/{topic_id}/verses/{verse_id}/note/fill", json={})
    assert resp.status_code == 200

    system_text = mock_client.messages.parse.call_args.kwargs["system"][0]["text"]
    for text in _neighbour_texts(_TEST_VERSE):
        assert text in system_text
    # Exactly one *line* uses ">>" as the subject marker — the phrase also
    # appears once in the passage header's own instructional text ("marked
    # >>"), so count marker lines specifically, not raw substring occurrences.
    assert len(re.findall(r"^>> ", system_text, re.MULTILINE)) == 1


# ---------------------------------------------------------------------------
# 10. Note is capped, and a blank note -> 502
# ---------------------------------------------------------------------------


def test_note_is_capped_at_300_chars(client, monkeypatch):
    topic_id = create_topic(client, "Faith")
    verse_id = _TEST_VERSE["id"]
    client.post(
        f"/api/topics/{topic_id}/verses",
        json={"verse_id": verse_id, "status": "approved", "source": "manual"},
    )
    fill = ai._NoteFill(note="x" * 400, reason="reason")
    mock_anthropic_returning(monkeypatch, fill)

    resp = client.post(f"/api/ai/topics/{topic_id}/verses/{verse_id}/note/fill", json={})
    assert resp.status_code == 200
    assert len(resp.json()["note"]) <= 300


def test_blank_note_returns_502(client, monkeypatch):
    topic_id = create_topic(client, "Faith")
    verse_id = _TEST_VERSE["id"]
    client.post(
        f"/api/topics/{topic_id}/verses",
        json={"verse_id": verse_id, "status": "approved", "source": "manual"},
    )
    fill = ai._NoteFill(note="   ", reason="reason")
    mock_anthropic_returning(monkeypatch, fill)

    resp = client.post(f"/api/ai/topics/{topic_id}/verses/{verse_id}/note/fill", json={})
    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# 11. Bad ids
# ---------------------------------------------------------------------------


def test_note_fill_unknown_topic_returns_404_without_calling_sdk(client, monkeypatch):
    mock_client = mock_anthropic_returning(monkeypatch, DEFAULT_NOTE_FILL)
    resp = client.post(
        f"/api/ai/topics/999999/verses/{_TEST_VERSE['id']}/note/fill", json={}
    )
    assert resp.status_code == 404
    mock_client.messages.parse.assert_not_called()


def test_note_fill_unknown_verse_returns_400_without_calling_sdk(client, monkeypatch):
    topic_id = create_topic(client, "Faith")
    mock_client = mock_anthropic_returning(monkeypatch, DEFAULT_NOTE_FILL)
    resp = client.post(
        f"/api/ai/topics/{topic_id}/verses/99999999/note/fill", json={}
    )
    assert resp.status_code == 400
    mock_client.messages.parse.assert_not_called()


# ---------------------------------------------------------------------------
# 12. A logging failure does not break a fill
# ---------------------------------------------------------------------------


def test_logging_failure_does_not_break_a_fill(client, monkeypatch):
    monkeypatch.setattr(
        ai, "AI_LOG_DB_PATH", "/nonexistent-directory-for-test/ai_log.db"
    )
    mock_anthropic_returning(monkeypatch, DEFAULT_TOPIC_FILL)

    resp = client.post("/api/ai/topics/fill", json={"prompt": "reluctant prayer"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Reluctant Prayer"
