from app.settings import Settings


def test_writing_to_urban_api_is_off_until_enabled_explicitly(monkeypatch):
    """A push to `dev` deploys automatically; writing to the shared stand must not switch on by itself."""
    monkeypatch.delenv("PUBLISH_TO_URBAN", raising=False)
    settings = Settings(_env_file=None, keycloak_url="http://keycloak", keycloak_client_secret="secret")
    assert settings.publish_to_urban is False
    assert settings.urban_write_enabled is False


def test_writing_needs_both_the_flag_and_the_service_account(monkeypatch):
    monkeypatch.delenv("KEYCLOAK_CLIENT_SECRET", raising=False)
    assert Settings(_env_file=None, publish_to_urban=True, keycloak_url="http://keycloak").urban_write_enabled is False
    assert (
        Settings(
            _env_file=None, publish_to_urban=True, keycloak_url="http://keycloak", keycloak_client_secret="secret"
        ).urban_write_enabled
        is True
    )


def test_construction_queue_needs_both_the_address_and_publication():
    """SIRTEP читает сценарий из Urban API: без публикации ему нечего читать."""
    common = {"_env_file": None, "keycloak_url": "http://keycloak", "keycloak_client_secret": "secret"}
    assert Settings(**common, sirtep_api="http://sirtep:5100", publish_to_urban=False).sirtep_enabled is False
    assert Settings(**common, sirtep_api="", publish_to_urban=True).sirtep_enabled is False
    assert Settings(**common, sirtep_api="http://sirtep:5100", publish_to_urban=True).sirtep_enabled is True
