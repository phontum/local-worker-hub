"""Order summaries."""
from pricing import discount, format_price


def order_total(lines, pct=0):
    subtotal = sum(l['price'] * l['qty'] for l in lines)
    return discount(subtotal, pct)


def summarize(lines, pct=0, currency='USD'):
    return f"{len(lines)} lines, total {format_price(order_total(lines, pct), currency)}"
