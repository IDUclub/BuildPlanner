from typing import Any

import aiohttp
from loguru import logger

from app.common.exceptions.http_exception import http_exception


class AsyncJsonApiHandler:
    """Тонкая обёртка над aiohttp: любой не-2xx ответ превращается в ``http_exception``.

    Один экземпляр на внешний сервис, живёт в ``app.state`` весь срок жизни приложения.
    """

    def __init__(self, base_url: str, timeout_seconds: int = 60, service_name: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self.service_name = service_name or self.base_url

    async def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return await self._request("GET", path, params=params, headers=headers)

    async def post(
        self,
        path: str,
        json_data: Any = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return await self._request("POST", path, json_data=json_data, params=params, headers=headers)

    async def _request(
        self,
        method: str,
        path: str,
        json_data: Any = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        logger.debug("{} {}", method, url)
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.request(method, url, json=json_data, params=params, headers=headers) as response:
                    body = await response.text()
                    if response.status < 200 or response.status >= 300:
                        raise http_exception(
                            response.status,
                            f"{self.service_name} ответил ошибкой",
                            _input={"method": method, "url": url},
                            _detail=body[:2000],
                        )
                    if not body:
                        return None
                    return await response.json(content_type=None)
        except aiohttp.ClientError as exc:
            raise http_exception(
                503,
                f"{self.service_name} недоступен",
                _input={"method": method, "url": url},
                _detail=str(exc),
            ) from exc
        except TimeoutError as exc:
            raise http_exception(
                504,
                f"{self.service_name} не ответил за отведённое время",
                _input={"method": method, "url": url, "timeout": self.timeout.total},
            ) from exc
