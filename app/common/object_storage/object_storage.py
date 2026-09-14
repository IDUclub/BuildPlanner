"""Хранилище сгенерированных слоёв: MinIO на стенде, локальный диск в разработке.

Объект адресуется ключом, выведенным из id результата, — базы соответствий нет, и ручке
отдачи файла не нужен никакой поиск. Настройки — переменные окружения `MINIO_*`.

Браузеру объекты напрямую не отдаются: MinIO живёт в закрытой сети, байты идут
потоком через API (`open_stream`), а не по presigned-ссылке.
"""

import io
import json
from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from loguru import logger

_CHUNK_SIZE = 64 * 1024
_CONTENT_TYPE = "application/geo+json"
DEFAULT_REGION = "us-east-1"


class ObjectStorageError(RuntimeError):
    """Объект не удалось записать или прочитать."""


class ObjectStorage(ABC):
    @abstractmethod
    def put_json(self, payload: dict[str, Any], object_key: str) -> None:
        """Кладёт `payload` как JSON в UTF-8."""

    @abstractmethod
    def exists(self, object_key: str) -> bool:
        """Лежит ли ещё объект."""

    @abstractmethod
    def open_stream(self, object_key: str) -> Iterator[bytes]:
        """Байты объекта кусками, без загрузки целиком в память."""


def _encode(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class LocalStorage(ObjectStorage):
    """Файловая система — когда MinIO не настроен."""

    def __init__(self, root: str):
        self._root = Path(root).resolve()

    def _resolve(self, object_key: str) -> Path:
        """Путь под корнем хранилища; выйти за корень ключом нельзя."""
        path = (self._root / object_key).resolve()
        if not path.is_relative_to(self._root):
            raise ObjectStorageError(f"Ключ указывает за пределы хранилища: {object_key!r}")
        return path

    def put_json(self, payload: dict[str, Any], object_key: str) -> None:
        path = self._resolve(object_key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(_encode(payload))
        except OSError as exc:
            raise ObjectStorageError(f"Не удалось записать {object_key}: {exc}") from exc

    def exists(self, object_key: str) -> bool:
        return self._resolve(object_key).is_file()

    def open_stream(self, object_key: str) -> Iterator[bytes]:
        with self._resolve(object_key).open("rb") as handle:
            while chunk := handle.read(_CHUNK_SIZE):
                yield chunk


@contextmanager
def _translated_errors(action: str) -> Iterator[None]:
    """Клиент MinIO бросает и `S3Error`, и сырые ошибки urllib3 — наружу уходит одна."""
    try:
        yield
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise ObjectStorageError(f"Хранилище не смогло {action}: {exc}") from exc


class MinioStorage(ObjectStorage):
    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        secure: bool = False,
        region: str = DEFAULT_REGION,
    ):
        from minio import Minio  # pylint: disable=import-outside-toplevel

        self._bucket = bucket
        # Регион явно: без него клиент сначала спрашивает расположение бакета, а на это
        # у сервисной учётки нет права. Бакет заводится заранее — создавать его тоже нечем.
        self._client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure, region=region)

    def put_json(self, payload: dict[str, Any], object_key: str) -> None:
        data = _encode(payload)
        with _translated_errors(f"записать {object_key}"):
            self._client.put_object(self._bucket, object_key, io.BytesIO(data), len(data), content_type=_CONTENT_TYPE)

    def exists(self, object_key: str) -> bool:
        from minio.error import S3Error  # pylint: disable=import-outside-toplevel

        try:
            self._client.stat_object(self._bucket, object_key)
        except S3Error as exc:
            if exc.code in ("NoSuchKey", "NoSuchBucket"):
                return False
            raise ObjectStorageError(f"Хранилище не смогло проверить {object_key}: {exc}") from exc
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise ObjectStorageError(f"Хранилище не смогло проверить {object_key}: {exc}") from exc
        return True

    def open_stream(self, object_key: str) -> Iterator[bytes]:
        with _translated_errors(f"прочитать {object_key}"):
            response = self._client.get_object(self._bucket, object_key)
        try:
            yield from response.stream(_CHUNK_SIZE)
        finally:
            response.close()
            response.release_conn()


def parse_minio_address(address: str) -> tuple[str, bool]:
    """`http://host:9000` -> (`host:9000`, secure). Клиенту MinIO нужен адрес без схемы.

    Без схемы — обычный HTTP: так MinIO обычно и стоит во внутренней сети.
    """
    parts = urlsplit(address if "://" in address else f"http://{address}")
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ObjectStorageError(f"MINIO_ADDRESS должен быть вида http://host:port, а не {address!r}")
    if parts.path.strip("/") or parts.query:
        raise ObjectStorageError(f"MINIO_ADDRESS не должен содержать путь: {address!r}")
    return parts.netloc, parts.scheme == "https"


def build_object_storage(
    *,
    address: str,
    access_key: str,
    secret_key: str,
    bucket: str,
    region: str,
    outputs_dir: str,
) -> ObjectStorage:
    """MinIO, если заданы все четыре параметра; локальный диск, если ни одного.

    Частичный набор — ошибка конфигурации: молча упасть на локальный диск на стенде
    значит выдать ссылки, которые умрут при первом перезапуске контейнера.
    """
    minio_params = {
        "MINIO_ADDRESS": address,
        "MINIO_ACCESS_KEY": access_key,
        "MINIO_SECRET_KEY": secret_key,
        "MINIO_BUCKET_NAME": bucket,
    }
    missing = [name for name, value in minio_params.items() if not value]
    if not missing:
        endpoint, secure = parse_minio_address(address)
        logger.info("Хранилище слоёв: MinIO {}, бакет {}", address, bucket)
        return MinioStorage(endpoint, access_key, secret_key, bucket, secure=secure, region=region)
    if len(missing) < len(minio_params):
        raise ObjectStorageError("MinIO настроен не полностью, не хватает: " + ", ".join(missing))
    logger.info("Хранилище слоёв: локальный диск {}", outputs_dir)
    return LocalStorage(outputs_dir)
