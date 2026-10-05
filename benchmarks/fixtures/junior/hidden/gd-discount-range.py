import pytest
from pricing import discount
assert discount(100, 10) == 90.0 and discount(100, 0) == 100.0 and discount(100, 100) == 0.0
for bad in (-1, 101, 150):
    with pytest.raises(ValueError):
        discount(100, bad)
