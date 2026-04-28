"""Minimal SSE parser for httpx streaming responses.

Yields (event_id, data_str) tuples. Comments (`:` lines) and keep-alives
are skipped. Multiline `data:` blocks are concatenated with newlines.
"""
from __future__ import annotations

from typing import Iterator

import httpx


def iter_sse(response: httpx.Response) -> Iterator[tuple[str | None, str]]:
    event_id: str | None = None
    data_lines: list[str] = []
    for raw_line in response.iter_lines():
        line = raw_line if isinstance(raw_line, str) else raw_line.decode("utf-8", "replace")
        if line == "":
            if data_lines:
                yield event_id, "\n".join(data_lines)
                data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("id:"):
            event_id = line[3:].strip() or None
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip(" "))
    if data_lines:
        yield event_id, "\n".join(data_lines)
