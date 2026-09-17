"""MCP tools for TodoMate."""

from collections.abc import Callable
from datetime import date, datetime
from secrets import compare_digest
from typing import Annotated
from zoneinfo import ZoneInfo

from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from .todomate import TodoMateAdapter, TodoNotFoundError

_TIME_ZONE = ZoneInfo("Asia/Seoul")


class StaticTokenVerifier:
    """Validate the single Bearer token used by the private HTTP endpoint."""

    def __init__(self, access_token: str):
        self._access_token = access_token

    async def verify_token(self, token: str) -> AccessToken | None:
        if not compare_digest(token, self._access_token):
            return None
        return AccessToken(token=token, client_id="todomate-mcp", scopes=[])


def create_server(
    adapter: TodoMateAdapter | None,
    *,
    today: Callable[[], date] | None = None,
    access_token: str | None = None,
    resource_server_url: str | None = None,
) -> MCPServer:
    if bool(access_token) != bool(resource_server_url):
        raise ValueError("access_token and resource_server_url must be configured together")

    auth = None
    token_verifier = None
    if access_token and resource_server_url:
        auth = AuthSettings(issuer_url=resource_server_url, resource_server_url=resource_server_url)
        token_verifier = StaticTokenVerifier(access_token)

    mcp = MCPServer(
        name="TodoMate",
        instructions="TodoMate todos. Dates without a value use today's date in Asia/Seoul.",
        auth=auth,
        token_verifier=token_verifier,
    )
    today = today or (lambda: datetime.now(_TIME_ZONE).date())

    def configured() -> TodoMateAdapter:
        if adapter is None:
            raise RuntimeError("TodoMate credentials are not configured")
        return adapter

    @mcp.tool(description="List the authenticated user's todos for a date. Defaults to today in Asia/Seoul.")
    async def list_todos(day: date | None = None) -> dict:
        return {"todos": [todo.model_dump(mode="json") for todo in await configured().list_todos(day or today())]}

    @mcp.tool(description="Get one of the authenticated user's todos by ID.")
    async def get_todo(todo_id: Annotated[str, Field(min_length=1)]) -> dict:
        try:
            return (await configured().get_todo(todo_id)).model_dump(mode="json")
        except TodoNotFoundError:
            raise ValueError("Todo not found") from None

    @mcp.tool(description="Create a todo for the authenticated user. Date defaults to today in Asia/Seoul.")
    async def create_todo(
        content: Annotated[str, Field(min_length=1)],
        day: date | None = None,
        goal_id: Annotated[str | None, Field(min_length=1)] = None,
    ) -> dict:
        return (await configured().create_todo(content, day or today(), goal_id)).model_dump(mode="json")

    @mcp.tool(description="Update provided fields of one authenticated user's todo.")
    async def update_todo(
        todo_id: Annotated[str, Field(min_length=1)],
        content: Annotated[str | None, Field(min_length=1)] = None,
        day: date | None = None,
        goal_id: Annotated[str | None, Field(min_length=1)] = None,
    ) -> dict:
        try:
            if content is None and day is None and goal_id is None:
                raise ValueError("At least one Todo field is required")
            return (await configured().update_todo(todo_id, content=content, day=day, goal_id=goal_id)).model_dump(mode="json")
        except TodoNotFoundError:
            raise ValueError("Todo not found") from None

    @mcp.tool(description="Mark one authenticated user's todo complete or incomplete.")
    async def complete_todo(todo_id: Annotated[str, Field(min_length=1)], completed: bool = True) -> dict:
        try:
            return (await configured().complete_todo(todo_id, completed)).model_dump(mode="json")
        except TodoNotFoundError:
            raise ValueError("Todo not found") from None

    @mcp.tool(description="Delete one authenticated user's todo by ID.")
    async def delete_todo(todo_id: Annotated[str, Field(min_length=1)]) -> dict:
        try:
            await configured().delete_todo(todo_id)
            return {"id": todo_id, "deleted": True}
        except TodoNotFoundError:
            raise ValueError("Todo not found") from None

    return mcp


def create_http_app(mcp: MCPServer, *, host: str):
    app = mcp.streamable_http_app(host=host)

    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    app.add_route("/healthz", healthz, methods=["GET"])
    return app
