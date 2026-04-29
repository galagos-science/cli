"""Persistent CLI config — multi-profile token / base URL / defaults.

A single ``config.toml`` holds one or more named profiles (``local``,
``dev``, ``prod``, …). Exactly one is active per invocation, selected by
this precedence (highest wins):

1. ``GALAGOS_API_URL`` / ``GALAGOS_TOKEN`` env vars — apply on top of the
   active profile but never written to disk. Useful for one-off requests
   and for the e2e test harness.
2. ``GALAGOS_PROFILE`` env var → picks the active profile by name.
3. ``--profile <name>`` global CLI flag (set via ``set_profile_override``).
4. The ``default_profile`` field in ``config.toml``.

Reading ``cfg.token`` / ``cfg.base_url`` always returns the resolved value
(env override → active profile). Writing those attributes mutates the
active profile in-memory; ``cfg.save()`` then persists every profile back
to disk without leaking env-var values.

Backwards compatible: a flat-shaped legacy config (``base_url``/``token``
at the top level) is migrated transparently into a profile called
``default`` on first load.
"""
from __future__ import annotations

import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import tomli_w
from platformdirs import user_config_dir

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

CONFIG_DIR = Path(user_config_dir("galagos", "galagos"))
CONFIG_PATH = CONFIG_DIR / "config.toml"
DEFAULT_BASE_URL = "http://localhost:8000/api"
DEFAULT_PROFILE_NAME = "default"

# Set by main.py's --profile callback. Module-level because Typer
# subcommands receive their own arg parsers and can't easily thread
# "active profile" through every signature.
_PROFILE_OVERRIDE: str | None = None


def set_profile_override(name: str | None) -> None:
    """Called from the top-level Typer callback when --profile is passed."""
    global _PROFILE_OVERRIDE
    _PROFILE_OVERRIDE = name


class ConfigError(Exception):
    pass


@dataclass
class Profile:
    base_url: str = DEFAULT_BASE_URL
    token: str | None = None
    default_project_id: str | None = None
    default_thread_id: str | None = None


@dataclass
class Config:
    profiles: dict[str, Profile] = field(
        default_factory=lambda: {DEFAULT_PROFILE_NAME: Profile()}
    )
    default_profile: str = DEFAULT_PROFILE_NAME
    # Active profile name resolved at load time; not persisted.
    _active: str = DEFAULT_PROFILE_NAME
    # Env-var overrides (e.g. GALAGOS_API_URL); reads only, never saved.
    _env: dict[str, str] = field(default_factory=dict)

    # ── Active-profile pass-through accessors ──────────────────────────────

    @property
    def active_profile(self) -> Profile:
        if self._active not in self.profiles:
            self.profiles[self._active] = Profile()
        return self.profiles[self._active]

    @property
    def active_name(self) -> str:
        return self._active

    @property
    def base_url(self) -> str:
        return self._env.get("base_url") or self.active_profile.base_url

    @base_url.setter
    def base_url(self, value: str) -> None:
        self.active_profile.base_url = value

    @property
    def token(self) -> str | None:
        return self._env.get("token") or self.active_profile.token

    @token.setter
    def token(self, value: str | None) -> None:
        self.active_profile.token = value

    @property
    def default_project_id(self) -> str | None:
        return self.active_profile.default_project_id

    @default_project_id.setter
    def default_project_id(self, value: str | None) -> None:
        self.active_profile.default_project_id = value

    @property
    def default_thread_id(self) -> str | None:
        return self.active_profile.default_thread_id

    @default_thread_id.setter
    def default_thread_id(self, value: str | None) -> None:
        self.active_profile.default_thread_id = value

    # ── Persistence ────────────────────────────────────────────────────────

    @classmethod
    def load(cls, *, profile: str | None = None) -> Config:
        cfg = cls(profiles={}, default_profile=DEFAULT_PROFILE_NAME)

        if CONFIG_PATH.exists():
            with CONFIG_PATH.open("rb") as f:
                data = tomllib.load(f)
        else:
            data = {}

        if isinstance(data.get("profiles"), dict) and data["profiles"]:
            for name, body in data["profiles"].items():
                if not isinstance(body, dict):
                    continue
                cfg.profiles[name] = Profile(
                    base_url=body.get("base_url", DEFAULT_BASE_URL),
                    token=body.get("token"),
                    default_project_id=body.get("default_project_id"),
                    default_thread_id=body.get("default_thread_id"),
                )
            cfg.default_profile = (
                data.get("default_profile")
                if data.get("default_profile") in cfg.profiles
                else next(iter(cfg.profiles))
            )
        elif data:
            # Legacy flat config — migrate into a "default" profile.
            cfg.profiles[DEFAULT_PROFILE_NAME] = Profile(
                base_url=data.get("base_url", DEFAULT_BASE_URL),
                token=data.get("token"),
                default_project_id=data.get("default_project_id"),
                default_thread_id=data.get("default_thread_id"),
            )
            cfg.default_profile = DEFAULT_PROFILE_NAME
        else:
            cfg.profiles[DEFAULT_PROFILE_NAME] = Profile()
            cfg.default_profile = DEFAULT_PROFILE_NAME

        # Pick active profile.
        chosen = (
            profile
            or os.environ.get("GALAGOS_PROFILE")
            or _PROFILE_OVERRIDE
            or cfg.default_profile
        )
        if chosen not in cfg.profiles:
            # If the user explicitly asked for a profile that doesn't exist,
            # auto-create it empty so commands like `auth login --profile X`
            # can populate it. Reading from it will return DEFAULT_BASE_URL.
            cfg.profiles[chosen] = Profile()
        cfg._active = chosen

        # Env overrides apply on top of the active profile (read-only).
        if env_url := os.environ.get("GALAGOS_API_URL"):
            cfg._env["base_url"] = env_url
        if env_token := os.environ.get("GALAGOS_TOKEN"):
            cfg._env["token"] = env_token

        return cfg

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "default_profile": self.default_profile,
            "profiles": {
                name: {k: v for k, v in asdict(profile).items() if v is not None}
                for name, profile in self.profiles.items()
            },
        }
        with CONFIG_PATH.open("wb") as f:
            tomli_w.dump(data, f)
        try:
            os.chmod(CONFIG_PATH, 0o600)
        except OSError:
            pass

    # ── Profile management helpers (used by `galagos config …` commands) ──

    def list_profiles(self) -> list[str]:
        return sorted(self.profiles.keys())

    def remove_profile(self, name: str) -> None:
        if name not in self.profiles:
            raise ConfigError(f"Profile {name!r} does not exist.")
        if name == self.default_profile:
            raise ConfigError(
                f"Profile {name!r} is the default; pick a different default first "
                f"with `galagos config use <other>`."
            )
        del self.profiles[name]

    def set_default(self, name: str) -> None:
        if name not in self.profiles:
            raise ConfigError(f"Profile {name!r} does not exist.")
        self.default_profile = name

    def upsert_profile(
        self,
        name: str,
        *,
        base_url: str | None = None,
        token: str | None = None,
    ) -> Profile:
        """Create or update a profile. Existing token is preserved when
        token is None."""
        existing = self.profiles.get(name) or Profile()
        if base_url is not None:
            existing.base_url = base_url
        if token is not None:
            existing.token = token
        self.profiles[name] = existing
        return existing
