from datetime import date

import pytest
from pydantic import ValidationError

from todomate_mcp.models import Todo, todo_from_document


def test_todo_normalizes_firestore_fields():
    todo = todo_from_document({"id": "todo", "content": "write", "date": 1788566400000, "isDone": False, "goalID": "goal"})
    assert todo == Todo(id="todo", content="write", date=date(2026, 9, 5), completed=False, goal_id="goal")


def test_todo_rejects_missing_required_document_fields():
    with pytest.raises((ValidationError, ValueError)):
        todo_from_document({"id": "todo", "date": 0, "isDone": False})
