import time

import requests

USER_AGENT = "Catalyst Research briantran07@berkeley.edu"
MIN_INTERVAL = 1.0 / 10  # SEC cap: 10 req/sec
REQUEST_TIMEOUT = 30  # seconds — no timeout was set before; a hung connection blocked forever
MAX_RETRIES = 3  # only for transient failures (network errors, 5xx) — 4xx never retried

_last_request_time = 0.0


def _request(url: str) -> requests.Response:
    global _last_request_time

    for attempt in range(MAX_RETRIES):
        elapsed = time.monotonic() - _last_request_time
        if elapsed < MIN_INTERVAL:
            time.sleep(MIN_INTERVAL - elapsed)

        try:
            response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        except (requests.ConnectionError, requests.Timeout):
            _last_request_time = time.monotonic()
            if attempt == MAX_RETRIES - 1:
                raise
            time.sleep(2**attempt)
            continue

        _last_request_time = time.monotonic()
        if response.status_code >= 500 and attempt < MAX_RETRIES - 1:
            time.sleep(2**attempt)
            continue

        response.raise_for_status()
        return response


def get(url: str) -> dict:
    """GET a SEC JSON endpoint, rate-limited to 10 req/sec across all callers."""
    return _request(url).json()


def get_text(url: str) -> str:
    """GET a SEC non-JSON endpoint (e.g. the atom/XML browse-edgar feed),
    rate-limited the same as get()."""
    return _request(url).text
