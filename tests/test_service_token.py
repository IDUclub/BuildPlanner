"""Разбор ответа Keycloak: любой сбой — HTTPException с понятным статусом, а не KeyError."""

import pytest
from aiohttp import web
from fastapi import HTTPException

from app.common.auth.service_token import ServiceTokenProvider, _expires_in


async def _keycloak(body: str, status: int = 200):
    async def handle(_request):
        return web.Response(text=body, status=status, content_type="application/json")

    app = web.Application()
    app.router.add_post("/realms/r/protocol/openid-connect/token", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, ServiceTokenProvider(f"http://127.0.0.1:{port}", "r", "c", "s")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "msg"),
    [
        ("<html>", "не JSON"),
        ("{}", "нет access_token"),
        ('{"access_token": 5}', "нет access_token"),
        ("[]", "нет access_token"),
    ],
)
async def test_malformed_token_response_is_a_502(body, msg):
    runner, tokens = await _keycloak(body)
    try:
        with pytest.raises(HTTPException) as caught:
            await tokens.get_token()
    finally:
        await runner.cleanup()
    assert caught.value.status_code == 502
    assert msg in caught.value.detail["msg"]


@pytest.mark.asyncio
async def test_garbage_expires_in_does_not_reject_the_token():
    runner, tokens = await _keycloak('{"access_token": "a.b.c", "expires_in": "soon"}')
    try:
        assert await tokens.get_token() == "a.b.c"
    finally:
        await runner.cleanup()


def test_expires_in_falls_back_on_garbage():
    assert (_expires_in(None), _expires_in("x"), _expires_in("120")) == (60, 60, 120)
