"""Fixtures for the e2e tier — drives the installed `galagos` binary
against a real backend.

Usage::

    pytest tests/e2e --env=local                   # default, local docker-compose
    pytest tests/e2e --env=dev                     # https://dev.galagos.ai
    pytest tests/e2e --env=prod                    # smoke-only, read-only
    pytest tests/e2e --env=local -m smoke          # opt into the prod-safe subset
    GALAGOS_TEST_ENV=dev pytest tests/e2e          # env var equivalent

The harness writes its own throwaway TOML config at
``$tmp_path/galagos/config.toml`` and points the CLI at it via
``XDG_CONFIG_HOME`` (Linux) / ``HOME`` (macOS) — the developer's real
config is never touched.

Authentication tokens come from:

- ``--env=local``: minted on demand inside the running ``backend_web``
  container via ``docker compose exec web python manage.py shell``.
- ``--env=dev``:   ``GALAGOS_DEV_TOKEN`` env var.
- ``--env=prod``:  ``GALAGOS_PROD_TOKEN`` env var.

Missing tokens cause the relevant tests to ``pytest.skip``, not fail.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

# ─── Pytest plumbing ───────────────────────────────────────────────────────


def pytest_addoption(parser):
    parser.addoption(
        "--env",
        action="store",
        default=os.environ.get("GALAGOS_TEST_ENV", "local"),
        choices=("local", "dev", "prod"),
        help="Which Galagos environment the e2e suite drives.",
    )
    parser.addoption(
        "--smoke-only",
        action="store_true",
        default=False,
        help="Run only tests marked with @pytest.mark.smoke. Auto-enabled "
             "when --env=prod.",
    )


def pytest_configure(config):
    if config.getoption("--env") == "prod" and not config.getoption("--smoke-only"):
        config.option.smoke_only = True


def pytest_collection_modifyitems(config, items):
    smoke_only = config.getoption("--smoke-only")
    if not smoke_only:
        return
    skip = pytest.mark.skip(reason="--smoke-only is set; non-smoke test skipped")
    for item in items:
        if "smoke" not in item.keywords:
            item.add_marker(skip)


# ─── Environment definitions ───────────────────────────────────────────────


@dataclass(frozen=True)
class TargetEnv:
    name: str
    base_url: str
    profile_name: str  # the per-env profile written into the throwaway config
    allow_mutations: bool


def _resolve_env(env: str) -> TargetEnv:
    if env == "local":
        return TargetEnv(
            name="local",
            base_url=os.environ.get(
                "GALAGOS_LOCAL_BASE_URL", "http://localhost:8000/api"
            ),
            profile_name="e2e-local",
            allow_mutations=True,
        )
    if env == "dev":
        return TargetEnv(
            name="dev",
            base_url=os.environ.get(
                "GALAGOS_DEV_BASE_URL", "https://dev.galagos.ai/api"
            ),
            profile_name="e2e-dev",
            allow_mutations=True,
        )
    if env == "prod":
        return TargetEnv(
            name="prod",
            base_url=os.environ.get(
                "GALAGOS_PROD_BASE_URL", "https://app.galagos.ai/api"
            ),
            profile_name="e2e-prod",
            allow_mutations=False,
        )
    raise ValueError(f"Unknown env: {env}")


@pytest.fixture(scope="session")
def target_env(pytestconfig) -> TargetEnv:
    return _resolve_env(pytestconfig.getoption("--env"))


# ─── Isolated config (XDG_CONFIG_HOME redirect) ────────────────────────────


@pytest.fixture(scope="session")
def cli_config_dir(tmp_path_factory) -> Path:
    """A throwaway config dir for the whole session. Both XDG_CONFIG_HOME
    (Linux) and HOME (macOS) are redirected so platformdirs writes here."""
    base = tmp_path_factory.mktemp("galagos-e2e-home")
    (base / ".config").mkdir(parents=True, exist_ok=True)
    (base / "Library" / "Application Support").mkdir(parents=True, exist_ok=True)
    return base


# ─── Token bootstrap ───────────────────────────────────────────────────────


def _mint_token_via_django_shell(backend_dir: Path) -> tuple[str, str]:
    """Mint a token for the first user via `docker compose exec web` and
    return (email, token_key). Raises CalledProcessError on failure."""
    script = textwrap.dedent(
        """
        from rest_framework.authtoken.models import Token
        from apps.core.models import CustomUser
        u = CustomUser.objects.filter(is_active=True).first()
        if not u:
            raise SystemExit('No active user found in local DB.')
        t, _ = Token.objects.get_or_create(user=u)
        print(u.email + '|' + t.key)
        """
    ).strip()
    result = subprocess.run(
        [
            "docker", "compose", "exec", "-T", "web",
            "python", "manage.py", "shell", "-c", script,
        ],
        cwd=str(backend_dir),
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    # Last non-empty line is "email|tokenkey".
    lines = [ln for ln in result.stdout.splitlines() if "|" in ln]
    if not lines:
        raise RuntimeError(f"Could not parse token from shell output: {result.stdout!r}")
    email, key = lines[-1].rsplit("|", 1)
    return email.strip(), key.strip()


@pytest.fixture(scope="session")
def bootstrap_token(target_env) -> str:
    """Return a valid API token for ``target_env``. Skip the test if one
    can't be obtained (remote env without a pre-provided env var)."""
    if target_env.name == "local":
        backend_dir = Path(__file__).resolve().parents[3] / "backend"
        if not backend_dir.is_dir():
            pytest.skip(f"Local backend dir not found at {backend_dir}")
        if shutil.which("docker") is None:
            pytest.skip("docker CLI not available; cannot mint local token")
        try:
            _, key = _mint_token_via_django_shell(backend_dir)
            return key
        except Exception as e:
            pytest.skip(f"Could not mint local token via Django shell: {e}")

    env_var = {"dev": "GALAGOS_DEV_TOKEN", "prod": "GALAGOS_PROD_TOKEN"}[target_env.name]
    token = os.environ.get(env_var)
    if not token:
        pytest.skip(f"{env_var} not set; skipping {target_env.name} tests")
    return token


# ─── CLI invocation fixtures ───────────────────────────────────────────────


@dataclass
class CliResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def __repr__(self) -> str:
        return (
            f"CliResult(rc={self.returncode}, "
            f"stdout={self.stdout!r:.200}, stderr={self.stderr!r:.200})"
        )


def _galagos_binary() -> str:
    """Locate the installed galagos binary. Prefer the dev venv's copy
    so changes to source are reflected without rebuild."""
    repo_root = Path(__file__).resolve().parents[2]
    venv_bin = repo_root / ".venv" / "bin" / "galagos"
    if venv_bin.is_file():
        return str(venv_bin)
    found = shutil.which("galagos")
    if found:
        return found
    raise RuntimeError(
        "galagos binary not found. Install with `pip install -e .` from the cli/ dir."
    )


@pytest.fixture(scope="session")
def cli_env(cli_config_dir, target_env, bootstrap_token):
    """Environment dict shared by every CLI invocation in the session.

    Pre-seeds a profile ``e2e-<env>`` and sets it as default so individual
    tests don't need to thread --profile every call.
    """
    binary = _galagos_binary()
    base_env = {
        # Redirect platformdirs at the throwaway config home
        "HOME": str(cli_config_dir),
        "XDG_CONFIG_HOME": str(cli_config_dir / ".config"),
        # Inherit just enough of the parent env to actually run
        "PATH": os.environ["PATH"],
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
        "TERM": "dumb",  # disable rich's colors in test output
    }
    # Seed the profile non-interactively.
    seed = subprocess.run(
        [
            binary, "config", "add", target_env.profile_name,
            "--base-url", target_env.base_url,
            "--token", bootstrap_token,
            "--default",
        ],
        env=base_env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if seed.returncode != 0:
        pytest.fail(
            f"Could not seed e2e profile: rc={seed.returncode} "
            f"stdout={seed.stdout!r} stderr={seed.stderr!r}"
        )
    return base_env


@pytest.fixture
def cli(cli_env):
    """Run the CLI as a subprocess and return CliResult.

    Usage:
        result = cli("projects", "ls")
        assert result.ok
        assert "my-project" in result.stdout
    """
    binary = _galagos_binary()

    def _run(*args: str, timeout: float = 60.0, input: str | None = None) -> CliResult:
        proc = subprocess.run(
            [binary, *args],
            env=cli_env,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input,
        )
        return CliResult(proc.returncode, proc.stdout, proc.stderr)

    return _run


@pytest.fixture
def agent_cli(cli_env):
    """Same as ``cli`` but with a longer default timeout for chat calls."""
    binary = _galagos_binary()

    def _run(*args: str, timeout: float = 300.0, input: str | None = None) -> CliResult:
        proc = subprocess.run(
            [binary, *args],
            env=cli_env,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input,
        )
        return CliResult(proc.returncode, proc.stdout, proc.stderr)

    return _run


# ─── Test data fixtures ────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def e2e_project(cli_env, target_env, bootstrap_token):
    """Pick a project ID for the test suite. Strategy:

    - ``local``: query /user_project/projects/, pick the first; if empty,
      skip with a hint about ``manage.py bootstrap_local``.
    - ``dev``/``prod``: same, but if ``GALAGOS_TEST_PROJECT_ID`` is set
      use that instead — lets CI pin a known sandbox.
    """
    if pid := os.environ.get("GALAGOS_TEST_PROJECT_ID"):
        return pid

    binary = _galagos_binary()
    proc = subprocess.run(
        [binary, "projects", "ls", "--no-pager"]
        if False  # keep --no-pager out of the contract until we add it
        else [binary, "projects", "ls"],
        env=cli_env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        pytest.skip(
            f"Could not list projects: rc={proc.returncode} "
            f"stderr={proc.stderr.strip()}"
        )
    # Crude UUID extraction from the rich-rendered table.
    import re
    uuids = re.findall(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        proc.stdout,
    )
    if not uuids:
        pytest.skip(
            f"No projects available on {target_env.name}. "
            f"For local: run `python manage.py bootstrap_local`. "
            f"For dev/prod: set GALAGOS_TEST_PROJECT_ID."
        )
    return uuids[0]


@pytest.fixture
def e2e_thread(cli, target_env, e2e_project):
    """A fresh chat thread for the test, deleted on teardown if the env
    permits mutations."""
    if not target_env.allow_mutations:
        pytest.skip(f"e2e_thread requires a mutable env; got {target_env.name}")
    title = "cli-e2e ephemeral"
    result = cli(
        "chats", "new",
        "--project", e2e_project,
        "--title", title,
        "--no-default",
    )
    assert result.ok, f"chats new failed: {result}"
    # Output: "✓ Created thread <UUID>"
    import re
    match = re.search(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        result.stdout,
    )
    assert match, f"Could not parse thread id from: {result.stdout!r}"
    tid = match.group(0)
    yield tid
    # No DELETE endpoint exposed by the CLI today — best-effort cleanup
    # is to cancel any in-flight stream. Threads accumulate harmlessly.
    cli("chats", "cancel", "--project", e2e_project, "--thread", tid, timeout=10)


# ─── Helpers re-exported for tests ─────────────────────────────────────────


def parse_json(stdout: str):
    """Tolerant JSON extractor for tests that ask the CLI for raw output."""
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return None


@pytest.fixture
def api_client(target_env, bootstrap_token):
    """Direct httpx client against the same backend the CLI is talking
    to. Use for status checks that don't need a CLI subcommand to exist
    (e.g. session.status on the backend) — avoids parsing rich tables."""
    import httpx
    return httpx.Client(
        base_url=target_env.base_url,
        headers={
            "Authorization": f"Token {bootstrap_token}",
            "Accept": "application/json",
        },
        timeout=15.0,
    )


def thread_status(api_client, project: str, thread: str) -> str | None:
    """Fetch a thread's session status (IDLE / PROCESSING / etc.)."""
    r = api_client.get(f"/session/projects/{project}/threads/{thread}/status/")
    if r.status_code != 200:
        return None
    return r.json().get("status")


def wait_for_idle(api_client, project: str, thread: str, timeout_s: int = 30) -> str:
    """Poll until the session leaves PROCESSING. Returns final status."""
    import time as _t
    deadline = _t.time() + timeout_s
    last = "?"
    while _t.time() < deadline:
        last = thread_status(api_client, project, thread) or last
        if last and last != "PROCESSING":
            return last
        _t.sleep(2)
    return last
