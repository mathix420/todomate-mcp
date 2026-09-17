"""Small authenticated task API for clients that do not need the MCP protocol."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date
import json
import re

from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse

from .firebase_auth import AuthenticationError
from .models import Todo
from .todomate import RecordNotFoundError, TodoMateAdapter


class ApiError(Exception):
    def __init__(self, code: str, message: str, status: int):
        self.code, self.message, self.status = code, message, status


def task_json(todo: Todo) -> dict:
    """Preserve the native ID, memo, calendar date, and actual reminder instant."""
    raw = todo.model_dump(mode="json")
    return {
        "id": raw["id"],
        "title": raw["content"],
        "goalId": raw["goal_id"],
        "memo": raw["memo"],
        "memoPublic": raw["memo_public"],
        "date": raw["date"],
        "dueAt": raw["remind_at"],
        "completed": raw["completed"],
    }


def register_api(
    mcp: MCPServer,
    adapter: TodoMateAdapter | None,
    *,
    verify_token: Callable[[str], Awaitable[object | None]] | None,
    today: Callable[[], date],
    timezone_name: str,
) -> None:
    # Serializing the read-before-write makes repeated completion requests on this
    # API idempotent, including a retry after an uncertain response was lost.
    completion_lock = asyncio.Lock()

    def response(value: dict, status: int = 200) -> JSONResponse:
        headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
        if status == 401:
            headers["WWW-Authenticate"] = 'Bearer realm="todomate"'
        return JSONResponse(value, status_code=status, headers=headers)

    async def authenticate(request: Request) -> None:
        headers = request.headers.getlist("authorization")
        parts = headers[0].split() if len(headers) == 1 else []
        valid = False
        if verify_token and len(parts) == 2 and parts[0].lower() == "bearer":
            try:
                valid = await verify_token(parts[1]) is not None
            except (TypeError, ValueError):
                pass
        if not valid:
            raise ApiError("unauthorized", "A valid bearer token is required.", 401)
        if adapter is None:
            raise ApiError("not_connected", "Connect the TodoMate account first.", 503)

    def endpoint(path: str, methods: list[str]):
        def decorate(handler):
            async def guarded(request: Request) -> JSONResponse:
                try:
                    await authenticate(request)
                    return response(await handler(request))
                except ApiError as error:
                    return response({"error": {"code": error.code, "message": error.message}}, error.status)
                except RecordNotFoundError:
                    return response({"error": {"code": "task_not_found", "message": "Task not found."}}, 404)
                except AuthenticationError:
                    return response({"error": {"code": "reauthentication_required", "message": "Sign in to TodoMate again."}}, 503)
                except Exception:
                    # Upstream errors can contain account details. Never return their
                    # text, request body, or credentials to an API caller.
                    return response({"error": {"code": "todomate_unavailable", "message": "TodoMate is unavailable. Please try again."}}, 502)
            mcp.custom_route(path, methods=methods)(guarded)
            return handler
        return decorate

    def selected_day(request: Request) -> tuple[date | None, bool]:
        query = request.query_params
        allowed = {"day", "unscheduled", "include_unscheduled"}
        if any(key not in allowed or len(query.getlist(key)) != 1 for key in query):
            raise ApiError("invalid_query", "Use day, unscheduled, or include_unscheduled once each.", 400)
        for key in ("unscheduled", "include_unscheduled"):
            if key in query and query[key] not in {"true", "false"}:
                raise ApiError("invalid_query", f"{key} must be true or false.", 400)
        undated = query.get("unscheduled") == "true"
        include_undated = query.get("include_unscheduled") == "true"
        if undated and ("day" in query or include_undated):
            raise ApiError("invalid_query", "Choose a date with optional undated tasks, or undated tasks only.", 400)
        day = today()
        if "day" in query:
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", query["day"]):
                    raise ValueError()
                day = date.fromisoformat(query["day"])
            except ValueError:
                raise ApiError("invalid_query", "day must be a valid YYYY-MM-DD date.", 400) from None
        return (None if undated else day), include_undated

    def task_id(request: Request) -> str:
        value = request.path_params["todo_id"]
        if not value or value in {".", ".."} or "/" in value or len(value.encode("utf8")) > 1500 or any(ord(char) < 32 for char in value):
            raise ApiError("invalid_task_id", "A valid task ID is required.", 400)
        return value

    @endpoint("/api/tasks", ["GET"])
    async def list_tasks(request: Request) -> dict:
        day, include_undated = selected_day(request)
        assert adapter is not None
        todos = await adapter.list_todos(day)
        if include_undated:
            known = {todo.id for todo in todos}
            todos += [todo for todo in await adapter.list_todos(None) if todo.id not in known]
        # Include finished groups too so a task never loses its category metadata.
        goals = await adapter.list_goals(include_finished=True)
        return {
            "tasks": [task_json(todo) for todo in todos],
            "goals": [goal.model_dump(mode="json") for goal in goals],
            "date": day.isoformat() if day else None,
            "timezone": timezone_name,
        }

    @endpoint("/api/tasks/{todo_id}", ["GET"])
    async def get_task(request: Request) -> dict:
        assert adapter is not None
        return {"task": task_json(await adapter.get_todo(task_id(request)))}

    @endpoint("/api/tasks/{todo_id}/complete", ["POST"])
    async def complete_task(request: Request) -> dict:
        ident = task_id(request)
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise ApiError("invalid_content_type", "Send application/json.", 415)
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 4096:
                raise ApiError("request_too_large", "The request body is too large.", 413)
            body.extend(chunk)
        try:
            value = json.loads(body)
        except (ValueError, UnicodeError):
            raise ApiError("invalid_request", "Send a JSON object with a completed boolean.", 400) from None
        if not isinstance(value, dict) or set(value) != {"completed"} or type(value["completed"]) is not bool:
            raise ApiError("invalid_request", "Send a JSON object with a completed boolean.", 400)
        assert adapter is not None
        async with completion_lock:
            todo = await adapter.get_todo(ident)
            if todo.completed != value["completed"]:
                todo = await adapter.complete_todo(ident, value["completed"])
            if todo.id != ident or todo.completed != value["completed"]:
                raise ApiError("completion_unconfirmed", "TodoMate did not confirm this change.", 502)
        return {"task": task_json(todo)}
