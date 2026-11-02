import copy
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import requests
from alembic import command
from fakes import FakeResponse
from sqlalchemy import text

from dota_meta_bot.core.config import Settings
from dota_meta_bot.core.db import get_engine
from dota_meta_bot.ingest import stratz as stratz_module
from dota_meta_bot.ingest.heroes import load_heroes
from dota_meta_bot.ingest.snapshot import (
    SOURCE_API,
    UnknownHeroes,
    ingest_snapshot,
    parse_base_stats,
)
from dota_meta_bot.ingest.stratz import (
    GRAPHQL_URL,
    SOURCE_STRATZ,
    TIMEOUT,
    USER_AGENT,
    StratzError,
    StratzRateLimited,
    fetch_hero_constants,
    fetch_latest_game_version,
    ingest_stratz_base_stats,
    main,
    run_query,
    to_base_stats_payload,
)

FIXTURES = Path(__file__).parent / "fixtures"
TOKEN = "secret-test-token-value"
RELEASE_7_41F = datetime(2026, 9, 15, tzinfo=UTC)
VERSIONS = [
    {"id": 189, "name": "7.41e", "asOfDateTime": int(datetime(2026, 7, 30, tzinfo=UTC).timestamp())},
    {"id": 190, "name": "7.41f", "asOfDateTime": int(RELEASE_7_41F.timestamp())},
]
TODAY = date(2026, 10, 20)


def sample_heroes() -> list[dict]:
    return json.loads((FIXTURES / "stratz_constants_sample.json").read_text(encoding="utf-8"))


def opendota_heroes() -> list[dict]:
    return json.loads((FIXTURES / "heroStats_sample.json").read_text(encoding="utf-8"))


def by_name(rows: list[dict], key: str, name: str) -> dict:
    return next(row for row in rows if row[key] == name)


def payload_by_id() -> dict[int, dict]:
    return {row["id"]: row for row in to_base_stats_payload(sample_heroes())}


class FakeStratz:
    def __init__(self, heroes=None, versions=None):
        self.heroes = sample_heroes() if heroes is None else heroes
        self.versions = VERSIONS if versions is None else versions
        self.calls = []

    def __call__(self, url, json, headers, timeout):
        self.calls.append({"url": url, "query": json["query"], "variables": json.get("variables"), "headers": headers, "timeout": timeout})
        if "gameVersions" in json["query"]:
            return FakeResponse(body={"data": {"constants": {"gameVersions": self.versions}}})
        return FakeResponse(body={"data": {"constants": {"heroes": copy.deepcopy(self.heroes)}}})

    @property
    def hero_calls(self) -> list[dict]:
        return [call for call in self.calls if "gameVersions" not in call["query"]]


class SingleResponse:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def __call__(self, url, json, headers, timeout):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if self.error:
            raise self.error
        return self.response


def preload_heroes() -> None:
    load_heroes(
        [
            {
                "id": hero["id"],
                "name": hero["name"],
                "localized_name": hero["displayName"],
                "primary_attr": hero["stats"]["primaryAttribute"],
                "attack_type": hero["stats"]["attackType"],
                "roles": ["Preloaded"],
            }
            for hero in sample_heroes()
        ]
    )


def count(table: str) -> int:
    with get_engine().connect() as connection:
        return connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()


def test_damage_loses_the_attribute_bonus_for_single_attribute_heroes():
    rows = payload_by_id()

    assert (rows[1]["base_attack_min"], rows[1]["base_attack_max"]) == (29, 33)
    assert (rows[2]["base_attack_min"], rows[2]["base_attack_max"]) == (31, 35)
    assert (rows[5]["base_attack_min"], rows[5]["base_attack_max"]) == (28, 34)


def test_universal_heroes_keep_their_damage_as_given():
    row = payload_by_id()[3]

    assert (row["base_attack_min"], row["base_attack_max"]) == (24, 30)


def test_armor_loses_the_agility_bonus():
    rows = payload_by_id()

    assert rows[1]["base_armor"] == pytest.approx(2.0)
    assert rows[2]["base_armor"] == pytest.approx(0.0)
    assert rows[3]["base_armor"] == pytest.approx(0.0)
    assert rows[5]["base_armor"] == pytest.approx(0.0)


def test_other_stats_are_copied_across_and_float_noise_is_removed():
    anti_mage = payload_by_id()[1]

    assert anti_mage["id"] == 1
    assert (anti_mage["base_str"], anti_mage["base_agi"], anti_mage["base_int"]) == (21, 25, 12)
    assert (anti_mage["str_gain"], anti_mage["agi_gain"], anti_mage["int_gain"]) == (1.6, 2.8, 1.8)
    assert anti_mage["move_speed"] == 315
    assert anti_mage["attack_range"] == 150
    assert anti_mage["attack_rate"] == 1.4
    assert anti_mage["base_health_regen"] == 1.5
    assert anti_mage["base_mana_regen"] == 0
    assert anti_mage["turn_rate"] == 0.6
    assert "base_mana" not in anti_mage


def test_the_payload_passes_the_same_validation_as_the_opendota_payload():
    rows = parse_base_stats(to_base_stats_payload(sample_heroes()))

    assert len(rows) == 4
    assert all(row.base_mana is None for row in rows)


def test_a_hero_that_has_not_changed_matches_opendota_exactly():
    stratz = by_name(to_base_stats_payload(sample_heroes()), "id", 5)
    opendota = by_name(opendota_heroes(), "localized_name", "Crystal Maiden")

    for field in (
        "base_str",
        "base_agi",
        "base_int",
        "str_gain",
        "agi_gain",
        "int_gain",
        "base_attack_min",
        "base_attack_max",
        "move_speed",
        "attack_range",
        "attack_rate",
        "attack_point",
        "day_vision",
        "night_vision",
    ):
        assert stratz[field] == pytest.approx(opendota[field]), field
    assert stratz["base_armor"] == pytest.approx(opendota["base_armor"])


def test_a_hero_changed_in_7_41f_differs_from_the_stale_opendota_values():
    stratz = by_name(to_base_stats_payload(sample_heroes()), "id", 1)
    opendota = by_name(opendota_heroes(), "localized_name", "Anti-Mage")

    assert (stratz["base_agi"], stratz["move_speed"]) == (25, 315)
    assert (opendota["base_agi"], opendota["move_speed"]) == (24, 310)


def test_a_hero_without_stats_is_an_error():
    heroes = sample_heroes()
    heroes[0]["stats"] = None

    with pytest.raises(StratzError, match="Anti-Mage"):
        to_base_stats_payload(heroes)


def test_a_fractional_attribute_is_rejected_by_validation():
    heroes = sample_heroes()
    heroes[0]["stats"]["strengthBase"] = 21.5

    with pytest.raises(Exception):
        parse_base_stats(to_base_stats_payload(heroes))


def test_run_query_returns_the_data_block():
    post = SingleResponse(FakeResponse(body={"data": {"constants": {"x": 1}}}))

    assert run_query("{ a }", {"v": 1}, token=TOKEN, post=post) == {"constants": {"x": 1}}


def test_run_query_sends_the_token_agent_url_and_timeout_but_never_leaks_the_token():
    post = SingleResponse(FakeResponse(body={"data": {}}))

    run_query("{ a }", None, token=TOKEN, post=post)

    call = post.calls[0]
    assert call["url"] == GRAPHQL_URL
    assert call["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert call["headers"]["User-Agent"] == USER_AGENT
    assert call["json"]["query"] == "{ a }"
    assert call["timeout"] == TIMEOUT == (3, 30)


def test_a_429_raises_rate_limited_with_retry_after():
    post = SingleResponse(FakeResponse(status_code=429, headers={"Retry-After": "12"}))

    with pytest.raises(StratzRateLimited) as error:
        run_query("{ a }", None, token=TOKEN, post=post)

    assert error.value.retry_after == 12
    assert TOKEN not in str(error.value)


@pytest.mark.parametrize(
    "response, error",
    [
        (None, requests.ConnectionError("down")),
        (FakeResponse(invalid_json=True), None),
        (FakeResponse(body={"errors": [{"message": "boom"}]}), None),
        (FakeResponse(status_code=503), None),
    ],
)
def test_no_failure_message_ever_contains_the_token(response, error):
    with pytest.raises(StratzError) as raised:
        run_query("{ a }", None, token=TOKEN, post=SingleResponse(response, error))

    assert TOKEN not in str(raised.value)


def test_a_server_error_raises_stratz_error_without_the_token():
    post = SingleResponse(FakeResponse(status_code=500))

    with pytest.raises(StratzError) as error:
        run_query("{ a }", None, token=TOKEN, post=post)

    assert TOKEN not in str(error.value)


def test_graphql_errors_inside_a_200_response_are_raised():
    post = SingleResponse(FakeResponse(body={"errors": [{"message": "Cannot query field"}], "data": None}))

    with pytest.raises(StratzError, match="Cannot query field"):
        run_query("{ a }", None, token=TOKEN, post=post)


@pytest.mark.parametrize(
    "response, error",
    [
        (None, requests.ConnectionError("down")),
        (None, requests.Timeout("slow")),
        (FakeResponse(invalid_json=True), None),
        (FakeResponse(body={"nothing": 1}), None),
        (FakeResponse(body=None), None),
    ],
)
def test_other_failures_are_wrapped(response, error):
    with pytest.raises(StratzError):
        run_query("{ a }", None, token=TOKEN, post=SingleResponse(response, error))


def test_the_latest_game_version_is_the_highest_id_with_its_release_date():
    version = fetch_latest_game_version(token=TOKEN, post=FakeStratz(versions=list(reversed(VERSIONS))))

    assert (version.id, version.name, version.released) == (190, "7.41f", date(2026, 9, 15))


def test_hero_constants_are_requested_for_the_given_game_version():
    post = FakeStratz()

    heroes = fetch_hero_constants(190, token=TOKEN, post=post)

    assert len(heroes) == 4
    assert post.hero_calls[0]["variables"] == {"gameVersionId": 190}


def test_ingest_stores_a_stratz_snapshot_with_base_stats_only(migrated_database):
    preload_heroes()
    post = FakeStratz()

    result = ingest_stratz_base_stats(token=TOKEN, post=post, today=TODAY)

    assert result.fetched is True
    assert (result.patch, result.source, result.heroes) == ("7.41f", SOURCE_STRATZ, 4)
    with get_engine().connect() as connection:
        snapshot = connection.execute(text("SELECT patch, source, days_since_patch FROM snapshots")).one()
    assert tuple(snapshot) == ("7.41f", SOURCE_STRATZ, 35)
    assert (count("snapshots"), count("hero_base_stats"), count("hero_winrate_stats")) == (1, 4, 0)


def test_unknown_base_mana_is_stored_as_null(migrated_database):
    preload_heroes()

    ingest_stratz_base_stats(token=TOKEN, post=FakeStratz(), today=TODAY)

    with get_engine().connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM hero_base_stats WHERE base_mana IS NULL")).scalar_one() == 4


def test_stored_values_are_the_converted_values(migrated_database):
    preload_heroes()

    ingest_stratz_base_stats(token=TOKEN, post=FakeStratz(), today=TODAY)

    with get_engine().connect() as connection:
        row = connection.execute(
            text(
                "SELECT base_agi, base_attack_min, base_attack_max, base_armor, move_speed, base_health_regen "
                "FROM hero_base_stats WHERE hero_id = 1"
            )
        ).one()
    assert tuple(row) == (25, 29, 33, pytest.approx(2.0), 315, 1.5)


def test_the_heroes_table_is_left_alone(migrated_database):
    preload_heroes()

    ingest_stratz_base_stats(token=TOKEN, post=FakeStratz(), today=TODAY)

    with get_engine().connect() as connection:
        roles = connection.execute(text("SELECT DISTINCT roles FROM heroes")).scalars().all()
    assert roles == [["Preloaded"]]


def test_a_repeat_run_makes_only_the_cheap_version_call(migrated_database):
    preload_heroes()
    ingest_stratz_base_stats(token=TOKEN, post=FakeStratz(), today=TODAY)
    post = FakeStratz()

    result = ingest_stratz_base_stats(token=TOKEN, post=post, today=TODAY)

    assert result.fetched is False
    assert len(post.calls) == 1
    assert post.hero_calls == []
    assert count("snapshots") == 1


def test_force_fetches_again(migrated_database):
    preload_heroes()
    ingest_stratz_base_stats(token=TOKEN, post=FakeStratz(), today=TODAY)
    post = FakeStratz()

    result = ingest_stratz_base_stats(token=TOKEN, post=post, force=True, today=TODAY)

    assert result.fetched is True
    assert len(post.hero_calls) == 1
    assert (count("snapshots"), count("hero_base_stats")) == (2, 8)


def test_a_hero_missing_from_the_heroes_table_stops_everything(migrated_database):
    preload_heroes()
    heroes = sample_heroes()
    extra = copy.deepcopy(heroes[0])
    extra.update(id=999, name="npc_dota_hero_new", displayName="New Hero")
    heroes.append(extra)

    with pytest.raises(UnknownHeroes, match="999"):
        ingest_stratz_base_stats(token=TOKEN, post=FakeStratz(heroes=heroes), today=TODAY)

    assert (count("snapshots"), count("hero_base_stats")) == (0, 0)


def test_a_stratz_snapshot_does_not_block_an_opendota_snapshot_of_the_same_patch(migrated_database):
    preload_heroes()
    ingest_stratz_base_stats(token=TOKEN, post=FakeStratz(), today=TODAY)
    calls = []

    def fetch():
        calls.append(1)
        return opendota_heroes()

    load_heroes(opendota_heroes())
    result = ingest_snapshot("7.41f", fetch=fetch, source=SOURCE_API)

    assert result.fetched is True
    assert calls == [1]
    assert count("snapshots") == 2


def test_downgrade_still_works_when_unknown_base_mana_rows_exist(migrated_database, alembic_config):
    preload_heroes()
    ingest_stratz_base_stats(token=TOKEN, post=FakeStratz(), today=TODAY)

    command.downgrade(alembic_config, "base")


def settings_without_token() -> Settings:
    return Settings(_env_file=None, postgres_password="x")


def settings_with_token() -> Settings:
    return Settings(_env_file=None, postgres_password="x", stratz_api_token=TOKEN)


def test_the_command_line_needs_a_token_and_makes_no_call_without_one(monkeypatch, capsys):
    monkeypatch.setattr(stratz_module, "get_settings", settings_without_token)
    post = FakeStratz()

    assert main([], post=post) == 1

    assert "STRATZ_API_TOKEN" in capsys.readouterr().err
    assert post.calls == []


def test_the_command_line_stores_then_skips_and_never_prints_the_token(monkeypatch, capsys, migrated_database):
    monkeypatch.setattr(stratz_module, "get_settings", settings_with_token)
    preload_heroes()

    assert main([], post=FakeStratz()) == 0
    first = capsys.readouterr()
    assert "Stored" in first.out and "7.41f" in first.out
    assert main([], post=FakeStratz()) == 0
    second = capsys.readouterr()
    assert "Skipped" in second.out
    assert TOKEN not in first.out + first.err + second.out + second.err


def test_the_command_line_reports_a_stratz_failure_with_exit_code_one(monkeypatch, capsys):
    monkeypatch.setattr(stratz_module, "get_settings", settings_with_token)
    post = SingleResponse(FakeResponse(status_code=500))

    assert main([], post=post) == 1

    captured = capsys.readouterr()
    assert "StratzError" in captured.err
    assert TOKEN not in captured.out + captured.err
