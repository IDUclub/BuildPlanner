"""Verify the installed wrapper on BuildPlanner's real router and dependencies."""

from types import SimpleNamespace

import httpx
import pytest
from sse_starlette.sse import AppStatus


@pytest.mark.asyncio
async def test_real_chat_route_reconnect(monkeypatch, tmp_path):
    monkeypatch.setenv("SSE_REPLAY_DB", str(tmp_path / "api.sqlite3"))
    monkeypatch.setattr(AppStatus, "should_exit_event", None)
    from app.dependencies import get_chat_service
    from app.main import app

    calls = []

    class FakeService:
        async def stream(self, scenario_id, turn, token, **kwargs):
            calls.append((scenario_id, turn.user_query, token))
            yield {"type": "chunk", "content": {"text": "hello", "done": False}}
            yield {"type": "done", "chat_id": "chat-one"}

    monkeypatch.setitem(app.dependency_overrides, get_chat_service, FakeService)
    monkeypatch.setattr(
        app.state,
        "config",
        SimpleNamespace(sse_keepalive_seconds=15, keycloak_url="", keycloak_realm=""),
        raising=False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"Authorization": "Bearer user-token"}
    ) as http:
        original = await http.post(
            "/buildplanner/scenarios/42/chat/stream", json={"request_id": "real-route", "user_query": "hello"}
        )
        replay = await http.post("/buildplanner/scenarios/42/chat/stream", json={"request_id": "real-route"})
        recovered = await http.get("/buildplanner/requests/real-route/stream")
    assert original.status_code == replay.status_code == recovered.status_code == 200
    assert original.text == replay.text == recovered.text
    assert calls == [(42, "hello", "user-token")]
