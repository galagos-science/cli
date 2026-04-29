"""`galagos chats` — list / create chat threads."""
from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from ..client import ApiError, get, post, require_token
from ..config import Config

app = typer.Typer(help="List and create chat threads in a project.")
console = Console()


def _resolve_project(cfg: Config, project: str | None) -> str:
    pid = project or cfg.default_project_id
    if not pid:
        console.print(
            "[red]No project selected.[/red] Pass --project <id> "
            "or set one with `galagos projects use <id>`."
        )
        raise typer.Exit(code=1)
    return pid


@app.command("ls")
def ls(
    project: str = typer.Option(None, "--project", "-p", help="Project ID."),
):
    """List threads in a project."""
    cfg = Config.load()
    require_token(cfg)
    pid = _resolve_project(cfg, project)
    try:
        data = get(cfg, f"/session/projects/{pid}/threads/")
    except ApiError as e:
        console.print(f"[red]Failed to list threads: {e}[/red]")
        raise typer.Exit(code=1)
    threads = data.get("results", []) if isinstance(data, dict) else data
    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Title")
    table.add_column("Status", style="dim")
    table.add_column("Last activity", style="dim")
    table.add_column("Default", style="green")
    for t in threads:
        is_default = "★" if cfg.default_thread_id == t.get("id") else ""
        table.add_row(
            t.get("id", "?"),
            t.get("title") or "(untitled)",
            t.get("status") or "",
            (t.get("lastActivityAt") or "")[:19].replace("T", " "),
            is_default,
        )
    console.print(table)


@app.command("new")
def new(
    project: str = typer.Option(None, "--project", "-p", help="Project ID."),
    title: str = typer.Option(None, "--title", "-T", help="Thread title."),
    use_as_default: bool = typer.Option(
        True, "--default/--no-default",
        help="Set the new thread as default.",
    ),
):
    """Create a new thread in a project."""
    cfg = Config.load()
    require_token(cfg)
    pid = _resolve_project(cfg, project)
    body: dict = {}
    if title:
        body["title"] = title
    try:
        thread = post(cfg, f"/session/projects/{pid}/threads/", json=body)
    except ApiError as e:
        console.print(f"[red]Failed to create thread: {e}[/red]")
        raise typer.Exit(code=1)
    tid = thread.get("id")
    if use_as_default and tid:
        cfg.default_thread_id = tid
        if not cfg.default_project_id:
            cfg.default_project_id = pid
        cfg.save()
    console.print(f"[green]✓[/green] Created thread [cyan]{tid}[/cyan]")


@app.command("use")
def use(thread_id: str = typer.Argument(..., help="Thread ID to set as default.")):
    """Set the default thread."""
    cfg = Config.load()
    cfg.default_thread_id = thread_id
    cfg.save()
    console.print(f"[green]✓[/green] Default thread set to {thread_id}")


@app.command("cancel")
def cancel(
    project: str = typer.Option(None, "--project", "-p", help="Project ID."),
    thread: str = typer.Option(None, "--thread", "-t", help="Thread ID."),
):
    """Cancel a thread that's currently processing."""
    cfg = Config.load()
    require_token(cfg)
    pid = _resolve_project(cfg, project)
    tid = thread or cfg.default_thread_id
    if not tid:
        console.print(
            "[red]No thread selected.[/red] Pass --thread or "
            "set one with `galagos chats use <id>`."
        )
        raise typer.Exit(code=1)
    url = f"/session/projects/{pid}/threads/{tid}/cancel/"
    try:
        post(cfg, url, json={})
    except ApiError as e:
        console.print(f"[red]Cancel failed: {e}[/red]")
        raise typer.Exit(code=1)
    console.print("[green]✓[/green] Cancellation signal sent.")
