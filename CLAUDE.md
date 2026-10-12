# Working on Bedrok

Read `STATE.md` first. It is where the project stands and what is open. Then
`docs/rebuild-plan.md` for where it is going, `README.md` if you need to run
something, and `docs/engineering/notes.md` before you debug anything in the
sandbox, the ingest path, or a vendor API — it is the list of things that
already cost somebody hours. Do not read `docs/archive/` unless a question sends
you there; it is history and it contradicts the present on purpose.

Bedrok measures, contextualises and premise-checks. The reader decides.

Portfolio tooling left this repo on 2026-10-08 and lives elsewhere. Nothing
here answers a portfolio question.

## The rules

**1. The folder and GitHub are one to one.** A session does not end with
`git status` dirty. Commit the work and the documents it produced, on a branch,
as a PR. A fix once sat uncommitted on `main` for two weeks while a fix plan
written after it was authored recorded the bug as unfixed. Only secrets,
`profile/`, fetched binaries and build output stay local.

**2. Open items live in `STATE.md` and nowhere else.** If another document has a
to-do list, it is stale by construction. Point at `STATE.md` instead of keeping a
second copy. This rule exists because for three months the real backlog lived in a
folder outside the repo, and it could not see the code: the 2026-08-07 plan opens by declaring the standing queue
unimplemented, two days after commit `10dcfdd` implemented a large part of it.

**3. Rewrite `STATE.md` in place at the end of every session.** Never date-copy
it and never start a second one. Git history is the record of what it used to
say.

**4. The repository is public.** Nothing tracked names a position size, a NAV, a euro amount, a
transaction or an API key. Instance-specific material lives in `profile/`,
which is gitignored and has never been committed. When a lesson is worth keeping
but its example is personal, write the general form here and leave the example
in `profile/`. `tests/test_publication.py` enforces the general part (home
paths, key values, lock files, anything under `profile/`), and also checks
every tracked file and the built wheel against the owner's own list of
personal terms in `profile/publication-terms.txt`, when that file exists.

Not even as a test fixture: that is exactly how a real NAV ended up inside the
test for a privacy gate, and survived being written and reviewed because "it is
only a fixture".

**5. Currency is a column, never an inference.** For a macro series the
authority is its catalog `units`; never infer a currency from a level or a
country. Convert via `fx_rates`, where an `EUR<CCY>` rate is CCY per 1 EUR, so
EUR value = amount / rate.

**6. Splice by returns, never by levels.** When one series has to stand in for
another — a short history extended by a proxy, an index rebased mid-series, a
vendor series that drifts — carry the *returns* of the stand-in onto the real
level, never the stand-in's level itself. A level splice once fabricated a 3x
phantom value.

**7. Premise-check the question before answering it.** "Why is inflation
re-accelerating?" deserves a check that it is, on the measure asked about.
Correct politely, then answer. The two times a wrong framing survived to a
deliverable were both times this was skipped.

**8. Never give financial advice and never generate buy/sell timing signals.**
Bedrok is a research and measurement tool. See `docs/DISCLAIMER.md`.

**9. Ad-hoc charts go inline in the conversation.** The scroll-through
experience is a core feature. Nothing in this repo publishes a standing
artifact.

**10. Before you say something works.** `./.venv/bin/python -m pytest -q` on
macOS. If you changed an ingest path, also run the command end to end against
the real store (`bedrok refresh`, Mac-side), because four of the six
recorded failures were commands that returned zero while doing the wrong thing.

**11. Never list the repo or profile roots recursively.** `.venv/` blows up the
listing. Touch `bin/`, `profile/data/` and `profile/seeds/` directly.

**12. The package is layered, and imports only go one way.**

```
surfaces  ->  ops  ->  engines  ->  ingest  ->  core
```

`core/` is configuration, the store and shared vocabulary; `ingest/` is source
adapters and the sync drivers; `engines/` is the analysis maths; `ops/` is the
audit reports and the connectivity check; `surfaces/` renders for a human or an assistant
(the MCP server). A `main()` may dispatch across
layers — that is a command composing, not a library depending — and nothing
else may. `tests/test_layering.py` enforces this, so it is a fact rather than a
habit. If an import has to go upward, the module is almost always filed in the
wrong layer: that is how one ingest module once came to hold both a command and
the helpers the engines needed, with the resulting cycle held apart only by
function-local imports.

**13. Derive paths from the package, never by counting `.parent`.**
`Path(__file__).parents[2]` is the repo root because it is the parent of the
`bedrok` package, and it stays correct when a module moves between layers.
`.parent.parent` silently became `bedrok/` the moment `config.py` moved into
`core/`, and every seed path went missing at once.

## How the owner works

The owner decides the product. Brainstorm until you genuinely agree on the
problem, then plan, then build, and show the plan before the diff — plans get
read, diffs do not. Bring consequences nobody has named yet; they get engaged
with and often change the course. Be honest about what is not working rather than
defending a document.

## Ending a session

Tests green. `STATE.md` rewritten to what is now true, including anything you
learned that is still open. Anything hard-won that a future session would waste
hours rediscovering goes in `docs/engineering/notes.md`. `git status` clean. The
commit message carries the reasoning, not a description of the diff.
