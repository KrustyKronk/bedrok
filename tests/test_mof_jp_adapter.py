"""Japan's Ministry of Finance JGB curve CSV (session 10). Pure parsing."""
from datetime import date

import pytest

from bedrok.ingest.adapters import mof_jp

# Shape of the live file (Shift-JIS decoded), cut short: a title line, the
# header, rows dated 1974/9/24 with "-" where a tenor was not quoted, and a
# trailing note line that is not a date.
SAMPLE = """Interest Rate,,,,,,,,,,,,,,,(Unit : %)
Date,1Y,2Y,3Y,4Y,5Y,6Y,7Y,8Y,9Y,10Y,15Y,20Y,25Y,30Y,40Y
1974/9/24,10.327,9.362,8.83,8.515,8.348,8.29,8.24,8.121,8.127,-,-,-,-,-,-
2026/10/8,1.657,1.936,2.084,2.277,2.414,2.54,2.669,2.824,2.954,3.089,3.618,3.916,4.165,4.136,4.134
  If you cannot download the latest csv data, please clear the browser's cache,,,,,,,,,,,,,,,
"""


def test_a_tenor_skips_dashes_and_the_note_line():
    df = mof_jp._parse(SAMPLE, "10Y")
    assert df["date"].tolist() == [date(2026, 10, 8)]
    assert df["value"].tolist() == [3.089]
    assert mof_jp._parse(SAMPLE, "1Y")["value"].tolist() == [10.327, 1.657]


def test_an_unknown_tenor_or_layout_fails_loudly():
    with pytest.raises(ValueError, match="no '11Y'"):
        mof_jp._parse(SAMPLE, "11Y")
    with pytest.raises(ValueError, match="layout"):
        mof_jp._parse("nothing here\n", "10Y")
