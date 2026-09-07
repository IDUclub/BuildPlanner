from fastapi import Request

from app.common.exceptions.http_exception import http_exception


async def verify_bearer_token(request: Request) -> str:
    """Достаёт пользовательский Bearer и отдаёт его как есть.

    Сервис токен не валидирует — это делают Urban API, GenPlanner и GenBuilder,
    в которые он прокидывается дальше по цепочке (так же устроено в обоих соседних сервисах).
    """
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        raise http_exception(403, "Отсутствует заголовок Authorization: Bearer <token>")
    token = header.split(" ", 1)[1].strip()
    if not token:
        raise http_exception(403, "Пустой Bearer-токен")
    return token
