"""Sandbox lifecycle helpers — status check + wake-up.

Each Galagos project has a sandbox (the per-project Docker stack /
Kubernetes deployment that runs the agent + executor containers). The
sandbox can be paused after idle to save resources; the next interaction
needs to wake it up first.

This module hides the wake-up dance behind a single ``ensure_running``
call. Used by ``galagos chat`` (auto-wake before streaming) and exposed
directly via ``galagos chats wake``.
"""
from __future__ import annotations

import time

from rich.console import Console

from .client import ApiError, client, get, post

err_console = Console(stderr=True)

# Status values the backend may report for a sandbox.
RUNNING = "RUNNING"
PAUSED = "PAUSED"
DEPLOYING = "DEPLOYING"
PENDING = "PENDING"
ERROR = "ERROR"

# Statuses we consider "needs wake-up to be useful".
WAKEABLE = {PAUSED}
# Statuses we can wait through without explicitly waking.
TRANSIENT = {DEPLOYING, PENDING}
# Statuses we can't recover from automatically.
TERMINAL = {ERROR, "NOT_FOUND", "REMOVING", "DELETED"}


def get_sandbox_status(cfg, project_id: str) -> str | None:
    """Read the sandbox status off the project detail endpoint.

    Returns the status string ("RUNNING", "PAUSED", ...) or ``None``
    when the project has no sandbox or the field is missing.
    """
    try:
        body = get(cfg, f"/user_project/projects/{project_id}/")
    except ApiError:
        return None
    info = (body or {}).get("agent_stack_info") or {}
    status = info.get("status")
    return str(status) if status else None


def trigger_wakeup(cfg, project_id: str) -> dict | None:
    """POST /sandbox/wakeup/ to start a wake-up. Returns the backend's
    202 body on success, ``None`` if the sandbox is already running or
    not wakeable (caller should re-check status).
    """
    try:
        return post(cfg, "/sandbox/wakeup/", json={"project_id": project_id})
    except ApiError as e:
        # 400 = "not wakeable" (already running or in a state where
        # wake-up doesn't apply); the caller's status check handles it.
        # 409 = wakeup already in progress; caller polls.
        if e.status_code in (400, 409):
            return None
        raise


def ensure_running(
    cfg,
    project_id: str,
    *,
    timeout_s: int = 600,
    poll_interval_s: float = 4.0,
    quiet: bool = False,
) -> None:
    """Make sure the project's sandbox is RUNNING.

    - If RUNNING already: returns instantly.
    - If PAUSED: triggers a wake-up, polls until RUNNING.
    - If DEPLOYING/PENDING: just polls.
    - If ERROR or any other terminal state: raises ``RuntimeError``.

    Set ``quiet=True`` to suppress the user-facing "waking up..." message
    (useful for the `wake` subcommand which prints its own UI).
    """
    status = get_sandbox_status(cfg, project_id)
    if status == RUNNING:
        return
    if status is None:
        raise RuntimeError(
            f"Project {project_id} has no sandbox; nothing to wake up."
        )
    if status in TERMINAL:
        raise RuntimeError(
            f"Sandbox is in state {status!r} and cannot be woken up "
            f"automatically. Check the project in the web UI."
        )

    if not quiet:
        if status in WAKEABLE:
            err_console.print(
                "[yellow]Project is sleeping. Waking it up — this usually "
                "takes 30–90 seconds.[/yellow]"
            )
        else:
            err_console.print(
                f"[yellow]Sandbox state: {status}. Waiting for it to "
                f"finish coming up.[/yellow]"
            )

    if status in WAKEABLE:
        trigger_wakeup(cfg, project_id)

    deadline = time.time() + timeout_s
    last_status = status
    while time.time() < deadline:
        time.sleep(poll_interval_s)
        status = get_sandbox_status(cfg, project_id)
        if status == RUNNING:
            if not quiet:
                err_console.print("[green]Sandbox is up.[/green]")
            return
        if status in TERMINAL:
            raise RuntimeError(
                f"Sandbox transitioned to {status!r} during wake-up. "
                f"Check the project in the web UI."
            )
        if status != last_status:
            if not quiet:
                err_console.print(f"[dim]· {status}[/dim]")
            last_status = status

    raise TimeoutError(
        f"Sandbox did not reach RUNNING within {timeout_s}s "
        f"(last status: {last_status!r})."
    )


# Lightweight client-only sanity check kept here so `client.py` stays
# domain-agnostic.
def _resolve_with_client(cfg, fn):
    with client(cfg) as c:  # noqa: F841 — kept as an extension point
        return fn()
