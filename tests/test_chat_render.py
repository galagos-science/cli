"""Tests for galagos_cli.commands.chat._render_event."""
from __future__ import annotations

from galagos_cli.commands import chat as chat_mod


def test_v2_text_delta_writes_to_stdout(capsys):
    payload = {
        "type": "message.part.delta",
        "properties": {"field": "text", "delta": "PONG"},
    }
    chat_mod._render_event(payload, show_tools=False)
    captured = capsys.readouterr()
    assert captured.out == "PONG"


def test_v2_non_text_delta_is_silent(capsys):
    payload = {
        "type": "message.part.delta",
        "properties": {"field": "reasoning", "delta": "thinking…"},
    }
    chat_mod._render_event(payload, show_tools=False)
    captured = capsys.readouterr()
    assert captured.out == ""


def test_legacy_text_message_content_writes_delta(capsys):
    payload = {"type": "TEXT_MESSAGE_CONTENT", "delta": "hello"}
    chat_mod._render_event(payload, show_tools=False)
    captured = capsys.readouterr()
    assert captured.out == "hello"


def test_legacy_text_message_content_unwraps_data_field(capsys):
    payload = {"type": "TEXT_MESSAGE_CONTENT", "data": {"delta": "wrapped"}}
    chat_mod._render_event(payload, show_tools=False)
    captured = capsys.readouterr()
    assert captured.out == "wrapped"


def test_connection_established_is_silent(capsys):
    chat_mod._render_event({"type": "CONNECTION_ESTABLISHED"}, show_tools=False)
    captured = capsys.readouterr()
    assert captured.out == ""


def test_lifecycle_events_are_silent_without_tools(capsys):
    for evt in ("session.updated", "session.status", "message.updated"):
        chat_mod._render_event({"type": evt}, show_tools=False)
    captured = capsys.readouterr()
    assert captured.out == ""


def test_tool_event_is_silent_without_show_tools(capsys):
    payload = {
        "type": "message.part.updated",
        "properties": {"part": {"type": "tool", "tool": "bash"}},
    }
    chat_mod._render_event(payload, show_tools=False)
    captured = capsys.readouterr()
    assert captured.out == ""


def test_unknown_event_does_not_write_to_stdout(capsys):
    chat_mod._render_event({"type": "SOMETHING_ELSE"}, show_tools=False)
    captured = capsys.readouterr()
    assert captured.out == ""


def test_empty_text_delta_is_a_noop(capsys):
    payload = {
        "type": "message.part.delta",
        "properties": {"field": "text", "delta": ""},
    }
    chat_mod._render_event(payload, show_tools=False)
    captured = capsys.readouterr()
    assert captured.out == ""
