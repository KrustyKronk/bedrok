# What no open source provides

Some things an economist would reasonably ask for are not published anywhere
under terms that let a personal tool fetch them. Bedrok cannot carry them, and
an assistant using Bedrok should say so rather than approximate them from
something nearby. Each gap was looked for across official, academic and open
data sources and not found; most are sold by data vendors.

**Prices that only vendors assess**

- Purchasing managers' indices (PMIs). The headline business surveys of most
  economies are owned by S&P Global or national associations and are not
  openly licensed. Official business-climate surveys (the European
  Commission's, INSEE's, the regional Fed surveys) are the open alternative.
- Battery-metal and rare-earth prices: lithium, cobalt, rare-earth oxides.
- Prices and differentials for regional crude grades outside the benchmarks
  the World Bank and the EIA publish (for example the Caspian grades).
- Wholesale power prices at the major US hubs, and GPU rental prices.
- Exchange-traded futures prices: ICE Brent, CME WTI and the rest. The
  exchanges license them. Bedrok's Brent (`eia_brent`, `wb_crude_brent`) is
  Dated Brent, the physical cargo price, which usually sits close to the
  front-month future but can diverge by tens of dollars in a squeeze, as in
  September and October 2026.

**Panels that do not exist openly**

- A house-price panel beyond the EU, EFTA and the major economies. Bedrok
  carries Eurostat's house prices for the euro-area countries and the BIS real
  residential property prices for the US, the UK and the euro area.
- A cross-country panel of wealth distribution.
- A term-premium estimate for any government bond market outside the US.
  Bedrok carries the New York Fed's ACM estimate for Treasuries (`us_acm_tp10`).
- Eurobond and local-currency sovereign yields for Central Asia and
  Sub-Saharan Africa, which index providers and exchanges license.
- Parallel (market) exchange rates where they differ from the official rate.

**Records that are not kept openly**

- Release-by-release revision histories (what a number was when first
  published) for emerging markets. For the US and some advanced economies the
  agencies keep vintages, and a later Bedrok version plans to use them.
- Open card-spending data, and a weekly GDP proxy that is licensed for reuse
  across countries.
- Deal-level data-centre financing and the data-centre construction pipeline.
- Smaller national gaps: a US layoff-notice database, an open global
  minimum-wage database, strike data outside the US and Europe, and a free
  commercial real-estate price index.

When a question needs one of these, the honest answer is that Bedrok does not
have it and that no open source does, followed by the nearest open measure if
there is one, clearly labelled as a different thing.
