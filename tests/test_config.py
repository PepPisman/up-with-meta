import pytest
from pydantic import ValidationError

from dota_meta_bot.core.config import Settings

POSTGRES_ENV_VARS = (
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "STRATZ_API_TOKEN",
)


@pytest.fixture(autouse=True)
def clean_postgres_environment(monkeypatch):
    for name in POSTGRES_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_database_url_uses_psycopg_driver_and_local_defaults():
    settings = Settings(_env_file=None, postgres_password="secret")

    url = settings.database_url

    assert url.drivername == "postgresql+psycopg"
    assert url.username == "dota"
    assert url.host == "localhost"
    assert url.port == 5432
    assert url.database == "dota_meta"
    assert url.password == "secret"


def test_database_url_honours_overrides():
    settings = Settings(
        _env_file=None,
        postgres_user="app",
        postgres_password="p",
        postgres_db="other",
        postgres_host="db",
        postgres_port=5432,
    )

    assert settings.database_url.render_as_string(hide_password=True) == (
        "postgresql+psycopg://app:***@db:5432/other"
    )


def test_password_is_required():
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_the_stratz_token_is_optional_and_hidden():
    assert Settings(_env_file=None, postgres_password="x").stratz_api_token is None

    settings = Settings(_env_file=None, postgres_password="x", stratz_api_token="tok-123")

    assert settings.stratz_api_token.get_secret_value() == "tok-123"
    assert "tok-123" not in repr(settings)


def test_password_is_hidden_in_repr():
    settings = Settings(_env_file=None, postgres_password="secret")

    assert "secret" not in repr(settings)
