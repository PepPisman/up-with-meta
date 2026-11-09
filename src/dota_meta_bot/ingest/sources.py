from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

import requests

from dota_meta_bot.ingest.snapshot import SOURCE_API, SnapshotResult, ingest_snapshot
from dota_meta_bot.ingest.stratz import (
    SOURCE_STRATZ,
    GameVersion,
    days_since_release,
    ingest_stratz_base_stats,
)

SOURCE_DOTABUFF = "dotabuff"
SOURCE_DOTA2PROTRACKER = "dota2protracker"
WEEKLY_MIN_AGE_DAYS = 7


class UnknownSource(ValueError):
    pass


@dataclass(frozen=True)
class SourceContext:
    token: str
    version: GameVersion
    force: bool
    post: Callable[..., requests.Response]
    fetch: Callable[[], object]
    today: date | None
    now: datetime | None


@dataclass(frozen=True)
class SourceSpec:
    name: str
    kind: str
    verification_rank: int
    run: Callable[[SourceContext], SnapshotResult] | None = None

    @property
    def automated(self) -> bool:
        return self.run is not None


def run_opendota(context: SourceContext) -> SnapshotResult:
    return ingest_snapshot(
        context.version.name,
        fetch=context.fetch,
        source=SOURCE_API,
        force=context.force,
        days_since_patch=days_since_release(context.version, context.today),
        min_age_days=WEEKLY_MIN_AGE_DAYS,
        now=context.now,
    )


def run_stratz(context: SourceContext) -> SnapshotResult:
    return ingest_stratz_base_stats(
        token=context.token,
        force=context.force,
        post=context.post,
        today=context.today,
        version=context.version,
    )


SOURCES = (
    SourceSpec(SOURCE_API, "winrates", verification_rank=4, run=run_opendota),
    SourceSpec(SOURCE_STRATZ, "base_stats", verification_rank=3, run=run_stratz),
    SourceSpec(SOURCE_DOTABUFF, "winrates", verification_rank=1),
    SourceSpec(SOURCE_DOTA2PROTRACKER, "winrates", verification_rank=2),
)


def get_source(name: str, sources: tuple[SourceSpec, ...] = SOURCES) -> SourceSpec:
    for spec in sources:
        if spec.name == name:
            return spec
    raise UnknownSource(f"unknown source {name!r}, known: {[spec.name for spec in sources]}")


def automated_sources(sources: tuple[SourceSpec, ...] = SOURCES) -> list[SourceSpec]:
    return [spec for spec in sources if spec.automated]


def verification_order(sources: tuple[SourceSpec, ...] = SOURCES) -> list[SourceSpec]:
    return sorted(sources, key=lambda spec: spec.verification_rank)
