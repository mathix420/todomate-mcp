from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from todomate_mcp.models import Todo, todo_from_document


def test_todo_normalizes_firestore_fields():
    todo = todo_from_document({"id": "todo", "content": "write", "date": 1788566400000, "isDone": False, "goalID": "goal"})
    assert todo == Todo(id="todo", content="write", date=date(2026, 9, 5), completed=False, goal_id="goal")


def test_todo_rejects_missing_required_document_fields():
    with pytest.raises((ValidationError, ValueError)):
        todo_from_document({"id": "todo", "date": 0, "isDone": False})


def test_undated_todo_and_memo_are_preserved():
    todo = todo_from_document({"id": "todo", "content": "later", "date": None, "isDone": False, "memo": "a note", "isMemoPublic": True})
    assert todo.date is None
    assert todo.memo == "a note"
    assert todo.memo_public is True


def test_null_memo_visibility_is_private():
    todo = todo_from_document({"id": "todo", "content": "later", "date": None, "isDone": False, "isMemoPublic": None})
    assert todo.memo is None
    assert todo.memo_public is False


@pytest.mark.parametrize("reminder, expected", [
    (None, None),
    (0, datetime(1970, 1, 1, tzinfo=timezone.utc)),
    (1789714800123, datetime(2026, 9, 18, 7, 0, 0, 123000, tzinfo=timezone.utc)),
])
def test_reminder_is_decoded_from_milliseconds(reminder, expected):
    todo = todo_from_document({"id": "todo", "content": "alarm", "date": None, "isDone": False, "remindAt": reminder})
    assert todo.remind_at == expected


@pytest.mark.parametrize("reminder", [True, "1789714800000", 1789714800000.0, {}])
def test_invalid_reminder_storage_is_rejected(reminder):
    with pytest.raises(ValueError, match="invalid reminder"):
        todo_from_document({"id": "todo", "content": "alarm", "date": None, "isDone": False, "remindAt": reminder})
