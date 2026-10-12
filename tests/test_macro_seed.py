"""Seed-file integrity for bedrok/seeds/macro_catalog.csv.

The seed is hand-edited, unquoted CSV feeding a table with a PRIMARY KEY and a
licence check, so the failure modes are all silent: a stray comma shifts every
later column, a duplicate id makes one row vanish into an upsert, a typo'd
licence trips validate on every install. These assertions are cheap and catch
all of that at commit time rather than on someone's machine.
"""
import pandas as pd
import pytest

import duckdb

from bedrok.core import licensing, store
from bedrok.ingest import pipeline

EXPECTED_HEADER = ["series_id", "source", "source_code", "name", "frequency",
                   "units", "notes", "staleness_days", "license", "cache"]

VALID_FREQUENCIES = {"daily", "weekly", "monthly", "quarterly", "annual", "event"}


@pytest.fixture(scope="module")
def seed():
    return pd.read_csv(pipeline.CATALOG_CSV, dtype=str)


def test_header_is_exactly_as_expected(seed):
    """Pinned so an added column can't silently shift the licence into notes."""
    assert list(seed.columns) == EXPECTED_HEADER


def test_no_duplicate_series_ids(seed):
    dupes = seed["series_id"][seed["series_id"].duplicated()].tolist()
    assert dupes == [], f"duplicate series_id(s) would be swallowed by the upsert: {dupes}"


def test_required_fields_are_present(seed):
    for col in ("series_id", "source", "source_code", "name"):
        blank = seed[seed[col].isna() | (seed[col].str.strip() == "")]
        assert blank.empty, f"blank {col} in rows: {blank['series_id'].tolist()}"


def test_every_source_has_a_fetcher_or_is_bulk(seed):
    known = set(pipeline.MACRO_FETCHERS) | set(pipeline.BULK_SOURCES)
    unknown = sorted(set(seed["source"]) - known)
    assert unknown == [], f"no fetcher registered for source(s): {unknown}"


def test_every_licence_is_in_the_vocabulary(seed):
    bad = seed[~seed["license"].isin(licensing.LICENSE_VALUES)]
    assert bad.empty, ("unrecognised licence(s) would trip validate on every install: "
                       f"{bad[['series_id', 'license']].to_dict('records')}")


def test_frequencies_are_recognised(seed):
    """An unknown frequency silently falls back to a 45-day staleness allowance."""
    bad = sorted(set(seed["frequency"].dropna()) - VALID_FREQUENCIES)
    assert bad == [], f"unrecognised frequency value(s): {bad}"


def test_every_row_says_how_stale_it_may_get(seed):
    """Explicit on every row (session 7b): a frequency default hid a daily
    series labelled 'event' behind a 450-day allowance. Allowances count from
    the period START, the date the store keeps."""
    for sid, val in zip(seed["series_id"], seed["staleness_days"]):
        assert isinstance(val, str) and val.strip().isdigit(), \
            f"{sid}: staleness_days={val!r} must be a whole number of days"


def test_restricted_series_document_their_substitute(seed):
    """A restricted series carries notes that say why, and what to use instead."""
    restricted = seed[seed["license"] == licensing.RESTRICTED]
    assert len(restricted) >= 4
    for sid, notes in zip(restricted["series_id"], restricted["notes"]):
        assert "PERSONAL USE" in notes, f"{sid}: notes must say it is personal-use only"
        assert "ubstitute" in notes or "us_nfci" in notes, f"{sid}: notes must name a substitute"


def test_cache_is_store_or_session(seed):
    assert set(seed["cache"]) <= licensing.CACHE_VALUES


def test_nothing_from_fred_is_stored(seed):
    """FRED's terms forbid storing its content: since session 3b every FRED row
    is session-only, fetched live and never written."""
    stored_fred = seed[(seed["source"] == "fred") & (seed["cache"] == "store")]["series_id"]
    assert stored_fred.tolist() == []


def test_every_row_speaks_the_units_vocabulary(seed):
    """Units are the authority on currency and scale (rule 5): written out,
    and an index names its base. Seed rows and the rows code builds alike."""
    from bedrok.core import units
    from bedrok.ingest import pipeline
    built = pipeline.static_catalog_rows()
    for sid, u in list(zip(seed["series_id"], seed["units"])) + \
            list(zip(built["series_id"], built["units"])):
        assert units.valid(u), f"{sid}: units {u!r} are outside core/units.py's vocabulary"


def test_pink_sheet_labels_are_written_out():
    from bedrok.core import units
    assert units.from_label("($/mt)") == "USD per metric ton"
    assert units.from_label("(cents/sheet)") == "US cents per sheet"
    assert units.valid(units.from_label("($/dmtu)"))


# Ids never change once published; where an old prefix misleads, the name tells the truth.
GLOBAL_DESPITE_PREFIX = {"us_gscpi"}


def test_names_are_unique_and_lead_with_their_geography(seed):
    from bedrok.ingest import pipeline
    names = list(seed["name"]) + list(pipeline.static_catalog_rows()["name"])
    assert len(names) == len(set(names)), "two series share a name"
    for sid, name in zip(seed["series_id"], seed["name"]):
        from bedrok.core import geography
        geo = geography.CODES.get(sid.split("_")[0])
        if geo and sid not in GLOBAL_DESPITE_PREFIX:
            assert name.startswith(geo), f"{sid}: name {name!r} should lead with {geo!r}"
