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
        }
        self.writes = []

    async def query_equal(self, collection, filters):
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
