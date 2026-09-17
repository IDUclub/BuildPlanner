"""Хранилище слоёв и ручка их отдачи: ключи, ссылки, конфигурация."""

import json

import pytest
from fastapi import HTTPException
from fastapi.responses import RedirectResponse

from app.common.object_storage.object_storage import (
    LocalStorage,
    ObjectStorageError,
    build_object_storage,
    parse_minio_address,
)
from app.pipeline.geo_layers import LayerStore, layer_descriptor, object_key
from app.pipeline.pipeline_controller import layer_file
from app.settings import Settings

RESULT_ID = "0123456789abcdef0123456789abcdef"
LAYER = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"name": "Квартал"}}]}


def _storage_params(**overrides: str) -> dict[str, object]:
    params: dict[str, object] = {
        "address": "",
        "access_key": "",
        "secret_key": "",
        "bucket": "",
        "region": "us-east-1",
        "outputs_dir": "outputs",
    }
    return {**params, **overrides}


def test_object_key_is_derived_from_the_run_and_the_slot():
    assert object_key(RESULT_ID, "zones") == f"{RESULT_ID}/zones.geojson"


@pytest.mark.parametrize("slot, result_id", [("secrets", RESULT_ID), ("zones", "../../etc"), ("zones", "ABC")])
def test_object_key_refuses_anything_but_a_known_slot_and_a_run_id(slot, result_id):
    with pytest.raises(ValueError):
        object_key(result_id, slot)


def test_local_storage_cannot_be_escaped(tmp_path):
    with pytest.raises(ObjectStorageError):
        LocalStorage(str(tmp_path)).put_json(LAYER, "../outside.geojson")


def test_descriptor_prefers_the_public_address():
    """В истории чата ссылка должна открываться с фронтенда, а не только из пода."""
    descriptor = layer_descriptor("roads", RESULT_ID, "https://planner.example/", "http://10.0.0.5:8080/")
    assert descriptor["url"] == f"https://planner.example/buildplanner/files/roads/{RESULT_ID}"
    assert descriptor["download_url"] is None
    assert descriptor["mime_type"] == "application/geo+json"


def test_descriptor_falls_back_to_the_request_address():
    descriptor = layer_descriptor("zones", RESULT_ID, None, "http://localhost:8080/")
    assert descriptor["url"] == f"http://localhost:8080/buildplanner/files/zones/{RESULT_ID}"


def test_partial_minio_config_is_an_error_not_a_silent_fallback():
    """Упасть молча на локальный диск на стенде — выдать ссылки, умирающие при перезапуске."""
    with pytest.raises(ObjectStorageError, match="MINIO_SECRET_KEY"):
        build_object_storage(**_storage_params(address="minio:9000", access_key="key", bucket="layers"))


def test_no_minio_config_means_local_disk(tmp_path):
    assert isinstance(build_object_storage(**_storage_params(outputs_dir=str(tmp_path))), LocalStorage)


@pytest.mark.parametrize(
    "address, expected",
    [
        ("http://10.0.0.1:9000", ("10.0.0.1:9000", False)),
        ("https://minio.example/", ("minio.example", True)),
        ("minio:9000", ("minio:9000", False)),
    ],
)
def test_minio_address_is_split_into_endpoint_and_tls_flag(address, expected):
    """Клиент MinIO падает на адресе со схемой, а в .env адрес удобнее писать как URL."""
    assert parse_minio_address(address) == expected


@pytest.mark.parametrize("address", ["ftp://minio:9000", "http://minio:9000/bucket", "http://"])
def test_minio_address_with_a_path_or_foreign_scheme_is_refused(address):
    with pytest.raises(ObjectStorageError):
        parse_minio_address(address)


def test_settings_read_minio_variables(monkeypatch):
    monkeypatch.setenv("MINIO_ADDRESS", "http://minio:9000")
    monkeypatch.setenv("MINIO_BUCKET_NAME", "buildplanner")
    settings = Settings(_env_file=None)
    assert settings.minio_address == "http://minio:9000"
    assert settings.minio_bucket_name == "buildplanner"


async def _read(response) -> bytes:
    return b"".join([chunk async for chunk in response.body_iterator])


@pytest.mark.asyncio
async def test_stored_layer_is_served_back_unchanged(tmp_path):
    storage = LocalStorage(str(tmp_path))
    descriptor = await LayerStore(storage).store("buildings", RESULT_ID, LAYER)
    assert descriptor["name"] == "buildings"

    response = layer_file("buildings", RESULT_ID, storage, Settings(_env_file=None))
    assert response.media_type == "application/geo+json"
    assert json.loads(await _read(response)) == LAYER


@pytest.mark.asyncio
async def test_durable_layer_url_redirects_to_a_fresh_object_storage_url(tmp_path):
    class PresigningStorage(LocalStorage):
        def presigned_url(self, object_key, expires_seconds):
            assert object_key == f"{RESULT_ID}/zones.geojson"
            assert expires_seconds == 120
            return "https://minio.example/layer?signature=fresh"

    storage = PresigningStorage(str(tmp_path))
    await LayerStore(storage, url_ttl_seconds=120).store("zones", RESULT_ID, LAYER)
    settings = Settings(_env_file=None, geo_layer_url_ttl_seconds=120)

    response = layer_file("zones", RESULT_ID, storage, settings)

    assert isinstance(response, RedirectResponse)
    assert response.status_code == 307
    assert response.headers["location"] == "https://minio.example/layer?signature=fresh"


@pytest.mark.parametrize("slot, result_id", [("zones", RESULT_ID), ("secrets", RESULT_ID), ("zones", "nope")])
def test_missing_or_malformed_layer_is_404(tmp_path, slot, result_id):
    with pytest.raises(HTTPException) as exc_info:
        layer_file(slot, result_id, LocalStorage(str(tmp_path)), Settings(_env_file=None))
    assert exc_info.value.status_code == 404


def test_service_without_storage_answers_404():
    with pytest.raises(HTTPException) as exc_info:
        layer_file("zones", RESULT_ID, None, Settings(_env_file=None))
    assert exc_info.value.status_code == 404
