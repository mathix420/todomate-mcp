# Remote MCP deployment

This service is intended for one private TodoMate account on one running instance. Expose only the Streamable HTTP endpoint at `https://<domain>/mcp`.

## Publishing images with GitHub Actions

The [Docker workflow](../.github/workflows/docker.yml) runs the Python tests and builds a container on branch pushes, pull requests, and tags starting with `v`. It starts the container and checks `/healthz` and rejection of unauthenticated `/mcp` requests before publishing.

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
| `TODOMATE_FIREBASE_API_KEY` | Firebase Web API key |
| `TODOMATE_MCP_ACCESS_TOKEN` | Required Bearer token for the MCP endpoint; generate with `openssl rand -hex 32` |
| `TODOMATE_MCP_PUBLIC_URL` | Public HTTPS MCP URL, e.g. `https://todos.example.com/mcp` |
| `TODOMATE_MCP_HOST` / `TODOMATE_MCP_PORT` | Optional host and port overrides |
| `TODOMATE_ENV_FILE` | Optional `.env` path |

The MCP client must send `Authorization: Bearer <TODOMATE_MCP_ACCESS_TOKEN>`. Requests without a valid token receive `401`.

## Refresh-token persistence

After a successful request, the server stores the Firebase refresh token in the host OS Keyring/Credential Manager. Configure a persistent Keyring backend for the runtime user before deploying the service.

Supply the Firebase API key, MCP token, and public URL from the platform secret store on every start. Create the Keyring credential with `todomate-mcp auth login` for the runtime user before starting the service.

Do not log the Authorization header, Firebase password, access token, or refresh token.

## Release checklist

1. Run `uv run pytest`.
2. Build and deploy the approved image to staging.
3. Confirm HTTPS access to `/mcp`, rejection without a Bearer token, and authenticated Todo read/write operations.
4. Restart the instance and confirm the Firebase session restores successfully.
5. Deploy the same image to production. Keep the previous image available for rollback.

## Operations

Configure the platform health check to send an unauthenticated `GET /healthz`; a healthy process returns `200` with `{"status":"ok"}`. This check verifies process availability only and does not call Firebase. Monitor process restarts, HTTP `401` and `5xx` rates, Firebase authentication failures, and response latency.

Rotate the MCP access token and Firebase refresh token when exposure is suspected and periodically according to the team's secret-management policy. Update the MCP client after rotating the access token.
