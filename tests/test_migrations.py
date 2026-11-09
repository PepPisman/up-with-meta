import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from dota_meta_bot.core.db import get_engine

TABLES = {"snapshots", "heroes", "hero_base_stats", "hero_winrate_stats"}
VIEWS = {
    "v_bracket_winrate",
    "v_bucket_winrate",
    "v_winrate_by_mode",
    "v_latest_winrate_snapshot",
    "v_latest_bracket_winrate",
    "v_latest_bucket_winrate",
    "v_latest_winrate_by_mode",
}

MARCH = "2026-03-01 10:00:00+00"
APRIL = "2026-04-01 10:00:00+00"
MAY = "2026-05-01 10:00:00+00"

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


def insert_snapshot(snapshot_at, patch, source="opendota", picks=None, wins=None) -> None:
    with get_engine().begin() as connection:
        connection.execute(
            text("INSERT INTO snapshots VALUES (:at, :patch, :source, NULL)"),
            {"at": snapshot_at, "patch": patch, "source": source},
        )
        if picks is None:
            return
        connection.execute(
            text(
                "INSERT INTO heroes VALUES "
                "(1, 'npc_dota_hero_antimage', 'Anti-Mage', 'agi', 'Melee', '{Carry}') "
                "ON CONFLICT DO NOTHING"
            )
        )
        columns = {"snapshot_at": snapshot_at, "hero_id": 1, "pro_ban": 0}
        for name in [f"b{index}" for index in range(1, 8)] + ["pub", "turbo", "pro"]:
            columns[f"{name}_pick"] = picks
            columns[f"{name}_win"] = wins
        column_list = ", ".join(columns)
        placeholders = ", ".join(f":{name}" for name in columns)
        connection.execute(
            text(f"INSERT INTO hero_winrate_stats ({column_list}) VALUES ({placeholders})"), columns
        )


def query_all(sql: str) -> list:
    with get_engine().connect() as connection:
        return connection.execute(text(sql)).all()


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


def test_the_latest_views_keep_only_the_newest_weekly_snapshot_of_a_patch(migrated_database):
    insert_snapshot(MARCH, "7.41", picks=100, wins=50)
    insert_snapshot(APRIL, "7.41", picks=200, wins=120)

    brackets = query_all("SELECT snapshot_at, picks, wins FROM v_latest_bracket_winrate")

    assert len(brackets) == 7
    assert {(row.picks, row.wins) for row in brackets} == {(200, 120)}
    assert {row.snapshot_at.month for row in brackets} == {4}


def test_the_plain_views_still_return_every_snapshot(migrated_database):
    insert_snapshot(MARCH, "7.41", picks=100, wins=50)
    insert_snapshot(APRIL, "7.41", picks=200, wins=120)

    assert len(query_all("SELECT 1 FROM v_bracket_winrate")) == 14


def test_the_bucket_and_mode_views_read_only_the_newest_snapshot(migrated_database):
    insert_snapshot(MARCH, "7.41", picks=100, wins=50)
    insert_snapshot(APRIL, "7.41", picks=200, wins=120)

    buckets = query_all("SELECT bucket, picks FROM v_latest_bucket_winrate")
    modes = query_all("SELECT mode, picks FROM v_latest_winrate_by_mode")

    assert {row.bucket: row.picks for row in buckets} == {"low": 600, "mid": 400, "high": 400}
    assert {row.mode: row.picks for row in modes} == {
        "ranked": 1400,
        "pub": 200,
        "turbo": 200,
        "pro": 200,
    }


def test_each_patch_keeps_its_own_latest_snapshot_and_only_the_newest_is_current(migrated_database):
    insert_snapshot(MARCH, "7.40", picks=100, wins=50)
    insert_snapshot(APRIL, "7.40", picks=150, wins=70)
    insert_snapshot(MAY, "7.41", picks=300, wins=160)

    rows = query_all("SELECT patch, snapshot_at, is_current FROM v_latest_winrate_snapshot")

    assert {row.patch: (row.snapshot_at.month, row.is_current) for row in rows} == {
        "7.40": (4, False),
        "7.41": (5, True),
    }


def test_the_current_flag_reaches_the_data_views(migrated_database):
    insert_snapshot(MARCH, "7.40", picks=100, wins=50)
    insert_snapshot(MAY, "7.41", picks=300, wins=160)

    rows = query_all("SELECT DISTINCT patch, is_current FROM v_latest_bracket_winrate")

    assert {row.patch: row.is_current for row in rows} == {"7.40": False, "7.41": True}


def test_each_source_has_its_own_current_snapshot(migrated_database):
    insert_snapshot(MARCH, "7.41", source="opendota", picks=100, wins=50)
    insert_snapshot(MAY, "7.41", source="opendota_file", picks=100, wins=50)

    rows = query_all("SELECT source, is_current FROM v_latest_winrate_snapshot")

    assert {row.source: row.is_current for row in rows} == {"opendota": True, "opendota_file": True}


def test_a_newer_snapshot_without_winrates_does_not_hide_the_one_that_has_them(migrated_database):
    insert_snapshot(MARCH, "7.41", source="opendota", picks=100, wins=50)
    insert_snapshot(APRIL, "7.41", source="opendota")
    insert_snapshot(MAY, "7.41", source="stratz")

    rows = query_all("SELECT source, snapshot_at FROM v_latest_winrate_snapshot")

    assert [(row.source, row.snapshot_at.month) for row in rows] == [("opendota", 3)]


def test_the_latest_views_agree_with_the_plain_views_for_the_newest_snapshot(migrated_database):
    insert_snapshot(MARCH, "7.41", picks=100, wins=50)
    insert_snapshot(APRIL, "7.41", picks=200, wins=120)
    with get_engine().begin() as connection:
        connection.execute(
            text(
                "UPDATE hero_winrate_stats SET "
                "b1_pick = 11, b1_win = 5, b2_pick = 22, b2_win = 9, b3_pick = 33, b3_win = 17, "
                "b4_pick = 44, b4_win = 20, b5_pick = 55, b5_win = 31, b6_pick = 66, b6_win = 30, "
                "b7_pick = 77, b7_win = 41, pub_pick = 88, pub_win = 40, "
                "turbo_pick = 99, turbo_win = 60, pro_pick = 10, pro_win = 3 "
                "WHERE snapshot_at = :at"
            ),
            {"at": APRIL},
        )
    pairs = [
        ("v_latest_bracket_winrate", "v_bracket_winrate", "bracket"),
        ("v_latest_bucket_winrate", "v_bucket_winrate", "bucket"),
        ("v_latest_winrate_by_mode", "v_winrate_by_mode", "mode"),
    ]

    for latest, plain, key in pairs:
        from_latest = query_all(f"SELECT hero_id, {key}, picks, wins, winrate FROM {latest} ORDER BY 1, 2")
        from_plain = query_all(
            f"SELECT hero_id, {key}, picks, wins, winrate FROM {plain} "
            f"WHERE snapshot_at = '{APRIL}' ORDER BY 1, 2"
        )
        assert from_latest, latest
        assert [tuple(row) for row in from_latest] == [tuple(row) for row in from_plain], latest


def test_the_latest_views_expose_the_same_columns_as_the_plain_views_plus_the_current_flag(migrated_database):
    pairs = [
        ("v_latest_bracket_winrate", "v_bracket_winrate"),
        ("v_latest_bucket_winrate", "v_bucket_winrate"),
        ("v_latest_winrate_by_mode", "v_winrate_by_mode"),
    ]
    inspector = inspect(get_engine())

    for latest, plain in pairs:
        latest_columns = [column["name"] for column in inspector.get_columns(latest)]
        plain_columns = [column["name"] for column in inspector.get_columns(plain)]
        assert latest_columns == [*plain_columns, "is_current"], latest


def test_downgrading_to_0006_restores_working_latest_views(migrated_database, alembic_config):
    insert_snapshot(MARCH, "7.41", picks=100, wins=50)

    command.downgrade(alembic_config, "0006")
    assert len(query_all("SELECT 1 FROM v_latest_bracket_winrate")) == 7
    command.upgrade(alembic_config, "head")

    assert len(query_all("SELECT 1 FROM v_latest_bracket_winrate")) == 7


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
