"""E2E: interactive question handling across the three modes.

Trigger prompt: forces Vertex Gemini to call its `question` tool. Live
testing during development confirmed this prompt reliably produces a
``question.asked`` SSE event.
"""
from __future__ import annotations

import pytest

from .conftest import _galagos_binary, wait_for_idle

QUESTION_PROMPT = (
    "Use the question tool right now to ask one multiple-choice question "
    "with two options labeled 'A' and 'B'. After I answer, briefly tell me "
    "which option I picked. Do not write any other text before calling "
    "the question tool."
)


@pytest.mark.e2e
def test_yes_mode_auto_rejects_question_and_returns_to_idle(
    agent_cli, api_client, e2e_project, e2e_thread, target_env
):
    """`--yes` auto-rejects the question; the agent unblocks; session
    goes back to IDLE."""
    if not target_env.allow_mutations:
        pytest.skip("requires mutable env")
    result = agent_cli(
        "chat",
        "--project", e2e_project,
        "--thread", e2e_thread,
        "--yes",
        QUESTION_PROMPT,
        timeout=180,
    )
    assert result.ok, f"chat --yes failed: {result}"
    # The reject message is on stderr.
    combined = result.stdout + result.stderr
    assert "auto-reject" in combined.lower() or "reject" in combined.lower(), (
        f"no auto-reject signal in output: {result}"
    )
    final_status = wait_for_idle(api_client, e2e_project, e2e_thread)
    assert final_status == "IDLE", (
        f"thread did not return to IDLE after --yes reject; got {final_status}"
    )


@pytest.mark.e2e
def test_no_interactive_exits_nonzero_and_unblocks_session(
    agent_cli, api_client, e2e_project, e2e_thread, target_env
):
    """`--no-interactive` rejects the question (so the agent unblocks)
    AND exits non-zero (so CI sees the failure)."""
    if not target_env.allow_mutations:
        pytest.skip("requires mutable env")
    result = agent_cli(
        "chat",
        "--project", e2e_project,
        "--thread", e2e_thread,
        "--no-interactive",
        QUESTION_PROMPT,
        timeout=180,
    )
    assert not result.ok, (
        f"--no-interactive should have exited non-zero: {result}"
    )
    assert result.returncode == 1, (
        f"expected exit 1 on --no-interactive question; got {result.returncode}"
    )
    assert "no-interactive" in (result.stdout + result.stderr).lower(), (
        f"no '--no-interactive' message in output: {result}"
    )
    final_status = wait_for_idle(api_client, e2e_project, e2e_thread)
    assert final_status == "IDLE", (
        f"thread did not return to IDLE after --no-interactive reject; "
        f"got {final_status}"
    )


@pytest.mark.e2e
def test_interactive_pexpect_round_trip(
    api_client, cli_env, e2e_project, e2e_thread, target_env
):
    """Drive the CLI as a real tty via pexpect: trigger the question,
    pick option 1, confirm the agent's follow-up text reflects the
    chosen option."""
    pexpect = pytest.importorskip("pexpect")
    if not target_env.allow_mutations:
        pytest.skip("requires mutable env")

    child = pexpect.spawn(
        _galagos_binary(),
        [
            "chat",
            "--project", e2e_project,
            "--thread", e2e_thread,
            QUESTION_PROMPT,
        ],
        env=cli_env,
        timeout=180,
        encoding="utf-8",
    )
    try:
        # Wait for the prompt line — ``pick (one number, …): ``.
        child.expect(r"pick \(", timeout=180)
        child.sendline("1")
        # After the answer is POSTed the agent resumes streaming. The
        # follow-up text should include the picked option label ("A").
        # We give the model up to 90s to respond after answering.
        child.expect(r"(?i)\bA\b", timeout=90)
        # Read remaining output until exit.
        child.expect(pexpect.EOF, timeout=60)
    finally:
        child.close(force=True)

    # Final status must be IDLE.
    final_status = wait_for_idle(api_client, e2e_project, e2e_thread)
    assert final_status == "IDLE", (
        f"thread did not return to IDLE after interactive answer; "
        f"got {final_status}"
    )
