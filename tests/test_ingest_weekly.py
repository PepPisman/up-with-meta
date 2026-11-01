from datetime import UTC, datetime, timedelta

import pytest
import requests
from fakes import FakeResponse
from sqlalchemy import text
from test_ingest_snapshot import FakeFetch
from test_ingest_stratz import TODAY, TOKEN, VERSIONS, FakeStratz, SingleResponse, preload_heroes

from dota_meta_bot.core.config import Settings
from dota_meta_bot.core.db import get_engine
from dota_meta_bot.ingest import weekly as weekly_module
from dota_meta_bot.ingest.opendota import OpenDotaError
from dota_meta_bot.ingest.snapshot import SOURCE_API
from dota_meta_bot.ingest.stratz import SOURCE_STRATZ, StratzError
from dota_meta_bot.ingest.weekly import WEEKLY_MIN_AGE_DAYS, main, run_weekly

START = datetime(2026, 10, 20, 12, tzinfo=UTC)
NEWER_VERSION = {
    "id": 191,
    "name": "7.42",
    "asOfDateTime": int(datetime(2026, 10, 25, tzinfo=UTC).timestamp()),
}


def count(table: str) -> int:
    with get_engine().connect() as connection:
        return connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()


def snapshots_of(source: str) -> int:
    with get_engine().connect() as connection:
        return connection.execute(
            text("SELECT count(*) FROM snapshots WHERE source = :source"), {"source": source}
        ).scalar_one()


def weekly(post: FakeStratz, fetch: FakeFetch, now: datetime = START, **options):
    return run_weekly(token=TOKEN, post=post, fetch=fetch, today=TODAY, now=now, **options)


def test_the_window_is_one_week():
    assert WEEKLY_MIN_AGE_DAYS == 7


def test_a_first_run_stores_winrates_for_the_latest_patch_and_base_stats(migrated_database):
    preload_heroes()
    post, fetch = FakeStratz(), FakeFetch()

    result = weekly(post, fetch)

    assert result.patch == "7.41f"
    assert result.winrates.fetched is True
    assert (result.winrates.patch, result.winrates.source) == ("7.41f", SOURCE_API)
    assert result.base_stats.fetched is True
    assert (result.base_stats.patch, result.base_stats.source) == ("7.41f", SOURCE_STRATZ)
    assert (snapshots_of(SOURCE_API), snapshots_of(SOURCE_STRATZ)) == (1, 1)
    assert (count("hero_winrate_stats"), count("hero_base_stats")) == (3, 4)
    with get_engine().connect() as connection:
        days = connection.execute(
            text("SELECT days_since_patch FROM snapshots WHERE source = :source"), {"source": SOURCE_API}
        ).scalar_one()
    assert days == 35


def test_a_repeat_run_the_same_day_calls_nothing_but_the_cheap_version_check(migrated_database):
    preload_heroes()
    weekly(FakeStratz(), FakeFetch())
    post, fetch = FakeStratz(), FakeFetch()

    result = weekly(post, fetch, now=START + timedelta(hours=1))

    assert (result.winrates.fetched, result.base_stats.fetched) == (False, False)
    assert fetch.calls == 0
    assert len(post.calls) == 1
    assert post.hero_calls == []
    assert (snapshots_of(SOURCE_API), snapshots_of(SOURCE_STRATZ)) == (1, 1)


def test_six_days_later_is_still_inside_the_window(migrated_database):
    preload_heroes()
    weekly(FakeStratz(), FakeFetch())
    fetch = FakeFetch()

    result = weekly(FakeStratz(), fetch, now=START + timedelta(days=6, hours=23))

    assert result.winrates.fetched is False
    assert fetch.calls == 0


def test_a_week_later_refreshes_winrates_but_not_base_stats_without_a_new_patch(migrated_database):
    preload_heroes()
    weekly(FakeStratz(), FakeFetch())
    post, fetch = FakeStratz(), FakeFetch()

    result = weekly(post, fetch, now=START + timedelta(days=7))

    assert result.winrates.fetched is True
    assert result.base_stats.fetched is False
    assert fetch.calls == 1
    assert post.hero_calls == []
    assert (snapshots_of(SOURCE_API), snapshots_of(SOURCE_STRATZ)) == (2, 1)
    assert (count("hero_winrate_stats"), count("hero_base_stats")) == (6, 4)


def test_a_new_patch_fetches_both_even_inside_the_window(migrated_database):
    preload_heroes()
    weekly(FakeStratz(), FakeFetch())
    post, fetch = FakeStratz(versions=[*VERSIONS, NEWER_VERSION]), FakeFetch()

    result = weekly(post, fetch, now=START + timedelta(days=1))

    assert result.patch == "7.42"
    assert (result.winrates.fetched, result.base_stats.fetched) == (True, True)
    assert fetch.calls == 1
    assert len(post.hero_calls) == 1
    with get_engine().connect() as connection:
        rows = connection.execute(text("SELECT patch, source FROM snapshots")).all()
    assert {tuple(row) for row in rows} == {
        ("7.41f", SOURCE_API),
        ("7.41f", SOURCE_STRATZ),
        ("7.42", SOURCE_API),
        ("7.42", SOURCE_STRATZ),
    }


def test_force_fetches_both_again(migrated_database):
    preload_heroes()
    weekly(FakeStratz(), FakeFetch())
    post, fetch = FakeStratz(), FakeFetch()

    result = weekly(post, fetch, now=START + timedelta(hours=1), force=True)

    assert (result.winrates.fetched, result.base_stats.fetched) == (True, True)
    assert fetch.calls == 1
    assert len(post.hero_calls) == 1


def test_a_failed_patch_check_fetches_nothing(migrated_database):
    fetch = FakeFetch()
    post = SingleResponse(error=requests.ConnectionError("down"))

    with pytest.raises(StratzError):
        run_weekly(token=TOKEN, post=post, fetch=fetch, today=TODAY, now=START)

    assert fetch.calls == 0
    assert count("snapshots") == 0


def test_an_opendota_failure_stops_the_run_before_base_stats(migrated_database):
    preload_heroes()
    post = FakeStratz()

    with pytest.raises(OpenDotaError):
        weekly(post, FakeFetch(error=OpenDotaError("down")))

    assert post.hero_calls == []
    assert count("snapshots") == 0


def _settings(token: str | None) -> Settings:
    return Settings(_env_file=None, postgres_password="x", stratz_api_token=token)


def test_the_command_line_needs_a_token_and_makes_no_call_without_one(monkeypatch, capsys):
    monkeypatch.setattr(weekly_module, "get_settings", lambda: _settings(None))
    post, fetch = FakeStratz(), FakeFetch()

    assert main([], post=post, fetch=fetch) == 1

    assert "STRATZ_API_TOKEN" in capsys.readouterr().err
    assert post.calls == []
    assert fetch.calls == 0


def test_the_command_line_stores_then_skips_and_never_prints_the_token(monkeypatch, capsys, migrated_database):
    monkeypatch.setattr(weekly_module, "get_settings", lambda: _settings(TOKEN))
    preload_heroes()

    assert main([], post=FakeStratz(), fetch=FakeFetch()) == 0
    first = capsys.readouterr()
    assert "Latest patch: 7.41f" in first.out
    assert first.out.count("Stored") == 2
    assert main([], post=FakeStratz(), fetch=FakeFetch()) == 0
    second = capsys.readouterr()
    assert second.out.count("Skipped") == 2
    assert TOKEN not in first.out + first.err + second.out + second.err


def test_the_command_line_reports_a_failure_with_exit_code_one(monkeypatch, capsys):
    monkeypatch.setattr(weekly_module, "get_settings", lambda: _settings(TOKEN))
    post = SingleResponse(FakeResponse(status_code=500))

    assert main([], post=post, fetch=FakeFetch()) == 1

    captured = capsys.readouterr()
    assert "StratzError" in captured.err
    assert TOKEN not in captured.out + captured.err
