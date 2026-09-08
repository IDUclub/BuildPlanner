import base64
import binascii
import json
import time

import aiohttp
from loguru import logger

from app.common.exceptions.http_exception import http_exception


class ServiceTokenProvider:
    """Keycloak client_credentials с кэшем.

    Двa потребителя. ChatStorage: туда ходят под сервисной учёткой, а конечного
    пользователя называет заголовок ``X-User-Id``. Urban API: под этой же учёткой
    создаётся проект-контейнер, в который складывается сгенерированный сценарий —
    чтобы он не оказался в проекте пользователя.
    """

    def __init__(self, keycloak_url: str, realm: str, client_id: str, client_secret: str):
        self._token_url = f"{keycloak_url.rstrip('/')}/realms/{realm}/protocol/openid-connect/token"
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._user_id: str | None = None

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
        self._user_id = _subject(self._token) or self._user_id
        logger.debug("Получен сервисный токен, живёт {} с", data.get("expires_in"))
        return self._token

    async def get_user_id(self) -> str:
        """Идентификатор самой сервисной учётки — claim ``sub`` из её же токена.

        Urban API ждёт его в `POST /api/v1/projects?user_id=...`. Отдельной настройки
        для него нет намеренно: id учётки и секрет, которым она получена, обязаны
        описывать одну и ту же учётку, а из токена это следует само.
        """
        if self._user_id is None or not self._token or time.monotonic() >= self._expires_at:
            await self.get_token()
        if not self._user_id:
            raise http_exception(502, "В сервисном токене нет claim `sub`")
        return self._user_id


def _subject(token: str) -> str | None:
    """Читает `sub` из payload JWT.

    Подпись не проверяем: токен только что получен от Keycloak по TLS и используется
    как есть — здесь нужен не факт доверия, а имя учётки, под которой мы пишем.
    """
    parts = token.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        logger.warning("Не удалось разобрать payload сервисного токена")
        return None
    subject = claims.get("sub")
    return subject if isinstance(subject, str) and subject else None
