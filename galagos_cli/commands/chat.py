"""`galagos chat` — send a message and stream the agent's response."""
from __future__ import annotations

import json
import sys

import httpx
import typer
from rich.console import Console

from ..client import require_token, stream_client
from ..config import Config
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
):
    """Send a message and print the agent's streamed reply to stdout."""
    if ctx.invoked_subcommand is not None:
        return
    cfg = Config.load()
    require_token(cfg)
    pid, tid = _resolve(cfg, project, thread)

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
                    _render_event(payload, show_tools=show_tools)
                    if payload.get("type") in TERMINAL_TYPES:
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


@app.command("cancel")
def cancel(
    project: str = typer.Option(None, "--project", "-p"),
    thread: str = typer.Option(None, "--thread", "-t"),
):
    """Cancel a thread that's currently processing."""
    cfg = Config.load()
    require_token(cfg)
    pid, tid = _resolve(cfg, project, thread)
    url = f"/session/projects/{pid}/threads/{tid}/cancel/"
    from ..client import ApiError, post
    try:
        post(cfg, url, json={})
    except ApiError as e:
        err_console.print(f"[red]Cancel failed: {e}[/red]")
        raise typer.Exit(code=1)
    console.print("[green]✓[/green] Cancellation signal sent.")
