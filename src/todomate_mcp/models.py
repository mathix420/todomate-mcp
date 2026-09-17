"""Public TodoMate domain models."""

from datetime import date, datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .firestore import JsonValue


class Todo(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    date: date
    completed: bool
    goal_id: str | None = None


def todo_from_document(document: dict[str, JsonValue], *, fallback_id: str | None = None) -> Todo:
    todo_id = document.get("id", fallback_id)
    millis = document.get("date")
    if not isinstance(millis, int) or isinstance(millis, bool):
        raise ValueError("Todo document has an invalid date")
    return Todo.model_validate(
        {
            "id": todo_id,
            "content": document.get("content"),
            "date": datetime.fromtimestamp(millis / 1000, timezone.utc).date(),
            "completed": document.get("isDone"),
            "goal_id": document.get("goalID"),
        }
    )
