"""The tie-out verdicts: equal, revised, or something to read before refreshing."""
import datetime as dt

import numpy as np
import pandas as pd

from bedrok.ops import tieout


def _f(start, values, freq="MS"):
    dates = pd.date_range(start, periods=len(values), freq=freq).date
    return pd.DataFrame({"date": dates, "value": values})


BASE = np.linspace(100, 200, 120) + np.sin(np.arange(120)) * 3


def test_identical_histories_are_the_same():
    c = tieout.compare(_f("2000-01-01", BASE), _f("2000-01-01", BASE))
    assert c["verdict"] == "same" and c["equal"] == 1.0 and c["overlap"] == 120


def test_a_small_revision_is_revised_not_a_failure():
    c = tieout.compare(_f("2000-01-01", BASE), _f("2000-01-01", BASE * 1.0005))
    assert c["verdict"] == "revised", c


def test_a_wrong_mapping_is_flagged():
    other = np.random.default_rng(0).normal(50, 10, 120)
    c = tieout.compare(_f("2000-01-01", BASE), _f("2000-01-01", other))
    assert c["verdict"] == "CHECK" and "corr" in c["why"]


def test_seasonally_unadjusted_against_adjusted_is_flagged():
    """The ECI trap: CIU (NSA) against CIS (SA) correlates well but the levels
    are not the same series."""
    seasonal = BASE + np.tile([4, -4, 4, -4], 30)
    c = tieout.compare(_f("2000-01-01", BASE), _f("2000-01-01", seasonal))
    assert c["verdict"] == "CHECK"


def test_a_shorter_new_history_is_flagged_even_when_equal():
    c = tieout.compare(_f("2000-01-01", BASE), _f("2005-01-01", BASE[60:]))
    assert c["verdict"] == "CHECK" and "starts later" in c["why"]


def test_new_source_with_nothing_is_flagged():
    c = tieout.compare(_f("2000-01-01", BASE), pd.DataFrame(columns=["date", "value"]))
    assert c["verdict"] == "CHECK"


def test_nothing_stored_is_new():
    c = tieout.compare(pd.DataFrame(columns=["date", "value"]), _f("2000-01-01", BASE))
    assert c["verdict"] == "new"
