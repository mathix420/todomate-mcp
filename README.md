# todomate-mcp

A Python MCP server that lists, creates, updates, completes, and deletes TodoMate Firestore todos.

## Original source

This repository is based on [LYJ0304's todomate-mcp, archived on Glama](https://glama.ai/mcp/servers/LYJ0304/todomate-mcp). Credit for the original project goes to LYJ0304. The source snapshot was recovered from Glama after the [upstream GitHub repository](https://github.com/LYJ0304/todomate-mcp) returned HTTP 404, and this repository includes subsequent changes. Recovery details are recorded in [.glama-recovery.json](.glama-recovery.json).

## Development environment

- Python 3.12 or later and uv
- Official MCP Python SDK (`mcp`), httpx, and pydantic
- Development dependency: pytest

## Installation

```sh
uv sync
```

Source code is in `src/` and tests are in `tests/`. Copy `.env.example` to a local `.env` file and configure its credentials. `.env` is excluded from Git.

Retrieve TodoMate's Firebase Web API key from its [public web configuration](https://www.todomate.net/__/firebase/init.json) (requires `curl` and `jq`):

```sh
curl -fsSL https://www.todomate.net/__/firebase/init.json | jq -r '.apiKey'
```

Set `TODOMATE_FIREBASE_API_KEY` in `.env` to the printed value. This key identifies TodoMate's Firebase project; you still need to sign in to your own TodoMate account with `uv run todomate-mcp auth login`.

## Tests

```sh
uv run pytest
```

Firebase authentication tests use mocked HTTP responses, so they do not require a real account or external API access.

## MCP server

Run the local stdio server with:

```sh
uv run todomate-mcp
```

The Streamable HTTP server runs at `/mcp`. Because it accesses personal todos, HTTP mode requires a separate Bearer token.

```sh
# Local development
TODOMATE_MCP_ACCESS_TOKEN="$(openssl rand -hex 32)" uv run todomate-mcp http --host 127.0.0.1 --port 8000

# MCP client endpoint: http://127.0.0.1:8000/mcp
# Authorization: Bearer <TODOMATE_MCP_ACCESS_TOKEN>
```

Set default host and port with `TODOMATE_MCP_HOST` and `TODOMATE_MCP_PORT`. For public deployments, run it behind a reverse proxy that terminates TLS and set `TODOMATE_MCP_PUBLIC_URL` to the external HTTPS URL, such as `https://todos.example.com/mcp`. The current authentication model uses one private Bearer token; requests to `/mcp` without it receive `401`.

The server provides 16 tools for groups, todos, scheduling, memos, and diaries. Default dates use the standard `TZ` environment variable (for example, `TZ=Europe/Paris`), falling back to `UTC`. Use `list_todos(unscheduled=true)` for undated todos and `schedule_todo(day=null)` to remove a date. Memos, new groups, and new diaries default to private.

Creating a todo requires a `goal_id` (the TodoMate group). Call `list_goals` to get your groups' IDs and titles, then pass the chosen ID to `create_todo`. TodoMate rejects creation with a null group ID with HTTP 403.

See the [MCP usage guide for LLMs and clients](docs/mcp-usage.md) for group selection, date handling, tool arguments, and safe retries. These instructions are also included in MCP tool discovery.

Set `TODOMATE_FIREBASE_API_KEY` in `.env`. Sign in interactively with the following commands; the refresh token and UID are stored in the OS Keyring/Credential Manager, and the stdio MCP server restores its Firebase session from that credential.

```sh
uv run todomate-mcp auth login
uv run todomate-mcp auth status
uv run todomate-mcp auth logout
```

## Deployment

GitHub Actions tests the project and publishes Docker images to `ghcr.io/<owner>/<repo>` on default-branch pushes (`latest`) and version tags such as `v1.0.0`. Images support AMD64 and ARM64 and use GitHub's built-in token; no registry secrets are required. See [container publishing](docs/deployment.md#publishing-images-with-github-actions) for setup, image visibility, and pull commands.

See the [deployment guide](docs/deployment.md) for HTTPS, secrets, refresh-token persistence, and operating procedures for a remote MCP deployment.

For a Hermes and Duplicacy stack, use the [Compose overlay](compose.todomate.yaml) and [setup instructions](docs/deployment.md#add-to-the-hermes-and-duplicacy-stack). The Docker image persists login credentials in `/data/credentials.json` on a mounted volume.
