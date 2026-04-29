"""E2E: `galagos files` round-trips against a real sandbox."""
from __future__ import annotations

import secrets

import pytest


def _unique_name(stem: str) -> str:
    return f"{stem}-{secrets.token_hex(4)}.txt"


@pytest.mark.e2e
@pytest.mark.smoke
def test_files_ls_returns_a_table(cli, e2e_project):
    result = cli("files", "ls", "--project", e2e_project)
    assert result.ok, f"files ls failed: {result}"
    # Either a populated table or a header-only one. What matters is
    # that the call succeeds — the listing itself is sandbox-state-
    # dependent and doesn't have a stable assertion.
    assert "Path" in result.stdout or result.stdout.strip() == ""


@pytest.mark.e2e
def test_put_then_cat_round_trips_bytes(
    cli, e2e_project, target_env, tmp_path
):
    if not target_env.allow_mutations:
        pytest.skip("requires mutable env")

    # Random payload so consecutive runs don't depend on prior state.
    name = _unique_name("e2e-roundtrip")
    payload = f"hello-from-cli-e2e-{secrets.token_hex(8)}\n"
    src = tmp_path / name
    src.write_text(payload, encoding="utf-8")

    put = cli(
        "files", "put", str(src),
        "--project", e2e_project,
        timeout=120,
    )
    assert put.ok, f"files put failed: {put}"
    assert "Uploaded as" in put.stdout, f"unexpected put output: {put.stdout!r}"

    cat = cli(
        "files", "cat", name,
        "--project", e2e_project,
        timeout=60,
    )
    assert cat.ok, f"files cat failed: {cat}"
    assert cat.stdout == payload, (
        f"round-trip mismatch.\n  sent={payload!r}\n  back={cat.stdout!r}"
    )


@pytest.mark.e2e
def test_files_ls_reflects_uploaded_file(
    cli, e2e_project, target_env, tmp_path
):
    if not target_env.allow_mutations:
        pytest.skip("requires mutable env")

    name = _unique_name("e2e-ls-check")
    src = tmp_path / name
    src.write_text("ls reflection\n", encoding="utf-8")

    put = cli("files", "put", str(src), "--project", e2e_project, timeout=120)
    assert put.ok, f"files put failed: {put}"

    listing = cli("files", "ls", "--project", e2e_project, timeout=30)
    assert listing.ok
    assert name in listing.stdout, (
        f"newly uploaded {name} missing from ls. stdout={listing.stdout!r}"
    )


@pytest.mark.e2e
def test_files_get_writes_correct_size(
    cli, e2e_project, target_env, tmp_path
):
    if not target_env.allow_mutations:
        pytest.skip("requires mutable env")

    name = _unique_name("e2e-get")
    payload = secrets.token_bytes(4096)  # 4 KiB of random bytes
    src = tmp_path / name
    src.write_bytes(payload)

    put = cli("files", "put", str(src), "--project", e2e_project, timeout=120)
    assert put.ok, f"files put failed: {put}"

    out_path = tmp_path / f"out-{name}"
    got = cli(
        "files", "get", name,
        "--out", str(out_path),
        "--project", e2e_project,
        timeout=120,
    )
    assert got.ok, f"files get failed: {got}"
    assert out_path.exists()
    downloaded = out_path.read_bytes()
    assert downloaded == payload, (
        f"size mismatch: sent {len(payload)} bytes, got {len(downloaded)}"
    )


# `--path` filtering is unit-tested via `_walk_tree` in tests/test_files.py
# (root-level filename prefixes are intentionally not matched — only
# directory prefixes). An e2e test that creates a subdirectory via the
# agent would be flaky and slow; skipped here.
