from pricing import discount, format_price


def test_discount():
    assert discount(100, 10) == 90.0


def test_format_price():
    assert format_price(1234.5) == '1,234.50 USD'
