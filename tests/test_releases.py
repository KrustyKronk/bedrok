"""Release rules (core/releases.py, bedrok/seeds/release_rules.csv): when a
series' next period is due, and whether the seed can be trusted."""
import csv
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from bedrok.core import releases, sources
from bedrok.ingest import pipeline
from bedrok.ops import freshness

ET = ZoneInfo("America/New_York")


def _rule(**kw):
    base = {"series_id": "x", "prefix": "", "frequency": "monthly", "lag_min": "10",
            "lag_max": "18", "weekday": "", "time": "08:30", "tz": "America/New_York", "note": ""}
    base.update({k: str(v) for k, v in kw.items()})
    return releases.parse_row(base)


def _et(*args):
    return datetime(*args, tzinfo=ET)


# ---- the arithmetic ------------------------------------------------------------

def test_the_cpi_morning_that_started_this():
    """2026-10-15: September CPI out at 08:30 ET, August stored. Before 08:30
    the next period is due (inside 10-18 days after September ended); a day
    before the window it is fresh; past the window and the grace it is late."""
    r = _rule()
    w = releases.resolve(r, date(2026, 8, 1), _et(2026, 10, 15, 9))
    assert w["label"] == date(2026, 9, 1)
    assert w["due_at"] == _et(2026, 10, 12, 8, 30)          # 10 Oct is a Saturday
    assert w["expected_by"] == _et(2026, 10, 19, 8, 30)     # 18 Oct is a Sunday
    assert releases.status(w, _et(2026, 10, 15, 9)) == "due"
    assert releases.status(w, _et(2026, 10, 12, 8, 29)) == "fresh"
    assert releases.status(w, _et(2026, 10, 21, 8, 31)) == "late"


def test_a_weekday_rule_lands_on_its_weekday():
    """Payrolls: the first Friday after the month (October 2026 ends on a Saturday)."""
    w = releases.resolve(_rule(lag_min=1, lag_max=8, weekday="fri"), date(2026, 9, 1),
                         _et(2026, 10, 15))
    assert w["due_at"] == _et(2026, 11, 6, 8, 30)
    assert w["expected_by"] == _et(2026, 11, 13, 8, 30)


def test_times_keep_the_publishers_clock_across_daylight_saving():
    """The window crosses the US change on 1 Nov 2026: 08:30 stays 08:30 local."""
    w = releases.resolve(_rule(lag_min=26, lag_max=36), date(2026, 8, 1), _et(2026, 10, 15))
    assert w["due_at"].utcoffset().total_seconds() == -4 * 3600     # 26 Oct, EDT
    assert w["expected_by"].utcoffset().total_seconds() == -5 * 3600  # 6 Nov, EST
    assert (w["due_at"].hour, w["expected_by"].hour) == (8, 8)
    spring = releases.resolve(_rule(), date(2027, 2, 1), _et(2027, 3, 1))
    assert spring["due_at"].utcoffset().total_seconds() == -4 * 3600  # 15 Mar 2027, EDT


def test_quarters_years_weeks_and_days():
    q = releases.resolve(_rule(frequency="quarterly", lag_min=26, lag_max=32),
                         date(2026, 4, 1), _et(2026, 10, 15))
    assert q["label"] == date(2026, 7, 1) and q["due_at"].date() == date(2026, 10, 26)
    y = releases.resolve(_rule(frequency="annual", lag_min=60, lag_max=90),
                         date(2025, 1, 1), _et(2026, 10, 15))
    assert y["label"] == date(2026, 1, 1) and y["due_at"].date() == date(2027, 3, 1)
    wk = releases.resolve(_rule(frequency="weekly", lag_min=1, lag_max=1, weekday="thu",
                                time="16:30"), date(2026, 10, 7), _et(2026, 10, 15))
    assert wk["label"] == date(2026, 10, 14) and wk["due_at"] == _et(2026, 10, 15, 16, 30)
    d = releases.resolve(_rule(frequency="daily", lag_min=1, lag_max=1, time="16:15"),
                         date(2026, 10, 9), _et(2026, 10, 15))       # a Friday
    assert d["label"] == date(2026, 10, 12) and d["due_at"] == _et(2026, 10, 13, 16, 15)


def test_next_label_crosses_the_year():
    assert releases.next_label(date(2026, 12, 1), "monthly") == date(2027, 1, 1)
    assert releases.next_label(date(2026, 10, 1), "quarterly") == date(2027, 1, 1)
    assert releases.period_end(date(2026, 10, 1), "quarterly") == date(2026, 12, 31)


def test_a_flash_can_come_before_its_month_ends():
    """Eurostat's HICP flash, on the last working day: a negative lag."""
    w = releases.resolve(_rule(lag_min=-2, lag_max=3, time="11:00", tz="Europe/Brussels"),
                         date(2026, 8, 1), datetime(2026, 9, 20, tzinfo=timezone.utc))
    assert w["due_at"].date() == date(2026, 9, 28)


def test_a_session_series_resolves_from_the_clock():
    """Claims are never stored: the next release is the first one ahead of now,
    and the week it covers ends the Saturday before."""
    r = _rule(frequency="weekly", lag_min=5, lag_max=5, weekday="thu")
    w = releases.resolve(r, None, _et(2026, 10, 15, 9))          # a Thursday, after 08:30
    assert w["due_at"] == _et(2026, 10, 22, 8, 30)
    assert w["label"] == date(2026, 10, 17) and w["label"].weekday() == 5
    early = releases.resolve(r, None, _et(2026, 10, 15, 8))
    assert early["due_at"] == _et(2026, 10, 15, 8, 30)


def test_what_an_answer_says():
    w = releases.resolve(_rule(), date(2026, 8, 1), _et(2026, 10, 15, 9))
    due = releases.describe(w, "monthly", "due", date(2026, 8, 1))
    assert due["message"] == ("September 2026 is usually out between 12 Oct and 19 Oct 2026, "
                              "08:30 EDT, and is not in the store yet; August 2026 is the "
                              "latest held.")
    assert due["approximate"] is True and due["source"] == "rule"
    assert due["due_at"] == "2026-10-12T08:30:00-04:00"
    late = releases.describe(w, "monthly", "late", date(2026, 8, 1))
    assert late["message"].startswith("September 2026 was expected by 19 Oct 2026, 08:30 EDT")


def test_a_bad_row_is_refused():
    for bad in ({"frequency": "fortnightly"}, {"time": "8:30"}, {"lag_min": 20, "lag_max": 10},
                {"weekday": "friday"}, {"tz": "Mars/Olympus"}, {"prefix": "us_"}):
        with pytest.raises(Exception):
            _rule(**bad)


def test_the_most_specific_rule_wins(monkeypatch):
    rows = (_rule(series_id="", prefix="hicp_"), _rule(series_id="", prefix="hicp_core_"),
            _rule(series_id="hicp_core_ea"))
    monkeypatch.setattr(releases, "load", lambda: rows)
    assert releases.rule_for("hicp_core_ea") is rows[2]
    assert releases.rule_for("hicp_core_de") is rows[1]
    assert releases.rule_for("hicp_yoy_de") is rows[0]
    assert releases.rule_for("us_cpi") is None


# ---- the seed ------------------------------------------------------------------

def _catalog() -> dict[str, dict]:
    cat = pd.read_csv(pipeline.CATALOG_CSV, dtype=str)
    cat = pd.concat([cat, pipeline.static_catalog_rows().astype(str)], ignore_index=True)
    return {r["series_id"]: r for r in cat.to_dict("records")}


def test_the_seed_has_the_agreed_columns_and_every_row_parses():
    with open(releases.RULES_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == releases.COLUMNS
        raw = list(reader)
    rules = releases.load()
    assert len(rules) == len(raw) >= 40
    keys = [r["series_id"] or r["prefix"] for r in rules]
    assert len(keys) == len(set(keys)), "a series or prefix has two rules"
    assert all(r["note"] for r in rules), "every rule says where its timing comes from"


def test_every_rule_names_a_series_or_family_bedrok_has():
    cat = _catalog()
    prefixes = tuple(pipeline.BULK_STAGE_FOR_PREFIX) + tuple(pipeline.BULK_PREFIXES)
    for r in releases.load():
        if r["series_id"]:
            assert r["series_id"] in cat, f"unknown series {r['series_id']}"
            assert cat[r["series_id"]]["frequency"] == r["frequency"], r["series_id"]
        else:
            assert r["prefix"] in prefixes, f"unknown family {r['prefix']}"


def test_no_rule_lets_the_age_backstop_fire_before_its_release_can_land():
    """Staleness overrides due and late, so a series' allowance must outlast
    the latest its next period can come: the label gap to that period's end,
    the lag, a weekend or weekday roll, and the grace. It raised eleven
    allowances on the way in (PCE, M2, Japan's CPIs among them)."""
    cat = _catalog()
    for r in releases.load():
        if not r["series_id"]:
            continue
        row = cat[r["series_id"]]
        if row.get("cache") == "session":
            continue
        allow = freshness.allowance(row["frequency"], row.get("staleness_days"), r["series_id"])
        need = (releases.SPAN_DAYS[r["frequency"]] + r["lag_max"]
                + (6 if r["weekday"] else 2) + releases.GRACE.days)
        assert allow >= need, f"{r['series_id']}: allowance {allow}d < {need}d"
    for r in releases.load():
        if r["prefix"]:
            allow = freshness.allowance(r["frequency"], None, r["prefix"] + "x")
            need = (releases.SPAN_DAYS[r["frequency"]] + r["lag_max"]
                    + (6 if r["weekday"] else 2) + releases.GRACE.days)
            assert allow >= need, f"{r['prefix']}: allowance {allow}d < {need}d"


def test_the_headline_series_all_have_a_rule():
    """The ones people ask about on a release morning."""
    for sid in ("us_cpi", "us_core_cpi", "us_payrolls", "us_unemployment", "us_pce",
                "us_real_gdp", "us_retail_sales", "ea_hicp_yoy", "uk_cpi_yoy",
                "de_cpi_destatis", "jp_cpi", "ca_cpi", "us_10y", "us_initial_claims"):
        assert releases.rule_for(sid), sid


def test_every_zone_in_the_seed_is_a_real_zone_and_sources_exist():
    assert {"bls", "bea", "eurostat", "ons"} <= set(sources.load())
    for r in releases.load():
        ZoneInfo(r["tz"])
