import asyncio
import json
import traceback
from urllib.parse import parse_qs

import httpx
import pytest

from todomate_mcp.firebase_auth import AuthenticationError, FirebaseAuthSession


def tokens(refresh=False, **changes):
    data = (
        dict(id_token='new-secret', refresh_token='rotated-secret', user_id='user', expires_in='3600')
        if refresh else
        dict(idToken='id-secret', refreshToken='refresh-secret', localId='user', expiresIn='3600')
    )
    return data | changes


def test_login_reuse_concurrent_refresh_and_rotation():
    async def run():
        now = [100.0]
        password = 'password-secret'
        requests = []

        async def handle(request):
            requests.append(request)
            assert request.method == 'POST'
            assert request.url.params['key'] == 'api-key'
            if len(requests) == 1:
                assert request.url.host == 'identitytoolkit.googleapis.com'
                assert request.url.path == '/v1/accounts:signInWithPassword'
                assert json.loads(request.content) == dict(email='me@example.com', password='password-secret', returnSecureToken=True)
                return httpx.Response(200, json=tokens())
            assert request.url.host == 'securetoken.googleapis.com'
            assert request.url.path == '/v1/token'
            assert request.headers['content-type'] == 'application/x-www-form-urlencoded'
            assert parse_qs(request.content.decode()) == {
                'grant_type': ['refresh_token'],
                'refresh_token': ['refresh-secret' if len(requests) == 2 else 'rotated-secret'],
            }
            await asyncio.sleep(0)
            return httpx.Response(200, json=tokens(True))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            session = FirebaseAuthSession('api-key', client, clock=lambda: now[0])
            await session.sign_in('me@example.com', password)
            assert session.uid == 'user'
            now[0] = 3639
            assert await session.id_token() == 'id-secret'
            assert len(requests) == 1
            now[0] = 3640
            assert await asyncio.gather(session.id_token(), session.id_token()) == ['new-secret'] * 2
            assert len(requests) == 2
            now[0] += 3540
            assert await session.id_token() == 'new-secret'
            assert len(requests) == 3
            assert 'secret' not in repr(session)
            assert 'secret' not in repr(session._token)
    asyncio.run(run())


@pytest.mark.parametrize('operation', ['sign_in', 'refresh'])
@pytest.mark.parametrize('failure,reason', [
    ('http', 'http_error'), ('network', 'network_error'),
    ('json', 'invalid_response'), ('missing', 'invalid_response'),
    ('nan', 'invalid_response'), ('inf', 'invalid_response'),
    ('zero', 'invalid_response'), ('negative', 'invalid_response'),
    ('number', 'invalid_response'), ('empty', 'invalid_response'),
])
def test_failures_are_sanitized_and_do_not_return_expired_tokens(operation, failure, reason):
    async def run():
        now = [0.0]
        calls = []
        password = 'password-secret'

        def handle(request):
            calls.append(request)
            if operation == 'refresh' and len(calls) == 1:
                return httpx.Response(200, json=tokens())
            if failure == 'http':
                return httpx.Response(400, json={'error': {'message': 'password-secret refresh-secret'}})
            if failure == 'network':
                raise httpx.ConnectError('password-secret refresh-secret', request=request)
            if failure == 'json':
                return httpx.Response(200, text='password-secret refresh-secret')
            if failure == 'missing':
                return httpx.Response(200, json={})
            data = tokens(operation == 'refresh')
            key = 'expires_in' if operation == 'refresh' else 'expiresIn'
            data[key] = {'nan': 'NaN', 'inf': 'Infinity', 'zero': '0', 'negative': '-1', 'number': 3600, 'empty': ''}[failure]
            return httpx.Response(200, json=data)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            session = FirebaseAuthSession('api-key', client, clock=lambda: now[0])
            if operation == 'refresh':
                await session.sign_in('me@example.com', password)
                now[0] = 3600
            with pytest.raises(AuthenticationError) as caught:
                if operation == 'refresh':
                    await session.id_token()
                else:
                    await session.sign_in('me@example.com', password)
            assert caught.value.operation == operation
            assert caught.value.reason == reason
            assert 'secret' not in ''.join(traceback.format_exception(caught.value))
            assert len(calls) == (2 if operation == 'refresh' else 1)
    asyncio.run(run())


def test_not_signed_in_and_failed_account_switch():
    async def run():
        password = 'password-secret'
        replies = iter([httpx.Response(200, json=tokens()), httpx.Response(401)])
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: next(replies))) as client:
            session = FirebaseAuthSession('api-key', client)
            with pytest.raises(AuthenticationError, match='not_signed_in'):
                await session.id_token()
            await session.sign_in('me@example.com', password)
            with pytest.raises(AuthenticationError, match='http_error'):
                await session.sign_in('other@example.com', 'bad-password')
            with pytest.raises(AuthenticationError, match='not_signed_in'):
                await session.id_token()
    asyncio.run(run())


def test_restore_uses_refresh_token_without_password_and_updates_state():
    async def run():
        seen = []

        def handle(request):
            seen.append(parse_qs(request.content.decode()))
            return httpx.Response(200, json=tokens(True))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            session = FirebaseAuthSession("api-key", client)
            await session.restore("stored-refresh-secret")
            assert await session.id_token() == "new-secret"
            assert session.uid == "user"
            assert session.refresh_token == "rotated-secret"
        assert seen == [{"grant_type": ["refresh_token"], "refresh_token": ["stored-refresh-secret"]}]
    asyncio.run(run())
