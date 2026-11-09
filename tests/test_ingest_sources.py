from datetime import UTC, datetime

import pytest
from test_ingest_snapshot import FakeFetch
from test_ingest_stratz import TODAY, TOKEN, FakeStratz, preload_heroes

from dota_meta_bot.ingest.snapshot import SOURCE_API, SnapshotResult
from dota_meta_bot.ingest.sources import (
    SOURCE_DOTA2PROTRACKER,
    SOURCE_DOTABUFF,
    SOURCES,
    SourceSpec,
    UnknownSource,
    automated_sources,
    get_source,
    verification_order,
)
from dota_meta_bot.ingest.stratz import GRAPHQL_URL, SOURCE_STRATZ
from dota_meta_bot.ingest.weekly import run_weekly

START = datetime(2026, 10, 20, 12, tzinfo=UTC)


def test_source_names_and_verification_ranks_are_unique():
    assert len({spec.name for spec in SOURCES}) == len(SOURCES)
    assert len({spec.verification_rank for spec in SOURCES}) == len(SOURCES)


def test_the_verification_order_is_the_owners_priority_list():
    assert [spec.name for spec in verification_order()] == [
        SOURCE_DOTABUFF,
        SOURCE_DOTA2PROTRACKER,
        SOURCE_STRATZ,
        SOURCE_API,
    ]


def test_only_opendota_and_stratz_are_automated_and_opendota_runs_first_to_load_the_heroes():
    assert [spec.name for spec in automated_sources()] == [SOURCE_API, SOURCE_STRATZ]


@pytest.mark.parametrize("name", [SOURCE_DOTABUFF, SOURCE_DOTA2PROTRACKER])
def test_dotabuff_and_protracker_are_reference_only_and_have_no_job(name):
    spec = get_source(name)

    assert spec.automated is False
    assert spec.run is None


def test_an_unknown_source_is_rejected_with_the_known_names():
    with pytest.raises(UnknownSource, match="opendota"):
        get_source("liquipedia")


def test_a_weekly_run_only_talks_to_stratz_and_calls_opendota_once(migrated_database):
    preload_heroes()
    post, fetch = FakeStratz(), FakeFetch()

    run_weekly(token=TOKEN, post=post, fetch=fetch, today=TODAY, now=START)

    assert {call["url"] for call in post.calls} == {GRAPHQL_URL}
    assert fetch.calls == 1


def test_adding_a_source_is_one_registry_entry(migrated_database):
    preload_heroes()
    seen = []

    def run_extra(context):
        seen.append(context.version.name)
        return SnapshotResult(
            fetched=False, snapshot_at=START, patch=context.version.name, source="extra", heroes=0
        )

    extra = SourceSpec("extra", "winrates", verification_rank=5, run=run_extra)
    reference_only = SourceSpec("reference_only", "winrates", verification_rank=6)

    result = run_weekly(
        token=TOKEN,
        post=FakeStratz(),
        fetch=FakeFetch(),
        today=TODAY,
        now=START,
        sources=(*SOURCES, extra, reference_only),
    )

    assert list(result.results) == [SOURCE_API, SOURCE_STRATZ, "extra"]
    assert seen == ["7.41f"]


def test_a_failing_source_stops_the_sources_after_it(migrated_database):
    preload_heroes()
    ran = []

    def run_broken(context):
        raise RuntimeError("boom")

    def run_after(context):
        ran.append("after")

    sources = (
        SourceSpec("broken", "winrates", verification_rank=1, run=run_broken),
        SourceSpec("after", "winrates", verification_rank=2, run=run_after),
    )

    with pytest.raises(RuntimeError, match="boom"):
        run_weekly(
            token=TOKEN, post=FakeStratz(), fetch=FakeFetch(), today=TODAY, now=START, sources=sources
        )

    assert ran == []
