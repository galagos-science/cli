"""Tests for galagos_cli.interactive."""
from __future__ import annotations

import io

import pytest

from galagos_cli import interactive
from galagos_cli.interactive import (
    InteractiveError,
    InteractivityMode,
    handle_permission_request,
    handle_question_request,
)


@pytest.fixture
def fake_stdin(monkeypatch):
    def _set(text: str, *, isatty: bool = True):
        stream = io.StringIO(text)
        # Force the tty check to whatever the test wants without touching
        # the real stdin (pytest's stdin is not a tty under capture).
        monkeypatch.setattr(interactive, "_stdin_is_tty", lambda: isatty)
        monkeypatch.setattr(interactive.sys, "stdin", stream)
        return stream

    return _set


# ─── Permission ─────────────────────────────────────────────────────────────

def test_permission_yes_mode_returns_allow_once(fake_stdin):
    payload = {"type": "PERMISSION_REQUEST", "data": {"id": "req-1"}}
    ans = handle_permission_request(payload, InteractivityMode.YES)
    assert ans.request_id == "req-1"
    assert ans.action == "allow_once"


def test_permission_refuse_mode_raises(fake_stdin):
    payload = {"type": "PERMISSION_REQUEST", "data": {"id": "req-2"}}
    with pytest.raises(InteractiveError):
        handle_permission_request(payload, InteractivityMode.REFUSE)


def test_permission_prompt_a_returns_allow_once(fake_stdin):
    fake_stdin("a\n")
    payload = {
        "type": "PERMISSION_REQUEST",
        "data": {"id": "req-3", "tool": {"name": "bash"}, "kind": "bash",
                 "message": "rm -rf /tmp/scratch"},
    }
    ans = handle_permission_request(payload, InteractivityMode.PROMPT)
    assert ans.action == "allow_once"


def test_permission_prompt_aa_returns_allow_always(fake_stdin):
    fake_stdin("aa\n")
    payload = {"type": "PERMISSION_REQUEST", "data": {"id": "req-4"}}
    ans = handle_permission_request(payload, InteractivityMode.PROMPT)
    assert ans.action == "allow_always"


def test_permission_prompt_d_returns_deny(fake_stdin):
    fake_stdin("d\n")
    payload = {"type": "PERMISSION_REQUEST", "data": {"id": "req-5"}}
    ans = handle_permission_request(payload, InteractivityMode.PROMPT)
    assert ans.action == "deny"


def test_permission_reprompts_on_garbage_input(fake_stdin):
    fake_stdin("huh\n???\nd\n")
    payload = {"type": "PERMISSION_REQUEST", "data": {"id": "req-6"}}
    ans = handle_permission_request(payload, InteractivityMode.PROMPT)
    assert ans.action == "deny"


def test_permission_no_tty_raises(fake_stdin):
    fake_stdin("", isatty=False)
    payload = {"type": "PERMISSION_REQUEST", "data": {"id": "req-7"}}
    with pytest.raises(InteractiveError):
        handle_permission_request(payload, InteractivityMode.PROMPT)


def test_permission_missing_id_raises(fake_stdin):
    payload = {"type": "PERMISSION_REQUEST", "data": {}}
    with pytest.raises(InteractiveError):
        handle_permission_request(payload, InteractivityMode.YES)


# ─── Question ───────────────────────────────────────────────────────────────

def test_question_yes_mode_rejects(fake_stdin):
    payload = {
        "type": "QUESTION_REQUEST",
        "data": {"id": "q-1", "questions": [{"question": "Which DB?"}]},
    }
    ans = handle_question_request(payload, InteractivityMode.YES)
    assert ans.reject is True
    assert ans.request_id == "q-1"


def test_question_refuse_mode_raises(fake_stdin):
    payload = {
        "type": "QUESTION_REQUEST",
        "data": {"id": "q-2", "questions": [{"question": "?"}]},
    }
    with pytest.raises(InteractiveError):
        handle_question_request(payload, InteractivityMode.REFUSE)


def test_question_with_no_questions_auto_rejects(fake_stdin):
    payload = {"type": "QUESTION_REQUEST", "data": {"id": "q-3", "questions": []}}
    ans = handle_question_request(payload, InteractivityMode.PROMPT)
    assert ans.reject is True


def test_question_free_text_answer(fake_stdin):
    fake_stdin("nucleotide blast\n")
    payload = {
        "type": "QUESTION_REQUEST",
        "data": {
            "id": "q-4",
            "questions": [{"question": "Which BLAST flavor?"}],
        },
    }
    ans = handle_question_request(payload, InteractivityMode.PROMPT)
    assert ans.reject is False
    assert ans.answers == [["nucleotide blast"]]


def test_question_numeric_choice_resolves_to_label(fake_stdin):
    fake_stdin("2\n")
    payload = {
        "type": "QUESTION_REQUEST",
        "data": {
            "id": "q-5",
            "questions": [
                {
                    "question": "Pick a database",
                    "options": [
                        {"label": "nr"},
                        {"label": "swissprot"},
                        {"label": "refseq"},
                    ],
                },
            ],
        },
    }
    ans = handle_question_request(payload, InteractivityMode.PROMPT)
    assert ans.answers == [["swissprot"]]


def test_question_multiple_select_csv(fake_stdin):
    fake_stdin("1, 3\n")
    payload = {
        "type": "QUESTION_REQUEST",
        "data": {
            "id": "q-6",
            "questions": [
                {
                    "question": "Which threads?",
                    "options": [{"label": "1"}, {"label": "2"}, {"label": "4"}],
                    "multiple": True,
                },
            ],
        },
    }
    ans = handle_question_request(payload, InteractivityMode.PROMPT)
    assert ans.answers == [["1", "4"]]


def test_question_multi_question_collects_per_question(fake_stdin):
    fake_stdin("hello\n2\n")
    payload = {
        "type": "QUESTION_REQUEST",
        "data": {
            "id": "q-7",
            "questions": [
                {"question": "Free-form?"},
                {
                    "question": "Pick one",
                    "options": [{"label": "alpha"}, {"label": "beta"}],
                },
            ],
        },
    }
    ans = handle_question_request(payload, InteractivityMode.PROMPT)
    assert ans.answers == [["hello"], ["beta"]]


def test_question_no_tty_raises(fake_stdin):
    fake_stdin("", isatty=False)
    payload = {
        "type": "QUESTION_REQUEST",
        "data": {"id": "q-8", "questions": [{"question": "?"}]},
    }
    with pytest.raises(InteractiveError):
        handle_question_request(payload, InteractivityMode.PROMPT)
