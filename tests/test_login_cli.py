import asyncio
import builtins

import httpx

import todomate_mcp.server as server
from todomate_mcp.auth.credentials import Credential
from todomate_mcp.firebase_auth import AuthenticationError


def test_login_saves_refresh_token_and_uid_without_password(monkeypatch, capsys):
    saved = []

    class Auth:
        def __init__(self, api_key, client):
            assert api_key == "api-key"
            assert isinstance(client, httpx.AsyncClient)
            self.refresh_token = "refresh-token"
            self.uid = "user-id"

        async def sign_in(self, email, password):
            assert (email, password) == ("me@example.com", "password-secret")

    monkeypatch.setattr(server, "FirebaseAuthSession", Auth)

    class Store:
        def save(self, credential):
            saved.append(credential)

    assert asyncio.run(server._login("api-key", "me@example.com", "password-secret", Store()))
    assert saved == [Credential("refresh-token", "user-id")]
    assert "password-secret" not in capsys.readouterr().out


def test_auth_login_prompts_for_credentials(monkeypatch):
    saved = []

    class Auth:
        def __init__(self, api_key, client):
            assert api_key == "api-key"
            self.refresh_token = "refresh-token"
            self.uid = "user-id"

        async def sign_in(self, email, password):
            assert (email, password) == ("me@example.com", "password-secret")

    class Store:
        def save(self, credential):
            saved.append(credential)

    monkeypatch.setattr(server, "FirebaseAuthSession", Auth)
    monkeypatch.setattr(server, "KeyringCredentialStore", Store)
    monkeypatch.setattr(server, "load_environment", lambda: {"TODOMATE_FIREBASE_API_KEY": "api-key"})
    monkeypatch.setattr(builtins, "input", lambda prompt: "me@example.com")
    monkeypatch.setattr(server, "getpass", lambda prompt: "password-secret")
    monkeypatch.setattr(server.sys, "argv", ["todomate-mcp", "auth", "login"])

    server.main()

    assert saved == [Credential("refresh-token", "user-id")]


def test_login_failure_is_friendly_and_does_not_save_or_expose_password(monkeypatch, capsys):
    class Auth:
        def __init__(self, *_):
            pass

        async def sign_in(self, *_):
            raise AuthenticationError("sign_in", "http_error")

    monkeypatch.setattr(server, "FirebaseAuthSession", Auth)

    class Store:
        def save(self, credential):
            raise AssertionError(f"must not save {credential}")

    assert not asyncio.run(server._login("api-key", "me@example.com", "password-secret", Store()))
    captured = capsys.readouterr()
    assert "Login failed" in captured.err
    assert "password-secret" not in captured.err + captured.out


def test_auth_status_reports_when_not_logged_in(capsys):
    class Store:
        def load(self):
            return None

    asyncio.run(server._status(None, Store()))

    assert capsys.readouterr().out == "Not logged in.\n"


def test_auth_status_restores_credential_and_prints_uid(monkeypatch, capsys):
    class Auth:
        def __init__(self, api_key, client):
            assert api_key == "api-key"
            self.uid = "restored-user"

        async def restore(self, refresh_token):
            assert refresh_token == "refresh-token"

    monkeypatch.setattr(server, "FirebaseAuthSession", Auth)

    class Store:
        def load(self):
            return Credential("refresh-token", "stored-user")

    asyncio.run(server._status("api-key", Store()))

    assert capsys.readouterr().out == "✓ Logged in\nUID: restored-user\n"


def test_auth_status_reports_expired_credential(monkeypatch, capsys):
    class Auth:
        def __init__(self, *_):
            pass

        async def restore(self, _):
            raise AuthenticationError("refresh", "http_error")

    monkeypatch.setattr(server, "FirebaseAuthSession", Auth)

    class Store:
        def load(self):
            return Credential("refresh-token", "user-id")

    asyncio.run(server._status("api-key", Store()))

    assert capsys.readouterr().out == "Session expired.\nRun: todomate-mcp auth login\n"


def test_auth_logout_deletes_credential_and_status_reports_not_logged_in(monkeypatch, capsys):
    class Store:
        credential = Credential("refresh-token", "user-id")

        def load(self):
            return type(self).credential

        def delete(self):
            type(self).credential = None

    monkeypatch.setattr(server, "KeyringCredentialStore", Store)
    monkeypatch.setattr(server, "load_environment", lambda: {})
    monkeypatch.setattr(server.sys, "argv", ["todomate-mcp", "auth", "logout"])
    server.main()
    monkeypatch.setattr(server.sys, "argv", ["todomate-mcp", "auth", "status"])
    server.main()

    assert capsys.readouterr().out == "✓ Logged out.\nNot logged in.\n"
