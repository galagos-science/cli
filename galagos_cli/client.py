"""Thin httpx wrapper that injects token auth and parses errors."""
from __future__ import annotations

from typing import Any

import httpx
import typer

from .config import Config


class ApiError(Exception):
    def __init__(self, status_code: int, body: Any):
        self.status_code = status_code
        self.body = body
        super().__init__(f"API {status_code}: {body!r}")


def _headers(cfg: Config) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if cfg.token:
        headers["Authorization"] = f"Token {cfg.token}"
    return headers


def require_token(cfg: Config) -> None:
    if not cfg.token:
        typer.echo(
            "No API token configured. Run `galagos auth login` first "
            "(or set GALAGOS_TOKEN).",
            err=True,
        )
        raise typer.Exit(code=1)


def client(cfg: Config, *, timeout: float | None = 30.0) -> httpx.Client:
    return httpx.Client(
        base_url=cfg.base_url,
        headers=_headers(cfg),
        timeout=timeout,
    )


def stream_client(cfg: Config, *, timeout: float | None = 28800) -> httpx.Client:
    """Long-lived client for SSE — 8h read timeout matches AGENT_READ_TIMEOUT."""
    return httpx.Client(
        base_url=cfg.base_url,
        headers=_headers(cfg),
        timeout=httpx.Timeout(timeout, connect=30.0),
    )


def _raise_for(response: httpx.Response) -> None:
    if response.is_success:
        return
    body: Any
    try:
        body = response.json()
    except ValueError:
        body = response.text
    raise ApiError(response.status_code, body)


def get(cfg: Config, path: str, **kwargs) -> Any:
    with client(cfg) as c:
        r = c.get(path, **kwargs)
        _raise_for(r)
        return r.json() if r.content else None


def post(cfg: Config, path: str, json: dict | None = None, **kwargs) -> Any:
    with client(cfg) as c:
        r = c.post(path, json=json, **kwargs)
        _raise_for(r)
        return r.json() if r.content else None
