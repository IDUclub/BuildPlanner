import time

import aiohttp
from loguru import logger

from app.common.exceptions.http_exception import http_exception


class ServiceTokenProvider:
    """Keycloak client_credentials с кэшем.

    Нужен только для ChatStorage: туда ходят под сервисной учёткой,
    а конечного пользователя называет заголовок ``X-User-Id``.
    """

    def __init__(self, keycloak_url: str, realm: str, client_id: str, client_secret: str):
        self._token_url = f"{keycloak_url.rstrip('/')}/realms/{realm}/protocol/openid-connect/token"
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._expires_at: float = 0.0

    async def get_token(self) -> str:
        if self._token and time.monotonic() < self._expires_at:
            return self._token
        payload = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                async with session.post(self._token_url, data=payload) as response:
                    if response.status != 200:
                        raise http_exception(
                            502,
                            "Keycloak не выдал сервисный токен",
                            _detail=(await response.text())[:1000],
                        )
                    data = await response.json()
        except aiohttp.ClientError as exc:
            raise http_exception(503, "Keycloak недоступен", _detail=str(exc)) from exc

        self._token = data["access_token"]
        # 30 секунд запаса, чтобы не отдать токен, который протухнет в полёте
        self._expires_at = time.monotonic() + max(int(data.get("expires_in", 60)) - 30, 10)
        logger.debug("Получен сервисный токен, живёт {} с", data.get("expires_in"))
        return self._token
