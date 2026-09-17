"""Check a running MCP server, optionally including a real read of today's todos."""

import argparse
import asyncio
import os

import httpx
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


async def check(url: str, token: str, require_credentials: bool) -> None:
    async with httpx.AsyncClient(timeout=15) as http:
        health = await http.get(url.removesuffix("/mcp") + "/healthz")
        assert health.status_code == 200 and health.json() == {"status": "ok"}
        for headers in ({}, {"Authorization": "Bearer invalid-token"}):
            assert (await http.post(url, json={}, headers=headers)).status_code == 401
    print("PASS: health check and rejection of missing/invalid bearer tokens")

    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as http:
        async with Client(streamable_http_client(url, http_client=http)) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            assert names == {"list_todos", "get_todo", "create_todo", "update_todo", "complete_todo", "delete_todo"}
            print("PASS: MCP initialization and discovery of all six tools")
            if require_credentials:
                result = await client.call_tool("list_todos")
                if result.is_error:
                    raise RuntimeError("list_todos failed; check TodoMate login and server logs")
                print("PASS: authenticated list_todos call (todo contents not printed)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000/mcp")
    parser.add_argument("--require-credentials", action="store_true")
    args = parser.parse_args()
    token = os.environ.get("TODOMATE_MCP_ACCESS_TOKEN")
    if not token:
        parser.error("Set TODOMATE_MCP_ACCESS_TOKEN in the environment")
    asyncio.run(check(args.url, token, args.require_credentials))
