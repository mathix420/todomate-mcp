import asyncio
import json
from datetime import datetime, timezone
import httpx
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from todomate_mcp.auth.credentials import Credential
import todomate_mcp.server as server
from todomate_mcp.server import _ConfiguredAdapter
from todomate_mcp.firebase_auth import AuthenticationError
from todomate_mcp.models import Goal, Todo
from todomate_mcp.tools import create_http_app, create_server
import todomate_mcp.tools as tool_module


EXPECTED_TOOLS = {
    "list_goals", "create_goal", "set_goal_status", "delete_goal",
    "list_diaries", "create_diary", "update_diary", "delete_diary",
    "list_todos", "get_todo", "create_todo", "update_todo", "schedule_todo",
    "set_todo_memo", "complete_todo", "delete_todo",
}


def test_default_dates_follow_tz_at_midnight_boundary(monkeypatch):
    class FixedDateTime:
        @staticmethod
        def now(zone):
            return datetime(2026, 9, 17, 23, 30, tzinfo=timezone.utc).astimezone(zone)

    class DateAdapter:
        async def list_todos(self, day):
            dates.append(day.isoformat())
            return []

    monkeypatch.setattr(tool_module, "datetime", FixedDateTime)
    dates = []

    async def run():
        for tz in ("Europe/Paris", "America/Los_Angeles", ""):
            monkeypatch.setenv("TZ", tz)
            async with Client(create_server(DateAdapter())) as client:
                result = await client.call_tool("list_todos")
                assert not result.is_error
                listed = next(t for t in (await client.list_tools()).tools if t.name == "list_todos")
                assert (tz or "UTC") in listed.description
    asyncio.run(run())
    assert dates == ["2026-09-18", "2026-09-17", "2026-09-17"]

def test_server_initializes_and_lists_tools():
    async def run():
        async with Client(create_server(None)) as client:
            tools = (await client.list_tools()).tools
            assert {tool.name for tool in tools} == EXPECTED_TOOLS
            create = next(tool for tool in tools if tool.name == "create_todo")
            assert "goal_id" in create.input_schema["required"]
            schedule = next(tool for tool in tools if tool.name == "schedule_todo")
            assert "day" in schedule.input_schema["required"]
    asyncio.run(run())


class Adapter:
    async def list_goals(self, include_finished=False):
        return [Goal(id="goal", title="Personal")]

    async def list_todos(self, day):
        assert day is None or day.isoformat() == "2026-09-05"
        return [Todo(id="one", content="write", date=day, completed=False, goal_id="goal")]

    async def get_todo(self, todo_id):
        return Todo(id=todo_id, content="write", date=__import__("datetime").date(2026, 9, 5), completed=False, goal_id="goal")

    async def create_todo(self, content, day, goal_id):
        assert (content, day.isoformat(), goal_id) == ("new", "2026-09-05", "goal")
        return Todo(id="new", content=content, date=day, completed=False, goal_id=goal_id)

    async def update_todo(self, todo_id, *, content=None, day=None, goal_id=None):
        assert (todo_id, content, day, goal_id) == ("one", "changed", None, None)
        return Todo(id=todo_id, content=content, date=__import__("datetime").date(2026, 9, 5), completed=False, goal_id="goal")

    async def complete_todo(self, todo_id, completed=True):
        assert (todo_id, completed) == ("one", False)
        return Todo(id=todo_id, content="write", date=__import__("datetime").date(2026, 9, 5), completed=completed, goal_id="goal")

    async def delete_todo(self, todo_id):
        assert todo_id == "one"

    async def schedule_todo(self, todo_id, day):
        assert todo_id == "one"
        return Todo(id=todo_id, content="write", date=day, completed=False, goal_id="goal")

    async def set_todo_memo(self, todo_id, memo, public):
        assert todo_id == "one"
        return Todo(id=todo_id, content="write", date=None, completed=False, memo=memo, memo_public=public)


def test_todo_tools_list_and_return_normalized_data():
    async def run():
        async with Client(create_server(Adapter(), today=lambda: __import__("datetime").date(2026, 9, 5))) as client:
            assert {tool.name for tool in (await client.list_tools()).tools} == EXPECTED_TOOLS
            goals = await client.call_tool("list_goals")
            assert json.loads(goals.content[0].text) == {"goals": [{"id": "goal", "title": "Personal", "status": "active", "visibility": "private", "color": None}]}
            listed = await client.call_tool("list_todos")
            fetched = await client.call_tool("get_todo", {"todo_id": "one"})
            created = await client.call_tool("create_todo", {"content": "new", "goal_id": "goal"})
            updated = await client.call_tool("update_todo", {"todo_id": "one", "content": "changed"})
            completed = await client.call_tool("complete_todo", {"todo_id": "one", "completed": False})
            deleted = await client.call_tool("delete_todo", {"todo_id": "one"})
            assert json.loads(listed.content[0].text)["todos"][0]["id"] == "one"
            assert json.loads(fetched.content[0].text)["id"] == "one"
            assert json.loads(created.content[0].text)["id"] == "new"
            assert json.loads(updated.content[0].text)["content"] == "changed"
            assert json.loads(completed.content[0].text)["completed"] is False
            assert json.loads(deleted.content[0].text) == {"id": "one", "deleted": True}
    asyncio.run(run())


def test_unscheduled_and_memo_tools_preserve_explicit_null_and_private_default():
    async def run():
        async with Client(create_server(Adapter())) as client:
            unscheduled = await client.call_tool("list_todos", {"unscheduled": True})
            assert json.loads(unscheduled.content[0].text)["todos"][0]["date"] is None
            moved = await client.call_tool("schedule_todo", {"todo_id": "one", "day": None})
            assert json.loads(moved.content[0].text)["date"] is None
            note = await client.call_tool("set_todo_memo", {"todo_id": "one", "memo": "private"})
            assert json.loads(note.content[0].text)["memo_public"] is False
    asyncio.run(run())


def test_mcp_rejects_invalid_tool_inputs():
    async def run():
        async with Client(create_server(Adapter(), today=lambda: __import__("datetime").date(2026, 9, 5))) as client:
            for name, arguments in [
                ("get_todo", {"todo_id": ""}),
                ("create_todo", {"content": "", "goal_id": "goal"}),
                ("create_todo", {"content": "valid"}),
                ("create_todo", {"content": "valid", "goal_id": None}),
                ("create_todo", {"content": "valid", "goal_id": ""}),
                ("update_todo", {"todo_id": "one"}),
                ("delete_todo", {"todo_id": ""}),
                ("schedule_todo", {"todo_id": "one"}),
                ("set_todo_memo", {"todo_id": "one"}),
                ("list_todos", {"day": "2026-09-17", "unscheduled": True}),
            ]:
                assert (await client.call_tool(name, arguments)).is_error is True
    asyncio.run(run())


def test_streamable_http_requires_a_bearer_token_and_lists_tools():
    async def run():
        mcp_server = create_server(
            Adapter(),
            today=lambda: __import__("datetime").date(2026, 9, 5),
            access_token="secret",
            resource_server_url="http://testserver/mcp",
        )
        app = create_http_app(mcp_server, host="testserver")
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
                response = await client.post("/mcp", json={})
                assert response.status_code == 401
                assert response.headers["www-authenticate"].startswith("Bearer ")

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
                headers={"Authorization": "Bearer secret"},
            ) as http_client:
                async with Client(streamable_http_client("http://testserver/mcp", http_client=http_client)) as client:
                    assert {tool.name for tool in (await client.list_tools()).tools} == EXPECTED_TOOLS

    asyncio.run(run())


def test_healthz_is_public_and_reports_ok():
    async def run():
        app = create_http_app(create_server(None), host="testserver")
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
                response = await client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    asyncio.run(run())


def test_configured_adapter_signs_in_once_before_the_first_operation():
    async def run():
        calls = []

        class Auth:
            refresh_token = "rotated"
            uid = "user"

            async def sign_in(self, email, password):
                calls.append((email, password))

        class RawAdapter:
            async def list_todos(self, day):
                return [day]

        class Store:
            def save(self, credential):
                calls.append(("store", credential))

        auth = Auth()

        async def authenticate():
            await auth.sign_in("me@example.com", "password")

        adapter = _ConfiguredAdapter(auth, RawAdapter(), authenticate, Store())
        assert await adapter.list_todos(__import__("datetime").date(2026, 9, 5))
        assert await adapter.list_todos(__import__("datetime").date(2026, 9, 6))
        assert calls == [
            ("me@example.com", "password"),
            ("store", Credential("rotated", "user")),
            ("store", Credential("rotated", "user")),
            ("store", Credential("rotated", "user")),
        ]
    asyncio.run(run())


def test_rejected_refresh_token_requires_reauthentication():
    async def run():
        class Auth:
            refresh_token = "old"

        class Store:
            def save(self, token):
                raise AssertionError("must not save a rejected token")

            def delete(self):
                deleted.append(True)

        deleted = []
        async def authenticate():
            raise AuthenticationError("refresh", "http_error", status_code=400)

        adapter = _ConfiguredAdapter(Auth(), object(), authenticate, Store())
        with __import__("pytest").raises(AuthenticationError) as caught:
            await adapter.list_todos(__import__("datetime").date(2026, 9, 5))
        assert (caught.value.operation, caught.value.reason) == ("session", "reauthentication_required")
        assert deleted == [True]
    asyncio.run(run())


def test_restored_session_saves_a_rotated_refresh_token():
    async def run():
        saved = []

        class Auth:
            refresh_token = "old-token"
            uid = "user"

            async def restore(self, refresh_token):
                assert refresh_token == "stored-token"
                self.refresh_token = "rotated-token"

        class RawAdapter:
            async def list_todos(self, day):
                return [day]

        class Store:
            def save(self, credential):
                saved.append(credential)

        auth = Auth()
        adapter = _ConfiguredAdapter(auth, RawAdapter(), lambda: auth.restore("stored-token"), Store())
        await adapter.list_todos(__import__("datetime").date(2026, 9, 5))

        assert saved == [Credential("rotated-token", "user"), Credential("rotated-token", "user")]

    asyncio.run(run())


def test_adapter_configuration_restores_the_keyring_credential(monkeypatch):
    calls = []

    class Store:
        def load(self):
            return Credential("stored", "user")

        def save(self, credential):
            calls.append(("save", credential))

        def delete(self):
            calls.append(("delete",))

    class Auth:
        def __init__(self, api_key, client):
            assert api_key == "api-key"

        async def restore(self, refresh_token):
            calls.append(("restore", refresh_token))

    monkeypatch.setattr(server, "load_firebase_api_key", lambda: "api-key")
    monkeypatch.setattr(server, "KeyringCredentialStore", Store)
    monkeypatch.setattr(server.httpx, "AsyncClient", object)
    monkeypatch.setattr(server, "FirebaseAuthSession", Auth)

    configured = server._adapter_from_local_credentials()

    assert configured is not None
    asyncio.run(configured._authenticate())
    assert calls == [("restore", "stored")]


def test_stdio_requires_a_connected_account(monkeypatch, capsys):
    monkeypatch.setattr(server, "_adapter_from_local_credentials", lambda: None)
    monkeypatch.setattr(server.sys, "argv", ["todomate-mcp"])

    with __import__("pytest").raises(SystemExit, match="1"):
        server.main()

    assert capsys.readouterr().err == "TodoMate account is not connected.\nRun: todomate-mcp auth login\n"
