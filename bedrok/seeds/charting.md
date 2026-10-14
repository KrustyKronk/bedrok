# How Bedrok charts look

Bedrok does not draw charts. The assistant does, with whatever chart tool its
host offers, so the same question can be drawn very differently in two places.
What Bedrok fixes is the content. Ask `chart_series` and it returns the data on
one calendar and a recipe; draw the recipe as written and every Bedrok chart
looks the same, wherever it is drawn.

## The rules

**Every chart sits in a card**, with a border, a title and a subtitle at the
top, the chart, the legend below it, the source line under the legend, and any
footnotes under that. A switch on the card shows the same numbers as a table.

**The title says what is shown, never the units.** "US high-yield spread and
the 10-year Treasury yield", not "US HY OAS (%)". Bedrok suggests one; the
assistant may write a better one.

**The subtitle carries the units and how the data is measured**, then the
span: "Percent; weekly closes; Oct 2023 to Oct 2026". The vertical axis is
labelled with the units too.

**One scale when the units agree, two when they differ.** Units agree when
they are the same kind of thing: a percent, a percentage point and a percent
change share a scale; two indices on different bases, or a price and a rate,
do not. The first series asked for sets the left scale, drawn with solid
lines. Series in other units go on the right scale, drawn dashed, so it is
always clear which line reads against which axis. Where two lines cross on a two-scale chart means nothing;
the scales set it. A third set of units is never given a third scale: draw two
charts, or ask for every series as an index (`transform="index_to"`). The
recipe marks a series it could not place `drawn: false`.

**Colours follow the order the series were asked for**, from a fixed set of
seven hues chosen to stay distinct for colour-blind readers (the Okabe–Ito
set without its yellow): `#0072B2`, `#E69F00`, `#009E73`, `#D55E00`,
`#56B4E9`, `#CC79A7`, `#7F7F7F`, in that order, repeating after the
seventh. A series keeps its colour across the charts of one conversation. Order sets the scales too:
to add a series to a chart already drawn, list it after the others, and
nothing already drawn moves.

**The legend goes below the chart**, and clicking a series hides or shows it.

**Dates along the bottom** follow the chart's calendar: "8 Oct" for days,
"2 Oct 2026" for weeks, "Oct 26" for months, "Q4 26" for quarters, "2026" for
years, with about ten labels shown however long the span. Daily data over up
to three years is drawn daily, one slot per trading day; over longer spans
weekly, so the line is not noise.

**Gaps stay gaps.** A month that was never published (US CPI in October 2025)
is an empty slot in the data, and the line breaks there. Never join across it.

**A series published less often than the chart's calendar is drawn as a
step**: the monthly Fed rate beside weekly spreads holds each month's value
until the next, and a footnote says so. **A rate dated at its decisions**
(the ECB's) is in force until the next decision, so it runs to the end of
the chart, and a footnote names its last change.

**Data the assistant fetched elsewhere may be drawn beside Bedrok's**, passed
as `extra` with a label, its units and its publisher; the source line names
that publisher as "fetched by Claude, not via Bedrok" and a footnote repeats
it, so the reader always knows which part did not come through Bedrok.

**The source line names every publisher once**, with its condition when it
has one ("personal use only", "fetched live, not stored").

**Footnotes say what the chart cannot**: a warning about the official numbers
for the period shown, a period not over yet, a quarter missing a month, rows
cut from a long span, a series drawn as a step.

**Lines by default.** Bars when every series has a single period (ask for
`at` a date: one bar per series, many places compared at once), and stacked
bars for contributions to a total (GDP growth by expenditure): the parts
stacked from zero, positive up and negative down, with the total's growth
rate as a dot or a line on the same scale, and a footnote saying whether the
parts drawn add up to it.
A chart whenever there is more than one period; a table only when the reader
asks for one, or to compare many places at a single date.

## The answer around the chart

A request to show, plot or chart something gets the chart and at most three
short lines: how recent the data is, the footnotes that matter, and anything
the chart cannot show. No analysis unless the reader asks for it; when they
do, give it fully.

## Why these rules

They were settled on 2026-10-11 after a working session in which eight charts
came out three different ways: two scales whenever units differ; units in the
subtitle and on the axis, never in the title; short answers by default; a
chart by default.
