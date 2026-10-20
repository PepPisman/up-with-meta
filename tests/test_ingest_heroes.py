import json
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from dota_meta_bot.core.db import get_engine
from dota_meta_bot.ingest.heroes import (
    DEFAULT_HEROES_FILE,
    InvalidHeroData,
    load_heroes,
    parse_heroes,
)

FIXTURE = Path(__file__).parent / "fixtures" / "heroStats_sample.json"


def hero(**overrides) -> dict:
    base = {
        "id": 1,
        "name": "npc_dota_hero_antimage",
        "localized_name": "Anti-Mage",
        "primary_attr": "agi",
        "attack_type": "Melee",
        "roles": ["Carry", "Escape", "Nuker"],
        "1_pick": 12296,
        "img": "/apps/dota2/images/dota_react/heroes/antimage.png?",
    }
    base.update(overrides)
    return base


def second_hero(**overrides) -> dict:
    values = {
        "id": 5,
        "name": "npc_dota_hero_crystal_maiden",
        "localized_name": "Crystal Maiden",
        "primary_attr": "int",
        "attack_type": "Ranged",
        "roles": ["Support", "Disabler"],
    }
    values.update(overrides)
    return hero(**values)


def stored_heroes() -> dict[int, tuple]:
    with get_engine().connect() as connection:
        rows = connection.execute(
            text(
                "SELECT hero_id, name, localized_name, primary_attr, attack_type, roles "
                "FROM heroes ORDER BY hero_id"
            )
        ).all()
    return {row.hero_id: tuple(row)[1:] for row in rows}


def row_versions() -> dict[int, str]:
    with get_engine().connect() as connection:
        rows = connection.execute(text("SELECT hero_id, xmin::text FROM heroes")).all()
    return {row.hero_id: row[1] for row in rows}


def test_parse_maps_an_api_object_and_ignores_extra_fields():
    [row] = parse_heroes([hero()])

    assert row.hero_id == 1
    assert row.name == "npc_dota_hero_antimage"
    assert row.localized_name == "Anti-Mage"
    assert row.primary_attr == "agi"
    assert row.attack_type == "Melee"
    assert row.roles == ["Carry", "Escape", "Nuker"]


def test_parse_keeps_api_order():
    rows = parse_heroes([second_hero(), hero()])

    assert [row.hero_id for row in rows] == [5, 1]


@pytest.mark.parametrize("payload", [[], None, {}, "heroes"])
def test_parse_rejects_an_empty_or_non_list_payload(payload):
    with pytest.raises(InvalidHeroData):
        parse_heroes(payload)


@pytest.mark.parametrize(
    "bad_hero",
    [
        hero(primary_attr="strength"),
        hero(attack_type="Hybrid"),
        hero(id=0),
        hero(id=40000),
        hero(name=""),
        hero(roles="Carry"),
    ],
)
def test_parse_rejects_invalid_values(bad_hero):
    with pytest.raises(InvalidHeroData):
        parse_heroes([bad_hero])


def test_parse_rejects_a_missing_id():
    without_id = hero()
    del without_id["id"]

    with pytest.raises(InvalidHeroData):
        parse_heroes([without_id])


def test_parse_error_names_the_offending_hero():
    with pytest.raises(InvalidHeroData, match="Crystal Maiden"):
        parse_heroes([hero(), second_hero(primary_attr="strength")])


def test_parse_rejects_duplicate_ids_and_names():
    with pytest.raises(InvalidHeroData, match="duplicate hero id"):
        parse_heroes([hero(), hero(name="other", localized_name="Other")])
    with pytest.raises(InvalidHeroData, match="duplicate hero name"):
        parse_heroes([hero(), hero(id=2)])


def test_parse_accepts_the_saved_fixture():
    rows = parse_heroes(json.loads(FIXTURE.read_text(encoding="utf-8")))

    assert {row.localized_name for row in rows} == {"Anti-Mage", "Crystal Maiden", "Bane"}
    assert {row.primary_attr for row in rows} == {"agi", "int", "all"}


@pytest.mark.skipif(not DEFAULT_HEROES_FILE.exists(), reason="data/heroStats.json not saved yet")
def test_parse_accepts_the_full_saved_api_response():
    payload = json.loads(DEFAULT_HEROES_FILE.read_text(encoding="utf-8"))

    rows = parse_heroes(payload)

    assert len(rows) == len(payload)


def test_load_inserts_every_hero(migrated_database):
    assert load_heroes([hero(), second_hero()]) == 2

    assert stored_heroes() == {
        1: ("npc_dota_hero_antimage", "Anti-Mage", "agi", "Melee", ["Carry", "Escape", "Nuker"]),
        5: ("npc_dota_hero_crystal_maiden", "Crystal Maiden", "int", "Ranged", ["Support", "Disabler"]),
    }


def test_loading_the_same_payload_twice_changes_nothing(migrated_database):
    payload = [hero(), second_hero()]
    load_heroes(payload)
    before = stored_heroes()

    load_heroes(payload)

    assert stored_heroes() == before


def test_an_unchanged_hero_row_is_not_rewritten(migrated_database):
    payload = [hero(), second_hero()]
    load_heroes(payload)
    before = row_versions()

    load_heroes(payload)

    assert row_versions() == before


def test_a_changed_hero_is_updated_and_untouched_heroes_stay_untouched(migrated_database):
    load_heroes([hero(), second_hero()])
    before = row_versions()

    load_heroes([hero(roles=["Carry"], localized_name="Anti Mage"), second_hero()])

    assert stored_heroes()[1] == ("npc_dota_hero_antimage", "Anti Mage", "agi", "Melee", ["Carry"])
    after = row_versions()
    assert after[1] != before[1]
    assert after[5] == before[5]


def test_a_hero_missing_from_a_later_payload_is_not_deleted(migrated_database):
    load_heroes([hero(), second_hero()])

    load_heroes([hero()])

    assert set(stored_heroes()) == {1, 5}


def test_an_invalid_payload_writes_nothing(migrated_database):
    with pytest.raises(InvalidHeroData):
        load_heroes([hero(), second_hero(primary_attr="strength")])

    assert stored_heroes() == {}


def test_a_database_error_rolls_back_the_whole_batch(migrated_database):
    load_heroes([hero()])
    clashing_name = second_hero(name="npc_dota_hero_antimage")
    new_valid = hero(id=9, name="npc_dota_hero_new", localized_name="New")

    with pytest.raises(IntegrityError):
        load_heroes([new_valid, clashing_name])

    assert set(stored_heroes()) == {1}
