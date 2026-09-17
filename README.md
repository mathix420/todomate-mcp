# todomate-mcp

A Python MCP server that lists, creates, updates, completes, and deletes TodoMate Firestore todos.

## Development environment

- Python 3.12 or later and uv
- Official MCP Python SDK (`mcp`), httpx, and pydantic
- Development dependency: pytest

## Installation

```sh
uv sync
```

Source code is in `src/` and tests are in `tests/`. Copy `.env.example` to a local `.env` file and configure its credentials. `.env` is excluded from Git.

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

The server supports `list_todos`, `get_todo`, `create_todo`, `update_todo`, `complete_todo`, and `delete_todo`. When a date is omitted, it uses the current date in `Asia/Seoul`.

Set `TODOMATE_FIREBASE_API_KEY` in `.env`. Sign in interactively with the following commands; the refresh token and UID are stored in the OS Keyring/Credential Manager, and the stdio MCP server restores its Firebase session from that credential.

```sh
uv run todomate-mcp auth login
uv run todomate-mcp auth status
uv run todomate-mcp auth logout
```

## Deployment

GitHub Actions tests the project and publishes Docker images to `ghcr.io/<owner>/<repo>` on default-branch pushes (`latest`) and version tags such as `v1.0.0`. Images support AMD64 and ARM64 and use GitHub's built-in token; no registry secrets are required. See [container publishing](docs/deployment.md#publishing-images-with-github-actions) for setup, image visibility, and pull commands.

See the [deployment guide](docs/deployment.md) for HTTPS, secrets, refresh-token persistence, and operating procedures for a remote MCP deployment.
