"""Public TodoMate domain models."""

from datetime import date, datetime, timezone
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from .firestore import JsonValue


class Goal(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    title: str
    status: str = "active"
    visibility: str = "private"
    color: int | None = None


class Diary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    date: date
    body: str
    emoji: str | None = None
    visibility: str


def document_visibility(document: dict[str, JsonValue]) -> str:
    if document.get("isPublic") is True:
        return "public"
    if document.get("isViewerIDsFollowers") is True:
        return "followers"
    return "selected" if document.get("viewerIDs") else "private"


def goal_from_document(document: dict[str, JsonValue]) -> Goal:
    return Goal.model_validate({
        "id": document.get("id"), "title": document.get("title"), "color": document.get("color"),
        "status": {None: "active", 0: "done", 1: "ended", 2: "stopped"}.get(document.get("finishType"), "unknown"),
        "visibility": document_visibility(document),
    })


def diary_from_document(document: dict[str, JsonValue]) -> Diary:
    millis = document.get("date")
    if not isinstance(millis, int) or isinstance(millis, bool):
        raise ValueError("Diary document has an invalid date")
    return Diary.model_validate({
        "id": document.get("id"), "body": document.get("body"), "emoji": document.get("emoji"),
        "date": datetime.fromtimestamp(millis / 1000, timezone.utc).date(),
        "visibility": document_visibility(document),
    })


class TodoTimer(BaseModel):
    model_config = ConfigDict(frozen=True)

    started_at: AwareDatetime | None
    elapsed_seconds: int = Field(strict=True, ge=0)


class Todo(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    date: date | None
    completed: bool
    goal_id: str | None = None
    memo: str | None = None
    memo_public: bool = False
    remind_at: AwareDatetime | None = None
    timer: TodoTimer | None = None
    spent_time_seconds: int | None = Field(default=None, strict=True, ge=0)


def timer_from_document(document: dict[str, JsonValue]) -> TodoTimer | None:
    if document.get("hasTimer") is not True:
        if document.get("timer") is not None:
            raise ValueError("Todo document has inconsistent timer state")
        return None
    raw = document.get("timer")
    if not isinstance(raw, dict) or "startTime" not in raw:
        raise ValueError("Todo document has an invalid timer")
    start = raw["startTime"]
    saved = raw.get("savedDuration")
    if start is not None and (type(start) is not int or start < 0):
        raise ValueError("Todo document has an invalid timer start")
    if type(saved) is not int or saved < 0:
        raise ValueError("Todo document has an invalid timer duration")
    return TodoTimer(
        started_at=datetime.fromtimestamp(start / 1000, timezone.utc) if start is not None else None,
        elapsed_seconds=saved,
    )


def todo_from_document(document: dict[str, JsonValue], *, fallback_id: str | None = None) -> Todo:
    todo_id = document.get("id", fallback_id)
    millis = document.get("date")
    if millis is not None and (not isinstance(millis, int) or isinstance(millis, bool)):
        raise ValueError("Todo document has an invalid date")
    reminder = document.get("remindAt")
    if reminder is not None and (not isinstance(reminder, int) or isinstance(reminder, bool)):
        raise ValueError("Todo document has an invalid reminder")
    return Todo.model_validate(
        {
            "id": todo_id,
            "content": document.get("content"),
            "date": datetime.fromtimestamp(millis / 1000, timezone.utc).date() if millis is not None else None,
            "completed": document.get("isDone"),
            "goal_id": document.get("goalID"),
            "memo": document.get("memo"),
            "memo_public": document.get("isMemoPublic") is True,
            "remind_at": datetime.fromtimestamp(reminder / 1000, timezone.utc) if reminder is not None else None,
            "timer": timer_from_document(document),
            "spent_time_seconds": document.get("spentTime"),
        }
    )
