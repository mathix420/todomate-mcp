"""TodoMate domain operations backed by Firestore documents."""

from datetime import date, datetime, time, timedelta, timezone
import secrets
import string
from typing import Any, Literal

from .firebase_auth import FirebaseAuthSession
from .firestore import FirestoreClient, FirestoreError, JsonValue
from .models import Diary, Goal, Todo, diary_from_document, goal_from_document, todo_from_document


class RecordNotFoundError(LookupError):
    pass


class TodoNotFoundError(RecordNotFoundError):
    pass


class TaskConflictError(Exception):
    """A stale write or an action incompatible with the current task state."""


class TodoMateAdapter:
    def __init__(self, auth: FirebaseAuthSession, firestore: FirestoreClient):
        self._auth = auth
        self._firestore = firestore

    async def list_goals(self, include_finished: bool = False) -> list[Goal]:
        goals = await self._firestore.query_equal("Goal", {"userID": self._auth.uid})
        return [
            goal_from_document(goal)
            for goal in sorted(goals, key=lambda goal: _number(goal.get("priority")))
            if include_finished or goal.get("finishType") is None
        ]

    async def _visibility_fields(self, visibility: str) -> dict[str, JsonValue]:
        if visibility not in {"private", "followers", "public"}:
            raise ValueError("Visibility must be private, followers, or public")
        viewers = []
        if visibility == "followers":
            user = await self._firestore.get_document(f"UserData/{self._auth.uid}")
            viewers = user.get("followerIds") or []
            if not isinstance(viewers, list) or not all(isinstance(value, str) for value in viewers):
                raise ValueError("Account follower IDs are invalid")
        return {"isPublic": visibility == "public", "viewerIDs": viewers, "isViewerIDsFollowers": visibility == "followers"}

    async def create_goal(self, title: str, color: int = 4294929858, visibility: str = "private") -> Goal:
        title = _required(title, "title")
        if isinstance(color, bool) or not isinstance(color, int) or not 0 <= color <= 0xFFFFFFFF:
            raise ValueError("Goal color must be an unsigned ARGB integer")
        goals = await self._firestore.query_equal("Goal", {"userID": self._auth.uid})
        goal_id = _random_id()
        fields = {
            "id": goal_id, "userID": self._auth.uid, "title": title, "color": color,
            "createTime": _now_millis(), "priority": int(min((_number(g.get("priority")) for g in goals), default=0)) - 1,
            "finishType": None, "crewId": None, **(await self._visibility_fields(visibility)),
        }
        return goal_from_document(await self._firestore.upsert_document(f"Goal/{goal_id}", fields))

    async def set_goal_status(self, goal_id: str, status: str) -> Goal:
        statuses = {"active": None, "done": 0, "ended": 1, "stopped": 2}
        if status not in statuses:
            raise ValueError("Unknown goal status")
        await self._owned_record("Goal", goal_id, "userID")
        return goal_from_document(await self._firestore.upsert_document(
            f"Goal/{goal_id}", {"finishType": statuses[status]}, update_mask=["finishType"]
        ))

    async def delete_goal(self, goal_id: str) -> None:
        await self._owned_record("Goal", goal_id, "userID")
        todos = await self._firestore.query_equal("TodoItem", {"writerID": self._auth.uid, "goalID": goal_id})
        if todos:
            raise ValueError("Group contains todos; move or delete them explicitly before deleting the group")
        await self._firestore.delete_document(f"Goal/{goal_id}")

    async def list_diaries(self, day: date) -> list[Diary]:
        documents = await self._firestore.query_equal("Diary", {"writerID": self._auth.uid, "date": _day_millis(day)})
        return [diary_from_document(document) for document in documents]

    async def create_diary(self, body: str, emoji: str, day: date, visibility: str = "private") -> Diary:
        body, emoji = _required(body, "body"), _required(emoji, "emoji")
        if await self.list_diaries(day):
            raise ValueError("A diary already exists for this date; use update_diary")
        diary_id = _random_id()
        fields = {
            "id": diary_id, "writerID": self._auth.uid, "date": _day_millis(day), "body": body, "emoji": emoji,
            "createTime": _now_millis(), "sticker": None, "color": None, "imageURL": None, "temperature": None,
            "likes": None, "likesTotalCount": 0, "likesTotalSenderIDs": None, "isDraft": False,
            **(await self._visibility_fields(visibility)),
        }
        return diary_from_document(await self._firestore.upsert_document(f"Diary/{diary_id}", fields))

    async def update_diary(self, diary_id: str, *, body: str | None = None, emoji: str | None = None, visibility: str | None = None) -> Diary:
        await self._owned_record("Diary", diary_id, "writerID")
        fields = {}
        if body is not None:
            fields["body"] = _required(body, "body")
        if emoji is not None:
            fields["emoji"] = _required(emoji, "emoji")
        if visibility is not None:
            fields.update(await self._visibility_fields(visibility))
        if not fields:
            raise ValueError("At least one diary field is required")
        return diary_from_document(await self._firestore.upsert_document(f"Diary/{diary_id}", fields, update_mask=list(fields)))

    async def delete_diary(self, diary_id: str) -> None:
        await self._owned_record("Diary", diary_id, "writerID")
        await self._firestore.delete_document(f"Diary/{diary_id}")

    async def list_todos(self, day: date | None) -> list[Todo]:
        todos = await self._firestore.query_equal(
            "TodoItem", {"writerID": self._auth.uid, "date": _day_millis(day) if day is not None else None}
        )
        return [todo_from_document(todo) for todo in sorted(todos, key=lambda todo: _number(todo.get("createTime")))]

    async def get_todo(self, todo_id: str) -> Todo:
        return todo_from_document(await self._owned(todo_id), fallback_id=todo_id)

    async def create_todo(self, content: str, day: date, goal_id: str) -> Todo:
        content = _required(content, "content")
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

    async def schedule_todo(self, todo_id: str, day: date | None) -> Todo:
        await self._owned(todo_id)
        document = await self._firestore.upsert_document(
            f"TodoItem/{todo_id}", {"date": _day_millis(day) if day is not None else None}, update_mask=["date"]
        )
        return todo_from_document(document, fallback_id=todo_id)

    async def set_todo_reminder(self, todo_id: str, remind_at: datetime | None) -> Todo:
        millis = None
        if remind_at is not None:
            if not isinstance(remind_at, datetime) or remind_at.utcoffset() is None:
                raise ValueError("Reminder must be a datetime with a timezone offset")
            millis = (remind_at.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)) // timedelta(milliseconds=1)
        await self._owned(todo_id)
        document = await self._firestore.upsert_document(
            f"TodoItem/{todo_id}", {"remindAt": millis}, update_mask=["remindAt"]
        )
        return todo_from_document(document, fallback_id=todo_id)

    async def set_todo_memo(self, todo_id: str, memo: str | None, public: bool = False) -> Todo:
        await self._owned(todo_id)
        fields = {"memo": memo, "isMemoPublic": public if memo is not None else False}
        document = await self._firestore.upsert_document(f"TodoItem/{todo_id}", fields, update_mask=list(fields))
        return todo_from_document(document, fallback_id=todo_id)

    async def complete_todo(self, todo_id: str, completed: bool = True) -> Todo:
        if type(completed) is not bool:
            raise ValueError("completed must be a boolean")
        document, version = await self._owned_versioned(todo_id)
        todo = todo_from_document(document, fallback_id=todo_id)
        if todo.completed == completed and (not completed or todo.timer is None):
            return todo
        now = _now_millis()
        fields: dict[str, JsonValue] = {"isDone": completed, "doneTime": now if completed else None}
        if completed and todo.timer is not None:
            fields.update(self._stop_fields(document, now))
        return await self._change_task(todo_id, fields, list(fields), version)

    async def timer_todo(self, todo_id: str, action: Literal["start", "pause", "stop"]) -> Todo:
        """Apply the native timer transition; stopping also completes the task."""
        if action not in {"start", "pause", "stop"}:
            raise ValueError("Unknown timer action")
        document, version = await self._owned_versioned(todo_id)
        todo = todo_from_document(document, fallback_id=todo_id)
        timer = todo.timer
        if action == "stop" and todo.completed and timer is None:
            return todo
        if todo.completed:
            raise TaskConflictError("Reopen the task before changing its timer")
        now = _now_millis()
        if action == "start":
            if timer is not None and timer.started_at is not None:
                return todo
            fields: dict[str, JsonValue] = {
                "hasTimer": True,
                "timer": {
                    "startTime": now,
                    "savedDuration": timer.elapsed_seconds if timer else (todo.spent_time_seconds or 0),
                    "todoItemId": todo_id,
                    "todoItemContent": todo.content,
                },
            }
            mask = ["hasTimer", "timer.savedDuration", "timer.startTime", "timer.todoItemContent", "timer.todoItemId"]
        else:
            if timer is None:
                raise TaskConflictError("This task has no timer")
            if action == "pause":
                if timer.started_at is None:
                    return todo
                fields = {"timer": {"startTime": None, "savedDuration": self._elapsed(document, now)}}
                mask = ["timer.savedDuration", "timer.startTime"]
            else:
                fields = self._stop_fields(document, now)
                mask = ["doneTime", "hasTimer", "isDone", "spentTime", "timer"]
        return await self._change_task(todo_id, fields, mask, version)

    @staticmethod
    def _elapsed(document: dict[str, JsonValue], now: int) -> int:
        # The model was validated before this helper. Native startTime uses ms;
        # savedDuration/spentTime use whole seconds. Paused time never accrues.
        timer = document["timer"]
        start = timer["startTime"]
        return timer["savedDuration"] + (max(0, now - start) // 1000 if start is not None else 0)

    @classmethod
    def _stop_fields(cls, document: dict[str, JsonValue], now: int) -> dict[str, JsonValue]:
        return {"isDone": True, "doneTime": now, "spentTime": min(72000, cls._elapsed(document, now)),
                "timer": None, "hasTimer": False}

    async def _change_task(self, todo_id: str, fields: dict[str, JsonValue], mask: list[str], version: str) -> Todo:
        try:
            document = await self._firestore.upsert_document(
                f"TodoItem/{todo_id}", fields, update_mask=mask, update_time=version,
            )
        except FirestoreError as error:
            if error.status_code in {409, 412}:
                raise TaskConflictError("Task changed on another device; refresh before retrying") from None
            if error.status_code == 404:
                raise TodoNotFoundError(todo_id) from None
            raise
        return todo_from_document(document, fallback_id=todo_id)

    async def _owned_versioned(self, todo_id: str) -> tuple[dict[str, JsonValue], str]:
        if not todo_id or "/" in todo_id:
            raise ValueError("A valid document ID is required")
        try:
            document, version = await self._firestore.get_document_versioned(f"TodoItem/{todo_id}")
        except FirestoreError as error:
            if error.status_code == 404:
                raise TodoNotFoundError(todo_id) from None
            raise
        if document.get("writerID") != self._auth.uid:
            raise TodoNotFoundError(todo_id)
        return document, version

    async def delete_todo(self, todo_id: str) -> None:
        await self._owned(todo_id)
        await self._firestore.delete_document(f"TodoItem/{todo_id}")

    async def _owned(self, todo_id: str) -> dict[str, JsonValue]:
        try:
            return await self._owned_record("TodoItem", todo_id, "writerID")
        except RecordNotFoundError:
            raise TodoNotFoundError(todo_id) from None

    async def _owned_record(self, collection: str, record_id: str, owner_field: str) -> dict[str, JsonValue]:
        if not record_id or "/" in record_id:
            raise ValueError("A valid document ID is required")
        try:
            document = await self._firestore.get_document(f"{collection}/{record_id}")
        except FirestoreError as error:
            if error.status_code == 404:
                raise RecordNotFoundError(record_id) from None
            raise
        if document.get(owner_field) != self._auth.uid:
            raise RecordNotFoundError(record_id)
        return document


def _random_id() -> str:
    return ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(20))


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
