"""E2E: `galagos projects` and `galagos chats` against a real backend."""
from __future__ import annotations

import re

import pytest

UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)


@pytest.mark.e2e
@pytest.mark.smoke
def test_projects_ls_returns_at_least_one_project(cli, e2e_project):
    result = cli("projects", "ls")
    assert result.ok, f"projects ls failed: {result}"
    assert e2e_project in result.stdout


@pytest.mark.e2e
@pytest.mark.smoke
def test_projects_use_sets_default_in_active_profile(cli, e2e_project):
    set_default = cli("projects", "use", e2e_project)
    assert set_default.ok, f"projects use failed: {set_default}"
    show = cli("config", "show")
    assert show.ok
    assert e2e_project in show.stdout


@pytest.mark.e2e
def test_chats_ls_renders_table(cli, e2e_project):
    result = cli("chats", "ls", "--project", e2e_project)
    assert result.ok, f"chats ls failed: {result}"
    # Either the table header is rendered (existing threads) or an empty
    # table is returned. Both are acceptable; what matters is no error.
    assert "ID" in result.stdout or result.stdout.strip() == ""


@pytest.mark.e2e
def test_chats_new_then_ls_includes_new_thread(cli, e2e_project, target_env):
    if not target_env.allow_mutations:
        pytest.skip("requires mutable env")
    title = "cli-e2e new-then-ls"
    new = cli(
        "chats", "new",
        "--project", e2e_project,
        "--title", title,
        "--no-default",
    )
    assert new.ok, f"chats new failed: {new}"
    match = UUID_RE.search(new.stdout)
    assert match, f"no thread UUID in stdout: {new.stdout!r}"
    new_tid = match.group(0)

    ls = cli("chats", "ls", "--project", e2e_project)
    assert ls.ok
    assert new_tid in ls.stdout, (
        f"newly created thread {new_tid} missing from ls output"
    )

    # Best-effort cancel; harmless if thread is IDLE.
    cli("chats", "cancel", "--project", e2e_project, "--thread", new_tid, timeout=10)


@pytest.mark.e2e
def test_e2e_thread_fixture_provides_fresh_idle_thread(cli, e2e_project, e2e_thread):
    """Sanity-check the per-test fixture itself: the thread it returns
    must show up in `chats ls`."""
    ls = cli("chats", "ls", "--project", e2e_project)
    assert ls.ok
    assert e2e_thread in ls.stdout
