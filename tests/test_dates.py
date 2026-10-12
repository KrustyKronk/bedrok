"""One reading of the dates a person types (core/dates.py)."""
from datetime import date

import pytest

from bedrok.core import dates


def test_a_year_or_month_is_its_first_day_or_as_an_end_its_last():
    assert dates.parse("2010") == date(2010, 1, 1)
    assert dates.parse("2010-03") == date(2010, 3, 1)
    assert dates.parse("2010-03-15") == date(2010, 3, 15)
    assert dates.parse("2025", end=True) == date(2025, 12, 31)
    assert dates.parse("2024-02", end=True) == date(2024, 2, 29)
    assert dates.parse(None) is None and dates.parse("") is None


@pytest.mark.parametrize("bad", ["2020-13-01", "2021-02-30", "March 2020", "2010/03", "20100315"])
def test_anything_else_is_refused_with_the_forms_it_takes(bad):
    with pytest.raises(ValueError, match="YYYY, YYYY-MM or YYYY-MM-DD"):
        dates.parse(bad, name="start")
