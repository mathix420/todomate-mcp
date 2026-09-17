import json
import stat

import pytest

from todomate_mcp.auth import credentials
from todomate_mcp.auth.credentials import ACCOUNT_NAME, SERVICE_NAME, Credential, FileCredentialStore, KeyringCredentialStore


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


def test_file_credentials_survive_new_store_and_rotate_privately(tmp_path):
    path = tmp_path / "data" / "credentials.json"
    store = FileCredentialStore(path)
    assert store.load() is None
    store.save(Credential("first-token", "user"))
    assert FileCredentialStore(path).load() == Credential("first-token", "user")
    store.save(Credential("rotated-token", "user"))
    assert FileCredentialStore(path).load() == Credential("rotated-token", "user")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert len(list(path.parent.iterdir())) == 1
    store.delete()
    store.delete()
    assert store.load() is None


@pytest.mark.parametrize("raw", ["invalid json", "[]", "null", "{}", '{"refresh_token": "", "uid": "user"}', '{"refresh_token": 123, "uid": "user"}'])
def test_file_credentials_reject_invalid_contents(tmp_path, raw):
    path = tmp_path / "credentials.json"
    path.write_text(raw)
    assert FileCredentialStore(path).load() is None


def test_failed_file_rotation_preserves_previous_credential(tmp_path, monkeypatch):
    path = tmp_path / "credentials.json"
    store = FileCredentialStore(path)
    store.save(Credential("previous", "user"))

    def fail_replace(*_):
        raise OSError("failed replacement")

    monkeypatch.setattr(credentials.os, "replace", fail_replace)
    with pytest.raises(OSError, match="failed replacement"):
        store.save(Credential("new", "user"))
    assert store.load() == Credential("previous", "user")
    assert list(tmp_path.iterdir()) == [path]
