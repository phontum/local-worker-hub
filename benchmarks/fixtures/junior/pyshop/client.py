"""Tiny HTTP-ish client that retries transient failures."""
from config import load_config
from retry import retry


class Client:
    def __init__(self, transport, cfg=None):
        self.transport = transport
        self.cfg = cfg or load_config()

    def get(self, url):
        return retry(
            lambda timeout: self.transport(url, timeout=timeout),
            attempts=self.cfg['max_retries'],
            backoff=self.cfg['backoff_s'],
            timeout=self.cfg['timeout_s'],
        )
