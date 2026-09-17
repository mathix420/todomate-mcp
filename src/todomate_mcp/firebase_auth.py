"""Firebase email/password authentication with in-memory token refresh."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
import math
import time
from typing import Literal

import httpx


class AuthenticationError(Exception):
    """Sanitized failure; inspect operation and reason without exposing secrets."""

    def __init__(self, operation: str, reason: str, *, status_code: int | None = None):
        self.operation = operation
        self.reason = reason
        self.status_code = status_code
        super().__init__(f"Firebase {operation} failed: {reason}")


@dataclass(frozen=True)
class _TokenState:
    id_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    uid: str
    expires_at: float


class FirebaseAuthSession:
    """Caller owns the AsyncClient lifetime. Call sign_in before id_token.

    State stays in memory; passwords are not retained. A lock serializes token
    updates so concurrent callers cannot overwrite a rotated refresh token.
    """

    def __init__(
        self,
        api_key: str,
        client: httpx.AsyncClient,
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("Firebase API key is required")
        self._api_key = api_key
        self._client = client
        self._clock = clock
        self._token: _TokenState | None = None
        self._lock = asyncio.Lock()

    @property
    def uid(self) -> str:
        if self._token is None:
            raise AuthenticationError("session", "not_signed_in")
        return self._token.uid

    @property
    def refresh_token(self) -> str:
        if self._token is None:
            raise AuthenticationError("session", "not_signed_in")
        return self._token.refresh_token

    async def sign_in(self, email: str, password: str) -> None:
        if not all(isinstance(value, str) and value.strip() for value in (email, password)):
            raise AuthenticationError("sign_in", "invalid_input")
        async with self._lock:
            # A failed account switch must not leave the previous user active.
            self._token = None
            self._token = await self._request(
                "sign_in", {"email": email, "password": password, "returnSecureToken": True}
            )

    async def restore(self, refresh_token: str) -> None:
        if not isinstance(refresh_token, str) or not refresh_token.strip():
            raise AuthenticationError("refresh", "invalid_input")
        async with self._lock:
            self._token = None
            self._token = await self._request(
                "refresh", {"grant_type": "refresh_token", "refresh_token": refresh_token}
            )

    async def id_token(self) -> str:
        async with self._lock:
            if self._token is None:
                raise AuthenticationError("session", "not_signed_in")
            if self._token.expires_at - self._clock() <= 60:
                try:
                    self._token = await self._request(
                        "refresh",
                        {"grant_type": "refresh_token", "refresh_token": self._token.refresh_token},
                    )
                except AuthenticationError:
                    self._token = None
                    raise
            return self._token.id_token

    async def _request(
        self, operation: Literal["sign_in", "refresh"], payload: dict[str, str | bool]
    ) -> _TokenState:
        started_at = self._clock()
        try:
            if operation == "sign_in":
                response = await self._client.post(
                    "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword",
                    params={"key": self._api_key}, json=payload, follow_redirects=False,
                )
            else:
                response = await self._client.post(
                    "https://securetoken.googleapis.com/v1/token",
                    params={"key": self._api_key}, data=payload, follow_redirects=False,
                )
        except httpx.RequestError:
            raise AuthenticationError(operation, "network_error") from None
        if not response.is_success:
            raise AuthenticationError(operation, "http_error", status_code=response.status_code)
        try:
            data = response.json()
            names = (
                ("idToken", "refreshToken", "localId", "expiresIn")
                if operation == "sign_in"
                else ("id_token", "refresh_token", "user_id", "expires_in")
            )
            token, refresh, uid, lifetime = (data[name] for name in names)
            if not all(isinstance(value, str) and value.strip() for value in (token, refresh, uid, lifetime)):
                raise ValueError
            seconds = float(lifetime)
            expires_at = started_at + seconds
            if not math.isfinite(seconds) or seconds <= 0 or not math.isfinite(expires_at):
                raise ValueError
            if expires_at <= self._clock():
                raise ValueError
            if operation == "refresh" and self._token is not None and uid != self._token.uid:
                raise ValueError
            return _TokenState(token, refresh, uid, expires_at)
        except (ValueError, KeyError, TypeError, OverflowError):
            raise AuthenticationError(operation, "invalid_response") from None
