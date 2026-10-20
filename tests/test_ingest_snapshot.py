import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text

from dota_meta_bot.core.db import get_engine
from dota_meta_bot.ingest.heroes import DEFAULT_HEROES_FILE, InvalidHeroData
from dota_meta_bot.ingest.opendota import OpenDotaError
from dota_meta_bot.ingest.snapshot import (
    LOCK_KEY,
    SOURCE_API,
    SOURCE_FILE,
    IngestAlreadyRunning,
    InvalidPatch,
    UnknownHeroes,
    ingest_snapshot,
    main,
    parse_base_stats,
    parse_winrates,
    validate_patch,
)

FIXTURE = Path(__file__).parent / "fixtures" / "heroStats_sample.json"


def sample_payload() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def hero_by_name(payload: list[dict], name: str) -> dict:
    return next(hero for hero in payload if hero["localized_name"] == name)


class FakeFetch:
    def __init__(self, payload=None, error=None):
        self.payload = sample_payload() if payload is None else payload
        self.error = error
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.error:
            raise self.error
        return copy.deepcopy(self.payload)


def count(table: str) -> int:
    with get_engine().connect() as connection:
        return connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()


@pytest.mark.parametrize("patch", ["7.41", "7.39c", "7.4", "10.0"])
def test_valid_patch_labels_are_accepted(patch):
    assert validate_patch(patch) == patch


@pytest.mark.parametrize("patch", ["", "7", "7.41.1", "latest", "7.41 ", "v7.41", "7.41C", "7.41cc"])
def test_invalid_patch_labels_are_rejected(patch):
    with pytest.raises(InvalidPatch):
        validate_patch(patch)


def test_base_stats_are_mapped_from_the_api_object():
    payload = sample_payload()
    anti_mage = hero_by_name(payload, "Anti-Mage")

    rows = {row.hero_id: row for row in parse_base_stats(payload)}
    row = rows[anti_mage["id"]]

    assert row.base_str == anti_mage["base_str"]
    assert row.base_agi == anti_mage["base_agi"]
    assert row.base_int == anti_mage["base_int"]
    assert row.agi_gain == anti_mage["agi_gain"]
    assert (row.base_attack_min, row.base_attack_max) == (
        anti_mage["base_attack_min"],
        anti_mage["base_attack_max"],
    )
    assert row.move_speed == anti_mage["move_speed"]
    assert row.turn_rate is None


def test_a_null_turn_rate_is_allowed_and_a_value_is_kept():
    payload = sample_payload()
    payload[0]["turn_rate"] = 0.8

    assert parse_base_stats(payload)[0].turn_rate == 0.8


@pytest.mark.parametrize(
    "change",
    [
        {"base_attack_min": 50, "base_attack_max": 40},
        {"str_gain": 9},
        {"agi_gain": -1},
        {"move_speed": 50},
        {"move_speed": 900},
        {"attack_range": 0},
        {"base_str": -1},
    ],
)
def test_base_stats_reject_values_the_database_would_reject(change):
    payload = sample_payload()
    payload[0].update(change)

    with pytest.raises(InvalidHeroData):
        parse_base_stats(payload)


def test_base_stats_reject_a_missing_field():
    payload = sample_payload()
    del payload[0]["base_str"]

    with pytest.raises(InvalidHeroData):
        parse_base_stats(payload)


def test_base_stats_errors_name_the_offending_hero():
    payload = sample_payload()
    hero_by_name(payload, "Crystal Maiden")["str_gain"] = 9

    with pytest.raises(InvalidHeroData, match="Crystal Maiden"):
        parse_base_stats(payload)


@pytest.mark.parametrize("payload", [[], None, {}])
def test_base_stats_and_winrates_reject_empty_or_non_list_payloads(payload):
    with pytest.raises(InvalidHeroData):
        parse_base_stats(payload)
    with pytest.raises(InvalidHeroData):
        parse_winrates(payload)


def test_winrates_are_mapped_from_the_api_keys():
    payload = sample_payload()
    anti_mage = hero_by_name(payload, "Anti-Mage")

    row = {row.hero_id: row for row in parse_winrates(payload)}[anti_mage["id"]]

    assert (row.b1_pick, row.b1_win) == (anti_mage["1_pick"], anti_mage["1_win"])
    assert (row.b7_pick, row.b7_win) == (anti_mage["7_pick"], anti_mage["7_win"])
    assert (row.pub_pick, row.pub_win) == (anti_mage["pub_pick"], anti_mage["pub_win"])
    assert (row.turbo_pick, row.turbo_win) == (anti_mage["turbo_picks"], anti_mage["turbo_wins"])
    assert (row.pro_pick, row.pro_win, row.pro_ban) == (
        anti_mage["pro_pick"],
        anti_mage["pro_win"],
        anti_mage["pro_ban"],
    )


def test_winrates_ignore_the_empty_immortal_bracket():
    payload = sample_payload()
    payload[0]["8_pick"] = 999

    row = parse_winrates(payload)[0]

    assert not hasattr(row, "b8_pick")
    assert "8_pick" not in row.model_dump()


@pytest.mark.parametrize(
    "change",
    [
        {"3_win": 10**9},
        {"pub_win": 10**9},
        {"turbo_wins": 10**9},
        {"pro_win": 10**9},
        {"2_pick": -1},
        {"pro_ban": -1},
    ],
)
def test_winrates_reject_wins_above_picks_and_negative_counts(change):
    payload = sample_payload()
    payload[0].update(change)

    with pytest.raises(InvalidHeroData):
        parse_winrates(payload)


def test_winrates_reject_a_missing_key():
    payload = sample_payload()
    del payload[0]["turbo_picks"]

    with pytest.raises(InvalidHeroData):
        parse_winrates(payload)


@pytest.mark.skipif(not DEFAULT_HEROES_FILE.exists(), reason="data/heroStats.json not saved yet")
def test_the_full_saved_api_response_passes_every_parser():
    payload = json.loads(DEFAULT_HEROES_FILE.read_text(encoding="utf-8"))

    assert len(parse_base_stats(payload)) == len(payload)
    assert len(parse_winrates(payload)) == len(payload)


def test_a_first_ingest_stores_the_snapshot_heroes_and_winrates_but_no_base_stats(migrated_database):
    fetch = FakeFetch()
    before = datetime.now(UTC)

    result = ingest_snapshot("7.41", fetch=fetch)

    assert fetch.calls == 1
    assert result.fetched is True
    assert result.patch == "7.41"
    assert result.source == SOURCE_API
    assert result.heroes == 3
    assert before <= result.snapshot_at <= datetime.now(UTC)
    assert (count("snapshots"), count("heroes"), count("hero_base_stats"), count("hero_winrate_stats")) == (
        1,
        3,
        0,
        3,
    )
    with get_engine().connect() as connection:
        row = connection.execute(text("SELECT patch, source, days_since_patch FROM snapshots")).one()
    assert tuple(row) == ("7.41", SOURCE_API, None)


def test_base_stats_are_stored_only_when_asked_for(migrated_database):
    ingest_snapshot("7.41", fetch=FakeFetch(), include_base_stats=True)

    assert (count("snapshots"), count("hero_base_stats"), count("hero_winrate_stats")) == (1, 3, 3)


def test_broken_base_stats_do_not_block_winrates_by_default(migrated_database):
    payload = sample_payload()
    payload[0]["str_gain"] = 9

    result = ingest_snapshot("7.41", fetch=FakeFetch(payload))

    assert result.fetched is True
    assert (count("hero_base_stats"), count("hero_winrate_stats")) == (0, 3)


def test_broken_base_stats_block_everything_when_base_stats_are_requested(migrated_database):
    payload = sample_payload()
    payload[0]["str_gain"] = 9

    with pytest.raises(InvalidHeroData):
        ingest_snapshot("7.41", fetch=FakeFetch(payload), include_base_stats=True)

    assert (count("snapshots"), count("heroes"), count("hero_base_stats"), count("hero_winrate_stats")) == (
        0,
        0,
        0,
        0,
    )


def test_a_second_ingest_of_the_same_patch_is_skipped_without_calling_the_api(migrated_database):
    first = ingest_snapshot("7.41", fetch=FakeFetch())
    fetch = FakeFetch()

    second = ingest_snapshot("7.41", fetch=fetch)

    assert fetch.calls == 0
    assert second.fetched is False
    assert second.snapshot_at == first.snapshot_at
    assert second.heroes == 0
    assert (count("snapshots"), count("hero_base_stats"), count("hero_winrate_stats")) == (1, 0, 3)


def test_force_fetches_again_and_adds_a_second_snapshot(migrated_database):
    ingest_snapshot("7.41", fetch=FakeFetch())
    fetch = FakeFetch()

    result = ingest_snapshot("7.41", fetch=fetch, force=True)

    assert fetch.calls == 1
    assert result.fetched is True
    assert (count("snapshots"), count("heroes"), count("hero_base_stats"), count("hero_winrate_stats")) == (
        2,
        3,
        0,
        6,
    )


def test_a_new_patch_label_fetches(migrated_database):
    ingest_snapshot("7.41", fetch=FakeFetch())
    fetch = FakeFetch()

    result = ingest_snapshot("7.41a", fetch=fetch)

    assert fetch.calls == 1
    assert result.fetched is True
    assert count("snapshots") == 2


def test_a_different_source_is_not_blocked_by_an_existing_snapshot(migrated_database):
    ingest_snapshot("7.41", fetch=FakeFetch(), source=SOURCE_FILE)
    fetch = FakeFetch()

    result = ingest_snapshot("7.41", fetch=fetch, source=SOURCE_API)

    assert fetch.calls == 1
    assert result.fetched is True


def test_an_invalid_patch_is_rejected_before_any_fetch(migrated_database):
    fetch = FakeFetch()

    with pytest.raises(InvalidPatch):
        ingest_snapshot("latest", fetch=fetch)

    assert fetch.calls == 0
    assert count("snapshots") == 0


def test_days_since_patch_is_stored(migrated_database):
    ingest_snapshot("7.41", fetch=FakeFetch(), days_since_patch=3)

    with get_engine().connect() as connection:
        assert connection.execute(text("SELECT days_since_patch FROM snapshots")).scalar_one() == 3


def test_a_bad_winrate_value_writes_nothing_at_all(migrated_database):
    payload = sample_payload()
    payload[1]["pub_win"] = 10**9

    with pytest.raises(InvalidHeroData):
        ingest_snapshot("7.41", fetch=FakeFetch(payload))

    assert (count("snapshots"), count("heroes"), count("hero_base_stats"), count("hero_winrate_stats")) == (
        0,
        0,
        0,
        0,
    )


def test_a_failed_fetch_writes_nothing_and_the_error_propagates(migrated_database):
    fetch = FakeFetch(error=OpenDotaError("down"))

    with pytest.raises(OpenDotaError):
        ingest_snapshot("7.41", fetch=fetch)

    assert (count("snapshots"), count("heroes")) == (0, 0)


def test_a_failed_attempt_does_not_block_the_next_one(migrated_database):
    with pytest.raises(OpenDotaError):
        ingest_snapshot("7.41", fetch=FakeFetch(error=OpenDotaError("down")))
    fetch = FakeFetch()

    result = ingest_snapshot("7.41", fetch=fetch)

    assert fetch.calls == 1
    assert result.fetched is True


def test_another_run_holding_the_lock_blocks_ingest_and_the_api_is_not_called(migrated_database):
    fetch = FakeFetch()
    with get_engine().connect() as other:
        other.execute(text("SELECT pg_advisory_lock(:key)"), {"key": LOCK_KEY})
        try:
            with pytest.raises(IngestAlreadyRunning):
                ingest_snapshot("7.41", fetch=fetch)
        finally:
            other.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY})

    assert fetch.calls == 0
    assert count("snapshots") == 0


def test_the_views_see_the_ingested_snapshot(migrated_database):
    ingest_snapshot("7.41", fetch=FakeFetch())

    with get_engine().connect() as connection:
        modes = connection.execute(text("SELECT count(*) FROM v_winrate_by_mode")).scalar_one()
        patches = connection.execute(text("SELECT DISTINCT patch FROM v_bucket_winrate")).scalars().all()

    assert modes == 3 * 4
    assert patches == ["7.41"]


def test_the_command_line_loads_a_file_then_skips_the_repeat(migrated_database, capsys):
    arguments = ["--patch", "7.41", "--file", str(FIXTURE)]

    assert main(arguments) == 0
    assert "Stored" in capsys.readouterr().out
    assert main(arguments) == 0
    assert "Skipped" in capsys.readouterr().out
    with get_engine().connect() as connection:
        assert connection.execute(text("SELECT source FROM snapshots")).scalar_one() == SOURCE_FILE


def test_winrates_can_be_skipped_without_even_parsing_them(migrated_database):
    payload = sample_payload()
    for hero in payload:
        for key in [key for key in hero if key[0].isdigit() or key.startswith(("pub_", "turbo_", "pro_"))]:
            del hero[key]

    result = ingest_snapshot("7.41", fetch=FakeFetch(payload), include_base_stats=True, include_winrates=False)

    assert result.fetched is True
    assert (count("hero_base_stats"), count("hero_winrate_stats")) == (3, 0)


def test_the_heroes_table_is_not_touched_when_updating_heroes_is_off(migrated_database):
    ingest_snapshot("7.40", fetch=FakeFetch())
    with get_engine().begin() as connection:
        connection.execute(text("UPDATE heroes SET roles = '{Marker}'"))

    ingest_snapshot("7.41", fetch=FakeFetch(), update_heroes=False)

    with get_engine().connect() as connection:
        roles = connection.execute(text("SELECT DISTINCT roles FROM heroes")).scalars().all()
    assert roles == [["Marker"]]


def test_unknown_heroes_stop_the_ingest_when_updating_heroes_is_off(migrated_database):
    with pytest.raises(UnknownHeroes):
        ingest_snapshot("7.41", fetch=FakeFetch(), update_heroes=False)

    assert (count("snapshots"), count("hero_winrate_stats")) == (0, 0)


def test_the_command_line_stores_base_stats_only_with_the_flag(migrated_database, capsys):
    assert main(["--patch", "7.41", "--file", str(FIXTURE)]) == 0
    assert count("hero_base_stats") == 0

    assert main(["--patch", "7.41", "--file", str(FIXTURE), "--force", "--with-base-stats"]) == 0
    assert count("hero_base_stats") == 3


def test_the_command_line_rejects_a_bad_patch_label():
    with pytest.raises(SystemExit):
        main(["--patch", "latest", "--file", str(FIXTURE)])


def test_the_command_line_reports_a_missing_file_with_a_failing_exit_code(migrated_database, capsys, tmp_path):
    assert main(["--patch", "7.41", "--file", str(tmp_path / "missing.json")]) == 1
    assert "FileNotFoundError" in capsys.readouterr().err
