# galagos-cli

Command-line client for the [Galagos](https://galagos.ai) AI bioinformatics platform. Drives sandboxes, chats, and files programmatically against the Django REST API.

## Install

```bash
pipx install galagos-cli      # recommended
# or
pip install galagos-cli
```

For local development:

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e .
```

## Quick start

```bash
# 1. Point at the API and paste a token (mint one from the web profile page)
galagos auth login --base-url https://app.galagos.ai/api

# 2. Pick a sandbox and a chat
galagos projects ls
galagos projects use <project-id>
galagos chats new --title "BLAST run from CLI"

# 3. Talk to the agent
galagos chat "Run BLAST on workspace/queries.fasta against nr"

# 4. Read results
galagos files cat results/hits.tsv
galagos files get results/hits.tsv -o ./hits.tsv
```

## Commands

| Command | Purpose |
|---|---|
| `galagos auth login \| logout \| whoami` | Manage API token and base URL |
| `galagos projects ls \| use <id>` | List projects, set the default |
| `galagos chats ls \| new \| use <id>` | Manage threads inside a project |
| `galagos chat "<msg>" [--resume] [--tools]` | Send a message and stream the reply |
| `galagos chat cancel` | Cancel the current turn |
| `galagos files ls \| cat <p> \| get <p> -o F \| put <local>` | Browse and transfer files |

`--project` / `--thread` override the saved defaults on any command.

## Configuration

Stored at `~/Library/Application Support/galagos/config.toml` on macOS (or the equivalent XDG path on Linux). The token is written with `0600` permissions.

Environment overrides:

- `GALAGOS_API_URL` — base URL (e.g. `http://localhost:8000/api`)
- `GALAGOS_TOKEN` — bypass the saved token

## Streaming and reconnection

`galagos chat` consumes the same SSE stream the web UI does. If the connection drops, the agent keeps running server-side; reconnect with:

```bash
galagos chat --resume
```

## Auth note

Phase 1 reuses the existing per-user launcher token (DRF `authtoken.Token`). One token per account, no expiration. A named/scoped API-key system is planned for Phase 2.
