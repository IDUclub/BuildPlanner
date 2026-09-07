import json
from typing import Any, AsyncIterator, Callable

from sse_starlette.sse import ServerSentEvent


async def sse_stream(
    events: AsyncIterator[dict[str, Any]],
    tail: Callable[[], ServerSentEvent] | None = None,
) -> AsyncIterator[ServerSentEvent]:
    """Превращает словари событий в SSE-кадры.

    Ключ `type` становится именем события, остальное — телом. Так же устроено
    в GenPlanner и GenBuilder, поэтому фронтенд читает все три потока одинаково.
    """
    async for event in events:
        payload = {key: value for key, value in event.items() if key != "type"}
        yield ServerSentEvent(event=event["type"], data=json.dumps(payload, ensure_ascii=False))
    if tail is not None:
        yield tail()
