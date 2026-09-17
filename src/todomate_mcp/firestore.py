"""Small authenticated Cloud Firestore REST client."""

import math
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

import httpx

from .firebase_auth import FirebaseAuthSession

JsonValue = None | bool | float | int | str | list["JsonValue"] | dict[str, "JsonValue"]


class FirestoreError(Exception):
    """A Firestore operation failure without request credentials or payloads."""

    def __init__(self, operation: str, status_code: int | None = None):
        self.operation = operation
        self.status_code = status_code
        suffix = "network_error" if status_code is None else f"http_{status_code}"
        super().__init__(f"Firestore {operation} failed: {suffix}")


def encode_value(value: JsonValue) -> dict[str, Any]:
    if value is None:
        return {"nullValue": None}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    if isinstance(value, int):
        return {"integerValue": str(value)}
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Firestore numbers must be finite")
        return {"doubleValue": value}
    if isinstance(value, list):
        return {"arrayValue": {"values": [encode_value(item) for item in value]}}
    if isinstance(value, dict):
        return {"mapValue": {"fields": encode_fields(value)}}
    raise TypeError(f"Unsupported Firestore value: {type(value).__name__}")


def encode_fields(fields: Mapping[str, JsonValue]) -> dict[str, Any]:
    if not all(isinstance(name, str) and name and not name.startswith("__") for name in fields):
        raise ValueError("Firestore field names must be non-empty and not reserved")
    return {name: encode_value(value) for name, value in fields.items()}


def decode_value(value: Any) -> JsonValue:
    if not isinstance(value, dict) or len(value) != 1:
        raise ValueError("Invalid Firestore value")
    name, raw = next(iter(value.items()))
    if name in {"nullValue", "booleanValue", "stringValue"}:
        if name == "nullValue" and raw is None:
            return None
        if name == "booleanValue" and isinstance(raw, bool):
            return raw
        if name == "stringValue" and isinstance(raw, str):
            return raw
    if name == "integerValue" and isinstance(raw, str):
        return int(raw)
    if name == "doubleValue" and isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    if name == "arrayValue" and isinstance(raw, dict):
        values = raw.get("values", [])
        if isinstance(values, list):
            return [decode_value(item) for item in values]
    if name == "mapValue" and isinstance(raw, dict):
        fields = raw.get("fields", {})
        if isinstance(fields, dict):
            return decode_fields(fields)
    raise ValueError("Unsupported or invalid Firestore value")


def decode_fields(fields: Any) -> dict[str, JsonValue]:
    if not isinstance(fields, dict) or not all(isinstance(name, str) for name in fields):
        raise ValueError("Invalid Firestore fields")
    return {name: decode_value(value) for name, value in fields.items()}


class FirestoreClient:
    def __init__(
        self,
        auth: FirebaseAuthSession,
        client: httpx.AsyncClient,
        *,
        project_id: str = "mate-914f3",
        database_id: str = "(default)",
    ):
        if not project_id or not database_id:
            raise ValueError("Firestore project and database IDs are required")
        self._auth = auth
        self._client = client
        self._base_url = (
            f"https://firestore.googleapis.com/v1/projects/{quote(project_id, safe='')}"
            f"/databases/{quote(database_id, safe='')}/documents"
        )

    async def get_document(self, path: str) -> dict[str, JsonValue]:
        return await self._request_document("get", "GET", path)

    async def upsert_document(
        self,
        path: str,
        fields: Mapping[str, JsonValue],
        *,
        update_mask: Sequence[str] = (),
    ) -> dict[str, JsonValue]:
        params = [("updateMask.fieldPaths", field) for field in update_mask]
        return await self._request_document(
            "upsert", "PATCH", path, params=params, json={"fields": encode_fields(fields)}
        )

    async def delete_document(self, path: str) -> None:
        await self._request("delete", "DELETE", path)

    async def query_equal(
        self, collection_id: str, filters: Mapping[str, JsonValue]
    ) -> list[dict[str, JsonValue]]:
        if not collection_id or "/" in collection_id:
            raise ValueError("Firestore collection ID must be a single segment")
        clauses = [
            {"fieldFilter": {"field": {"fieldPath": name}, "op": "EQUAL", "value": encode_value(value)}}
            for name, value in filters.items()
        ]
        where: dict[str, Any] | None = None
        if len(clauses) == 1:
            where = clauses[0]
        elif clauses:
            where = {"compositeFilter": {"op": "AND", "filters": clauses}}
        body: dict[str, Any] = {"structuredQuery": {"from": [{"collectionId": collection_id}]}}
        if where is not None:
            body["structuredQuery"]["where"] = where
        response = await self._request("query", "POST", "", json=body, query=True)
        try:
            rows = response.json()
            if not isinstance(rows, list):
                raise ValueError
            return [decode_fields(row["document"].get("fields", {})) for row in rows if isinstance(row, dict) and isinstance(row.get("document"), dict)]
        except (KeyError, TypeError, ValueError):
            raise FirestoreError("query", response.status_code) from None

    async def _request_document(self, operation: str, method: str, path: str, **kwargs: Any) -> dict[str, JsonValue]:
        response = await self._request(operation, method, path, **kwargs)
        try:
            document = response.json()
            return decode_fields(document.get("fields", {}))
        except (AttributeError, TypeError, ValueError):
            raise FirestoreError(operation, response.status_code) from None

    async def _request(self, operation: str, method: str, path: str, *, query: bool = False, **kwargs: Any) -> httpx.Response:
        try:
            response = await self._client.request(
                method,
                f"{self._base_url}:runQuery" if query else self._document_url(path),
                headers={"Authorization": f"Bearer {await self._auth.id_token()}"},
                **kwargs,
            )
        except httpx.RequestError:
            raise FirestoreError(operation) from None
        if not response.is_success:
            raise FirestoreError(operation, response.status_code)
        return response

    def _document_url(self, path: str) -> str:
        segments = path.split("/")
        if not path or len(segments) % 2 or any(not segment for segment in segments):
            raise ValueError("Firestore document path must contain collection/document pairs")
        return f"{self._base_url}/{'/'.join(quote(segment, safe='') for segment in segments)}"
