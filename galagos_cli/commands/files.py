"""`galagos files` — list, read, download, and upload files in a sandbox."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.table import Table

from ..client import ApiError, client, get, post, require_token, stream_client
from ..config import Config

app = typer.Typer(help="Browse and transfer files in a project's sandbox.")
console = Console()
err_console = Console(stderr=True)

DEFAULT_CHUNK_SIZE = 5 * 1024 * 1024  # 5 MiB


def _resolve_project(cfg: Config, project: str | None) -> str:
    pid = project or cfg.default_project_id
    if not pid:
        err_console.print(
            "[red]No project selected.[/red] Pass --project or "
            "`galagos projects use <id>`."
        )
        raise typer.Exit(code=1)
    return pid


@app.command("ls")
def ls(
    project: str = typer.Option(None, "--project", "-p"),
):
    """List files known to the project (UserFile records)."""
    cfg = Config.load()
    require_token(cfg)
    pid = _resolve_project(cfg, project)
    try:
        data = get(cfg, "/user_project/files/", params={"project": pid})
    except ApiError as e:
        err_console.print(f"[red]Failed to list files: {e}[/red]")
        raise typer.Exit(code=1)
    files = data.get("results", []) if isinstance(data, dict) else data
    table = Table(show_header=True, header_style="bold")
    table.add_column("File")
    table.add_column("Size", justify="right")
    table.add_column("Path", style="dim")
    for f in files:
        table.add_row(
            f.get("original_file_name") or f.get("file_name") or "?",
            str(f.get("file_size") or ""),
            f.get("file_path") or "",
        )
    console.print(table)


@app.command("cat")
def cat(
    path: str = typer.Argument(..., help="File path within the workspace."),
    project: str = typer.Option(None, "--project", "-p"),
):
    """Print a workspace file to stdout."""
    cfg = Config.load()
    require_token(cfg)
    pid = _resolve_project(cfg, project)
    params = {"projectId": pid, "file_name": path}
    try:
        with stream_client(cfg, timeout=300) as c:
            with c.stream("GET", "/user_project/load_file/", params=params) as r:
                if r.status_code != 200:
                    err_console.print(
                        f"[red]HTTP {r.status_code}: {r.read().decode('utf-8', 'replace')}[/red]"
                    )
                    raise typer.Exit(code=1)
                for chunk in r.iter_bytes():
                    sys.stdout.buffer.write(chunk)
                sys.stdout.flush()
    except httpx.RequestError as e:
        err_console.print(f"[red]Connection error: {e}[/red]")
        raise typer.Exit(code=1)


@app.command("get")
def get_cmd(
    path: str = typer.Argument(..., help="File path within the workspace."),
    out: str = typer.Option(
        None, "--out", "-o",
        help="Local destination (default: basename of path).",
    ),
    project: str = typer.Option(None, "--project", "-p"),
):
    """Download a workspace file to disk."""
    cfg = Config.load()
    require_token(cfg)
    pid = _resolve_project(cfg, project)
    out_path = Path(out or os.path.basename(path))
    params = {"projectId": pid, "file_name": path, "as_attachment": "1"}
    try:
        with stream_client(cfg, timeout=600) as c:
            with c.stream("GET", "/user_project/load_file/", params=params) as r:
                if r.status_code != 200:
                    err_console.print(
                        f"[red]HTTP {r.status_code}: {r.read().decode('utf-8', 'replace')}[/red]"
                    )
                    raise typer.Exit(code=1)
                with out_path.open("wb") as f:
                    for chunk in r.iter_bytes():
                        f.write(chunk)
    except httpx.RequestError as e:
        err_console.print(f"[red]Connection error: {e}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]✓[/green] Saved {out_path}")


@app.command("put")
def put(
    local: str = typer.Argument(..., help="Local file to upload."),
    project: str = typer.Option(None, "--project", "-p"),
    chunk_size: int = typer.Option(
        DEFAULT_CHUNK_SIZE, "--chunk-size",
        help="Bytes per chunk (default 5 MiB).",
    ),
):
    """Upload a local file to the project's workspace via chunked upload."""
    cfg = Config.load()
    require_token(cfg)
    pid = _resolve_project(cfg, project)

    src = Path(local)
    if not src.is_file():
        err_console.print(f"[red]Not a file: {local}[/red]")
        raise typer.Exit(code=1)
    file_size = src.stat().st_size
    total_chunks = max(1, -(-file_size // chunk_size))

    try:
        session = post(
            cfg,
            "/user_project/upload_session/",
            json={
                "projectId": pid,
                "fileName": src.name,
                "fileSize": file_size,
                "chunkSize": chunk_size,
                "totalChunks": total_chunks,
            },
        )
    except ApiError as e:
        err_console.print(f"[red]Failed to create upload session: {e}[/red]")
        raise typer.Exit(code=1)
    sid = session["session_id"]
    chunk_size = session.get("chunk_size", chunk_size)
    total_chunks = session.get("total_chunks", total_chunks)
    console.print(
        f"Uploading [bold]{src.name}[/bold] "
        f"({file_size} bytes, {total_chunks} chunks)…"
    )

    try:
        with client(cfg, timeout=300) as c:
            with src.open("rb") as f:
                for i in range(total_chunks):
                    data = f.read(chunk_size)
                    r = c.put(
                        f"/user_project/upload_session/{sid}/chunks/{i}/",
                        content=data,
                        headers={"Content-Type": "application/octet-stream"},
                    )
                    if r.status_code >= 400:
                        err_console.print(
                            f"[red]Chunk {i} failed (HTTP {r.status_code}): {r.text}[/red]"
                        )
                        raise typer.Exit(code=1)
                    console.print(f"  chunk {i + 1}/{total_chunks} ✓", end="\r")
    except httpx.RequestError as e:
        err_console.print(f"\n[red]Connection error: {e}[/red]")
        raise typer.Exit(code=1)
    console.print()

    try:
        result = post(
            cfg,
            f"/user_project/upload_session/{sid}/complete/",
            json={},
        )
    except ApiError as e:
        err_console.print(f"[red]Complete failed: {e}[/red]")
        raise typer.Exit(code=1)
    name = result.get("original_file_name") or result.get("file_name") or src.name
    console.print(f"[green]✓[/green] Uploaded as [bold]{name}[/bold]")
