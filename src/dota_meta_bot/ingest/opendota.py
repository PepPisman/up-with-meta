from collections.abc import Callable

import requests

HERO_STATS_URL = "https://api.opendota.com/api/heroStats"
TIMEOUT = (3, 10)
HTTP_TOO_MANY_REQUESTS = 429


class OpenDotaError(RuntimeError):
    pass


class RateLimited(OpenDotaError):
    def __init__(self, retry_after: int | None) -> None:
        message = "OpenDota rate limit hit"
        if retry_after is not None:
            message += f", retry after {retry_after} seconds"
        super().__init__(message)
        self.retry_after = retry_after


def parse_retry_after(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def fetch_hero_stats(get: Callable[..., requests.Response] = requests.get) -> object:
    try:
        response = get(HERO_STATS_URL, timeout=TIMEOUT)
    except requests.RequestException as error:
        raise OpenDotaError(f"request failed: {type(error).__name__}: {error}") from error

    if response.status_code == HTTP_TOO_MANY_REQUESTS:
        raise RateLimited(parse_retry_after(response.headers.get("Retry-After")))

    try:
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as error:
        raise OpenDotaError(f"bad response: {type(error).__name__}: {error}") from error
