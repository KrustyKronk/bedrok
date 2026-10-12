# Contributing to Bedrok

Bedrok fetches public macroeconomic data from the publishers that produce it
and gives it to an AI assistant with its licence, its attribution and its
caveats. It measures and contextualises. It never gives financial advice and
never produces buy/sell timing signals; a contribution that does will not be
merged ([DISCLAIMER](docs/DISCLAIMER.md)).

## Asking for a publisher

Open a [source request](https://github.com/KrustyKronk/bedrok/issues/new?template=source-request.yml):
the publisher, where its data or API lives, its terms page, and the three series
you want first.

## Adding a publisher

A publisher is one row of `bedrok/seeds/sources.csv` plus its series in
`bedrok/seeds/macro_catalog.csv`. If it speaks SDMX or PxWeb, that is all: the
shared clients read the row. If it has its own format, it also needs a small
adapter in `bedrok/ingest/adapters/`. The row contract, the `source_code`
format for each protocol and the adapter signature are in
[SETUP → Adding a publisher](docs/SETUP.md#adding-a-publisher).

Before choosing the row's `license` and `cache`, read the publisher's terms
page. `cache = session` means the data is fetched live for the conversation and
never written to disk; use it whenever the terms forbid storing the data.
[LICENSING](docs/LICENSING.md) explains the five licence classes.

### Tests

There are no recorded cassettes and no network in the test suite. An adapter is
tested on an **inline payload**: copy one real response, cut it short, paste it
into the test as a literal, and either feed it to the adapter's parser or serve
it from a fake `requests.get`:

```python
def test_request_shape(monkeypatch):
    seen = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        seen.update(url=url, params=params)
        return _Resp(PAYLOAD)              # status_code, text, raise_for_status

    monkeypatch.setattr(myadapter.requests, "get", fake_get)
    df = myadapter.fetch("CODE", start="2025-01-01")
    assert seen["url"].endswith("/CODE")
    assert df["value"].iloc[-1] == 2.4
```

The canonical example is `tests/test_sdmx_adapter.py::test_request_shape_by_format`.
Say in the payload's comment when it was captured. `tests/test_sources.py`
already checks that every row is complete and names a smoke series of its own.

Then check against the live publisher:

```bash
bedrok smoke            # one live call per publisher; yours included
bedrok refresh          # fills your new series in your local store
```

## The rules the tests enforce

- **Layering.** The package imports one way only:
  `surfaces -> ops -> engines -> ingest -> core`. `tests/test_layering.py`
  fails on an upward import. A new adapter lives in `ingest/`.
- **Units and currency are columns,** never inferred from a level or a country.
  `core/units.py` holds the vocabulary.
- **Publication.** `tests/test_publication.py` fails on home-directory paths,
  literal key values, token shapes and files that belong only in a private
  `profile/` folder. Never commit a key, even an expired one.

## Pull requests

A branch, the tests green (`python -m pytest -q`), and `bedrok docs --check`
clean (it regenerates `docs/SOURCES.md`, `LICENSES.md`, `WARNINGS.md`,
`GAPS.md` and `CHARTING.md` from the seeds). Say in the description what you
checked against the live publisher.
