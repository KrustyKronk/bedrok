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

**One scale when the units agree, two when they differ.** The first series
asked for sets the left scale, drawn with solid lines. Series in other units go
on the right scale, drawn dashed, so it is always clear which line reads
against which axis. Where two lines cross on a two-scale chart means nothing;
the scales set it. A third set of units is never given a third scale: draw two
charts, or ask for every series as an index (`transform="index_to"`).

**Colours follow the order the series were asked for**, from a fixed set of
seven hues chosen to stay distinct for colour-blind readers, so a series keeps
its colour across the charts of one conversation.

**The legend goes below the chart**, and clicking a series hides or shows it.

**Dates along the bottom** follow the chart's calendar: "2 Oct 2026" for weeks,
"Oct 26" for months, "Q4 26" for quarters, "2026" for years, with about ten
labels shown however long the span.

**Gaps stay gaps.** A month that was never published (US CPI in October 2025)
is an empty slot in the data, and the line breaks there. Never join across it.

**A series published less often than the chart's calendar is drawn as a
step**: the monthly Fed rate beside weekly spreads holds each month's value
until the next, and a footnote says so.

**The source line names every publisher once**, with its condition when it
has one ("personal use only", "fetched live, not stored").

**Footnotes say what the chart cannot**: a warning about the official numbers
for the period shown, a period not over yet, a quarter missing a month, rows
cut from a long span, a series drawn as a step.

**Lines by default.** Bars only when every series has a single period.
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
