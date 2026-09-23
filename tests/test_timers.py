"""Native timer transitions, conditional writes, and actual HTTP/MCP routes."""

import asyncio
from copy import deepcopy
from datetime import datetime, timezone

import pytest
from mcp import Client

from test_api import api
from todomate_mcp.firestore import FirestoreError
from todomate_mcp.models import todo_from_document
from todomate_mcp.todomate import TaskConflictError, TodoMateAdapter, TodoNotFoundError
from todomate_mcp.tools import create_server
import todomate_mcp.todomate as domain


NOW = 1790150400000


class Auth:
    uid = "owner"


class Store:
    def __init__(self):
        self.document = {"id": "test", "content": "Disposable task", "writerID": "owner",
                         "date": None, "isDone": False, "doneTime": None, "hasTimer": False,
                         "timer": None, "spentTime": None, "memo": "Keep this", "goalID": "work",
                         "remindAt": NOW + 3600000}
        self.version = 0
        self.writes = []
        self.conflict = False

    async def get_document(self, path):
        if path != "TodoItem/test":
            raise FirestoreError("get", 404)
        return deepcopy(self.document)

    async def get_document_versioned(self, path):
        snapshot = await self.get_document(path)
        version = str(self.version)
        await asyncio.sleep(0)  # Concurrent writers can read the same version.
        return snapshot, version

    async def upsert_document(self, path, fields, *, update_mask, update_time):
        assert path == "TodoItem/test"
        if self.conflict or update_time != str(self.version):
            raise FirestoreError("upsert", 412)
        self.writes.append((deepcopy(fields), list(update_mask), update_time))
        for field in update_mask:
            keys = field.split(".")
            source, target = fields, self.document
            for key in keys[:-1]:
                source = source[key]
                if not isinstance(target.get(key), dict):
                    target[key] = {}
                target = target[key]
            target[keys[-1]] = deepcopy(source[keys[-1]])
        self.version += 1
        return deepcopy(self.document)


@pytest.fixture
def setup(monkeypatch):
    clock = [NOW]
    monkeypatch.setattr(domain, "_now_millis", lambda: clock[0])
    store = Store()
    return TodoMateAdapter(Auth(), store), store, clock


def test_native_start_pause_resume_stop_excludes_pause_and_preserves_fields(setup):
    adapter, store, clock = setup

    async def run():
        first = await adapter.timer_todo("test", "start")
        assert first.timer.started_at == datetime.fromtimestamp(NOW / 1000, timezone.utc)
        assert first.timer.elapsed_seconds == 0
        assert store.document["timer"] == {"startTime": NOW, "savedDuration": 0,
                                            "todoItemId": "test", "todoItemContent": "Disposable task"}
        assert store.writes[-1][1] == ["hasTimer", "timer.savedDuration", "timer.startTime", "timer.todoItemContent", "timer.todoItemId"]
        clock[0] += 2900
        paused = await adapter.timer_todo("test", "pause")
        assert paused.timer.started_at is None and paused.timer.elapsed_seconds == 2
        assert not paused.completed and store.document["hasTimer"] is True
        assert store.writes[-1][1] == ["timer.savedDuration", "timer.startTime"]
        assert store.document["timer"]["todoItemId"] == "test"
        clock[0] += 10000
        resumed = await adapter.timer_todo("test", "start")
        assert resumed.timer.elapsed_seconds == 2
        clock[0] += 2100
        stopped = await adapter.timer_todo("test", "stop")
        assert stopped.completed and stopped.timer is None and stopped.spent_time_seconds == 4
        assert store.document["doneTime"] == clock[0] and store.document["hasTimer"] is False
        assert store.writes[-1][1] == ["doneTime", "hasTimer", "isDone", "spentTime", "timer"]
        assert stopped.memo == "Keep this" and stopped.goal_id == "work"
        assert store.document["remindAt"] == NOW + 3600000
        assert len(store.writes) == 4
    asyncio.run(run())


def test_repeat_actions_are_noops_and_reopen_retains_saved_time(setup):
    adapter, store, clock = setup

    async def run():
        await adapter.timer_todo("test", "start")
        clock[0] += 5000
        await adapter.timer_todo("test", "start")
        assert store.document["timer"]["startTime"] == NOW
        await adapter.timer_todo("test", "pause")
        clock[0] += 5000
        await adapter.timer_todo("test", "pause")
        done = await adapter.timer_todo("test", "stop")
        await adapter.timer_todo("test", "stop")
        await adapter.complete_todo("test")
        assert done.spent_time_seconds == 5 and len(store.writes) == 3
        with pytest.raises(TaskConflictError):
            await adapter.timer_todo("test", "start")
        await adapter.complete_todo("test", False)
        assert store.document["doneTime"] is None
        again = await adapter.timer_todo("test", "start")
        assert again.timer.elapsed_seconds == 5 and not again.completed
    asyncio.run(run())


@pytest.mark.parametrize("action", ["start", "pause", "stop"])
def test_ownership_and_missing_task_checked_before_timer_mutations(setup, action):
    adapter, store, _ = setup

    async def run():
        with pytest.raises(TodoNotFoundError):
            await adapter.timer_todo("missing", action)
        store.document["writerID"] = "someone-else"
        with pytest.raises(TodoNotFoundError):
            await adapter.timer_todo("test", action)
        assert store.writes == []
    asyncio.run(run())


def test_stale_write_is_rejected_without_overwriting_other_device(setup):
    adapter, store, _ = setup

    async def run():
        store.conflict = True
        for change in (adapter.timer_todo("test", "start"), adapter.complete_todo("test")):
            with pytest.raises(TaskConflictError):
                await change
        assert not store.document["isDone"] and not store.document["hasTimer"] and not store.writes
        store.conflict = False
        results = await asyncio.gather(adapter.timer_todo("test", "start"), adapter.complete_todo("test"), return_exceptions=True)
        assert sum(isinstance(result, TaskConflictError) for result in results) == 1
        assert len(store.writes) == 1
    asyncio.run(run())


def test_firestore_failed_precondition_http_400_is_public_conflict(setup):
    adapter, store, _ = setup

    async def rejected(*_, **__):
        raise FirestoreError("upsert", 400, canonical_status="FAILED_PRECONDITION")

    store.upsert_document = rejected

    async def run():
        async with api(adapter) as client:
            for suffix, body in (("timer", {"action": "start"}), ("complete", {"completed": True})):
                result = await client.post("/api/tasks/test/" + suffix, json=body)
                assert result.status_code == 409 and result.json()["error"]["code"] == "task_conflict"
        assert not store.writes
    asyncio.run(run())


@pytest.mark.parametrize("paused", [False, True])
def test_normal_completion_finalizes_running_or_paused_timer(setup, paused):
    adapter, store, clock = setup

    async def run():
        await adapter.timer_todo("test", "start")
        clock[0] += 3200
        if paused:
            await adapter.timer_todo("test", "pause")
            clock[0] += 60000
        done = await adapter.complete_todo("test")
        assert done.spent_time_seconds == 3 and done.completed and done.timer is None
        assert not store.document["hasTimer"]
    asyncio.run(run())


def test_future_clock_and_native_twenty_hour_stop_cap(setup):
    adapter, _, clock = setup

    async def run():
        await adapter.timer_todo("test", "start")
        clock[0] -= 5000
        assert (await adapter.timer_todo("test", "pause")).timer.elapsed_seconds == 0
        await adapter.timer_todo("test", "start")
        clock[0] += 25 * 3600 * 1000
        assert (await adapter.timer_todo("test", "stop")).spent_time_seconds == 72000
    asyncio.run(run())


@pytest.mark.parametrize("fields", [
    {"hasTimer": True, "timer": None}, {"timer": {"startTime": None, "savedDuration": 0}},
    {"hasTimer": True, "timer": {"startTime": True, "savedDuration": 0}},
    {"hasTimer": True, "timer": {"startTime": NOW, "savedDuration": -1}},
    {"hasTimer": True, "timer": {"startTime": NOW, "savedDuration": True}},
    {"spentTime": True}, {"spentTime": -2},
])
def test_malformed_native_timer_fails_before_write(setup, fields):
    adapter, store, _ = setup
    store.document.update(fields)

    async def run():
        with pytest.raises(ValueError):
            await adapter.timer_todo("test", "start")
        assert not store.writes
    asyncio.run(run())


def test_http_timer_routes_and_conflicts_use_same_authenticated_adapter(setup):
    adapter, store, clock = setup

    async def run():
        async with api(adapter) as client:
            for action in ("pause", "stop"):
                assert (await client.post("/api/tasks/test/timer", json={"action": action})).status_code == 409
            start = await client.post("/api/tasks/test/timer", json={"action": "start"})
            assert start.status_code == 200
            assert start.json()["task"]["timer"] == {"startedAt": "2026-09-23T08:00:00Z", "elapsedSeconds": 0}
            clock[0] += 4100
            pause = await client.post("/api/tasks/test/timer", json={"action": "pause"})
            assert pause.json()["task"]["timer"] == {"startedAt": None, "elapsedSeconds": 4}
            clock[0] += 10000
            stop = await client.post("/api/tasks/test/timer", json={"action": "stop"})
            assert stop.json()["task"]["spentTimeSeconds"] == 4 and stop.json()["task"]["completed"]
            assert (await client.post("/api/tasks/test/timer", json={"action": "start"})).status_code == 409
            assert (await client.post("/api/tasks/missing/timer", json={"action": "start"})).status_code == 404
            await client.post("/api/tasks/test/complete", json={"completed": False})
            store.conflict = True
            result = await client.post("/api/tasks/test/timer", json={"action": "start"})
            assert result.status_code == 409 and result.json()["error"]["code"] == "task_conflict"
    asyncio.run(run())


def test_http_rejects_bad_timer_inputs_and_unconfirmed_results(setup):
    adapter, store, _ = setup

    async def run():
        async with api(adapter) as client:
            for value in ({}, {"action": "cancel"}, {"action": None}, {"action": []}, {"action": "start", "other": 1}, [], True):
                assert (await client.post("/api/tasks/test/timer", json=value)).status_code == 400
            assert (await client.post("/api/tasks/test/timer", content="{}" )).status_code == 415
            assert (await client.post("/api/tasks/test/timer", content=" " * 4097, headers={"Content-Type": "application/json"})).status_code == 413
        assert not store.writes

        async def unconfirmed(*_):
            return todo_from_document(store.document)
        adapter.timer_todo = unconfirmed
        async with api(adapter) as client:
            result = await client.post("/api/tasks/test/timer", json={"action": "start"})
            assert result.status_code == 502 and result.json()["error"]["code"] == "timer_unconfirmed"
    asyncio.run(run())


def test_mcp_timer_tool_uses_native_transitions_and_validates_actions(setup):
    adapter, store, clock = setup

    async def run():
        async with Client(create_server(adapter)) as client:
            assert not (await client.call_tool("set_todo_timer", {"todo_id": "test", "action": "start"})).is_error
            clock[0] += 2000
            assert not (await client.call_tool("set_todo_timer", {"todo_id": "test", "action": "pause"})).is_error
            assert not (await client.call_tool("set_todo_timer", {"todo_id": "test", "action": "stop"})).is_error
            assert (await client.call_tool("set_todo_timer", {"todo_id": "test", "action": "cancel"})).is_error
            assert (await client.call_tool("set_todo_timer", {"todo_id": "test", "action": "start"})).is_error
        assert store.document["spentTime"] == 2 and store.document["isDone"]
    asyncio.run(run())
