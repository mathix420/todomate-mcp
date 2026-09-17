"""Persistent Firebase credentials backed by the OS keyring or a private file."""

import json
import os
from dataclasses import dataclass
from pathlib import Path
import tempfile
from typing import Protocol

import keyring


SERVICE_NAME = "todomate-mcp"
ACCOUNT_NAME = "firebase"


@dataclass(frozen=True)
class Credential:
    refresh_token: str
    uid: str


class CredentialStore(Protocol):
    def load(self) -> Credential | None: ...

    def save(self, credential: Credential) -> None: ...

    def delete(self) -> None: ...


class FileCredentialStore:
    """Store container credentials on a persistent volume with owner-only access."""

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> Credential | None:
        try:
            data = json.loads(self.path.read_text())
        except (FileNotFoundError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        refresh_token, uid = data.get("refresh_token"), data.get("uid")
        if not all(isinstance(value, str) and value for value in (refresh_token, uid)):
            return None
        return Credential(refresh_token, uid)

    def save(self, credential: Credential) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # A private temporary file and atomic replacement protect token rotation.
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent, delete=False) as temporary:
                temporary_path = Path(temporary.name)
                json.dump({"refresh_token": credential.refresh_token, "uid": credential.uid}, temporary)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)


class KeyringCredentialStore:
    def load(self) -> Credential | None:
        try:
            raw = keyring.get_password(SERVICE_NAME, ACCOUNT_NAME)
        except keyring.errors.NoKeyringError:
            return None
        if raw is None:
            return None
        try:
            data = json.loads(raw)
            refresh_token, uid = data["refresh_token"], data["uid"]
        except (TypeError, ValueError, KeyError):
            return None
        if not all(isinstance(value, str) and value for value in (refresh_token, uid)):
            return None
        return Credential(refresh_token, uid)

    def save(self, credential: Credential) -> None:
        keyring.set_password(
            SERVICE_NAME,
            ACCOUNT_NAME,
            json.dumps({"refresh_token": credential.refresh_token, "uid": credential.uid}),
        )

    def delete(self) -> None:
        try:
            keyring.delete_password(SERVICE_NAME, ACCOUNT_NAME)
        except keyring.errors.PasswordDeleteError:
            pass
