"""`galagos config` — list, switch, and manage profiles."""
from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from ..config import Config, ConfigError

app = typer.Typer(help="Manage profiles (one set of base_url + token per environment).")
console = Console()
err_console = Console(stderr=True)


def _mask(token: str | None) -> str:
    if not token:
        return "(unset)"
    if len(token) <= 8:
        return "***"
    return f"{token[:4]}…{token[-4:]}"


@app.command("list")
def list_cmd():
    """List all profiles."""
    cfg = Config.load()
    table = Table(show_header=True, header_style="bold")
    table.add_column("Profile", style="cyan", no_wrap=True)
    table.add_column("Base URL")
    table.add_column("Token", style="dim")
    table.add_column("Default", style="green")
    for name in cfg.list_profiles():
        p = cfg.profiles[name]
        table.add_row(
            name,
            p.base_url,
            _mask(p.token),
            "★" if name == cfg.default_profile else "",
        )
    console.print(table)
    console.print(f"\nActive: [bold]{cfg.active_name}[/bold]")


@app.command("show")
def show(
    profile: str = typer.Option(
        None, "--profile",
        help="Profile to show. Defaults to the active one.",
    ),
):
    """Show the active (or named) profile's settings."""
    cfg = Config.load(profile=profile)
    p = cfg.active_profile
    name = cfg.active_name
    console.print(f"[bold]Profile:[/bold] {name}")
    console.print(f"  base_url: {cfg.base_url}")
    console.print(f"  token:    {_mask(cfg.token)}")
    console.print(f"  default project: {p.default_project_id or '(unset)'}")
    console.print(f"  default thread:  {p.default_thread_id or '(unset)'}")


@app.command("use")
def use(name: str = typer.Argument(..., help="Profile name to set as default.")):
    """Set the default profile for future invocations."""
    cfg = Config.load()
    try:
        cfg.set_default(name)
    except ConfigError as e:
        err_console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)
    cfg.save()
    console.print(f"[green]✓[/green] Default profile is now [bold]{name}[/bold].")


@app.command("add")
def add(
    name: str = typer.Argument(..., help="Profile name."),
    base_url: str = typer.Option(
        ..., "--base-url", "-u",
        help="API base URL, e.g. https://dev.galagos.ai/api",
    ),
    token: str = typer.Option(
        None, "--token", "-t",
        help="API token. Omit to leave the existing token unchanged "
             "(or empty for new profiles — `auth login` later sets it).",
    ),
    use_as_default: bool = typer.Option(
        False, "--default",
        help="Also make this the default profile.",
    ),
):
    """Create or update a profile non-interactively."""
    cfg = Config.load()
    cfg.upsert_profile(name, base_url=base_url, token=token)
    if use_as_default:
        cfg.set_default(name)
    cfg.save()
    msg = f"[green]✓[/green] Profile [bold]{name}[/bold] saved ({base_url})."
    if use_as_default:
        msg += " Now the default."
    console.print(msg)


@app.command("remove")
def remove(name: str = typer.Argument(..., help="Profile name to delete.")):
    """Delete a profile. Refuses if it is the default."""
    cfg = Config.load()
    try:
        cfg.remove_profile(name)
    except ConfigError as e:
        err_console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)
    cfg.save()
    console.print(f"[green]✓[/green] Profile [bold]{name}[/bold] removed.")
