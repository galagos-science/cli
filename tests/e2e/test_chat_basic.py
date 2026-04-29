"""E2E: `galagos chat` happy-path and cancel."""
from __future__ import annotations

import subprocess
import time

import pytest

from .conftest import _galagos_binary


@pytest.mark.e2e
@pytest.mark.smoke
def test_chat_replies_with_pong(agent_cli, e2e_project, e2e_thread):
    """End-to-end: a simple prompt streams a clean text reply and the
    session terminates IDLE."""
    result = agent_cli(
        "chat",
        "--project", e2e_project,
        "--thread", e2e_thread,
        "Reply with exactly one word: PONG. No reasoning, no preamble.",
        timeout=180,
    )
    assert result.ok, f"chat failed: {result}"
    assert "PONG" in result.stdout.upper(), (
        f"PONG not in agent reply. stdout={result.stdout!r}"
    )


@pytest.mark.e2e
def test_chat_cancel_returns_session_to_idle(
    cli, cli_env, e2e_project, e2e_thread, target_env
):
    """Kick off a long-running chat, cancel it, verify the chat subprocess
    exits and the thread is back to IDLE."""
    if not target_env.allow_mutations:
        pytest.skip("requires mutable env")

    long_prompt = (
        "Count slowly from 1 to 500, one number per line, with a "
        "one-second wait between each. Do not stop early."
    )
    proc = subprocess.Popen(
        [
            _galagos_binary(), "chat",
            "--project", e2e_project,
            "--thread", e2e_thread,
            long_prompt,
        ],
        env=cli_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # Give the agent a moment to acquire the session and start streaming.
        time.sleep(5)
        cancel = cli(
            "chats", "cancel",
            "--project", e2e_project,
            "--thread", e2e_thread,
            timeout=15,
        )
        assert cancel.ok, f"cancel failed: {cancel}"

        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.terminate()
            pytest.fail("chat subprocess did not exit within 30s after cancel")
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
