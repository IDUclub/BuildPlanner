import base64
import binascii
import json

from loguru import logger


def extract_user_id(access_token: str) -> str | None:
    """Читает `sub` из payload'а JWT без проверки подписи.

    Подпись валидируют нижестоящие сервисы; здесь `sub` нужен только как `X-User-Id`
    для ChatStorage, поэтому неразобранный токен — это не ошибка, а отсутствие идентити.
    """
    try:
        payload_part = access_token.split(".")[1]
        padded = payload_part + "=" * (-len(payload_part) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (IndexError, ValueError, binascii.Error, json.JSONDecodeError) as exc:
        logger.warning("Не удалось разобрать JWT для X-User-Id: {}", exc)
        return None
    return payload.get("sub")
