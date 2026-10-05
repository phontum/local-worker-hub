"""A small TTL cache."""
import time


class TTLCache:
    def __init__(self, ttl_s, clock=time.monotonic):
        self.ttl_s = ttl_s
        self.clock = clock
        self._items = {}

    def set(self, key, value):
        self._items[key] = (value, self.clock() + self.ttl_s)

    def get(self, key, default=None):
        item = self._items.get(key)
        if item is None:
            return default
        value, expires = item
        if self.clock() > expires:
            del self._items[key]
            return default
        return value

    def __len__(self):
        return len(self._items)
