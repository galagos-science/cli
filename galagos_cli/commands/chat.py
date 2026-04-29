"""`galagos chat` — send a message and stream the agent's response."""
from __future__ import annotations

import json
import sys

import httpx
import typer
from rich.console import Console

from ..client import client, require_token, stream_client
from ..config import Config
from ..interactive import (
    InteractiveError,
    InteractivityMode,
    handle_permission_request,
    handle_question_request,
)
from ..sse import iter_sse

app = typer.Typer(invoke_without_command=True, help="Chat with the agent.")
console = Console()
err_console = Console(stderr=True)

# Terminal SSE event types — when seen, stop reading the stream.
TERMINAL_TYPES = {
    "session.idle",
    "session.error",
    "budget_exceeded",
    "stream_cancelled",
}


def _resolve(cfg: Config, project: str | None, thread: str | None) -> tuple[str, str]:
    pid = project or cfg.default_project_id
    tid = thread or cfg.default_thread_id
    if not pid:
        err_console.print(
            "[red]No project selected.[/red] Pass --project or "
            "`galagos projects use <id>`."
        )
        raise typer.Exit(code=1)
    if not tid:
        err_console.print(
            "[red]No thread selected.[/red] Create one with "
            "`galagos chats new` or pass --thread."
        )
        raise typer.Exit(code=1)
    return pid, tid


def _render_event(payload: dict, *, show_tools: bool) -> None:
    """Render one SSE event payload to stdout."""
    evt_type = payload.get("type", "")
    props = payload.get("properties") or {}

    # OpenCode v2 streaming text: incremental delta on the "text" field of a part.
    if evt_type == "message.part.delta":
        if props.get("field") == "text":
            delta = props.get("delta", "")
            if delta:
                sys.stdout.write(delta)
                sys.stdout.flush()
        return

    # Legacy / alternative text event shape — kept for compatibility.
    if evt_type == "TEXT_MESSAGE_CONTENT":
        delta = payload.get("delta") or payload.get("data", {}).get("delta", "")
        if delta:
            sys.stdout.write(delta)
            sys.stdout.flush()
        return

    if evt_type in ("CONNECTION_ESTABLISHED", "AGENT_HEARTBEAT"):
        return

    # Session/message lifecycle events — silent unless --tools.
    if evt_type in ("session.updated", "session.status", "message.updated"):
        return

    if not show_tools:
        return

    if evt_type == "message.part.updated":
        part = props.get("part") or {}
        ptype = part.get("type") or "?"
        if ptype == "tool":
            tool = part.get("tool") or "tool"
            state = (part.get("state") or {}).get("status") or ""
            err_console.print(f"[dim]· {tool} {state}[/dim]")
        elif ptype in ("step-start", "step-finish"):
            err_console.print(f"[dim]· {ptype}[/dim]")
        return

    if evt_type == "OPENCODE_TOOL":
        data = payload.get("data") or payload
        tool_name = data.get("tool") or data.get("name") or "tool"
        state = data.get("state") or data.get("status") or ""
        err_console.print(f"[dim]· {tool_name} {state}[/dim]")
        return

    if evt_type == "STEP_STARTED":
        err_console.print("[dim]· step started[/dim]")
    elif evt_type == "STEP_FINISHED":
        err_console.print("[dim]· step finished[/dim]")
    elif evt_type == "ERROR":
        msg = payload.get("error") or payload.get("data", {}).get("message", "")
        err_console.print(f"[red]error:[/red] {msg}")


def _post_permission_response(
    cfg: Config, pid: str, tid: str, request_id: str, action: str
) -> None:
    url = f"/session/projects/{pid}/threads/{tid}/permission/respond/"
    with client(cfg, timeout=30) as c:
        r = c.post(url, json={"request_id": request_id, "action": action})
        if r.status_code >= 400:
            err_console.print(
                f"[red]Permission response failed (HTTP {r.status_code}): {r.text}[/red]"
            )


def _post_question_response(
    cfg: Config,
    pid: str,
    tid: str,
    request_id: str,
    *,
    answers: list[list[str]] | None,
    reject: bool,
) -> None:
    url = f"/session/projects/{pid}/threads/{tid}/question/respond/"
    body: dict = {"request_id": request_id}
    if reject:
        body["action"] = "reject"
    else:
        body["answers"] = answers or []
    with client(cfg, timeout=30) as c:
        r = c.post(url, json=body)
        if r.status_code >= 400:
            err_console.print(
                f"[red]Question response failed (HTTP {r.status_code}): {r.text}[/red]"
            )


@app.callback()
def chat_main(
    ctx: typer.Context,
    message: str = typer.Argument(None, help="Message to send (omit to reconnect)."),
    project: str = typer.Option(None, "--project", "-p", help="Project ID."),
    thread: str = typer.Option(None, "--thread", "-t", help="Thread ID."),
    agent: str = typer.Option("build", "--agent", help="Agent profile name."),
    resume: bool = typer.Option(
        False, "--resume", help="Reconnect to an in-flight stream (no new message)."
    ),
    show_tools: bool = typer.Option(
        False, "--tools/--no-tools",
        help="Print tool-call activity to stderr.",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help="Auto-allow permission requests; reject questions (they need input).",
    ),
    no_interactive: bool = typer.Option(
        False, "--no-interactive",
        help="Fail loudly if the agent asks a question or for permission.",
    ),
):
    """Send a message and print the agent's streamed reply to stdout."""
    if ctx.invoked_subcommand is not None:
        return
    cfg = Config.load()
    require_token(cfg)
    pid, tid = _resolve(cfg, project, thread)

    if yes and no_interactive:
        err_console.print(
            "[red]--yes and --no-interactive are mutually exclusive.[/red]"
        )
        raise typer.Exit(code=2)
    mode = (
        InteractivityMode.REFUSE if no_interactive
        else InteractivityMode.YES if yes
        else InteractivityMode.PROMPT
    )

    body: dict = {"agent": agent, "last_event_id": "0"}
    if resume:
        body["content"] = ""
    else:
        if not message:
            err_console.print(
                "[red]No message given.[/red] Pass a message or --resume."
            )
            raise typer.Exit(code=1)
        body["content"] = message

    url = f"/session/projects/{pid}/threads/{tid}/chat/"
    last_event_id = "0"
    try:
        with stream_client(cfg) as c:
            with c.stream("POST", url, json=body, headers={"Accept": "text/event-stream"}) as r:
                if r.status_code != 200:
                    try:
                        err_console.print(f"[red]HTTP {r.status_code}: {r.read().decode('utf-8', 'replace')}[/red]")
                    except Exception:
                        err_console.print(f"[red]HTTP {r.status_code}[/red]")
                    raise typer.Exit(code=1)
                for event_id, data_str in iter_sse(r):
                    if event_id:
                        last_event_id = event_id
                    try:
                        payload = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    evt_type = payload.get("type")
                    # Permission events: the backend currently auto-approves
                    # `permission.asked` server-side, so the CLI typically
                    # doesn't need to prompt. Kept for parity in case the
                    # auto-approve is removed; legacy `PERMISSION_REQUEST`
                    # is also accepted.
                    if evt_type in ("PERMISSION_REQUEST", "permission.asked"):
                        if show_tools:
                            err_console.print(
                                "[dim]· permission.asked (backend auto-approves)[/dim]"
                            )
                        # If the CLI is started with --no-interactive we still
                        # honour it — better to fail loudly than to silently
                        # depend on backend auto-approve.
                        if mode is InteractivityMode.REFUSE:
                            try:
                                handle_permission_request(payload, mode)
                            except InteractiveError as e:
                                err_console.print(f"[red]{e}[/red]")
                                raise typer.Exit(code=1)
                        continue
                    if evt_type in ("QUESTION_REQUEST", "question.asked"):
                        try:
                            ans = handle_question_request(payload, mode)
                        except InteractiveError as e:
                            err_console.print(f"[red]{e}[/red]")
                            raise typer.Exit(code=1)
                        _post_question_response(
                            cfg, pid, tid, ans.request_id,
                            answers=ans.answers, reject=ans.reject,
                        )
                        # In --no-interactive mode the agent has now been
                        # unblocked via reject; exit non-zero so the caller
                        # (CI, scripts) sees the failure.
                        if mode is InteractivityMode.REFUSE:
                            raise typer.Exit(code=1)
                        continue
                    # The agent also emits an inline tool form via
                    # message.part.updated with part.tool=="question". The
                    # bus-level question.asked above is the canonical one,
                    # so we ignore the inline form for prompting and let
                    # _render_event surface it as a tool call under --tools.

                    _render_event(payload, show_tools=show_tools)
                    if evt_type in TERMINAL_TYPES:
                        break
    except KeyboardInterrupt:
        err_console.print(
            f"\n[yellow]Disconnected.[/yellow] The agent keeps running. "
            f"Reconnect with `galagos chat --resume` "
            f"(last_event_id={last_event_id})."
        )
        raise typer.Exit(code=130)
    except httpx.RequestError as e:
        err_console.print(f"[red]Connection error: {e}[/red]")
        raise typer.Exit(code=1)
    sys.stdout.write("\n")


# `cancel` lives on the `chats` typer (commands/chats.py) because the
# `chat` typer accepts a positional MESSAGE and Typer can't disambiguate
# `chat cancel --project X` between "chat with message='cancel'" and
# "invoke the cancel subcommand". Use `galagos chats cancel ...`.
