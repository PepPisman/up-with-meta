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
    IngestAlreadyRunning,
    InvalidPatch,
    SnapshotResult,
    UnknownHeroes,
    describe_result,
)
from dota_meta_bot.ingest.sources import (
    SOURCES,
    SourceContext,
    SourceSpec,
    automated_sources,
)
from dota_meta_bot.ingest.stratz import StratzError, fetch_latest_game_version


@dataclass(frozen=True)
class WeeklyResult:
    patch: str
    results: dict[str, SnapshotResult]


def run_weekly(
    *,
    token: str,
    force: bool = False,
    post: Callable[..., requests.Response] = requests.post,
    fetch: Callable[[], object] = fetch_hero_stats,
    today: date | None = None,
    now: datetime | None = None,
    sources: tuple[SourceSpec, ...] = SOURCES,
) -> WeeklyResult:
    version = fetch_latest_game_version(token=token, post=post)
    context = SourceContext(
        token=token, version=version, force=force, post=post, fetch=fetch, today=today, now=now
    )
    results = {spec.name: spec.run(context) for spec in automated_sources(sources)}
    return WeeklyResult(patch=version.name, results=results)


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
    for name, source_result in result.results.items():
        print(f"{name}: {describe_result(source_result)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
