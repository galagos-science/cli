"""Persistent CLI config — token, base URL, default project/thread."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field, asdict
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


@dataclass
class Config:
    base_url: str = DEFAULT_BASE_URL
    token: str | None = None
    default_project_id: str | None = None
    default_thread_id: str | None = None

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        if env_url := os.environ.get("GALAGOS_API_URL"):
            cfg.base_url = env_url
        if env_token := os.environ.get("GALAGOS_TOKEN"):
            cfg.token = env_token
        if CONFIG_PATH.exists():
            with CONFIG_PATH.open("rb") as f:
                data = tomllib.load(f)
            for key in ("base_url", "token", "default_project_id", "default_thread_id"):
                if key in data and getattr(cfg, key, None) in (None, DEFAULT_BASE_URL):
                    setattr(cfg, key, data[key])
            # Env vars override file
            if env_url:
                cfg.base_url = env_url
            if env_token:
                cfg.token = env_token
        return cfg

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = {k: v for k, v in asdict(self).items() if v is not None}
        with CONFIG_PATH.open("wb") as f:
            tomli_w.dump(data, f)
        try:
            os.chmod(CONFIG_PATH, 0o600)
        except OSError:
            pass
