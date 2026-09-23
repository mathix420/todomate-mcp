"""Check HTTP/MCP authentication, optionally reading the connected account's tasks."""

import argparse
import asyncio
import os

import httpx
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


async def check(url: str, token: str, require_credentials: bool) -> None:
    api_url = url.removesuffix("/mcp") + "/api/tasks"
    async with httpx.AsyncClient(timeout=15) as http:
        health = await http.get(url.removesuffix("/mcp") + "/healthz")
        assert health.status_code == 200 and health.json() == {"status": "ok"}
        for headers in ({}, {"Authorization": "Bearer invalid-token"}):
            assert (await http.post(url, json={}, headers=headers)).status_code == 401
            assert (await http.get(api_url, headers=headers)).status_code == 401
            assert (await http.post(api_url + "/unauthenticated-check/complete", json={"completed": True}, headers=headers)).status_code == 401
            assert (await http.post(api_url + "/unauthenticated-check/timer", json={"action": "start"}, headers=headers)).status_code == 401
        tasks = await http.get(api_url, headers={"Authorization": f"Bearer {token}"})
        assert tasks.status_code in (200, 503)
        assert tasks.headers["cache-control"] == "no-store"
        if require_credentials:
            assert tasks.status_code == 200, "Task API failed; check TodoMate login and server logs"
        if tasks.status_code == 200:
            assert set(tasks.json()) == {"tasks", "goals", "date", "timezone"}
            assert isinstance(tasks.json()["tasks"], list) and isinstance(tasks.json()["goals"], list)
        else:
            assert tasks.json()["error"]["code"] in {"not_connected", "reauthentication_required"}
    print("PASS: health check, task API, and HTTP/MCP rejection of missing/invalid bearer tokens")

    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as http:
        async with Client(streamable_http_client(url, http_client=http)) as client:
            tools = (await client.list_tools()).tools
            names = {tool.name for tool in tools}
            assert names == {'list_goals', 'create_goal', 'set_goal_status', 'delete_goal', 'list_diaries', 'create_diary', 'update_diary', 'delete_diary', 'list_todos', 'get_todo', 'create_todo', 'update_todo', 'schedule_todo', 'set_todo_memo', 'set_todo_reminder', 'set_todo_timer', 'complete_todo', 'delete_todo'}
            reminder = next(tool for tool in tools if tool.name == "set_todo_reminder")
            assert set(reminder.input_schema["required"]) == {"todo_id", "remind_at"}
            assert {part["type"] for part in reminder.input_schema["properties"]["remind_at"]["anyOf"]} == {"string", "null"}
            timer = next(tool for tool in tools if tool.name == "set_todo_timer")
            assert set(timer.input_schema["required"]) == {"todo_id", "action"}
            assert set(timer.input_schema["properties"]["action"]["enum"]) == {"start", "pause", "stop"}
            print("PASS: MCP initialization and discovery of all 18 tools, including reminder and timer schemas")
            if require_credentials:
                goals = await client.call_tool("list_goals")
                if goals.is_error:
                    raise RuntimeError("list_goals failed; check TodoMate login and server logs")
                result = await client.call_tool("list_todos")
                if result.is_error:
                    raise RuntimeError("list_todos failed; check TodoMate login and server logs")
                print("PASS: authenticated list_goals and list_todos calls (contents not printed)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000/mcp")
    parser.add_argument("--require-credentials", action="store_true")
    args = parser.parse_args()
    token = os.environ.get("TODOMATE_MCP_ACCESS_TOKEN")
    if not token:
        parser.error("Set TODOMATE_MCP_ACCESS_TOKEN in the environment")
    asyncio.run(check(args.url, token, args.require_credentials))
