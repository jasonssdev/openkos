"""Bounded-context application services (ADR-0018).

Granularity is one module per bounded context, not per verb --
`application/query.py` is the first member. `application/` may import
`model`, `bundle`, `state`, `retrieval`, `graph`, `resolution`, `llm`,
`config` and `fsio`; nothing in those packages may import
`openkos.application`, and `openkos.application` must never import
`openkos.cli`. This module exports nothing beyond this docstring, matching
`retrieval/__init__.py` -- callers import the context module directly
(e.g. `from openkos.application import query`).
"""
