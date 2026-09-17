import json

from todomate_mcp.auth import credentials
from todomate_mcp.auth.credentials import ACCOUNT_NAME, SERVICE_NAME, Credential, KeyringCredentialStore


def test_keyring_credential_store_saves_and_loads(monkeypatch):
    stored = {}
    monkeypatch.setattr(credentials.keyring, "set_password", lambda service, account, value: stored.update({(service, account): value}))
    monkeypatch.setattr(credentials.keyring, "get_password", lambda service, account: stored.get((service, account)))
    store = KeyringCredentialStore()

    store.save(Credential("refresh-token", "user-id"))

    assert json.loads(stored[(SERVICE_NAME, ACCOUNT_NAME)]) == {"refresh_token": "refresh-token", "uid": "user-id"}
    assert store.load() == Credential("refresh-token", "user-id")


def test_keyring_credential_store_returns_none_when_empty(monkeypatch):
    monkeypatch.setattr(credentials.keyring, "get_password", lambda *_: None)

    assert KeyringCredentialStore().load() is None


def test_keyring_credential_store_deletes_credential(monkeypatch):
    deleted = []
    monkeypatch.setattr(credentials.keyring, "delete_password", lambda service, account: deleted.append((service, account)))

    KeyringCredentialStore().delete()

    assert deleted == [(SERVICE_NAME, ACCOUNT_NAME)]


def test_keyring_credential_store_ignores_missing_credential_on_delete(monkeypatch):
    def delete(*_):
        raise credentials.keyring.errors.PasswordDeleteError("missing")

    monkeypatch.setattr(credentials.keyring, "delete_password", delete)

    KeyringCredentialStore().delete()
