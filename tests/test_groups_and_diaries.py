import asyncio
from datetime import date

import pytest

from todomate_mcp.firestore import FirestoreError
from todomate_mcp.models import document_visibility
from todomate_mcp.todomate import RecordNotFoundError, TodoMateAdapter


class Auth:
    uid = "owner"


class MemoryFirestore:
    def __init__(self):
        self.documents = {"UserData/owner": {"followerIds": ["follower"]}}
        self.writes = []

    async def query_equal(self, collection, filters):
        return [dict(doc) for path, doc in self.documents.items() if path.startswith(collection + "/") and all(doc.get(k) == v for k, v in filters.items())]

    async def get_document(self, path):
        if path not in self.documents:
            raise FirestoreError("get", 404)
        return dict(self.documents[path])

    async def upsert_document(self, path, fields, *, update_mask=()):
        self.writes.append((path, dict(fields), list(update_mask)))
        self.documents[path] = self.documents.get(path, {}) | fields
        return dict(self.documents[path])

    async def delete_document(self, path):
        del self.documents[path]


def test_group_lifecycle_and_active_filter():
    async def run():
        store = MemoryFirestore()
        adapter = TodoMateAdapter(Auth(), store)
        group = await adapter.create_goal("First")
        assert group.status == "active" and group.visibility == "private"
        raw = store.documents[f"Goal/{group.id}"]
        assert raw["userID"] == "owner" and raw["viewerIDs"] == []
        assert raw["isViewerIDsFollowers"] is False
        assert isinstance(raw["priority"], int)
        assert (await adapter.set_goal_status(group.id, "stopped")).status == "stopped"
        assert await adapter.list_goals() == []
        assert len(await adapter.list_goals(include_finished=True)) == 1
        assert store.writes[-1][1:] == ({"finishType": 2}, ["finishType"])
        await adapter.set_goal_status(group.id, "active")
        assert len(await adapter.list_goals()) == 1
        await adapter.delete_goal(group.id)
        assert await adapter.list_goals() == []
    asyncio.run(run())


def test_nonempty_group_cannot_be_deleted_or_foreign_group_modified():
    async def run():
        store = MemoryFirestore()
        adapter = TodoMateAdapter(Auth(), store)
        group = await adapter.create_goal("Keep")
        store.documents["TodoItem/task"] = {"writerID": "owner", "goalID": group.id}
        with pytest.raises(ValueError, match="contains todos"):
            await adapter.delete_goal(group.id)
        assert f"Goal/{group.id}" in store.documents
        store.documents["Goal/foreign"] = {"id": "foreign", "userID": "someone-else"}
        with pytest.raises(RecordNotFoundError):
            await adapter.set_goal_status("foreign", "done")
        with pytest.raises(RecordNotFoundError):
            await adapter.delete_goal("foreign")
    asyncio.run(run())


def test_diary_lifecycle_visibility_and_duplicate_date_guard():
    async def run():
        store = MemoryFirestore()
        adapter = TodoMateAdapter(Auth(), store)
        diary = await adapter.create_diary("A day", "🙂", date(2026, 9, 17))
        assert diary.visibility == "private"
        assert store.documents[f"Diary/{diary.id}"]["viewerIDs"] == []
        assert (await adapter.list_diaries(date(2026, 9, 17)))[0].id == diary.id
        with pytest.raises(ValueError, match="already exists"):
            await adapter.create_diary("Duplicate", "🙂", date(2026, 9, 17))
        shared = await adapter.update_diary(diary.id, visibility="followers")
        assert shared.visibility == "followers"
        assert store.documents[f"Diary/{diary.id}"]["viewerIDs"] == ["follower"]
        edited = await adapter.update_diary(diary.id, body="Edited")
        assert edited.visibility == "followers" and edited.body == "Edited"
        assert store.writes[-1][1:] == ({"body": "Edited"}, ["body"])
        private = await adapter.update_diary(diary.id, visibility="private")
        assert private.visibility == "private"
        assert store.documents[f"Diary/{diary.id}"]["viewerIDs"] == []
        await adapter.delete_diary(diary.id)
        assert await adapter.list_diaries(date(2026, 9, 17)) == []
    asyncio.run(run())


def test_diary_ownership_and_update_validation():
    async def run():
        store = MemoryFirestore()
        adapter = TodoMateAdapter(Auth(), store)
        store.documents["Diary/foreign"] = {"id": "foreign", "writerID": "someone-else"}
        with pytest.raises(RecordNotFoundError):
            await adapter.update_diary("foreign", body="Overwrite")
        with pytest.raises(RecordNotFoundError):
            await adapter.delete_diary("foreign")
        diary = await adapter.create_diary("A day", "🙂", date(2026, 9, 17))
        with pytest.raises(ValueError, match="At least one"):
            await adapter.update_diary(diary.id)
        with pytest.raises(ValueError, match="Visibility"):
            await adapter.update_diary(diary.id, visibility="invalid")
    asyncio.run(run())


def test_nonpublic_documents_are_not_always_private():
    assert document_visibility({"isPublic": False, "isViewerIDsFollowers": True}) == "followers"
    assert document_visibility({"isPublic": False, "viewerIDs": ["person"]}) == "selected"
    assert document_visibility({"isPublic": False, "viewerIDs": []}) == "private"
    assert document_visibility({"isPublic": True}) == "public"
