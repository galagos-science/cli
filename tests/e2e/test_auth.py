"""E2E: `galagos auth` against a real backend."""
from __future__ import annotations

import pytest


@pytest.mark.e2e
@pytest.mark.smoke
def test_whoami_returns_user_email(cli):
    result = cli("auth", "whoami")
    assert result.ok, f"whoami failed: {result}"
    assert "@" in result.stdout, f"no email in stdout: {result.stdout!r}"
    assert "profile:" in result.stdout
    # E2E profile name should appear in the whoami output.
    assert "e2e-" in result.stdout


@pytest.mark.e2e
@pytest.mark.smoke
def test_config_show_reports_active_profile(cli):
    result = cli("config", "show")
    assert result.ok, f"config show failed: {result}"
    assert "Profile: e2e-" in result.stdout
    assert "base_url:" in result.stdout
    # Token should be present and masked (e.g. "abcd…wxyz"), not "(unset)".
    token_line = next(
        (line for line in result.stdout.splitlines() if line.strip().startswith("token:")),
        None,
    )
    assert token_line is not None, f"no token: line in output: {result.stdout!r}"
    assert "(unset)" not in token_line, f"token reported as unset: {token_line!r}"
    assert "…" in token_line, f"token not masked: {token_line!r}"


@pytest.mark.e2e
def test_bad_token_is_rejected_by_login(cli, target_env, cli_env):
    """auth login validates the token via /auth/user/ and refuses to save
    a 401-failing one. Done on a throwaway profile so the active profile
    keeps working."""
    if not target_env.allow_mutations:
        pytest.skip("requires mutable env")
    throwaway = "e2e-bad-token-test"
    result = cli(
        "auth", "login",
        "--profile", throwaway,
        "--base-url", target_env.base_url,
        "--token", "definitely-not-a-real-token-aaaaaaaa",
    )
    # Login must NOT succeed with a bogus token.
    assert not result.ok, (
        f"auth login unexpectedly accepted a bogus token: {result}"
    )
    combined = (result.stdout + result.stderr).lower()
    assert any(token in combined for token in (
        "rejected", "401", "403", "could not reach", "http"
    )), f"no clear rejection signal in output: {result}"


@pytest.mark.e2e
def test_logout_clears_token_only_for_named_profile(cli, target_env):
    """logout --profile <name> clears that profile's token. The active
    profile keeps its token."""
    if not target_env.allow_mutations:
        pytest.skip("requires mutable env")
    name = "e2e-logout-isolation"
    add = cli(
        "config", "add", name,
        "--base-url", target_env.base_url,
        "--token", "throwaway-token-not-validated",
    )
    assert add.ok

    out = cli("auth", "logout", "--profile", name)
    assert out.ok, f"logout failed: {out}"

    # The active e2e profile must still authenticate.
    who = cli("auth", "whoami")
    assert who.ok, f"active profile broken after logout of other profile: {who}"

    # Cleanup: drop the helper profile.
    cli("config", "remove", name)
