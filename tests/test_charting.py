"""The chart recipe (session 9): the content every Bedrok chart carries."""
from datetime import date

from bedrok.core import charting


def test_colours_are_stable_by_order_and_wrap():
    assert charting.colour(0) == charting.PALETTE[0]
    assert charting.colour(len(charting.PALETTE)) == charting.PALETTE[0]
    assert len(set(charting.PALETTE)) == len(charting.PALETTE)


def test_one_unit_is_one_scale():
    placed = charting.axes([("us_hy_oas", "percent"), ("us_10y", "percent")])
    assert placed["left"]["series"] == ["us_hy_oas", "us_10y"]
    assert placed["right"] is None and placed["problem"] is None


def test_two_units_split_left_and_right_by_the_first_series():
    placed = charting.axes([("ea_hicp_yoy", "percent"), ("eia_brent", "USD per barrel"),
                            ("us_cpi", "percent")])
    assert placed["left"] == {"units": "percent", "series": ["ea_hicp_yoy", "us_cpi"]}
    assert placed["right"] == {"units": "USD per barrel", "series": ["eia_brent"]}


def test_three_units_are_a_problem_named_not_a_third_scale():
    placed = charting.axes([("a", "percent"), ("b", "USD per barrel"), ("c", "index 2015=100")])
    assert placed["right"]["series"] == ["b"]
    assert "c" in placed["problem"] and "index_to" in placed["problem"]


def test_units_go_in_the_subtitle_never_the_title():
    one = charting.axes([("us_10y", "percent")])
    assert charting.subtitle(one, "weekly", "last", date(2023, 10, 13), date(2026, 10, 9)) == \
        "Percent; weekly closes; Oct 2023 to Oct 2026"
    two = charting.axes([("x", "percent"), ("y", "USD per barrel")])
    assert charting.subtitle(two, "quarterly", "mean", date(2024, 7, 1), date(2026, 4, 1)) == \
        "Left: percent. Right: USD per barrel; quarterly averages; Q3 2024 to Q2 2026"
    # Nothing aggregated (a monthly series charted monthly): say the frequency, not "averages".
    assert charting.subtitle(one, "monthly", None, date(2025, 1, 1), date(2026, 8, 1)) == \
        "Percent; monthly; Jan 2025 to Aug 2026"
    assert "percent" not in charting.title_suggestion(["US 10-year Treasury yield"]).lower()


def test_titles_for_one_two_and_many():
    assert charting.title_suggestion(["A"]) == "A"
    assert charting.title_suggestion(["A", "B"]) == "A and B"
    assert charting.title_suggestion(["A", "B", "C", "D", "E"]) == "A and 4 others"


def test_x_labels_by_frequency_and_about_ten_ticks():
    weeks = [date(2026, 10, 2), date(2026, 10, 9)]
    assert charting.x_labels(weeks, "weekly")["labels"] == ["2 Oct 2026", "9 Oct 2026"]
    assert charting.x_labels([date(2026, 9, 1)], "monthly")["labels"] == ["Sep 26"]
    assert charting.x_labels([date(2026, 4, 1)], "quarterly")["labels"] == ["Q2 26"]
    assert charting.x_labels([date(2026, 1, 1)], "annual")["labels"] == ["2026"]
    assert charting.x_labels([date(2026, 1, 2)] * 157, "weekly")["tick_every"] == 16


def test_the_source_line_names_each_publisher_once_with_its_condition():
    line = charting.source_line([("Federal Reserve Board", False, False),
                                 ("FRED", True, True), ("Federal Reserve Board", False, False)])
    assert line == ("Source: Federal Reserve Board; "
                    "FRED (personal use only, fetched live, not stored).")


def test_footnotes_carry_what_the_chart_cannot_say():
    entries = [{"series_id": "us_cpi", "name": "US CPI", "warnings": [
                   {"period_start": "2025-10", "period_end": "2026-04",
                    "effect_on_data": "Most October 2025 indexes do not exist. More detail."}]},
               {"series_id": "us_fed_funds", "name": "Fed funds"}]
    table = {"stepped": {"us_cpi": False, "us_fed_funds": True},
             "partial_last": {"us_cpi": True, "us_fed_funds": False},
             "incomplete": {"us_cpi": ["2025-10-01"]},
             "cut": "the oldest 3 of 10 rows were cut"}
    notes = charting.footnotes(entries, table, "weekly")
    assert notes[0] == ("US CPI, 2025-10 to 2026-04: Most October 2025 indexes do not exist. "
                        "More detail.")
    assert any("Fed funds" in n and "step" in n for n in notes)
    assert any("not over yet" in n for n in notes)
    table["partial"] = {"us_cpi": {"n": 6, "first": "2026-10-01", "last": "2026-10-08"}}
    notes = charting.footnotes(entries, table, "quarterly")
    assert "US CPI: the latest period is not over yet (it holds 6 observations, 1 to 8 Oct 2026)." in notes
    assert any("2025-10-01" in n for n in notes)
    assert notes[-1].startswith("the oldest 3")


def test_rate_like_units_share_one_scale_and_index_bases_do_not():
    """Session D1: "percent" and "percent change on previous quarter" are one
    dimension (the self-test drew two Spanish growth rates on two scales);
    two index bases are not."""
    from bedrok.core import units
    placed = charting.axes([("gdp_yoy_es", "percent"),
                            ("es_gdp_qoq", "percent change on previous quarter"),
                            ("gdp_contrib_hh_es", "percentage points")])
    assert placed["right"] is None and placed["left"]["units"] == "percent"
    placed = charting.axes([("a", "index 2015=100"), ("b", "index 2020=100")])
    assert placed["right"]["series"] == ["b"]
    assert units.dimension("billions USD") == units.dimension("millions USD") == "money:USD"
    assert units.dimension("percent of GDP") == units.dimension("percentage points of GDP")
    assert units.is_money_level("millions USD") and not units.is_money_level("percent")


def test_a_rebased_series_names_the_period_it_was_rebased_to():
    assert charting.index_base_label(date(2020, 2, 15), "quarterly") == "index 2020Q1=100"
    assert charting.index_base_label(date(2020, 2, 15), "monthly") == "index Feb-2020=100"
    assert charting.index_base_label(date(2020, 2, 15), "annual") == "index 2020=100"
    assert charting.index_base_label(date(2020, 2, 15), "daily") == "index 2020-02-15=100"
