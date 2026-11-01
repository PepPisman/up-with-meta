import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

import requests

from dota_meta_bot.core.config import get_settings
from dota_meta_bot.ingest.heroes import InvalidHeroData
from dota_meta_bot.ingest.opendota import OpenDotaError, fetch_hero_stats
from dota_meta_bot.ingest.snapshot import (
    SOURCE_API,
    IngestAlreadyRunning,
    InvalidPatch,
    SnapshotResult,
    UnknownHeroes,
    describe_result,
    ingest_snapshot,
)
from dota_meta_bot.ingest.stratz import (
    StratzError,
    days_since_release,
    fetch_latest_game_version,
    ingest_stratz_base_stats,
)

WEEKLY_MIN_AGE_DAYS = 7


@dataclass(frozen=True)
class WeeklyResult:
    patch: str
    winrates: SnapshotResult
    base_stats: SnapshotResult


def run_weekly(
    *,
    token: str,
    force: bool = False,
    post: Callable[..., requests.Response] = requests.post,
    fetch: Callable[[], object] = fetch_hero_stats,
    today: date | None = None,
    now: datetime | None = None,
) -> WeeklyResult:
    version = fetch_latest_game_version(token=token, post=post)
    winrates = ingest_snapshot(
        version.name,
        fetch=fetch,
        source=SOURCE_API,
        force=force,
        days_since_patch=days_since_release(version, today),
        min_age_days=WEEKLY_MIN_AGE_DAYS,
        now=now,
    )
    base_stats = ingest_stratz_base_stats(
        token=token, force=force, post=post, today=today, version=version
    )
    return WeeklyResult(patch=version.name, winrates=winrates, base_stats=base_stats)


def main(
    arguments: list[str],
    post: Callable[..., requests.Response] = requests.post,
    fetch: Callable[[], object] = fetch_hero_stats,
) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m dota_meta_bot.ingest.weekly",
        description="Weekly update: latest patch from STRATZ, OpenDota winrates every 7 days, STRATZ base stats on a new patch.",
    )
    parser.add_argument("--force", action="store_true")
    options = parser.parse_args(arguments)

    token = get_settings().stratz_api_token
    if token is None:
        print("STRATZ_API_TOKEN is not set in .env", file=sys.stderr)
        return 1

    try:
        result = run_weekly(
            token=token.get_secret_value(), force=options.force, post=post, fetch=fetch
        )
    except (
        StratzError,
        OpenDotaError,
        InvalidPatch,
        InvalidHeroData,
        UnknownHeroes,
        IngestAlreadyRunning,
    ) as error:
        print(f"Weekly update failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1

    print(f"Latest patch: {result.patch}")
    print(f"Winrates: {describe_result(result.winrates)}")
    print(f"Base stats: {describe_result(result.base_stats)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
