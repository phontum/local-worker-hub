# Requirements

1. Retries must honour the configured per-call timeout (SHOP_TIMEOUT_S).
2. The cache must expire entries after cache_ttl_s seconds.
3. Discounts above 100 percent or below 0 percent must be rejected with ValueError.
4. Every config value can be overridden from an environment variable named SHOP_<KEY>.
