import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from urllib.parse import quote

import httpx
import pytest

from todomate_mcp.firebase_auth import AuthenticationError
from todomate_mcp.firestore import FirestoreError
from todomate_mcp.models import Goal, Todo
from todomate_mcp.todomate import TodoNotFoundError
from todomate_mcp.tools import create_http_app, create_server
import todomate_mcp.tools as tool_module


TODAY = date(2026, 9, 18)
TASK_ID = "native:task café"
TASK_URL = "/api/tasks/" + quote(TASK_ID, safe="")


class Adapter:
    def __init__(self):
        self.tasks = [
            Todo(id=TASK_ID, content="Préparer le rendez-vous", date=TODAY, completed=False,
                 goal_id="work", memo="Bring the notes.\n\nCall Léa first.", memo_public=False,
                 remind_at=datetime(2026, 9, 18, 7, 30, tzinfo=timezone.utc)),
            Todo(id="done", content="Already finished", date=TODAY, completed=True, goal_id="work"),
            Todo(id="undated", content="A later thought", date=None, completed=False),
        ]
        self.reads = []
        self.writes = []

    async def list_todos(self, day):
        self.reads.append(day)
        return [todo for todo in self.tasks if todo.date == day]

    async def list_goals(self, include_finished=False):
        assert include_finished is True
        return [Goal(id="work", title="Work", color=0xFFFF1122),
                Goal(id="old", title="Finished goal", status="done")]

    async def get_todo(self, todo_id):
        for todo in self.tasks:
            if todo.id == todo_id:
                return todo
        raise TodoNotFoundError("upstream path must stay private")

    async def complete_todo(self, todo_id, completed):
        self.writes.append((todo_id, completed))
        await asyncio.sleep(0)
        todo = (await self.get_todo(todo_id)).model_copy(update={"completed": completed})
        self.tasks = [todo if item.id == todo_id else item for item in self.tasks]
        return todo


@asynccontextmanager
async def api(adapter, *, token="test-token", today=lambda: TODAY):
    server = create_server(adapter, today=today, access_token=token,
                           resource_server_url="http://testserver/mcp" if token else None)
    app = create_http_app(server, host="testserver")
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver",
                                     headers={"Authorization": "Bearer test-token"}) as client:
            yield client


@pytest.mark.parametrize("authorization", ["", "Bearer wrong", "Basic test-token", "Bearer test-token extra"])
def test_every_api_route_requires_same_bearer_token(authorization):
    async def run():
        adapter = Adapter()
        async with api(adapter) as client:
            for method, path in [("GET", "/api/tasks"), ("GET", TASK_URL), ("POST", TASK_URL + "/complete")]:
                response = await client.request(method, path, headers={"Authorization": authorization}, json={"completed": True})
                assert response.status_code == 401
                assert response.headers["www-authenticate"].startswith("Bearer")
                assert response.headers["cache-control"] == "no-store"
        assert adapter.reads == [] and adapter.writes == []
    asyncio.run(run())


def test_api_without_configured_http_token_is_never_public():
    async def run():
        async with api(Adapter(), token=None) as client:
            assert (await client.get("/api/tasks")).status_code == 401
    asyncio.run(run())


def test_lists_native_task_fields_completed_tasks_and_group_metadata(monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Paris")

    async def run():
        async with api(Adapter()) as client:
            response = await client.get("/api/tasks")
            assert response.status_code == 200
            data = response.json()
            assert data["date"] == "2026-09-18" and data["timezone"] == "Europe/Paris"
            assert data["tasks"] == [
                {"id": TASK_ID, "title": "Préparer le rendez-vous", "goalId": "work",
                 "memo": "Bring the notes.\n\nCall Léa first.", "memoPublic": False,
                 "date": "2026-09-18", "dueAt": "2026-09-18T07:30:00Z", "completed": False},
                {"id": "done", "title": "Already finished", "goalId": "work", "memo": None,
                 "memoPublic": False, "date": "2026-09-18", "dueAt": None, "completed": True},
            ]
            assert [(goal["id"], goal["status"]) for goal in data["goals"]] == [("work", "active"), ("old", "done")]
            assert data["goals"][0]["color"] == 0xFFFF1122
            assert response.headers["cache-control"] == "no-store"
            assert (await client.get(TASK_URL)).json() == {"task": data["tasks"][0]}
    asyncio.run(run())


def test_today_uses_server_timezone_at_midnight(monkeypatch):
    class FixedDateTime:
        @staticmethod
        def now(zone):
            return datetime(2026, 9, 17, 23, 30, tzinfo=timezone.utc).astimezone(zone)

    monkeypatch.setattr(tool_module, "datetime", FixedDateTime)

    async def run():
        for zone, expected in [("Europe/Paris", "2026-09-18"), ("America/Los_Angeles", "2026-09-17")]:
            monkeypatch.setenv("TZ", zone)
            async with api(Adapter(), today=None) as client:
                assert (await client.get("/api/tasks")).json()["date"] == expected
    asyncio.run(run())


def test_date_and_undated_queries_do_not_invent_deadlines():
    async def run():
        adapter = Adapter()
        async with api(adapter) as client:
            undated = (await client.get("/api/tasks?unscheduled=true")).json()
            assert undated["date"] is None
            assert [(task["id"], task["dueAt"]) for task in undated["tasks"]] == [("undated", None)]
            both = (await client.get("/api/tasks?include_unscheduled=true")).json()
            assert [task["id"] for task in both["tasks"]] == [TASK_ID, "done", "undated"]
            future = (await client.get("/api/tasks?day=2026-10-01")).json()
            assert future["tasks"] == [] and future["date"] == "2026-10-01"
        assert adapter.reads == [None, TODAY, None, date(2026, 10, 1)]
    asyncio.run(run())


@pytest.mark.parametrize("query", ["day=2026-02-30", "day=20260918", "day=2026-09-18&unscheduled=true",
                                   "unscheduled=true&include_unscheduled=true", "unscheduled=yes",
                                   "include_unscheduled=1", "day=2026-09-18&day=2026-09-19", "unknown=value"])
def test_rejects_invalid_queries_before_reading_upstream(query):
    async def run():
        adapter = Adapter()
        async with api(adapter) as client:
            assert (await client.get("/api/tasks?" + query)).status_code == 400
        assert adapter.reads == []
    asyncio.run(run())


def test_completion_uses_original_id_and_concurrent_retries_only_write_once():
    async def run():
        adapter = Adapter()
        async with api(adapter) as client:
            responses = await asyncio.gather(*[client.post(TASK_URL + "/complete", json={"completed": True}) for _ in range(3)])
            assert all(result.status_code == 200 and result.json()["task"]["completed"] is True for result in responses)
            assert all(result.json()["task"]["id"] == TASK_ID for result in responses)
            assert adapter.writes == [(TASK_ID, True)]
            assert (await client.post(TASK_URL + "/complete", json={"completed": False})).json()["task"]["completed"] is False
            assert adapter.writes == [(TASK_ID, True), (TASK_ID, False)]
    asyncio.run(run())


def test_retry_after_uncertain_write_reads_back_and_does_not_rewrite():
    class LostReplyAdapter(Adapter):
        async def complete_todo(self, todo_id, completed):
            await super().complete_todo(todo_id, completed)
            raise FirestoreError("write")

    async def run():
        adapter = LostReplyAdapter()
        async with api(adapter) as client:
            assert (await client.post(TASK_URL + "/complete", json={"completed": True})).status_code == 502
            retried = await client.post(TASK_URL + "/complete", json={"completed": True})
            assert retried.status_code == 200 and retried.json()["task"]["completed"] is True
        assert adapter.writes == [(TASK_ID, True)]
    asyncio.run(run())


@pytest.mark.parametrize("body", [{}, {"completed": "true"}, {"completed": 1}, {"completed": None},
                                  {"completed": True, "memo": "unexpected"}, [], None])
def test_invalid_completion_does_not_write(body):
    async def run():
        adapter = Adapter()
        async with api(adapter) as client:
            result = await client.post(TASK_URL + "/complete", content=__import__("json").dumps(body), headers={"Content-Type": "application/json"})
            assert result.status_code == 400
        assert adapter.writes == []
    asyncio.run(run())


def test_completion_rejects_non_json_oversize_and_unconfirmed_upstream_result():
    class IncorrectAdapter(Adapter):
        async def complete_todo(self, todo_id, completed):
            return await self.get_todo(todo_id)

    async def run():
        async with api(IncorrectAdapter()) as client:
            assert (await client.post(TASK_URL + "/complete", content='{"completed":true}')).status_code == 415
            assert (await client.post(TASK_URL + "/complete", content="{" * 4097, headers={"Content-Type": "application/json"})).status_code == 413
            assert (await client.post(TASK_URL + "/complete", content="{", headers={"Content-Type": "application/json"})).status_code == 400
            result = await client.post(TASK_URL + "/complete", json={"completed": True})
            assert result.status_code == 502 and result.json()["error"]["code"] == "completion_unconfirmed"
    asyncio.run(run())


def test_missing_unconfigured_and_upstream_failures_are_sanitized():
    class FailedAdapter(Adapter):
        async def list_todos(self, day):
            raise RuntimeError("private account token and response")

    class RejectedAdapter(Adapter):
        async def list_todos(self, day):
            raise AuthenticationError("session", "reauthentication_required")

    async def run():
        async with api(Adapter()) as client:
            for method, path in [("GET", "/api/tasks/missing"), ("POST", "/api/tasks/missing/complete")]:
                result = await client.request(method, path, json={"completed": True})
                assert result.status_code == 404 and "upstream" not in result.text
        for adapter, status, code in [(None, 503, "not_connected"), (FailedAdapter(), 502, "todomate_unavailable"),
                                      (RejectedAdapter(), 503, "reauthentication_required")]:
            async with api(adapter) as client:
                result = await client.get("/api/tasks")
                assert result.status_code == status and result.json()["error"]["code"] == code
                assert "private account" not in result.text
    asyncio.run(run())
