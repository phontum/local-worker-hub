"""Price helpers."""


def discount(price, pct):
    return round(price * (1 - pct / 100), 2)


def format_price(amount, currency='USD'):
    return f'{amount:,.2f} {currency}'
