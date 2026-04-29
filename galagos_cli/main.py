"""Top-level Typer app for the Galagos CLI."""
from __future__ import annotations

import typer

from . import __version__
from .commands import auth as auth_cmd
from .commands import chat as chat_cmd
from .commands import chats as chats_cmd
from .commands import config as config_cmd
from .commands import files as files_cmd
from .commands import projects as projects_cmd
from .config import set_profile_override

app = typer.Typer(
    name="galagos",
    help="Command-line client for the Galagos AI bioinformatics platform.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def _root(
    profile: str = typer.Option(
        None, "--profile",
        envvar="GALAGOS_PROFILE",
        help="Use the named profile from config.toml. "
             "Defaults to the saved default_profile.",
    ),
):
    """Top-level callback — captures global flags before any subcommand runs."""
    set_profile_override(profile)


app.add_typer(auth_cmd.app, name="auth")
app.add_typer(projects_cmd.app, name="projects")
app.add_typer(chats_cmd.app, name="chats")
app.add_typer(chat_cmd.app, name="chat")
app.add_typer(files_cmd.app, name="files")
app.add_typer(config_cmd.app, name="config")


@app.command("version")
def version():
    """Print the CLI version."""
    typer.echo(f"galagos-cli {__version__}")


if __name__ == "__main__":
    app()
