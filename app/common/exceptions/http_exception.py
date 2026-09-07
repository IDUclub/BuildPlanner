from typing import Any

from fastapi import HTTPException


def http_exception(
    status_code: int,
    msg: str,
    _input: Any = None,
    _detail: Any = None,
) -> HTTPException:
    """Единая форма ошибки во всех сервисах платформы: ``{msg, input, detail}``."""
    return HTTPException(
        status_code=status_code,
        detail={"msg": msg, "input": _input, "detail": _detail},
    )
