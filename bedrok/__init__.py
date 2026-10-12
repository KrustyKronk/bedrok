"""Bedrok — a local store of public macroeconomic and market data, for analysis
from inside an AI assistant.

The package is layered, and the direction of dependency is one way:

    surfaces -> ops -> engines -> ingest -> core

  core/      configuration, the store, and shared vocabulary
  ingest/    source adapters and the sync drivers
  engines/   the analysis maths — percentiles, rates
  ops/       the audit reports and the connectivity check
  surfaces/  things that render the store for a human or an assistant

`config` and `store` are re-exported here because they are the documented
embedding entry point — a sandbox session does `from bedrok import config;
config.set_profile(...)`, and that recipe is written down in
docs/engineering/notes.md. Those two names do not move. Everything else should
be imported from its real home so the layer it belongs to stays visible.
"""
from bedrok.core import config, store  # noqa: F401

__all__ = ["config", "store"]
