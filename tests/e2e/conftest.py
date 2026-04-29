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

**Project lifecycle**: every session that requests ``e2e_project``
provisions a brand-new ``cli-e2e-<unix-ts>`` project via the API,
waits for its sandbox to come up, runs the tests, and deletes the
project on teardown. There's no override; tests are intentionally
isolated from any pre-existing project state. Set
``GALAGOS_KEEP_TEST_PROJECT=1`` to skip the teardown delete (post-mortem
debugging only).

Authentication tokens come from:

- ``--env=local``: minted on demand inside the running ``backend_web``
  container via ``docker compose exec web python manage.py shell``.
  The harness picks the most-recently-active user; in CI / shared dev
  setups consider seeding ``michael@galagos.ai`` so behaviour matches
  remote envs.
- ``--env=dev``:   ``GALAGOS_DEV_TOKEN`` env var. Canonical user is
  ``michael@galagos.ai`` — mint that user's token and use it.
- ``--env=prod``:  ``GALAGOS_PROD_TOKEN`` env var. Canonical user is
  also ``michael@galagos.ai``.

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


CANONICAL_TEST_USER = "michael@galagos.ai"


def _mint_token_via_django_shell(backend_dir: Path) -> tuple[str, str]:
    """Mint a token for the canonical e2e user via `docker compose exec web`
    and return (email, token_key). Falls back to the first active user on
    local-only setups where the canonical user doesn't exist."""
    script = textwrap.dedent(
        f"""
        from rest_framework.authtoken.models import Token
        from apps.core.models import CustomUser
        u = CustomUser.objects.filter(email='{CANONICAL_TEST_USER}', is_active=True).first()
        if u is None:
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


def _provision_e2e_project(
    base_url: str, token: str, *, name: str, ready_timeout_s: int = 600
) -> str:
    """Create a fresh project via POST /user_project/projects/, then poll
    /sandbox/list_files/ until it answers 200 (i.e. the sandbox executor
    is reachable). Returns the new project id.

    Provisioning a fresh sandbox can take 30–120s on a warm registry and
    longer on a cold pull, hence the generous default.
    """
    import time as _t

    import httpx

    headers = {
        "Authorization": f"Token {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    with httpx.Client(base_url=base_url, headers=headers, timeout=30.0) as c:
        r = c.post("/user_project/projects/", json={"name": name})
        if r.status_code >= 400:
            pytest.skip(
                f"Could not create test project: HTTP {r.status_code} {r.text}"
            )
        body = r.json()
        pid = body.get("id")
        if not pid:
            pytest.skip(f"Project create returned no id: {body!r}")

        # Poll list_files as a liveness probe. 200 means the executor is
        # up and the sandbox is responding to remote bash. The agent
        # container in the same stack is started concurrently, so by the
        # time files come up the agent is usually ready or seconds away.
        deadline = _t.time() + ready_timeout_s
        last = "starting"
        while _t.time() < deadline:
            try:
                r = c.get(
                    "/sandbox/list_files/",
                    params={"projectId": pid},
                    timeout=15.0,
                )
            except httpx.RequestError as e:
                last = f"request error: {e}"
                _t.sleep(3)
                continue
            if r.status_code == 200:
                # Give the agent a few seconds head-start so chat tests
                # don't race the agent's first poll.
                _t.sleep(5)
                return pid
            # 503 with agent_starting is the expected "not yet" signal.
            try:
                last = f"HTTP {r.status_code} body={r.json()}"
            except ValueError:
                last = f"HTTP {r.status_code}"
            _t.sleep(5)
        pytest.skip(
            f"Sandbox for {pid} did not become reachable within "
            f"{ready_timeout_s}s (last={last})"
        )


def _delete_project(base_url: str, token: str, pid: str) -> None:
    """Best-effort cleanup. Failures are logged but don't fail the suite."""
    try:
        import httpx
        with httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Token {token}"},
            timeout=30.0,
        ) as c:
            c.delete(f"/user_project/projects/{pid}/")
    except Exception as e:
        import warnings
        warnings.warn(f"Could not delete e2e project {pid}: {e}", stacklevel=1)


@pytest.fixture(scope="session")
def e2e_project(target_env, bootstrap_token):
    """Always provision a fresh project for the test session, no exceptions.

    Skipping shared / pinned projects keeps the laptop and shared envs
    clean and avoids tests passing for the wrong reason against a stale
    sandbox state. Set ``GALAGOS_KEEP_TEST_PROJECT=1`` to retain the
    project after the session for post-mortem inspection; otherwise the
    fixture deletes it on teardown.

    Prod is read-only by design (``allow_mutations=False``) — running
    the fixture there is forbidden, and project-dependent tests skip.
    Prod smoke runs should target only ``@pytest.mark.smoke`` cases that
    don't request this fixture.
    """
    if not target_env.allow_mutations:
        pytest.skip(
            "e2e_project always provisions a fresh project; that's a "
            "mutation, which is forbidden on a read-only env. "
            "Mark this test @pytest.mark.smoke and avoid e2e_project "
            "if it should run against prod."
        )

    import time as _t
    name = f"cli-e2e-{int(_t.time())}"
    pid = _provision_e2e_project(target_env.base_url, bootstrap_token, name=name)
    try:
        yield pid
    finally:
        if not os.environ.get("GALAGOS_KEEP_TEST_PROJECT"):
            _delete_project(target_env.base_url, bootstrap_token, pid)


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
