from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация сервиса. Читается из окружения и `.env.development`."""

    model_config = SettingsConfigDict(env_file=(".env.development", ".env"), extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"
    log_file: str = "buildplanner.log"

    # внешние сервисы пайплайна
    urban_api: str = "https://urban-api.testing.idulab.ru"
    genplanner_api: str = "http://localhost:8081"
    genbuilder_api: str = "http://localhost:8082"
    sirtep_api: str = ""
    chat_storage_api: str = ""

    # LLM
    llm_api: str = ""
    llm_model: str = ""
    llm_temperature: float = 0.2
    llm_timeout_seconds: int = 900

    # Keycloak service account: под ним ходим в ChatStorage и пишем сценарии в Urban API
    keycloak_url: str = ""
    keycloak_realm: str = "IDU"
    keycloak_client_id: str = "buildplanner"
    keycloak_client_secret: str = ""

    # публикация результата в Urban API
    publish_to_urban: bool = False
    service_project_prefix: str = "BuildPlanner"
    publish_zone_source: str = "BuildPlanner"
    publish_max_concurrency: int = 8

    # хранилище слоёв для истории чата: MinIO, если заданы все четыре MINIO_*, иначе локальный диск
    public_base_url: str = ""
    outputs_dir: str = "outputs"
    minio_address: str = ""
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket_name: str = ""
    minio_region: str = "us-east-1"

    # очерёдность строительства по опубликованному сценарию
    sirtep_periods: int = 20
    sirtep_max_area_per_period: int = 100_000

    # таймауты и лимиты
    urban_api_timeout_seconds: int = 60
    genplanner_timeout_seconds: int = 1800
    genbuilder_timeout_seconds: int = 1800
    sirtep_timeout_seconds: int = 1800
    sirtep_provision_timeout_seconds: int = 300
    sirtep_poll_seconds: int = 5
    sse_keepalive_seconds: int = 15
    genplanner_cache_ttl_seconds: int = 3600

    @property
    def chat_storage_enabled(self) -> bool:
        """История чата опциональна: без ChatStorage поток работает, но не запоминается."""
        return bool(self.chat_storage_api and self.keycloak_url and self.keycloak_client_secret)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api and self.llm_model)

    @property
    def service_account_enabled(self) -> bool:
        return bool(self.keycloak_url and self.keycloak_client_secret)

    @property
    def urban_write_enabled(self) -> bool:
        """Писать в Urban API можно только под сервисной учёткой.

        Токен пользователя тут не годится: сгенерированный сценарий не должен
        оказаться в его проекте — за это отвечает владелец, а владельца задаёт
        только `POST /api/v1/projects?user_id=...`.
        """
        return self.publish_to_urban and self.service_account_enabled

    @property
    def sirtep_enabled(self) -> bool:
        """Очередь строительства считается по опубликованному сценарию.

        Без записи в Urban API SIRTEP нечего читать: сценарий существует только у нас
        в памяти, а он ходит за ним в Urban API сам.
        """
        return bool(self.sirtep_api) and self.urban_write_enabled
