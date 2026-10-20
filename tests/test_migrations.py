import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from dota_meta_bot.core.db import get_engine

TABLES = {"snapshots", "heroes", "hero_base_stats", "hero_winrate_stats"}
VIEWS = {"v_bracket_winrate", "v_bucket_winrate", "v_winrate_by_mode"}

SNAPSHOT_AT = "2026-10-19 10:00:00+03"
ANTI_MAGE_BRACKETS = [
    (12296, 6226),
    (43584, 22015),
    (87053, 44563),
    (138861, 71076),
    (154208, 79473),
    (88068, 45098),
    (61323, 31343),
]


def existing_names() -> set[str]:
    inspector = inspect(get_engine())
    return set(inspector.get_table_names()) | set(inspector.get_view_names())


def insert_anti_mage() -> None:
    columns = {"snapshot_at": SNAPSHOT_AT, "hero_id": 1}
    for index, (picks, wins) in enumerate(ANTI_MAGE_BRACKETS, start=1):
        columns[f"b{index}_pick"] = picks
        columns[f"b{index}_win"] = wins
    columns.update(
        pub_pick=636697,
        pub_win=325662,
        turbo_pick=274656,
        turbo_win=139036,
        pro_pick=43,
        pro_win=18,
        pro_ban=59,
    )
    column_list = ", ".join(columns)
    placeholders = ", ".join(f":{name}" for name in columns)
    with get_engine().begin() as connection:
        connection.execute(
            text("INSERT INTO snapshots VALUES (:at, '7.41', 'opendota', NULL)"),
            {"at": SNAPSHOT_AT},
        )
        connection.execute(
            text(
                "INSERT INTO heroes VALUES "
                "(1, 'npc_dota_hero_antimage', 'Anti-Mage', 'agi', 'Melee', '{Carry}')"
            )
        )
        connection.execute(
            text(f"INSERT INTO hero_winrate_stats ({column_list}) VALUES ({placeholders})"),
            columns,
        )


def test_upgrade_from_an_empty_database_creates_every_table_and_view(migrated_database, alembic_config):
    command.downgrade(alembic_config, "base")
    assert not ((TABLES | VIEWS) & existing_names())

    command.upgrade(alembic_config, "head")

    assert (TABLES | VIEWS) <= existing_names()


def test_downgrade_removes_every_table_and_view(migrated_database, alembic_config):
    command.downgrade(alembic_config, "base")

    assert not ((TABLES | VIEWS) & existing_names())


def test_upgrade_is_repeatable_after_downgrade(migrated_database, alembic_config):
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")

    assert (TABLES | VIEWS) <= existing_names()


def test_mode_view_keeps_pub_separate_from_summed_brackets(migrated_database):
    insert_anti_mage()

    with get_engine().connect() as connection:
        rows = connection.execute(
            text("SELECT mode, picks, wins FROM v_winrate_by_mode ORDER BY mode")
        ).all()

    assert {row.mode: (row.picks, row.wins) for row in rows} == {
        "ranked": (585393, 299794),
        "pub": (636697, 325662),
        "turbo": (274656, 139036),
        "pro": (43, 18),
    }


def test_bucket_view_sums_picks_and_wins_per_bucket(migrated_database):
    insert_anti_mage()

    with get_engine().connect() as connection:
        rows = connection.execute(
            text("SELECT bucket, picks, wins FROM v_bucket_winrate")
        ).all()

    assert {row.bucket: (row.picks, row.wins) for row in rows} == {
        "low": (142933, 72804),
        "mid": (293069, 150549),
        "high": (149391, 76441),
    }


def test_bracket_view_exposes_one_row_per_bracket(migrated_database):
    insert_anti_mage()

    with get_engine().connect() as connection:
        row = connection.execute(
            text("SELECT picks, wins FROM v_bracket_winrate WHERE bracket = 5")
        ).one()

    assert (row.picks, row.wins) == (154208, 79473)


def test_wins_above_picks_are_rejected(migrated_database):
    insert_anti_mage()

    with pytest.raises(IntegrityError):
        with get_engine().begin() as connection:
            connection.execute(text("UPDATE hero_winrate_stats SET turbo_win = turbo_pick + 1"))


def test_stat_rows_require_an_existing_snapshot(migrated_database):
    insert_anti_mage()

    with pytest.raises(IntegrityError):
        with get_engine().begin() as connection:
            connection.execute(
                text("INSERT INTO hero_winrate_stats (snapshot_at, hero_id) VALUES ('2030-01-01', 1)")
            )
