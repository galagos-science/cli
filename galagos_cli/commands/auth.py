"""`galagos auth` — token management."""
from __future__ import annotations

import getpass
from urllib.parse import urlparse

import httpx
import typer
from rich.console import Console

from ..client import client
from ..config import Config

app = typer.Typer(help="Manage API token and base URL.")
console = Console()


def _profile_url(base_url: str) -> str:
    """Best-effort URL where users can mint a token in the web UI."""
    parsed = urlparse(base_url)
    host = f"{parsed.scheme}://{parsed.netloc}"
    return f"{host}/profile"


@app.command("login")
def login(
    base_url: str = typer.Option(
        None,
        "--base-url",
        "-u",
        help="API base URL (e.g. https://app.galagos.ai/api). Saved to the profile.",
    ),
    token: str = typer.Option(
        None,
        "--token",
        "-t",
        help="Paste token non-interactively (otherwise prompted).",
    ),
    profile: str = typer.Option(
        None,
        "--profile",
        help="Profile to update. Defaults to the active profile (set via "
             "the global --profile flag, GALAGOS_PROFILE env, or default_profile).",
    ),
    set_default: bool = typer.Option(
        False,
        "--default",
        help="Also make this the default profile after a successful login.",
    ),
):
    """Save the API token and base URL into a profile in config.toml."""
    cfg = Config.load(profile=profile)
    if base_url:
        cfg.base_url = base_url.rstrip("/")
    console.print(
        f"Generate a token in the web UI at "
        f"[cyan]{_profile_url(cfg.base_url)}[/cyan]"
    )
    if not token:
        token = getpass.getpass("Paste token: ").strip()
    if not token:
        console.print("[red]No token provided.[/red]")
        raise typer.Exit(code=1)
    cfg.token = token

    # Verify the token works
    try:
        with client(cfg) as c:
            r = c.get("/auth/user/")
    except httpx.RequestError as e:
        console.print(f"[red]Could not reach {cfg.base_url}: {e}[/red]")
        raise typer.Exit(code=1)
    if r.status_code != 200:
        console.print(
            f"[red]Token rejected by {cfg.base_url} "
            f"(HTTP {r.status_code}).[/red]"
        )
        raise typer.Exit(code=1)
    user = r.json()
    if set_default:
        cfg.set_default(cfg.active_name)
    cfg.save()
    email = user.get("email") or user.get("username") or "(unknown)"
    console.print(
        f"[green]✓[/green] Logged in as [bold]{email}[/bold] "
        f"(profile [cyan]{cfg.active_name}[/cyan])"
    )


@app.command("whoami")
def whoami():
    """Show the user authenticated by the active profile."""
    cfg = Config.load()
    if not cfg.token:
        console.print(
            f"[yellow]Not logged in[/yellow] (profile "
            f"[cyan]{cfg.active_name}[/cyan]). Run `galagos auth login`."
        )
        raise typer.Exit(code=1)
    try:
        with client(cfg) as c:
            r = c.get("/auth/user/")
    except httpx.RequestError as e:
        console.print(f"[red]Could not reach {cfg.base_url}: {e}[/red]")
        raise typer.Exit(code=1)
    if r.status_code != 200:
        console.print(f"[red]Token check failed (HTTP {r.status_code}).[/red]")
        raise typer.Exit(code=1)
    user = r.json()
    console.print(f"[bold]{user.get('email', '?')}[/bold]")
    console.print(f"  profile:  {cfg.active_name}")
    console.print(f"  base_url: {cfg.base_url}")
    if cfg.default_project_id:
        console.print(f"  default project: {cfg.default_project_id}")
    if cfg.default_thread_id:
        console.print(f"  default thread:  {cfg.default_thread_id}")


@app.command("logout")
def logout(
    profile: str = typer.Option(
        None, "--profile",
        help="Profile to clear. Defaults to the active profile.",
    ),
):
    """Forget the saved token on the active (or named) profile."""
    cfg = Config.load(profile=profile)
    cfg.token = None
    cfg.save()
    console.print(
        f"[green]✓[/green] Token cleared on profile "
        f"[cyan]{cfg.active_name}[/cyan]."
    )
