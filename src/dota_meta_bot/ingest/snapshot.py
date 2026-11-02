import argparse
import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Connection, text

from dota_meta_bot.core.db import get_engine
from dota_meta_bot.ingest.heroes import (
    InvalidHeroData,
    parse_heroes,
    reject_duplicates,
    upsert_heroes,
    validate_payload,
)
from dota_meta_bot.ingest.opendota import OpenDotaError, fetch_hero_stats

SOURCE_API = "opendota"
SOURCE_FILE = "opendota_file"
LOCK_KEY = 4_217_001
PATCH_PATTERN = re.compile(r"^\d+\.\d+[a-z]?$")
WIN_PAIR_PREFIXES = ("b1", "b2", "b3", "b4", "b5", "b6", "b7", "pub", "turbo", "pro")

INSERT_SNAPSHOT = text(
    """
    INSERT INTO snapshots (snapshot_at, patch, source, days_since_patch)
    VALUES (:snapshot_at, :patch, :source, :days_since_patch)
    """
)
LATEST_SNAPSHOT = text(
    "SELECT max(snapshot_at) FROM snapshots WHERE source = :source AND patch = :patch"
)
TRY_RUN_LOCK = text("SELECT pg_try_advisory_xact_lock(:key)")


class InvalidPatch(ValueError):
    pass


class IngestAlreadyRunning(RuntimeError):
    pass


class UnknownHeroes(RuntimeError):
    pass


class BaseStatsRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    hero_id: int = Field(alias="id", gt=0, lt=32768)
    base_str: int = Field(ge=0)
    base_agi: int = Field(ge=0)
    base_int: int = Field(ge=0)
    str_gain: float = Field(ge=0, le=6)
    agi_gain: float = Field(ge=0, le=6)
    int_gain: float = Field(ge=0, le=6)
    base_attack_min: int = Field(ge=0)
    base_attack_max: int = Field(ge=0)
    base_armor: float
    base_health_regen: float
    base_mana_regen: float
    base_mana: int | None = Field(default=None, ge=0)
    attack_range: int = Field(gt=0)
    attack_rate: float = Field(gt=0)
    attack_point: float = Field(ge=0)
    move_speed: int = Field(ge=100, le=700)
    turn_rate: float | None = None
    day_vision: int = Field(ge=0)
    night_vision: int = Field(ge=0)

    @model_validator(mode="after")
    def attack_range_is_ordered(self) -> Self:
        if self.base_attack_min > self.base_attack_max:
            raise ValueError("base_attack_min is above base_attack_max")
        return self


class WinrateRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    hero_id: int = Field(alias="id", gt=0, lt=32768)
    b1_pick: int = Field(alias="1_pick", ge=0)
    b1_win: int = Field(alias="1_win", ge=0)
    b2_pick: int = Field(alias="2_pick", ge=0)
    b2_win: int = Field(alias="2_win", ge=0)
    b3_pick: int = Field(alias="3_pick", ge=0)
    b3_win: int = Field(alias="3_win", ge=0)
    b4_pick: int = Field(alias="4_pick", ge=0)
    b4_win: int = Field(alias="4_win", ge=0)
    b5_pick: int = Field(alias="5_pick", ge=0)
    b5_win: int = Field(alias="5_win", ge=0)
    b6_pick: int = Field(alias="6_pick", ge=0)
    b6_win: int = Field(alias="6_win", ge=0)
    b7_pick: int = Field(alias="7_pick", ge=0)
    b7_win: int = Field(alias="7_win", ge=0)
    pub_pick: int = Field(ge=0)
    pub_win: int = Field(ge=0)
    turbo_pick: int = Field(alias="turbo_picks", ge=0)
    turbo_win: int = Field(alias="turbo_wins", ge=0)
    pro_pick: int = Field(ge=0)
    pro_win: int = Field(ge=0)
    pro_ban: int = Field(ge=0)

    @model_validator(mode="after")
    def wins_do_not_exceed_picks(self) -> Self:
        for prefix in WIN_PAIR_PREFIXES:
            if getattr(self, f"{prefix}_win") > getattr(self, f"{prefix}_pick"):
                raise ValueError(f"{prefix}_win is above {prefix}_pick")
        return self


@dataclass(frozen=True)
class SnapshotResult:
    fetched: bool
    snapshot_at: datetime
    patch: str
    source: str
    heroes: int


def validate_patch(value: str) -> str:
    if not PATCH_PATTERN.fullmatch(value):
        raise InvalidPatch(f"patch must look like 7.41 or 7.39c, got {value!r}")
    return value


def parse_base_stats(payload: object) -> list[BaseStatsRow]:
    return validate_payload(BaseStatsRow, payload)


def parse_winrates(payload: object) -> list[WinrateRow]:
    return validate_payload(WinrateRow, payload)


def acquire_run_lock(connection: Connection) -> None:
    if not connection.execute(TRY_RUN_LOCK, {"key": LOCK_KEY}).scalar_one():
        raise IngestAlreadyRunning("another snapshot ingest is already running")


def latest_snapshot_at(connection: Connection, source: str, patch: str) -> datetime | None:
    return connection.execute(LATEST_SNAPSHOT, {"source": source, "patch": patch}).scalar_one()


def is_stale(snapshot_at: datetime, now: datetime, min_age_days: int | None) -> bool:
    return min_age_days is not None and now - snapshot_at >= timedelta(days=min_age_days)


def reject_unknown_heroes(connection: Connection, hero_ids: set[int]) -> None:
    known = set(
        connection.execute(
            text("SELECT hero_id FROM heroes WHERE hero_id = ANY(:ids)"), {"ids": sorted(hero_ids)}
        ).scalars()
    )
    missing = sorted(hero_ids - known)
    if missing:
        raise UnknownHeroes(f"heroes missing from the heroes table, load heroes first: {missing}")


def insert_rows(
    connection: Connection,
    table: str,
    model: type[BaseModel],
    snapshot_at: datetime,
    rows: list[BaseStatsRow] | list[WinrateRow],
) -> None:
    columns = ["snapshot_at", *model.model_fields]
    statement = text(
        f"INSERT INTO {table} ({', '.join(columns)}) "
        f"VALUES ({', '.join(f':{column}' for column in columns)})"
    )
    connection.execute(statement, [{"snapshot_at": snapshot_at, **row.model_dump()} for row in rows])


def ingest_snapshot(
    patch: str,
    *,
    fetch: Callable[[], object],
    source: str = SOURCE_API,
    force: bool = False,
    days_since_patch: int | None = None,
    include_base_stats: bool = False,
    include_winrates: bool = True,
    update_heroes: bool = True,
    min_age_days: int | None = None,
    now: datetime | None = None,
) -> SnapshotResult:
    validate_patch(patch)
    snapshot_at = now or datetime.now(UTC)
    with get_engine().begin() as connection:
        acquire_run_lock(connection)
        existing = latest_snapshot_at(connection, source, patch)
        if existing is not None and not force and not is_stale(existing, snapshot_at, min_age_days):
            return SnapshotResult(
                fetched=False, snapshot_at=existing, patch=patch, source=source, heroes=0
            )

        payload = fetch()
        heroes = parse_heroes(payload) if update_heroes else []
        base_stats = parse_base_stats(payload) if include_base_stats else []
        winrates = parse_winrates(payload) if include_winrates else []
        hero_ids = [row.hero_id for row in heroes or base_stats or winrates]
        reject_duplicates("id", hero_ids)

        if update_heroes:
            upsert_heroes(connection, heroes)
        else:
            reject_unknown_heroes(connection, set(hero_ids))
        connection.execute(
            INSERT_SNAPSHOT,
            {
                "snapshot_at": snapshot_at,
                "patch": patch,
                "source": source,
                "days_since_patch": days_since_patch,
            },
        )
        if include_base_stats:
            insert_rows(connection, "hero_base_stats", BaseStatsRow, snapshot_at, base_stats)
        if include_winrates:
            insert_rows(connection, "hero_winrate_stats", WinrateRow, snapshot_at, winrates)

    return SnapshotResult(
        fetched=True, snapshot_at=snapshot_at, patch=patch, source=source, heroes=len(hero_ids)
    )


def describe_result(result: SnapshotResult) -> str:
    if result.fetched:
        return (
            f"Stored snapshot {result.snapshot_at.isoformat()} "
            f"patch {result.patch} source {result.source}: {result.heroes} heroes"
        )
    return (
        f"Skipped: already have patch {result.patch} from {result.source} "
        f"at {result.snapshot_at.isoformat()} (use --force to fetch again)"
    )


def patch_argument(value: str) -> str:
    try:
        return validate_patch(value)
    except InvalidPatch as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def read_file(path: Path) -> Callable[[], object]:
    def fetch() -> object:
        return json.loads(path.read_text(encoding="utf-8"))

    return fetch


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m dota_meta_bot.ingest.snapshot",
        description="Store one OpenDota heroStats snapshot, skipping the API call if the patch is already stored.",
    )
    parser.add_argument("--patch", required=True, type=patch_argument)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--file", type=Path)
    parser.add_argument("--days-since-patch", type=int)
    parser.add_argument("--with-base-stats", action="store_true")
    return parser


def main(arguments: list[str]) -> int:
    options = build_parser().parse_args(arguments)
    if options.file is not None:
        source, fetch = SOURCE_FILE, read_file(options.file)
    else:
        source, fetch = SOURCE_API, fetch_hero_stats

    try:
        result = ingest_snapshot(
            options.patch,
            fetch=fetch,
            source=source,
            force=options.force,
            days_since_patch=options.days_since_patch,
            include_base_stats=options.with_base_stats,
        )
    except (
        FileNotFoundError,
        json.JSONDecodeError,
        InvalidHeroData,
        OpenDotaError,
        IngestAlreadyRunning,
    ) as error:
        print(f"Snapshot ingest failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1

    print(describe_result(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
