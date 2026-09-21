"""Bounded-context application services (ADR-0018).

Granularity is one module per bounded context, not per verb --
`application/query.py` is the first member. `application/` may import
`model`, `bundle`, `state`, `retrieval`, `graph`, `resolution`, `llm`,
`config`, `fsio`, and `read_outcome`; nothing in those packages may import
`openkos.application`, and `openkos.application` must never import
`openkos.cli`. `read_outcome` is a LEAF, not part of `application/` itself
(ADR-0022, design.md Decision 1) -- it imports nothing from `openkos`, so
naming it here adds no canonical-to-derived edge; it exists because
`openkos/lint.py` needs the same "not-run" vocabulary `application/doctor.py`
and `application/lint.py` use, and a leaf is the only shape that keeps every
edge downward. This module exports nothing beyond this docstring, matching
`retrieval/__init__.py` -- callers import the context module directly
(e.g. `from openkos.application import query`).
"""
