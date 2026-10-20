import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime

import requests

from dota_meta_bot.core.config import get_settings
from dota_meta_bot.ingest.heroes import InvalidHeroData
from dota_meta_bot.ingest.opendota import parse_retry_after
from dota_meta_bot.ingest.snapshot import (
    IngestAlreadyRunning,
    InvalidPatch,
    SnapshotResult,
    UnknownHeroes,
    describe_result,
    ingest_snapshot,
)

GRAPHQL_URL = "https://api.stratz.com/graphql"
TIMEOUT = (3, 30)
USER_AGENT = "STRATZ_API"
SOURCE_STRATZ = "stratz"
HTTP_TOO_MANY_REQUESTS = 429
SINGLE_ATTRIBUTE_BASE_FIELD = {
    "str": "strengthBase",
    "agi": "agilityBase",
    "int": "intelligenceBase",
}

VERSIONS_QUERY = "{ constants { gameVersions { id name asOfDateTime } } }"
HEROES_QUERY = """
query Heroes($gameVersionId: Short) {
  constants {
    heroes(gameVersionId: $gameVersionId) {
      id
      name
      displayName
      gameVersionId
      stats {
        attackType
        primaryAttribute
        startingArmor
        startingDamageMin
        startingDamageMax
        attackRate
        attackAnimationPoint
        attackRange
        strengthBase
        strengthGain
        intelligenceBase
        intelligenceGain
        agilityBase
        agilityGain
        hpRegen
        mpRegen
        moveSpeed
        moveTurnRate
        visionDaytimeRange
        visionNighttimeRange
      }
    }
  }
}
"""


class StratzError(RuntimeError):
    pass


class StratzRateLimited(StratzError):
    def __init__(self, retry_after: int | None) -> None:
        message = "STRATZ rate limit hit"
        if retry_after is not None:
            message += f", retry after {retry_after} seconds"
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True)
class GameVersion:
    id: int
    name: str
    released: date


def run_query(
    query: str,
    variables: dict | None,
    *,
    token: str,
    post: Callable[..., requests.Response] = requests.post,
) -> dict:
    try:
        response = post(
            GRAPHQL_URL,
            json={"query": query, "variables": variables or {}},
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json",
            },
            timeout=TIMEOUT,
        )
    except requests.RequestException as error:
        raise StratzError(f"request failed: {type(error).__name__}") from error

    if response.status_code == HTTP_TOO_MANY_REQUESTS:
        raise StratzRateLimited(parse_retry_after(response.headers.get("Retry-After")))

    try:
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError) as error:
        raise StratzError(f"bad response: {type(error).__name__}") from error

    if not isinstance(body, dict):
        raise StratzError("unexpected response shape")
    if body.get("errors"):
        messages = "; ".join(str(error.get("message")) for error in body["errors"])
        raise StratzError(f"GraphQL errors: {messages}")
    data = body.get("data")
    if not isinstance(data, dict):
        raise StratzError("response has no data")
    return data


def fetch_latest_game_version(
    *, token: str, post: Callable[..., requests.Response] = requests.post
) -> GameVersion:
    data = run_query(VERSIONS_QUERY, None, token=token, post=post)
    try:
        latest = max(data["constants"]["gameVersions"], key=lambda version: version["id"])
        released = datetime.fromtimestamp(latest["asOfDateTime"], UTC).date()
        return GameVersion(id=latest["id"], name=latest["name"], released=released)
    except (KeyError, TypeError, ValueError) as error:
        raise StratzError(f"unexpected game version data: {type(error).__name__}") from error


def fetch_hero_constants(
    game_version_id: int,
    *,
    token: str,
    post: Callable[..., requests.Response] = requests.post,
) -> list[dict]:
    data = run_query(HEROES_QUERY, {"gameVersionId": game_version_id}, token=token, post=post)
    try:
        return data["constants"]["heroes"]
    except (KeyError, TypeError) as error:
        raise StratzError(f"unexpected hero data: {type(error).__name__}") from error


def clean(value: float | None, digits: int = 3) -> float | None:
    return None if value is None else round(float(value), digits)


def convert_hero(hero: dict) -> dict:
    stats = hero.get("stats")
    if not stats:
        raise StratzError(f"hero {hero.get('displayName')} has no stats")
    try:
        bonus_field = SINGLE_ATTRIBUTE_BASE_FIELD.get(stats["primaryAttribute"])
        attribute_bonus = stats[bonus_field] if bonus_field else 0
        return {
            "id": hero["id"],
            "base_str": clean(stats["strengthBase"]),
            "base_agi": clean(stats["agilityBase"]),
            "base_int": clean(stats["intelligenceBase"]),
            "str_gain": clean(stats["strengthGain"]),
            "agi_gain": clean(stats["agilityGain"]),
            "int_gain": clean(stats["intelligenceGain"]),
            "base_attack_min": clean(stats["startingDamageMin"] - attribute_bonus),
            "base_attack_max": clean(stats["startingDamageMax"] - attribute_bonus),
            "base_armor": clean(stats["startingArmor"] - stats["agilityBase"] / 6),
            "base_health_regen": clean(stats["hpRegen"]),
            "base_mana_regen": clean(stats["mpRegen"]),
            "attack_range": clean(stats["attackRange"]),
            "attack_rate": clean(stats["attackRate"]),
            "attack_point": clean(stats["attackAnimationPoint"]),
            "move_speed": clean(stats["moveSpeed"]),
            "turn_rate": clean(stats["moveTurnRate"]),
            "day_vision": clean(stats["visionDaytimeRange"]),
            "night_vision": clean(stats["visionNighttimeRange"]),
        }
    except (KeyError, TypeError) as error:
        raise StratzError(f"hero {hero.get('displayName')}: missing stat {error}") from error


def to_base_stats_payload(raw_heroes: list[dict]) -> list[dict]:
    return [convert_hero(hero) for hero in raw_heroes]


def ingest_stratz_base_stats(
    *,
    token: str,
    force: bool = False,
    post: Callable[..., requests.Response] = requests.post,
    today: date | None = None,
) -> SnapshotResult:
    version = fetch_latest_game_version(token=token, post=post)
    days_since_patch = max(0, ((today or datetime.now(UTC).date()) - version.released).days)

    def fetch() -> list[dict]:
        return to_base_stats_payload(fetch_hero_constants(version.id, token=token, post=post))

    return ingest_snapshot(
        version.name,
        fetch=fetch,
        source=SOURCE_STRATZ,
        force=force,
        days_since_patch=days_since_patch,
        include_base_stats=True,
        include_winrates=False,
        update_heroes=False,
    )


def main(arguments: list[str], post: Callable[..., requests.Response] = requests.post) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m dota_meta_bot.ingest.stratz",
        description="Store the latest patch's hero base stats from STRATZ, skipping the heavy call if already stored.",
    )
    parser.add_argument("--force", action="store_true")
    options = parser.parse_args(arguments)

    token = get_settings().stratz_api_token
    if token is None:
        print("STRATZ_API_TOKEN is not set in .env", file=sys.stderr)
        return 1

    try:
        result = ingest_stratz_base_stats(
            token=token.get_secret_value(), force=options.force, post=post
        )
    except (
        StratzError,
        InvalidPatch,
        InvalidHeroData,
        UnknownHeroes,
        IngestAlreadyRunning,
    ) as error:
        print(f"STRATZ ingest failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1

    print(describe_result(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
