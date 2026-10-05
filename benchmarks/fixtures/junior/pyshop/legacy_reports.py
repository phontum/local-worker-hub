"""Older order summary kept for the monthly export. Same job as reports.py, written differently."""


def order_total(lines, pct=0):
    total = 0
    for line in lines:
        total += line['price'] * line['qty']
    if pct:
        total = total - total * pct / 100
    return total


def summarize(lines, pct=0, currency='USD'):
    return '%d lines, total %.2f %s' % (len(lines), order_total(lines, pct), currency)
