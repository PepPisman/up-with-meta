import json
import sys
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import Connection, text

from dota_meta_bot.core.db import get_engine

DEFAULT_HEROES_FILE = Path(__file__).resolve().parents[3] / "data" / "heroStats.json"

UPSERT_HERO = text(
    """
    INSERT INTO heroes (hero_id, name, localized_name, primary_attr, attack_type, roles)
    VALUES (:hero_id, :name, :localized_name, :primary_attr, :attack_type, :roles)
    ON CONFLICT (hero_id) DO UPDATE SET
        name = EXCLUDED.name,
        localized_name = EXCLUDED.localized_name,
        primary_attr = EXCLUDED.primary_attr,
        attack_type = EXCLUDED.attack_type,
        roles = EXCLUDED.roles
    WHERE (heroes.name, heroes.localized_name, heroes.primary_attr, heroes.attack_type, heroes.roles)
        IS DISTINCT FROM
        (EXCLUDED.name, EXCLUDED.localized_name, EXCLUDED.primary_attr, EXCLUDED.attack_type, EXCLUDED.roles)
    """
)


class InvalidHeroData(ValueError):
    pass


class HeroRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    hero_id: int = Field(alias="id", gt=0, lt=32768)
    name: str = Field(min_length=1)
    localized_name: str = Field(min_length=1)
    primary_attr: Literal["str", "agi", "int", "all"]
    attack_type: Literal["Melee", "Ranged"]
    roles: list[str]


def validate_payload[Row: BaseModel](model: type[Row], payload: object) -> list[Row]:
    if not isinstance(payload, list) or not payload:
        raise InvalidHeroData("payload must be a non-empty list of hero objects")

    rows: list[Row] = []
    for position, raw in enumerate(payload):
        try:
            rows.append(model.model_validate(raw))
        except ValidationError as error:
            label = raw.get("localized_name") if isinstance(raw, dict) else None
            raise InvalidHeroData(f"hero at position {position} ({label}): {error}") from error
    return rows


def parse_heroes(payload: object) -> list[HeroRow]:
    rows = validate_payload(HeroRow, payload)
    reject_duplicates("id", [row.hero_id for row in rows])
    reject_duplicates("name", [row.name for row in rows])
    return rows


def reject_duplicates(field: str, values: list[int] | list[str]) -> None:
    duplicated = sorted(value for value, count in Counter(values).items() if count > 1)
    if duplicated:
        raise InvalidHeroData(f"duplicate hero {field}: {duplicated}")


def upsert_heroes(connection: Connection, rows: list[HeroRow]) -> int:
    if not rows:
        return 0
    connection.execute(UPSERT_HERO, [row.model_dump() for row in rows])
    return len(rows)


def load_heroes(payload: object) -> int:
    rows = parse_heroes(payload)
    with get_engine().begin() as connection:
        return upsert_heroes(connection, rows)


def main(arguments: list[str]) -> int:
    path = Path(arguments[0]) if arguments else DEFAULT_HEROES_FILE
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        count = load_heroes(payload)
    except (FileNotFoundError, json.JSONDecodeError, InvalidHeroData) as error:
        print(f"Hero load failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(f"Processed {count} heroes from {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
