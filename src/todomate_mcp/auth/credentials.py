"""Persistent Firebase credentials backed by the operating system keyring."""

import json
from dataclasses import dataclass
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
