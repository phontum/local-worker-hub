"""Runtime configuration for pyshop, read from the environment with defaults."""
import os

DEFAULTS = {'timeout_s': 5.0, 'max_retries': 3, 'backoff_s': 0.5, 'cache_ttl_s': 60}


def load_config(env=None):
    env = os.environ if env is None else env
    cfg = dict(DEFAULTS)
    if 'SHOP_TIMEOUT_S' in env:
        cfg['timeout_s'] = float(env['SHOP_TIMEOUT_S'])
    if 'SHOP_MAX_RETRIES' in env:
        cfg['max_retries'] = int(env['SHOP_MAX_RETRIES'])
    if 'SHOP_BACKOFF_S' in env:
        cfg['backoff_s'] = float(env['SHOP_BACKOFF_S'])
    return cfg
