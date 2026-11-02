import pytest
import requests

from dota_meta_bot.ingest.opendota import (
    HERO_STATS_URL,
    TIMEOUT,
    OpenDotaError,
    RateLimited,
    fetch_hero_stats,
)


class FakeResponse:
    def __init__(self, status_code=200, body=None, headers=None, invalid_json=False):
        self.status_code = status_code
        self.body = body
        self.headers = headers or {}
        self.invalid_json = invalid_json

    def json(self):
        if self.invalid_json:
            raise ValueError("not json")
        return self.body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


class FakeGet:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def __call__(self, url, timeout):
        self.calls.append((url, timeout))
        if self.error:
            raise self.error
        return self.response


def test_fetch_returns_the_decoded_body_and_uses_the_agreed_url_and_timeout():
    get = FakeGet(FakeResponse(body=[{"id": 1}]))

    assert fetch_hero_stats(get=get) == [{"id": 1}]
    assert get.calls == [(HERO_STATS_URL, TIMEOUT)]
    assert TIMEOUT == (3, 10)


def test_a_429_raises_rate_limited_with_the_retry_after_value():
    get = FakeGet(FakeResponse(status_code=429, headers={"Retry-After": "30"}))

    with pytest.raises(RateLimited) as error:
        fetch_hero_stats(get=get)

    assert error.value.retry_after == 30


def test_a_429_without_a_retry_after_header_still_raises_rate_limited():
    get = FakeGet(FakeResponse(status_code=429))

    with pytest.raises(RateLimited) as error:
        fetch_hero_stats(get=get)

    assert error.value.retry_after is None


def test_rate_limited_is_an_opendota_error():
    assert issubclass(RateLimited, OpenDotaError)


def test_a_server_error_raises_opendota_error_but_not_rate_limited():
    get = FakeGet(FakeResponse(status_code=500))

    with pytest.raises(OpenDotaError) as error:
        fetch_hero_stats(get=get)

    assert not isinstance(error.value, RateLimited)


@pytest.mark.parametrize("network_error", [requests.ConnectionError("down"), requests.Timeout("slow")])
def test_network_failures_are_wrapped(network_error):
    with pytest.raises(OpenDotaError):
        fetch_hero_stats(get=FakeGet(error=network_error))


def test_an_invalid_json_body_is_wrapped():
    with pytest.raises(OpenDotaError):
        fetch_hero_stats(get=FakeGet(FakeResponse(invalid_json=True)))
