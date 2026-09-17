"""TodoMate MCP server entry point."""

import argparse
import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from getpass import getpass
from pathlib import Path
import sys
from typing import Any

import httpx
import uvicorn

from .auth.credentials import Credential, CredentialStore, FileCredentialStore, KeyringCredentialStore
from .firebase_auth import AuthenticationError, FirebaseAuthSession
from .firestore import FirestoreClient
from .settings import load_environment, load_firebase_api_key
from .todomate import TodoMateAdapter
from .tools import create_http_app, create_server


class _ConfiguredAdapter:
    def __init__(
        self,
        auth: FirebaseAuthSession,
        adapter: TodoMateAdapter,
        authenticate: Callable[[], Awaitable[None]],
        credential_store: CredentialStore,
    ):
        self._auth = auth
        self._adapter = adapter
        self._authenticate: Callable[[], Awaitable[None]] | None = authenticate
        self._credential_store = credential_store
        self._login_lock = asyncio.Lock()

    async def list_goals(self, include_finished: bool = False) -> Any:
        return await self._call(lambda: self._adapter.list_goals(include_finished))

    async def create_goal(self, title: str, color: int, visibility: str) -> Any:
        return await self._call(lambda: self._adapter.create_goal(title, color, visibility))

    async def set_goal_status(self, goal_id: str, status: str) -> Any:
        return await self._call(lambda: self._adapter.set_goal_status(goal_id, status))

    async def delete_goal(self, goal_id: str) -> None:
        await self._call(lambda: self._adapter.delete_goal(goal_id))

    async def list_diaries(self, day: date) -> Any:
        return await self._call(lambda: self._adapter.list_diaries(day))

    async def create_diary(self, body: str, emoji: str, day: date, visibility: str) -> Any:
        return await self._call(lambda: self._adapter.create_diary(body, emoji, day, visibility))

    async def update_diary(self, diary_id: str, **fields: Any) -> Any:
        return await self._call(lambda: self._adapter.update_diary(diary_id, **fields))

    async def delete_diary(self, diary_id: str) -> None:
        await self._call(lambda: self._adapter.delete_diary(diary_id))

    async def list_todos(self, day: date | None) -> Any:
        return await self._call(lambda: self._adapter.list_todos(day))

    async def get_todo(self, todo_id: str) -> Any:
        return await self._call(lambda: self._adapter.get_todo(todo_id))

    async def create_todo(self, content: str, day: date, goal_id: str) -> Any:
        return await self._call(lambda: self._adapter.create_todo(content, day, goal_id))

    async def update_todo(self, todo_id: str, **fields: Any) -> Any:
        return await self._call(lambda: self._adapter.update_todo(todo_id, **fields))

    async def complete_todo(self, todo_id: str, completed: bool) -> Any:
        return await self._call(lambda: self._adapter.complete_todo(todo_id, completed))

    async def schedule_todo(self, todo_id: str, day: date | None) -> Any:
        return await self._call(lambda: self._adapter.schedule_todo(todo_id, day))

    async def set_todo_memo(self, todo_id: str, memo: str | None, public: bool) -> Any:
        return await self._call(lambda: self._adapter.set_todo_memo(todo_id, memo, public))

    async def set_todo_reminder(self, todo_id: str, remind_at: datetime | None) -> Any:
        return await self._call(lambda: self._adapter.set_todo_reminder(todo_id, remind_at))

    async def delete_todo(self, todo_id: str) -> None:
        await self._call(lambda: self._adapter.delete_todo(todo_id))

    async def _call(self, operation: Callable[[], Awaitable[Any]]) -> Any:
        if self._authenticate is not None:
            async with self._login_lock:
                if self._authenticate is not None:
                    try:
                        await self._authenticate()
                    except AuthenticationError as error:
                        self._delete_rejected_credential(error)
                        raise _reauthentication_error(error) from None
                    self._authenticate = None
                    self._save_credential()
        try:
            result = await operation()
        except AuthenticationError as error:
            self._delete_rejected_credential(error)
            raise _reauthentication_error(error) from None
        self._save_credential()
        return result

    def _save_credential(self) -> None:
        self._credential_store.save(Credential(self._auth.refresh_token, self._auth.uid))

    def _delete_rejected_credential(self, error: AuthenticationError) -> None:
        if error.operation == "refresh" and error.status_code in {400, 401}:
            self._credential_store.delete()


def _credential_store(environment: dict[str, str] | None = None) -> CredentialStore:
    if environment is None:
        environment = load_environment()
    if path := environment.get("TODOMATE_CREDENTIALS_FILE"):
        return FileCredentialStore(Path(path))
    return KeyringCredentialStore()


def _adapter_from_local_credentials() -> _ConfiguredAdapter | None:
    api_key = load_firebase_api_key()
    credential_store = _credential_store()
    credential = credential_store.load()
    if not api_key or credential is None:
        return None
    client = httpx.AsyncClient()
    auth = FirebaseAuthSession(api_key, client)
    authenticate = lambda: auth.restore(credential.refresh_token)
    return _ConfiguredAdapter(
        auth, TodoMateAdapter(auth, FirestoreClient(auth, client)), authenticate, credential_store
    )


def _reauthentication_error(error: AuthenticationError) -> AuthenticationError:
    if error.operation == "refresh" and error.status_code in {400, 401}:
        return AuthenticationError("session", "reauthentication_required", status_code=error.status_code)
    return error


async def _login(api_key: str, email: str, password: str, credential_store: CredentialStore) -> bool:
    try:
        async with httpx.AsyncClient() as client:
            auth = FirebaseAuthSession(api_key, client)
            await auth.sign_in(email, password)
        credential_store.save(Credential(auth.refresh_token, auth.uid))
    except AuthenticationError:
        print("Login failed. Check your email and password, then try again.", file=sys.stderr)
        return False
    print("✓ Logged in successfully.")
    return True


async def _status(api_key: str | None, credential_store: CredentialStore) -> None:
    credential = credential_store.load()
    if credential is None:
        print("Not logged in.")
        return
    if api_key:
        try:
            async with httpx.AsyncClient() as client:
                auth = FirebaseAuthSession(api_key, client)
                await auth.restore(credential.refresh_token)
            uid = auth.uid
        except AuthenticationError:
            print("Session expired.\nRun: todomate-mcp auth login")
            return
    else:
        uid = credential.uid
    print(f"✓ Logged in\nUID: {uid}")


def main() -> None:
    environment = load_environment()

    parser = argparse.ArgumentParser(description="Run the TodoMate MCP server.")
    commands = parser.add_subparsers(dest="command", required=True)
    auth_parser = commands.add_parser("auth")
    auth_commands = auth_parser.add_subparsers(dest="auth_command", required=True)
    auth_commands.add_parser("login")
    auth_commands.add_parser("status")
    auth_commands.add_parser("logout")
    serve_parser = commands.add_parser("serve")
    serve_parser.add_argument("transport", choices=("stdio", "http"), nargs="?", default="stdio")
    serve_parser.add_argument("--host", default=environment.get("TODOMATE_MCP_HOST", "127.0.0.1"))
    serve_parser.add_argument("--port", type=int, default=int(environment.get("TODOMATE_MCP_PORT", "8000")))
    argv = sys.argv[1:]
    args = parser.parse_args(argv if argv and argv[0] in {"auth", "serve"} else ["serve", *argv])

    if args.command == "auth":
        api_key = environment.get("TODOMATE_FIREBASE_API_KEY")
        credential_store = _credential_store(environment)
        if args.auth_command == "login":
            if not api_key:
                parser.error("TODOMATE_FIREBASE_API_KEY is required for login")
            email = input("Email: ")
            password = getpass("Password: ")
            if not asyncio.run(_login(api_key, email, password, credential_store)):
                raise SystemExit(1)
        elif args.auth_command == "status":
            asyncio.run(_status(api_key, credential_store))
        else:
            credential_store.delete()
            print("✓ Logged out.")
        return

    adapter = _adapter_from_local_credentials()

    if args.transport == "stdio":
        if adapter is None:
            print("TodoMate account is not connected.\nRun: todomate-mcp auth login", file=sys.stderr)
            raise SystemExit(1)
        server = create_server(adapter)
        server.run(transport="stdio")
        return

    access_token = environment.get("TODOMATE_MCP_ACCESS_TOKEN")
    if not access_token:
        parser.error(
            "TODOMATE_MCP_ACCESS_TOKEN is required for HTTP transport"
            )

    resource_server_url = environment.get(
        "TODOMATE_MCP_PUBLIC_URL", 
        f"http://{args.host}:{args.port}/mcp"
        )

    server = create_server(
        adapter,
        access_token=access_token,
        resource_server_url=resource_server_url,
    )
    uvicorn.run(create_http_app(server, host=args.host), host=args.host, port=args.port)
