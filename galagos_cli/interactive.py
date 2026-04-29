"""Interactive prompts for `question.asked` and `permission.asked` events.

The Django chat-stream SSE pipeline can interrupt the agent's response with
two OpenCode-bus event types that block until the user replies:

- ``permission.asked``: the agent wants to do something (run a command,
  write a file) and waits for allow/deny. NOTE: the backend currently
  auto-approves these (see ``tasks_streaming.py``), so in practice the
  CLI never needs to prompt — they're informational. Kept here for the
  day the auto-approve is removed.
- ``question.asked``:   the agent asks one or more clarifying questions
  before continuing.

The legacy event names ``PERMISSION_REQUEST`` / ``QUESTION_REQUEST`` and
the legacy ``data.{id,questions}`` field shape are also accepted so the
CLI keeps working against older backends.

While the request is open the SSE stream stays connected; the agent resumes
emitting events after the response is POSTed back through Django.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import Enum
from typing import Any

from rich.console import Console

err = Console(stderr=True)


class InteractivityMode(Enum):
    """How to handle question / permission events."""

    PROMPT = "prompt"
    """Ask the user (the default — requires a tty)."""
    YES = "yes"
    """Auto-allow permissions; fail on questions (questions need real input)."""
    REFUSE = "refuse"
    """Fail loudly if any interactive event arrives."""


class InteractiveError(Exception):
    """Raised when an interactive event arrives but cannot be answered."""


@dataclass
class PermissionAnswer:
    request_id: str
    action: str  # "allow_once" | "allow_always" | "deny"


@dataclass
class QuestionAnswer:
    request_id: str
    answers: list[list[str]] | None = None  # one inner list per question
    reject: bool = False


def _payload_body(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the body dict for an event, preferring v2 ``properties`` over
    the legacy ``data`` envelope."""
    body = payload.get("properties")
    if isinstance(body, dict) and body:
        return body
    body = payload.get("data")
    return body if isinstance(body, dict) else {}


# ─── Permission ─────────────────────────────────────────────────────────────

def handle_permission_request(
    payload: dict[str, Any], mode: InteractivityMode
) -> PermissionAnswer:
    data = _payload_body(payload)
    request_id = str(data.get("id") or "")
    if not request_id:
        raise InteractiveError("Permission event missing request id.")

    tool_data = data.get("tool")
    tool_name = (
        (tool_data.get("name") if isinstance(tool_data, dict) else None)
        or data.get("permission")
        or "tool"
    )
    kind = data.get("kind") or data.get("permission") or "permission"
    message = data.get("message") or ""
    allow_text = data.get("allowText") or ""

    if mode is InteractivityMode.YES:
        return PermissionAnswer(request_id=request_id, action="allow_once")
    if mode is InteractivityMode.REFUSE:
        raise InteractiveError(
            f"Agent requested permission ({kind}, {tool_name}) "
            f"but --no-interactive is set."
        )

    if not _stdin_is_tty():
        raise InteractiveError(
            f"Agent requested permission ({kind}, {tool_name}) but "
            f"stdin is not a tty. Re-run with --yes or attach a terminal."
        )

    err.print()
    err.rule(f"[yellow]Permission request[/yellow] · {tool_name} ({kind})")
    if message:
        err.print(message)
    if allow_text:
        err.print(f"[dim]{allow_text}[/dim]")
    err.print(
        "[bold]\\[a][/bold]llow once   "
        "[bold]\\[A][/bold]llow always   "
        "[bold]\\[d][/bold]eny"
    )

    while True:
        choice = _read_line("> ").strip().lower()
        if choice in ("a", "allow", "allow_once", "y", "yes", ""):
            return PermissionAnswer(request_id, "allow_once")
        if choice in ("aa", "always"):
            return PermissionAnswer(request_id, "allow_always")
        if choice in ("d", "deny", "n", "no"):
            return PermissionAnswer(request_id, "deny")
        err.print("[red]Type a, A, or d.[/red]")


# ─── Question ───────────────────────────────────────────────────────────────

def handle_question_request(
    payload: dict[str, Any], mode: InteractivityMode
) -> QuestionAnswer:
    data = _payload_body(payload)
    request_id = str(data.get("id") or "")
    if not request_id:
        raise InteractiveError("Question event missing request id.")
    questions = data.get("questions") or []
    if not isinstance(questions, list) or not questions:
        # Reject — nothing to ask. Lets the agent see the response and
        # fall back to a sensible default rather than hanging forever.
        return QuestionAnswer(request_id=request_id, reject=True)

    if mode is InteractivityMode.YES:
        # Questions need real input — auto-yes is meaningless. Reject so
        # the agent unblocks rather than hanging the run.
        err.print(
            "[yellow]Agent asked a question; auto-rejecting under --yes "
            "(questions need real input).[/yellow]"
        )
        return QuestionAnswer(request_id=request_id, reject=True)
    if mode is InteractivityMode.REFUSE:
        # Reject so the agent unblocks and the session leaves PROCESSING,
        # but the caller will still exit non-zero. The error message goes
        # via the caller after we POST the rejection.
        err.print(
            "[red]Agent asked a clarifying question but --no-interactive is "
            "set.[/red]"
        )
        return QuestionAnswer(request_id=request_id, reject=True)

    if not _stdin_is_tty():
        raise InteractiveError(
            "Agent asked a clarifying question but stdin is not a tty."
        )

    answers: list[list[str]] = []
    err.print()
    err.rule("[cyan]Question from agent[/cyan]")
    for idx, q in enumerate(questions):
        if not isinstance(q, dict):
            answers.append([])
            continue
        header = q.get("header")
        text = q.get("question") or "(no prompt)"
        options = q.get("options") or []
        multiple = bool(q.get("multiple"))

        if header:
            err.print(f"[bold]{header}[/bold]")
        err.print(f"  Q{idx + 1}: {text}")
        if options:
            for i, opt in enumerate(options):
                label = opt.get("label") if isinstance(opt, dict) else str(opt)
                desc = opt.get("description") if isinstance(opt, dict) else ""
                line = f"    [{i + 1}] {label}"
                if desc:
                    line += f"  [dim]— {desc}[/dim]"
                err.print(line)
            hint = "comma-separated numbers" if multiple else "one number"
            picked = _prompt_for_choice(options, multiple=multiple, hint=hint)
            answers.append(picked)
        else:
            line = _read_line("    > ")
            answers.append([line] if line else [])
    return QuestionAnswer(request_id=request_id, answers=answers)


def _prompt_for_choice(
    options: list[Any], *, multiple: bool, hint: str
) -> list[str]:
    while True:
        raw = _read_line(f"    pick ({hint}, or text answer): ").strip()
        if not raw:
            err.print("[red]Pick at least one option.[/red]")
            continue
        if multiple:
            parts = [p.strip() for p in raw.split(",") if p.strip()]
        else:
            parts = [raw]
        # Try to interpret each part as an index.
        picked: list[str] = []
        free_text = False
        for part in parts:
            if part.isdigit():
                idx = int(part) - 1
                if 0 <= idx < len(options):
                    opt = options[idx]
                    label = opt.get("label") if isinstance(opt, dict) else str(opt)
                    picked.append(str(label))
                    continue
            free_text = True
            picked.append(part)
        if free_text and len(parts) > 1:
            err.print(
                "[red]Mixed indices and free text isn't supported — "
                "pick all numbers or type one free-form answer.[/red]"
            )
            continue
        return picked


# ─── Helpers ────────────────────────────────────────────────────────────────

def _stdin_is_tty() -> bool:
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def _read_line(prompt: str) -> str:
    """Read one line from stdin, with prompt on stderr.

    Using stderr for the prompt keeps stdout clean for the agent's text
    response — the user can `galagos chat "..." > out.md` and still see
    interactive prompts on the terminal.
    """
    err.file.write(prompt)
    err.file.flush()
    line = sys.stdin.readline()
    if not line:
        raise InteractiveError("stdin closed while awaiting answer.")
    return line.rstrip("\n")
