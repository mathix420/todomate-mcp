"""TodoMate domain operations backed by Firestore documents."""

from datetime import date, datetime, time, timezone
import secrets
import string
from typing import Any

from .firebase_auth import FirebaseAuthSession
from .firestore import FirestoreClient, FirestoreError, JsonValue
from .models import Todo, todo_from_document


class TodoNotFoundError(LookupError):
    pass


class TodoMateAdapter:
    def __init__(self, auth: FirebaseAuthSession, firestore: FirestoreClient):
        self._auth = auth
        self._firestore = firestore

    async def list_todos(self, day: date) -> list[Todo]:
        todos = await self._firestore.query_equal(
            "TodoItem", {"writerID": self._auth.uid, "date": _day_millis(day)}
        )
        return [todo_from_document(todo) for todo in sorted(todos, key=lambda todo: _number(todo.get("createTime")))]

    async def get_todo(self, todo_id: str) -> Todo:
        return todo_from_document(await self._owned(todo_id), fallback_id=todo_id)

    async def create_todo(self, content: str, day: date, goal_id: str | None = None) -> Todo:
        content = _required(content, "content")
        if goal_id is not None:
            goal_id = _required(goal_id, "goal_id")
        uid, now = self._auth.uid, _now_millis()
        todo_id = f"{uid}{''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(20))}"
        return todo_from_document(await self._firestore.upsert_document(
            f"TodoItem/{todo_id}",
            {
                "id": todo_id,
                "writerID": uid,
                "content": content,
                "date": _day_millis(day),
                "createTime": now,
                "isDone": False,
                "doneTime": None,
                "goalID": goal_id,
                "remindAt": None,
                "spentTime": None,
                "hasPhoto": False,
                "hasTimer": False,
                "isMemoPublic": False,
                "likes": None,
                "likesTotalCount": 0,
                "likesTotalSenderIDs": None,
                "memo": None,
                "photoURL": None,
                "routineID": None,
                "timer": None,
            },
        ), fallback_id=todo_id)

    async def update_todo(
        self, todo_id: str, *, content: str | None = None, day: date | None = None, goal_id: str | None = None
    ) -> Todo:
        await self._owned(todo_id)
        fields: dict[str, JsonValue] = {}
        if content is not None:
            fields["content"] = _required(content, "content")
        if day is not None:
            fields["date"] = _day_millis(day)
        if goal_id is not None:
            fields["goalID"] = _required(goal_id, "goal_id")
        if not fields:
            raise ValueError("At least one Todo field is required")
        document = await self._firestore.upsert_document(f"TodoItem/{todo_id}", fields, update_mask=list(fields))
        return todo_from_document(document, fallback_id=todo_id)

    async def complete_todo(self, todo_id: str, completed: bool = True) -> Todo:
        await self._owned(todo_id)
        fields: dict[str, JsonValue] = {"isDone": completed}
        if completed:
            fields["doneTime"] = _now_millis()
        document = await self._firestore.upsert_document(f"TodoItem/{todo_id}", fields, update_mask=list(fields))
        return todo_from_document(document, fallback_id=todo_id)

    async def delete_todo(self, todo_id: str) -> None:
        await self._owned(todo_id)
        await self._firestore.delete_document(f"TodoItem/{todo_id}")

    async def _owned(self, todo_id: str) -> dict[str, JsonValue]:
        if not todo_id or "/" in todo_id:
            raise ValueError("Todo ID is required")
        try:
            todo = await self._firestore.get_document(f"TodoItem/{todo_id}")
        except FirestoreError as error:
            if error.status_code == 404:
                raise TodoNotFoundError(todo_id) from None
            raise
        if todo.get("writerID") != self._auth.uid:
            raise TodoNotFoundError(todo_id)
        return todo


def _day_millis(day: date) -> int:
    if not isinstance(day, date):
        raise TypeError("Todo date must be a date")
    return int(datetime.combine(day, time.min, timezone.utc).timestamp() * 1000)


def _now_millis() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _required(value: str, name: str) -> str:
    if not isinstance(value, str) or not (result := value.strip()):
        raise ValueError(f"Todo {name} is required")
    return result


def _number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0
