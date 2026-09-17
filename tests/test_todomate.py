import asyncio
from datetime import date

import pytest

from todomate_mcp.firestore import FirestoreError
from todomate_mcp.todomate import TodoMateAdapter, TodoNotFoundError


class Auth:
    uid = "user"


class Firestore:
    def __init__(self):
        self.documents = {
            "TodoItem/one": {"id": "one", "writerID": "user", "content": "first", "date": 1788566400000, "isDone": False, "goalID": "goal", "createTime": 2},
            "TodoItem/two": {"id": "two", "writerID": "user", "content": "second", "date": 1788566400000, "isDone": False, "goalID": "goal", "createTime": 1},
            "TodoItem/other": {"id": "other", "writerID": "other"},
            "Goal/goal": {"id": "goal", "userID": "user", "title": "Personal", "priority": 1},
            "Goal/work": {"id": "work", "userID": "user", "title": "Work", "priority": 0},
            "Goal/other": {"id": "other", "userID": "other", "title": "Private", "priority": 0},
        }
        self.writes = []

    async def query_equal(self, collection, filters):
        if collection == "Goal":
            assert filters == {"userID": "user"}
            return [doc for path, doc in self.documents.items() if path.startswith("Goal/") and doc["userID"] == filters["userID"]]
        assert collection == "TodoItem"
        assert filters["writerID"] == "user"
        return [self.documents["TodoItem/one"], self.documents["TodoItem/two"]]

    async def get_document(self, path):
        if path not in self.documents:
            raise FirestoreError("get", 404)
        return self.documents[path]

    async def upsert_document(self, path, fields, *, update_mask=()):
        self.writes.append((path, fields, update_mask))
        return self.documents.get(path, {}) | fields | {"id": path.split("/")[1]}

    async def delete_document(self, path):
        self.writes.append((path, None, ()))


def test_todo_crud_maps_fields_validates_ownership_and_sorts_list():
    async def run():
        firestore = Firestore()
        adapter = TodoMateAdapter(Auth(), firestore)
        assert [todo.id for todo in await adapter.list_todos(date(2026, 9, 5))] == ["two", "one"]
        created = await adapter.create_todo("  write tests ", date(2026, 9, 5), "goal")
        assert created.content == "write tests"
        assert created.goal_id == "goal"
        assert len(created.id) == len("user") + 20
        updated = await adapter.update_todo("one", content="changed")
        assert updated.content == "changed"
        done = await adapter.complete_todo("one")
        assert done.completed is True
        await adapter.delete_todo("one")
        with pytest.raises(TodoNotFoundError):
            await adapter.get_todo("other")
        with pytest.raises(TodoNotFoundError):
            await adapter.get_todo("missing")
    asyncio.run(run())


def test_list_goals_uses_goal_ownership_field_and_display_order():
    async def run():
        adapter = TodoMateAdapter(Auth(), Firestore())
        goals = await adapter.list_goals()
        assert [(goal.id, goal.title) for goal in goals] == [("work", "Work"), ("goal", "Personal")]
    asyncio.run(run())


@pytest.mark.parametrize("goal_id", [None, "", "   "])
def test_create_rejects_missing_group_before_firestore_write(goal_id):
    async def run():
        firestore = Firestore()
        adapter = TodoMateAdapter(Auth(), firestore)
        with pytest.raises(ValueError, match="goal_id is required"):
            await adapter.create_todo("Test todo", date(2026, 9, 17), goal_id)
        assert firestore.writes == []
    asyncio.run(run())


def test_schedule_and_unschedule_only_change_the_date():
    async def run():
        firestore = Firestore()
        adapter = TodoMateAdapter(Auth(), firestore)
        undated = await adapter.schedule_todo("one", None)
        assert undated.date is None
        assert firestore.writes[-1] == ("TodoItem/one", {"date": None}, ["date"])
        scheduled = await adapter.schedule_todo("one", date(2026, 9, 5))
        assert scheduled.date == date(2026, 9, 5)
        assert firestore.writes[-1] == ("TodoItem/one", {"date": 1788566400000}, ["date"])
        with pytest.raises(TodoNotFoundError):
            await adapter.schedule_todo("other", None)
    asyncio.run(run())


def test_unscheduled_query_keeps_ownership_filter():
    class UndatedFirestore:
        async def query_equal(self, collection, filters):
            assert collection == "TodoItem"
            assert filters == {"writerID": "user", "date": None}
            return [{"id": "later", "content": "later", "date": None, "isDone": False}]

    async def run():
        todos = await TodoMateAdapter(Auth(), UndatedFirestore()).list_todos(None)
        assert len(todos) == 1 and todos[0].date is None
    asyncio.run(run())


def test_memo_updates_include_visibility_and_enforce_ownership():
    async def run():
        firestore = Firestore()
        adapter = TodoMateAdapter(Auth(), firestore)
        private = await adapter.set_todo_memo("one", "private note")
        assert private.memo == "private note" and private.memo_public is False
        assert firestore.writes[-1] == ("TodoItem/one", {"memo": "private note", "isMemoPublic": False}, ["memo", "isMemoPublic"])
        public = await adapter.set_todo_memo("one", "public note", True)
        assert public.memo_public is True
        cleared = await adapter.set_todo_memo("one", None, True)
        assert cleared.memo is None and cleared.memo_public is False
        with pytest.raises(TodoNotFoundError):
            await adapter.set_todo_memo("other", "not mine")
    asyncio.run(run())
