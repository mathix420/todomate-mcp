import asyncio
import json

import httpx
import pytest

from todomate_mcp.firestore import FirestoreClient, FirestoreError, decode_fields, encode_fields


class Auth:
    async def id_token(self):
        return "id-token"


def test_codec_round_trip_and_rejects_invalid_values():
    fields = {"name": "task", "done": False, "count": 1, "ratio": 1.5, "none": None, "items": ["x"], "meta": {"a": 1}}
    assert decode_fields(encode_fields(fields)) == fields
    with pytest.raises(ValueError):
        encode_fields({"__reserved__": "x"})
    with pytest.raises(ValueError):
        decode_fields({"count": {"integerValue": "not-an-integer"}})


def test_crud_uses_token_encodes_paths_masks_and_decodes_documents():
    async def run():
        requests = []

        def handle(request):
            requests.append(request)
            assert request.headers["authorization"] == "Bearer id-token"
            if request.method == "DELETE":
                return httpx.Response(200, json={})
            return httpx.Response(200, json={"fields": {"id": {"stringValue": "todo 1"}, "count": {"integerValue": "2"}}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            firestore = FirestoreClient(Auth(), http)
            assert await firestore.get_document("TodoItem/todo 1") == {"id": "todo 1", "count": 2}
            assert await firestore.upsert_document("TodoItem/todo 1", {"content": "write"}, update_mask=["content"]) == {"id": "todo 1", "count": 2}
            await firestore.delete_document("TodoItem/todo 1")
        assert str(requests[0].url).endswith("/TodoItem/todo%201")
        assert requests[1].url.params.get_list("updateMask.fieldPaths") == ["content"]
        assert json.loads(requests[1].content) == {"fields": {"content": {"stringValue": "write"}}}
    asyncio.run(run())


def test_query_equal_builds_a_structured_query_and_discards_progress_rows():
    async def run():
        seen = []

        def handle(request):
            seen.append(request)
            return httpx.Response(200, json=[
                {"readTime": "2026-01-01T00:00:00Z"},
                {"document": {"fields": {"id": {"stringValue": "todo"}}}},
            ])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            firestore = FirestoreClient(Auth(), http)
            assert await firestore.query_equal("TodoItem", {"writerID": "user", "date": 1}) == [{"id": "todo"}]
        body = json.loads(seen[0].content)
        assert str(seen[0].url).endswith("/documents:runQuery")
        assert body["structuredQuery"]["where"]["compositeFilter"]["op"] == "AND"
    asyncio.run(run())


def test_errors_are_classified_and_document_paths_are_validated():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(403))) as http:
            firestore = FirestoreClient(Auth(), http)
            with pytest.raises(FirestoreError) as caught:
                await firestore.get_document("TodoItem/id")
            assert (caught.value.operation, caught.value.status_code) == ("get", 403)
            with pytest.raises(ValueError):
                await firestore.get_document("TodoItem")
    asyncio.run(run())


def test_null_query_uses_is_null_instead_of_equality():
    async def run():
        def handle(request):
            filters = json.loads(request.content)["structuredQuery"]["where"]["compositeFilter"]["filters"]
            assert filters == [
                {"fieldFilter": {"field": {"fieldPath": "writerID"}, "op": "EQUAL", "value": {"stringValue": "user"}}},
                {"unaryFilter": {"field": {"fieldPath": "date"}, "op": "IS_NULL"}},
            ]
            return httpx.Response(200, json=[])
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            assert await FirestoreClient(Auth(), http).query_equal("TodoItem", {"writerID": "user", "date": None}) == []
    asyncio.run(run())


@pytest.mark.parametrize("version", [
    "2026-09-23T12:34:56Z",
    "2026-09-23T12:34:56.123Z",
    "2026-09-23T12:34:56.123456Z",
    "2026-09-23T12:34:56.123456000Z",
    "2026-09-23T14:34:56.123456+02:00",
])
def test_versioned_read_and_conditional_patch_preserve_exact_version(version):
    async def run():
        requests = []

        def handle(request):
            requests.append(request)
            assert request.headers["authorization"] == "Bearer id-token"
            return httpx.Response(200, json={
                "fields": {"elapsed": {"integerValue": "42"}},
                "updateTime": version,
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            firestore = FirestoreClient(Auth(), http)
            fields, actual_version = await firestore.get_document_versioned("TodoItem/id")
            assert fields == {"elapsed": 42}
            assert actual_version == version
            assert await firestore.upsert_document(
                "TodoItem/id", {"elapsed": 43, "running": False},
                update_mask=["elapsed", "running"], update_time=actual_version,
            ) == {"elapsed": 42}

        assert [request.method for request in requests] == ["GET", "PATCH"]
        assert not requests[0].url.params
        assert requests[1].url.params.get_list("updateMask.fieldPaths") == ["elapsed", "running"]
        assert requests[1].url.params.get_list("currentDocument.updateTime") == [version]
        assert json.loads(requests[1].content) == {"fields": {
            "elapsed": {"integerValue": "43"}, "running": {"booleanValue": False},
        }}
    asyncio.run(run())


INVALID_VERSIONS = [
    None, False, 123, {}, [], "", "not-a-version", "2026-09-23",
    "2026-09-23T12:34:56", "2026-09-23 12:34:56Z",
    "2026-09-23T12:34:56Z\n", "2026-02-30T12:34:56Z",
    "0000-01-01T00:00:00Z", "2026-09-23T24:00:00Z",
    "2026-09-23T12:34:60Z", "2026-09-23T12:34:56+02:60",
    "2026-09-23T12:34:56+24:00", "2026-09-23T12:34:56.123456789Z",
    "2026-09-23T12:34:56.1234560000Z",
]


@pytest.mark.parametrize("version", INVALID_VERSIONS)
def test_versioned_read_rejects_invalid_versions_without_changing_regular_get(version):
    async def run():
        document = {"fields": {"content": {"stringValue": "private-task"}}, "updateTime": version}
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=document)
        )) as http:
            firestore = FirestoreClient(Auth(), http)
            with pytest.raises(FirestoreError) as caught:
                await firestore.get_document_versioned("TodoItem/id")
            assert (caught.value.operation, caught.value.status_code) == ("get", 200)
            assert str(caught.value) == "Firestore get failed: http_200"
            assert await firestore.get_document("TodoItem/id") == {"content": "private-task"}
    asyncio.run(run())


@pytest.mark.parametrize("document", [{}, {"fields": {}}, [], None, "private-payload"])
def test_versioned_read_rejects_missing_version_and_invalid_documents(document):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=document)
        )) as http:
            with pytest.raises(FirestoreError) as caught:
                await FirestoreClient(Auth(), http).get_document_versioned("TodoItem/id")
            assert str(caught.value) == "Firestore get failed: http_200"
    asyncio.run(run())


@pytest.mark.parametrize("version", [value for value in INVALID_VERSIONS if value is not None])
def test_conditional_patch_rejects_invalid_version_before_network_request(version):
    async def run():
        def handle(_):
            pytest.fail("An invalid version must not send any write")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            with pytest.raises(ValueError, match="^Invalid Firestore updateTime precondition$"):
                await FirestoreClient(Auth(), http).upsert_document(
                    "TodoItem/id", {"elapsed": 43}, update_time=version,
                )
    asyncio.run(run())


@pytest.mark.parametrize("status", [404, 409, 412])
def test_stale_or_deleted_document_never_retries_unconditionally(status):
    async def run():
        requests = []

        def handle(request):
            requests.append(request)
            return httpx.Response(status, json={"error": "private-payload"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            with pytest.raises(FirestoreError) as caught:
                await FirestoreClient(Auth(), http).upsert_document(
                    "TodoItem/id", {"elapsed": 43},
                    update_time="2026-09-23T12:34:56.123456Z",
                )
            assert (caught.value.operation, caught.value.status_code) == ("upsert", status)
            assert "private-payload" not in str(caught.value)
        assert len(requests) == 1
        assert requests[0].url.params["currentDocument.updateTime"] == "2026-09-23T12:34:56.123456Z"
    asyncio.run(run())
