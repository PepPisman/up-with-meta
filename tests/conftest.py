from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from dota_meta_bot.core.config import get_settings
from dota_meta_bot.core.db import check_connection, get_engine

ROOT = Path(__file__).resolve().parents[1]
DATA_TABLES = ("hero_winrate_stats", "hero_base_stats", "snapshots", "heroes")


@pytest.fixture
def alembic_config() -> Config:
    return Config(str(ROOT / "alembic.ini"))


@pytest.fixture
def migrated_database(alembic_config):
    try:
        settings = get_settings()
    except ValidationError:
        pytest.skip("POSTGRES_PASSWORD is not set")
    if not settings.postgres_db.endswith("_test"):
        pytest.skip("database tests only run against a database whose name ends in _test")
    try:
        check_connection()
    except OperationalError:
        pytest.skip("test database is not reachable")

    command.upgrade(alembic_config, "head")
    with get_engine().begin() as connection:
        connection.execute(text(f"TRUNCATE {', '.join(DATA_TABLES)}"))
    yield
