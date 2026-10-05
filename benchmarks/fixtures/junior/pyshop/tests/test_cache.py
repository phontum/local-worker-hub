from cache import TTLCache


def test_entry_expires_after_ttl():
    now = [0.0]
    cache = TTLCache(10, clock=lambda: now[0])
    cache.set('a', 1)
    now[0] = 10.0
    assert cache.get('a') is None


def test_entry_alive_before_ttl():
    now = [0.0]
    cache = TTLCache(10, clock=lambda: now[0])
    cache.set('a', 1)
    now[0] = 9.9
    assert cache.get('a') == 1
