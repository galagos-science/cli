"""Tests for galagos_cli.config — multi-profile load/save/migration."""
from __future__ import annotations

from pathlib import Path

import pytest
import tomli_w

from galagos_cli import config as config_mod
from galagos_cli.config import (
    DEFAULT_BASE_URL,
    DEFAULT_PROFILE_NAME,
    Config,
    ConfigError,
    set_profile_override,
)


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Redirect CONFIG_DIR / CONFIG_PATH at a tmp dir and clear all
    profile-affecting env vars and the global override."""
    cfg_dir = tmp_path / "galagos"
    cfg_dir.mkdir()
    cfg_path = cfg_dir / "config.toml"
    monkeypatch.setattr(config_mod, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg_path)
    for var in ("GALAGOS_PROFILE", "GALAGOS_API_URL", "GALAGOS_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    set_profile_override(None)
    yield cfg_path
    set_profile_override(None)


def _write_toml(path: Path, data: dict) -> None:
    with path.open("wb") as f:
        tomli_w.dump(data, f)


# ─── Migration ──────────────────────────────────────────────────────────────

def test_legacy_flat_config_migrates_into_default_profile(isolated_config):
    _write_toml(
        isolated_config,
        {
            "base_url": "https://app.galagos.ai/api",
            "token": "tok-old",
            "default_project_id": "proj-1",
        },
    )
    cfg = Config.load()
    assert cfg.list_profiles() == [DEFAULT_PROFILE_NAME]
    assert cfg.default_profile == DEFAULT_PROFILE_NAME
    assert cfg.active_name == DEFAULT_PROFILE_NAME
    assert cfg.base_url == "https://app.galagos.ai/api"
    assert cfg.token == "tok-old"
    assert cfg.default_project_id == "proj-1"


def test_migration_persists_as_profiles_table_on_save(isolated_config):
    _write_toml(isolated_config, {"base_url": "http://x/api", "token": "tok-1"})
    cfg = Config.load()
    cfg.save()
    text = isolated_config.read_text()
    assert "[profiles.default]" in text
    assert "default_profile = \"default\"" in text
    # Re-load to confirm round-trip.
    cfg2 = Config.load()
    assert cfg2.token == "tok-1"


def test_empty_file_yields_empty_default_profile(isolated_config):
    cfg = Config.load()
    assert cfg.active_name == DEFAULT_PROFILE_NAME
    assert cfg.base_url == DEFAULT_BASE_URL
    assert cfg.token is None


# ─── Multi-profile round-trip ───────────────────────────────────────────────

def test_load_multiple_profiles_and_pick_default(isolated_config):
    _write_toml(
        isolated_config,
        {
            "default_profile": "dev",
            "profiles": {
                "local": {"base_url": "http://localhost:8000/api", "token": "L"},
                "dev": {"base_url": "https://dev.galagos.ai/api", "token": "D"},
                "prod": {"base_url": "https://app.galagos.ai/api", "token": "P"},
            },
        },
    )
    cfg = Config.load()
    assert sorted(cfg.list_profiles()) == ["dev", "local", "prod"]
    assert cfg.active_name == "dev"
    assert cfg.token == "D"


def test_explicit_profile_argument_overrides_default(isolated_config):
    _write_toml(
        isolated_config,
        {
            "default_profile": "local",
            "profiles": {
                "local": {"base_url": "http://localhost:8000/api", "token": "L"},
                "prod": {"base_url": "https://app.galagos.ai/api", "token": "P"},
            },
        },
    )
    cfg = Config.load(profile="prod")
    assert cfg.active_name == "prod"
    assert cfg.token == "P"


def test_set_profile_override_picks_active_profile(isolated_config):
    _write_toml(
        isolated_config,
        {
            "default_profile": "local",
            "profiles": {
                "local": {"base_url": "http://localhost:8000/api", "token": "L"},
                "dev": {"base_url": "https://dev.galagos.ai/api", "token": "D"},
            },
        },
    )
    set_profile_override("dev")
    cfg = Config.load()
    assert cfg.active_name == "dev"
    assert cfg.token == "D"


def test_galagos_profile_env_picks_active(isolated_config, monkeypatch):
    _write_toml(
        isolated_config,
        {
            "default_profile": "local",
            "profiles": {
                "local": {"base_url": "http://localhost:8000/api", "token": "L"},
                "dev": {"base_url": "https://dev.galagos.ai/api", "token": "D"},
            },
        },
    )
    monkeypatch.setenv("GALAGOS_PROFILE", "dev")
    cfg = Config.load()
    assert cfg.active_name == "dev"


# ─── Selection precedence ───────────────────────────────────────────────────

def test_load_argument_beats_env_var(isolated_config, monkeypatch):
    _write_toml(
        isolated_config,
        {
            "default_profile": "local",
            "profiles": {
                "local": {"base_url": "http://l/api", "token": "L"},
                "dev": {"base_url": "https://d/api", "token": "D"},
                "prod": {"base_url": "https://p/api", "token": "P"},
            },
        },
    )
    monkeypatch.setenv("GALAGOS_PROFILE", "prod")
    cfg = Config.load(profile="dev")
    assert cfg.active_name == "dev"


# ─── Env-var token/base_url overrides (read-only) ──────────────────────────

def test_env_vars_override_active_profile_for_reads_only(
    isolated_config, monkeypatch
):
    _write_toml(
        isolated_config,
        {
            "default_profile": "local",
            "profiles": {"local": {"base_url": "http://l/api", "token": "L"}},
        },
    )
    monkeypatch.setenv("GALAGOS_API_URL", "http://override/api")
    monkeypatch.setenv("GALAGOS_TOKEN", "override-tok")
    cfg = Config.load()
    assert cfg.base_url == "http://override/api"
    assert cfg.token == "override-tok"
    # The profile on disk must not have been mutated by env vars.
    cfg.save()
    raw = isolated_config.read_text()
    assert "http://l/api" in raw
    assert "override" not in raw


# ─── Profile management ─────────────────────────────────────────────────────

def test_upsert_profile_preserves_token_when_omitted(isolated_config):
    cfg = Config.load()
    cfg.upsert_profile("dev", base_url="https://dev/api", token="initial")
    cfg.upsert_profile("dev", base_url="https://dev2/api")  # no token
    assert cfg.profiles["dev"].base_url == "https://dev2/api"
    assert cfg.profiles["dev"].token == "initial"


def test_remove_profile_refuses_to_delete_default(isolated_config):
    cfg = Config.load()
    cfg.upsert_profile("dev", base_url="https://dev/api")
    cfg.set_default("dev")
    with pytest.raises(ConfigError):
        cfg.remove_profile("dev")


def test_remove_profile_deletes_non_default(isolated_config):
    cfg = Config.load()
    cfg.upsert_profile("dev", base_url="https://dev/api")
    cfg.remove_profile("dev")
    assert "dev" not in cfg.list_profiles()


def test_set_default_to_unknown_profile_raises(isolated_config):
    cfg = Config.load()
    with pytest.raises(ConfigError):
        cfg.set_default("never-existed")


def test_load_with_unknown_profile_creates_empty_one(isolated_config):
    _write_toml(
        isolated_config,
        {"default_profile": "local", "profiles": {"local": {"token": "L"}}},
    )
    cfg = Config.load(profile="brand-new")
    # Auto-created so subsequent `auth login --profile brand-new` works.
    assert cfg.active_name == "brand-new"
    assert cfg.base_url == DEFAULT_BASE_URL
    assert cfg.token is None


# ─── File permissions (best-effort) ────────────────────────────────────────

def test_save_uses_0600_perms(isolated_config):
    cfg = Config.load()
    cfg.upsert_profile("dev", base_url="https://x/api", token="T")
    cfg.save()
    mode = isolated_config.stat().st_mode & 0o777
    # Some sandbox FSes ignore chmod silently; accept 0o600 or any mask
    # that doesn't grant world access.
    assert (mode & 0o077) == 0
