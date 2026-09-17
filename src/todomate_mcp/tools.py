"""MCP tools for TodoMate."""

from collections.abc import Callable
from datetime import date, datetime
import os
from secrets import compare_digest
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from pydantic import AwareDatetime, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from .todomate import RecordNotFoundError, TodoMateAdapter, TodoNotFoundError

Visibility = Literal["private", "followers", "public"]
GoalStatus = Literal["active", "done", "ended", "stopped"]

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
    timezone_name = os.environ.get("TZ") or "UTC"
    time_zone = ZoneInfo(timezone_name)
    if bool(access_token) != bool(resource_server_url):
        raise ValueError("access_token and resource_server_url must be configured together")

    auth = None
    token_verifier = None
    if access_token and resource_server_url:
        auth = AuthSettings(issuer_url=resource_server_url, resource_server_url=resource_server_url)
        token_verifier = StaticTokenVerifier(access_token)

    mcp = MCPServer(
        name="TodoMate",
        instructions=(
            "TodoMate manages the connected user's todos. Before creating, call list_goals and use an "
            "actual group ID as goal_id, never a title, invented ID, or null. Match the user's requested "
            "group; reuse a clearly established choice or the only available group. Otherwise ask which "
            f"group to use. Dates default to today in {timezone_name} (TZ); pass YYYY-MM-DD when another local date "
            "is intended. Use todo IDs from list_todos/get_todo for changes. After an uncertain write, "
            "read back before retrying to avoid duplicates. Only report success after a successful tool result. "
            "Use list_todos(unscheduled=true) for undated todos and schedule_todo(day=null) to remove a date. "
            "Use set_todo_reminder to set or clear a native TodoMate alarm; remind_at requires a timestamp "
            "with a timezone offset, or explicit null to clear. Ask for the time/timezone if unknown. "
            "Todo results include remind_at in UTC. Date changes preserve the existing reminder instant; "
            "update the reminder separately when needed. A stored reminder does not verify notification delivery. "
            "Memos, new groups, and new diaries default to private; share only when explicitly requested. "
            "List diaries before creating or editing one for a date; do not overwrite an existing entry. "
            "Use list_goals(include_finished=true) to find groups to resume. Delete only empty groups."
        ),
        auth=auth,
        token_verifier=token_verifier,
    )
    today = today or (lambda: datetime.now(time_zone).date())

    def configured() -> TodoMateAdapter:
        if adapter is None:
            raise RuntimeError("TodoMate credentials are not configured")
        return adapter

    @mcp.tool(description="List the connected user's active todo groups (goals) in display order, with IDs, titles, status, and visibility. Set include_finished=true to also find done/ended/stopped groups. Call before creating a todo; use the selected group's ID, not its title, and ask if ambiguous.")
    async def list_goals(include_finished: bool = False) -> dict:
        return {"goals": [goal.model_dump(mode="json") for goal in await configured().list_goals(include_finished)]}

    @mcp.tool(description="Create a todo group (goal). First list_goals to avoid accidental duplicates. Defaults to private; followers/public visibility requires an explicit user request. color is an unsigned 32-bit ARGB integer.")
    async def create_goal(title: Annotated[str, Field(min_length=1)], color: Annotated[int, Field(ge=0, le=0xFFFFFFFF)] = 4294929858, visibility: Visibility = "private") -> dict:
        return (await configured().create_goal(title, color, visibility)).model_dump(mode="json")

    @mcp.tool(description="Change a group's status: active resumes it, done marks it achieved, ended ends it, stopped stops it. Use list_goals(include_finished=true) to discover finished groups. This does not complete or delete the group's todos.")
    async def set_goal_status(goal_id: Annotated[str, Field(min_length=1)], status: GoalStatus) -> dict:
        try:
            return (await configured().set_goal_status(goal_id, status)).model_dump(mode="json")
        except RecordNotFoundError:
            raise ValueError("Group not found") from None

    @mcp.tool(description="Delete an empty group belonging to the connected user. Refuses groups containing todos; move or delete those todos explicitly first. Use an ID from list_goals.")
    async def delete_goal(goal_id: Annotated[str, Field(min_length=1)]) -> dict:
        try:
            await configured().delete_goal(goal_id)
            return {"id": goal_id, "deleted": True}
        except RecordNotFoundError:
            raise ValueError("Group not found") from None

    @mcp.tool(description=f"List the connected user's diary entries for a date, including body, emoji, and visibility. Defaults to today in {timezone_name} (TZ). Call before creating/updating/deleting a diary; use returned IDs.")
    async def list_diaries(day: date | None = None) -> dict:
        return {"diaries": [diary.model_dump(mode="json") for diary in await configured().list_diaries(day or today())]}

    @mcp.tool(description=f"Create a diary with body and mood emoji for a date (defaults to today in {timezone_name}, TZ). Refuses an existing entry for that date; use update_diary instead. Defaults to private; followers/public sharing requires an explicit user request.")
    async def create_diary(body: Annotated[str, Field(min_length=1)], emoji: Annotated[str, Field(min_length=1)], day: date | None = None, visibility: Visibility = "private") -> dict:
        return (await configured().create_diary(body, emoji, day or today(), visibility)).model_dump(mode="json")

    @mcp.tool(description="Update an owned diary's body, mood emoji, or visibility using its ID from list_diaries. Omitted fields stay unchanged, including existing visibility; inspect that visibility before adding sensitive text. Only change sharing when the user asks.")
    async def update_diary(diary_id: Annotated[str, Field(min_length=1)], body: Annotated[str | None, Field(min_length=1)] = None, emoji: Annotated[str | None, Field(min_length=1)] = None, visibility: Visibility | None = None) -> dict:
        try:
            return (await configured().update_diary(diary_id, body=body, emoji=emoji, visibility=visibility)).model_dump(mode="json")
        except RecordNotFoundError:
            raise ValueError("Diary not found") from None

    @mcp.tool(description="Delete one diary belonging to the connected user. Use an ID from list_diaries and resolve ambiguous entries before deleting.")
    async def delete_diary(diary_id: Annotated[str, Field(min_length=1)]) -> dict:
        try:
            await configured().delete_diary(diary_id)
            return {"id": diary_id, "deleted": True}
        except RecordNotFoundError:
            raise ValueError("Diary not found") from None

    @mcp.tool(description=f"List the user's todos for a date (defaults to today in {timezone_name}, configured by TZ), or set unscheduled=true to list todos with no date. Do not combine day and unscheduled=true.")
    async def list_todos(day: date | None = None, unscheduled: bool = False) -> dict:
        if unscheduled and day is not None:
            raise ValueError("Choose either a day or unscheduled todos")
        selected_day = None if unscheduled else (day or today())
        return {"todos": [todo.model_dump(mode="json") for todo in await configured().list_todos(selected_day)]}

    @mcp.tool(description="Get one of the authenticated user's todos by ID.")
    async def get_todo(todo_id: Annotated[str, Field(min_length=1)]) -> dict:
        try:
            return (await configured().get_todo(todo_id)).model_dump(mode="json")
        except TodoNotFoundError:
            raise ValueError("Todo not found") from None

    @mcp.tool(description=f"Create a todo in one of the user's groups. First call list_goals and choose its goal_id. Date defaults to today in {timezone_name}, configured by TZ.")
    async def create_todo(
        content: Annotated[str, Field(min_length=1)],
        goal_id: Annotated[str, Field(min_length=1, description="Actual group ID returned by list_goals; required. Do not pass the group title.")],
        day: Annotated[date | None, Field(description="Date as YYYY-MM-DD. Omitted means today in the server's TZ timezone (UTC if unset). Provide another intended local date explicitly.")] = None,
    ) -> dict:
        return (await configured().create_todo(content, day or today(), goal_id)).model_dump(mode="json")

    @mcp.tool(description="Update provided fields of one authenticated user's todo. To set or clear its native alarm, use set_todo_reminder.")
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

    @mcp.tool(description="Move a todo to a calendar date, or remove its date. day is required: pass YYYY-MM-DD to schedule, or null to make it unscheduled. Find undated todos with list_todos(unscheduled=true). This preserves any reminder instant; use set_todo_reminder to change or clear its alarm.")
    async def schedule_todo(todo_id: Annotated[str, Field(min_length=1)], day: date | None) -> dict:
        try:
            return (await configured().schedule_todo(todo_id, day)).model_dump(mode="json")
        except TodoNotFoundError:
            raise ValueError("Todo not found") from None

    @mcp.tool(description="Set or clear a todo's native TodoMate reminder/alarm. remind_at is required: an ISO 8601 timestamp with timezone offset (for example 2026-09-18T09:00:00+02:00), or null to clear. Ask for the time/timezone if unknown. Writes the native remindAt field, preserving the todo's date and other fields. Returns the stored reminder in UTC; does not verify device notification delivery.")
    async def set_todo_reminder(
        todo_id: Annotated[str, Field(min_length=1)],
        remind_at: Annotated[AwareDatetime | None, Field(description="Reminder timestamp with an explicit timezone offset or Z; null clears the reminder.")],
    ) -> dict:
        try:
            return (await configured().set_todo_reminder(todo_id, remind_at)).model_dump(mode="json")
        except TodoNotFoundError:
            raise ValueError("Todo not found") from None

    @mcp.tool(description="Set a todo's memo (note), or pass memo=null to clear it. Defaults to private, including when editing a previously public memo. Set public=true only when the user explicitly wants the memo visible to others. Does not change account-wide preferences.")
    async def set_todo_memo(
        todo_id: Annotated[str, Field(min_length=1)], memo: str | None, public: bool = False
    ) -> dict:
        try:
            return (await configured().set_todo_memo(todo_id, memo, public)).model_dump(mode="json")
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
