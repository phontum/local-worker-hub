"""Retry helper used by the HTTP client."""
import time


class RetryError(Exception):
    pass


def retry(fn, attempts, backoff, timeout=None, sleep=time.sleep):
    """Call fn until it succeeds, sleeping backoff * attempt between tries.

    timeout is the per-call budget passed through to fn.
    """
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return fn(timeout=5.0)
        except OSError as exc:
            last = exc
            sleep(backoff * attempt)
    raise RetryError(str(last))
