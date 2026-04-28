"""`galagos projects` — list / select sandboxes."""
from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from ..client import ApiError, get, require_token
from ..config import Config

app = typer.Typer(help="List and select projects (sandboxes).")
console = Console()


def _resolve_projects(cfg: Config) -> list[dict]:
    data = get(cfg, "/user_project/projects/")
    if isinstance(data, dict) and "results" in data:
        data = data["results"]
    if not isinstance(data, list):
        raise typer.BadParameter("Unexpected projects response.")
    return data


@app.command("ls")
def ls():
    """List all projects you can access."""
    cfg = Config.load()
    require_token(cfg)
    try:
        projects = _resolve_projects(cfg)
    except ApiError as e:
        console.print(f"[red]Failed to list projects: {e}[/red]")
        raise typer.Exit(code=1)
    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Name")
    table.add_column("Sessions", justify="right")
    table.add_column("Permission", style="dim")
    table.add_column("Default", style="green")
    for p in projects:
        is_default = "★" if cfg.default_project_id == p.get("id") else ""
        table.add_row(
            p.get("id", "?"),
            p.get("name", "(unnamed)"),
            str(p.get("session_count", "")),
            p.get("permission_level") or "",
            is_default,
        )
    console.print(table)


@app.command("use")
def use(project_id: str = typer.Argument(..., help="Project ID to set as default.")):
    """Set the default project for subsequent commands."""
    cfg = Config.load()
    require_token(cfg)
    try:
        projects = _resolve_projects(cfg)
    except ApiError as e:
        console.print(f"[red]Failed to verify project: {e}[/red]")
        raise typer.Exit(code=1)
    match = next((p for p in projects if p.get("id") == project_id), None)
    if not match:
        console.print(f"[red]Project {project_id} not found.[/red]")
        raise typer.Exit(code=1)
    cfg.default_project_id = project_id
    cfg.save()
    console.print(
        f"[green]✓[/green] Default project set to "
        f"[bold]{match.get('name')}[/bold] ({project_id})"
    )
