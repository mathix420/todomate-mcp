# todomate-mcp

A Python MCP server that lists, creates, updates, completes, and deletes TodoMate Firestore todos.

The same HTTP service also exposes a small authenticated task API for applications such as Alfred.

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

The server provides 17 tools for groups, todos, scheduling, reminders, memos, and diaries. Default dates use the standard `TZ` environment variable (for example, `TZ=Europe/Paris`), falling back to `UTC`. Use `list_todos(unscheduled=true)` for undated todos and `schedule_todo(day=null)` to remove a date. Set a native alarm with `set_todo_reminder(todo_id, remind_at="2026-09-18T09:00:00+02:00")`, or clear it with `remind_at=null`; todo results include `remind_at` in UTC. Memos, new groups, and new diaries default to private.

Creating a todo requires a `goal_id` (the TodoMate group). Call `list_goals` to get your groups' IDs and titles, then pass the chosen ID to `create_todo`. TodoMate rejects creation with a null group ID with HTTP 403.

See the [MCP usage guide for LLMs and clients](docs/mcp-usage.md) for group selection, date handling, tool arguments, and safe retries. These instructions are also included in MCP tool discovery.

Set `TODOMATE_FIREBASE_API_KEY` in `.env`. Sign in interactively with the following commands; the refresh token and UID are stored in the OS Keyring/Credential Manager, and the stdio MCP server restores its Firebase session from that credential.

```sh
uv run todomate-mcp auth login
uv run todomate-mcp auth status
uv run todomate-mcp auth logout
```

## Task HTTP API

HTTP mode serves these routes on the same port as `/mcp`. Each request requires `Authorization: Bearer <TODOMATE_MCP_ACCESS_TOKEN>` and uses the same connected TodoMate account. No MCP initialization or separate API key is needed.

| Route | Result |
| --- | --- |
| `GET /api/tasks` | Today's tasks, including completed tasks, with group metadata. Today follows the server's `TZ`. |
| `GET /api/tasks?day=2026-09-18` | Tasks scheduled on that calendar date. |
| `GET /api/tasks?include_unscheduled=true` | Today's tasks followed by undated tasks; can also be combined with `day`. |
| `GET /api/tasks?unscheduled=true` | Undated tasks only; cannot be combined with `day` or `include_unscheduled=true`. |
| `GET /api/tasks/{id}` | One task, returned as `{"task": {...}}`. URL-encode the original task ID. |
| `POST /api/tasks/{id}/complete` | Set completion with JSON `{"completed": true}` or `{"completed": false}`; returns `{"task": {...}}`. |
| `POST /api/tasks/{id}/timer` | Native timer action with JSON `{"action": "start"}`, `"pause"`, or `"stop"`; returns `{"task": {...}}`. Start also resumes; **Stop saves elapsed time and completes the task**. |

The task-list response is:

```json
{
  "tasks": [{
    "id": "original-todomate-id",
    "title": "Prepare the meeting",
    "goalId": "original-goal-id",
    "memo": "Bring the notes.",
    "memoPublic": false,
    "date": "2026-09-18",
    "dueAt": "2026-09-18T07:30:00Z",
    "completed": false,
    "timer": {"startedAt": "2026-09-18T08:00:00Z", "elapsedSeconds": 120},
    "spentTimeSeconds": null
  }],
  "goals": [{
    "id": "original-goal-id",
    "title": "Work",
    "status": "active",
    "visibility": "private",
    "color": 4294929858
  }],
  "date": "2026-09-18",
  "timezone": "Europe/Paris"
}
```

`goalId`, `memo`, `date`, and `dueAt` can be `null`. `dueAt` is the actual native reminder instant, not an invented time for a scheduled date. Group metadata includes finished groups, so existing tasks retain their labels and colors. Responses preserve full task text; clients choose their own display limits and focus priority.

Completion reads the current task before writing. Repeating an already-applied completion returns success without changing its completion timestamp again. After a lost response, retry the same desired state; the read confirms whether the first write persisted. The API never treats an unconfirmed upstream result as success.

`timer` is `null` when disabled. Otherwise, `elapsedSeconds` is the accumulated whole seconds from previous intervals; while `startedAt` is non-null, add the seconds since that UTC instant. A paused timer has `startedAt: null`, so its elapsed time stays fixed. `spentTimeSeconds` is the saved total from the last stop, or `null`. Stop caps saved time at 20 hours, matching TodoMate. Completing a task with its checkbox also stops and saves its timer. Reopening preserves saved time; starting it again includes that time. Start on a completed task and Pause/Stop without a timer return `409`. Repeating Start while running, Pause while paused, or Stop after completion is a no-op.

Timer and completion writes use the document's update-time precondition. If another client edits it after the read, the API returns `409` without overwriting the newer change. Refresh before deciding to retry. This guards one task at a time; it does not enforce a single running timer across different tasks. MCP clients can use `set_todo_timer(todo_id, action)` with the same semantics; MCP task fields use `timer.started_at`, `timer.elapsed_seconds`, and `spent_time_seconds`.

Errors use `{"error":{"code":"...","message":"..."}}`: invalid inputs return `400`, missing or invalid bearer tokens `401`, missing or unowned tasks `404`, conflicting task state `409`, oversized bodies `413`, non-JSON writes `415`, upstream failures `502`, and disconnected or expired TodoMate credentials `503`. API responses use `Cache-Control: no-store`. `/healthz` remains public and does not expose account or task data.

## Deployment

GitHub Actions tests the project and publishes Docker images to `ghcr.io/<owner>/<repo>` on default-branch pushes (`latest`) and version tags such as `v1.0.0`. Images support AMD64 and ARM64 and use GitHub's built-in token; no registry secrets are required. See [container publishing](docs/deployment.md#publishing-images-with-github-actions) for setup, image visibility, and pull commands.

See the [deployment guide](docs/deployment.md) for HTTPS, secrets, refresh-token persistence, and operating procedures for a remote MCP deployment.

For a Hermes and Duplicacy stack, use the [Compose overlay](compose.todomate.yaml) and [setup instructions](docs/deployment.md#add-to-the-hermes-and-duplicacy-stack). The Docker image persists login credentials in `/data/credentials.json` on a mounted volume.
