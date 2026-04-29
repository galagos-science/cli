"""Tests for galagos_cli.sse.iter_sse."""
from __future__ import annotations

from galagos_cli.sse import iter_sse


class _FakeResponse:
    """Mimics the slice of httpx.Response that iter_sse uses."""

    def __init__(self, lines: list[str]):
        self._lines = lines

    def iter_lines(self):
        yield from self._lines


def test_yields_event_with_id_and_data():
    r = _FakeResponse(["id: 123-0", "data: hello", ""])
    events = list(iter_sse(r))
    assert events == [("123-0", "hello")]


def test_concatenates_multiline_data():
    r = _FakeResponse([
        "id: 1",
        "data: line one",
        "data: line two",
        "",
    ])
    events = list(iter_sse(r))
    assert events == [("1", "line one\nline two")]


def test_skips_comments_and_keepalives():
    r = _FakeResponse([
        ":",
        ": keep-alive",
        "id: 5",
        "data: payload",
        "",
    ])
    events = list(iter_sse(r))
    assert events == [("5", "payload")]


def test_handles_data_without_id():
    r = _FakeResponse(["data: anonymous", ""])
    events = list(iter_sse(r))
    assert events == [(None, "anonymous")]


def test_emits_trailing_event_without_terminating_blank_line():
    r = _FakeResponse(["id: 99", "data: last"])
    events = list(iter_sse(r))
    assert events == [("99", "last")]


def test_strips_leading_whitespace_after_data_prefix():
    # Implementation strips all leading whitespace after `data:` (slightly
    # looser than the SSE spec's single-space rule, but matches the Django
    # SSE producer which always emits exactly `data: <payload>`).
    r = _FakeResponse(["data:   indented", ""])
    events = list(iter_sse(r))
    assert events == [(None, "indented")]


def test_id_can_be_updated_within_a_block():
    r = _FakeResponse([
        "id: 1",
        "id: 2",
        "data: payload",
        "",
    ])
    # The last `id:` wins for the dispatched event.
    events = list(iter_sse(r))
    assert events == [("2", "payload")]


def test_blank_line_without_data_is_a_no_op():
    r = _FakeResponse(["", "id: 1", "data: x", ""])
    events = list(iter_sse(r))
    assert events == [("1", "x")]
