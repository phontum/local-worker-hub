import inspect, pricing, reports
for fn in (pricing.discount, reports.order_total, reports.summarize):
    params = inspect.signature(fn).parameters
    assert 'discount_pct' in params and 'pct' not in params, fn
rows = [{'price': 10, 'qty': 2}]
assert reports.summarize(rows, 50) == '1 lines, total 10.00 USD'
assert reports.summarize(rows, discount_pct=50) == '1 lines, total 10.00 USD'
