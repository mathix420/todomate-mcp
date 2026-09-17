# Remote MCP deployment

This service is intended for one private TodoMate account on one running instance. Expose only the Streamable HTTP endpoint at `https://<domain>/mcp`.

## Publishing images with GitHub Actions

The [Docker workflow](../.github/workflows/docker.yml) runs the Python tests and builds a container on branch pushes, pull requests, and tags starting with `v`. It starts the container and checks `/healthz`, rejection of unauthenticated `/mcp` requests, MCP initialization, and discovery of all 17 tools (including the reminder schema) before publishing.

After you upload this project to GitHub, pushes to the repository's default branch publish `ghcr.io/<owner>/<repo>:latest`. A Git tag such as `v1.0.0` publishes `ghcr.io/<owner>/<repo>:v1.0.0`. Every published build also receives a `sha-<full-commit-sha>` tag. Version tags do not move `latest`; it tracks the default branch. Pull requests and other branches only run the checks. You can also run the workflow manually from the Actions tab; publishing uses the same branch and tag rules.

Images support `linux/amd64` and `linux/arm64`. The Dockerfile installs runtime dependencies from `uv.lock`, omits development dependencies, and runs the server as an unprivileged user. Action versions are pinned to commit SHAs; when updating uv, keep the versions in the Dockerfile and workflow aligned.

Publishing uses GitHub's automatic `GITHUB_TOKEN` with `packages: write`; no Docker Hub account, registry password, or application secrets are needed in CI. If your organization restricts Actions, allow the GitHub, Docker, and Astral actions used in the workflow and package publishing.

New GitHub container packages are private by default, even for a public repository. After the first successful publish, open the package's **Package settings** and change its visibility to **Public** if you want unauthenticated pulls. See [GitHub's Container registry documentation](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry) for permissions and private-image authentication.

Replace `owner/repo` with your GitHub repository name in lowercase:

```sh
docker pull ghcr.io/owner/repo:latest
docker run --rm --env-file .env -p 127.0.0.1:8000:8000 ghcr.io/owner/repo:latest
```

GitHub Container Registry stores the image. Run the container on your own machine or hosting platform using the runtime configuration and persistent credential backend described below.

## Required configuration

Run the application with:

```sh
todomate-mcp http --host 0.0.0.0 --port 8000
```

Build and run the provided image locally with runtime secrets:

```sh
docker build -t todomate-mcp .
docker run --rm --env-file .env -p 8000:8000 todomate-mcp
```

Place it behind a reverse proxy or hosting platform that terminates TLS and forwards `/mcp` to port 8000. Configure these values through the platform's secret store, never in the image or repository.

| Variable | Purpose |
| --- | --- |
| `TZ` | Standard IANA timezone for default dates, such as `Europe/Paris`; defaults to `UTC` |
| `TODOMATE_FIREBASE_API_KEY` | Firebase Web API key |
| `TODOMATE_MCP_ACCESS_TOKEN` | Required Bearer token for the MCP endpoint; generate with `openssl rand -hex 32` |
| `TODOMATE_MCP_PUBLIC_URL` | Public HTTPS MCP URL, e.g. `https://todos.example.com/mcp` |
| `TODOMATE_MCP_HOST` / `TODOMATE_MCP_PORT` | Optional host and port overrides |
| `TODOMATE_ENV_FILE` | Optional `.env` path |
| `TODOMATE_CREDENTIALS_FILE` | Optional credential file instead of OS Keyring; defaults to `/data/credentials.json` in Docker |

The MCP client must send `Authorization: Bearer <TODOMATE_MCP_ACCESS_TOKEN>`. Requests without a valid token receive `401`.

## Refresh-token persistence

The Docker image stores the Firebase refresh token and UID in `/data/credentials.json`. Mount a persistent volume at `/data`; files are written atomically with owner-only permissions (`0600`). This file contains an unencrypted refresh token, so protect the volume and its backups. The account password is not stored.

Outside Docker, the default remains the OS Keyring/Credential Manager. Set `TODOMATE_CREDENTIALS_FILE` to explicitly select file storage.

Supply the Firebase API key, MCP token, and public URL from the platform secret store on every start. Run `todomate-mcp auth login` with the same volume and runtime user before starting the service. If you log in while the server is running, restart it so it loads the new credential.

Do not log the Authorization header, Firebase password, access token, or refresh token.

## Add to the Hermes and Duplicacy stack

[compose.todomate.yaml](../compose.todomate.yaml) is an overlay for the existing stack. It adds the MCP service and persistent `todomate_data` volume, gives Hermes the MCP access token, waits for the MCP health check, and mounts the data read-only into Duplicacy for backups. It leaves port 8000 on the Compose network; Hermes connects using the service name.

In the stack's `.env` file or Portainer environment settings, set `TODOMATE_FIREBASE_API_KEY` and `TODOMATE_MCP_ACCESS_TOKEN` (generate the latter with `openssl rand -hex 32`). Save the original stack as `stack.yml` and place the overlay alongside it. For Portainer's stack editor, merge the overlay's service fields and volume declaration into the original YAML instead.

```sh
docker compose -f stack.yml -f compose.todomate.yaml pull todomate-mcp
docker compose -f stack.yml -f compose.todomate.yaml run --rm --no-deps todomate-mcp todomate-mcp auth login
docker compose -f stack.yml -f compose.todomate.yaml up -d todomate-mcp hermes duplicacy
```

Login prompts for your TodoMate email and password. If the stack is already deployed in Portainer, open a console in the `todomate-mcp` container, run `todomate-mcp auth login`, then restart that service.

Merge this into Hermes' existing `config.yaml` under its configured `HERMES_HOME` (or `~/.hermes` by default):

```yaml
mcp_servers:
  todomate:
    url: http://todomate-mcp:8000/mcp
    headers:
      Authorization: "Bearer ${TODOMATE_MCP_ACCESS_TOKEN}"
```

This is a Hermes configuration file, separate from Compose YAML. Hermes resolves the token from its environment; see the [official Hermes MCP documentation](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp). Restart Hermes after saving it. The overlay recreates Hermes when its environment changes.

The health check confirms the server process is running; it does not confirm TodoMate login. Verify the live account with a read-only `list_todos` call from Hermes. To test from a local checkout against an accessible endpoint:

```sh
# Export the same MCP token first. The script never prints todo contents.
uv run python scripts/check_mcp.py --url http://127.0.0.1:8000/mcp --require-credentials
```

For a local host test, temporarily add `ports: ["127.0.0.1:8000:8000"]` to the MCP service. Without `--require-credentials`, the script checks health, bearer authentication, and tool discovery without needing a TodoMate account.

## Release checklist

1. Run `uv run pytest`.
2. Build and deploy the approved image to staging.
3. Confirm HTTPS access to `/mcp`, rejection without a Bearer token, and authenticated Todo read/write operations.
4. Restart the instance and confirm the Firebase session restores successfully.
5. Deploy the same image to production. Keep the previous image available for rollback.

## Operations

Configure the platform health check to send an unauthenticated `GET /healthz`; a healthy process returns `200` with `{"status":"ok"}`. This check verifies process availability only and does not call Firebase. Monitor process restarts, HTTP `401` and `5xx` rates, Firebase authentication failures, and response latency.

Rotate the MCP access token and Firebase refresh token when exposure is suspected and periodically according to the team's secret-management policy. Update the MCP client after rotating the access token.
