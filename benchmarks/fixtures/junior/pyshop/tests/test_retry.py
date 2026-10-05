import pytest
from client import Client
from retry import RetryError, retry


def test_retry_succeeds_after_failures():
    calls = []

    def flaky(timeout):
        calls.append(timeout)
        if len(calls) < 3:
            raise OSError('boom')
        return 'ok'

    assert retry(flaky, attempts=3, backoff=0, sleep=lambda s: None) == 'ok'


def test_retry_passes_configured_timeout():
    seen = []

    def fn(timeout):
        seen.append(timeout)
        return 'ok'

    retry(fn, attempts=1, backoff=0, timeout=2.5, sleep=lambda s: None)
    assert seen == [2.5]


def test_client_uses_config_timeout():
    seen = []
    client = Client(lambda url, timeout: seen.append(timeout) or 'ok', cfg={'max_retries': 1, 'backoff_s': 0, 'timeout_s': 9.0})
    client.get('http://x')
    assert seen == [9.0]


def test_retry_gives_up():
    def fn(timeout):
        raise OSError('down')

    with pytest.raises(RetryError):
        retry(fn, attempts=2, backoff=0, sleep=lambda s: None)
