"""Top-level Typer app for the Galagos CLI."""
from __future__ import annotations

import typer

from . import __version__
from .commands import auth as auth_cmd
from .commands import chat as chat_cmd
from .commands import chats as chats_cmd
from .commands import files as files_cmd
from .commands import projects as projects_cmd

app = typer.Typer(
    name="galagos",
    help="Command-line client for the Galagos AI bioinformatics platform.",
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(auth_cmd.app, name="auth")
app.add_typer(projects_cmd.app, name="projects")
app.add_typer(chats_cmd.app, name="chats")
app.add_typer(chat_cmd.app, name="chat")
app.add_typer(files_cmd.app, name="files")


@app.command("version")
def version():
    """Print the CLI version."""
    typer.echo(f"galagos-cli {__version__}")


if __name__ == "__main__":
    app()
