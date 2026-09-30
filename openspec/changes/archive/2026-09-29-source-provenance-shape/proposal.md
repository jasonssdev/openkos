# Proposal: source-provenance-shape — stop writing `provenance` on a Source concept

## Intent

Refs #1076.

`ingest` writes `provenance: [raw/<file>]` on every generated Source concept
(`okf.build_source_concept`, called with `provenance=[resource]` from
`application/ingest.py`). The canonical example bundle
(`examples/good-life-demo/bundle/sources/*.md`) — the concrete target output
of `ingest` and the fixture the conformance tests run against — carries no
`provenance` key on either of its two Sources: they always did, since before
this repository split `raw/` out of the bundle. The two disagree about what
`ingest` should actually produce, and the disagreement is papered over rather
than resolved: `tests/unit/test_canonical_example.py`'s
`test_provenance_names_concept_ids_not_raw_paths` forbids a `provenance`
entry that names a `raw/` path on ANY concept, and
`lint.check_dangling_provenance` carries a dedicated exclusion — matching
`doc.resource == provenance entry` — that exists for no other reason than to
keep a Source's own `provenance: [raw/<file>]` from being reported as
dangling on every bundle, every run. `bundle.provenance.find_unresolvable_provenance`
carries the same exclusion for the same reason (see
`resolve_backfill_raises`'s design-D8 note and `sensitivity-backfill/spec.md`'s
Non-Goals section, "every Source cites its raw `resource`").

## Decision

A Source's `resource:` field already names its one raw original. Recording
that same path again under `provenance:` adds a second name for the same
fact, contributes nothing to any consumer, and is exactly the raw-path
provenance shape every other conformance rule forbids. `ingest` stops
writing it: `okf.build_source_concept`'s `provenance` parameter becomes
optional (`None` by default) and the frontmatter key is emitted only when a
caller passes a non-empty list; the two `application/ingest.py` call sites
drop `provenance=[resource]`. No other builder or call site changes — a
derived object's own `provenance` (`build_concept`, always required,
non-empty) is untouched, since it correctly names a Concept ID, never a raw
path, and every consumer of a *derived* concept's `provenance` keeps working
exactly as before.

### Evidence a Source's own `provenance` has no real consumer

Read every module that reads a Source's `provenance` field (CodeGraph +
`grep -rn "\.provenance\b" src/openkos`), not just the two named in the
issue:

- `model/okf.py::project_sources` — never projects a `raw/`-prefixed entry
  by design (its own docstring: "a workspace path under `raw/` (a Source's
  own provenance) is never projected"); `build_source_concept` never even
  calls it. A Source's `provenance` therefore never produces a `sources` key
  — the code already treats it as inert.
- `bundle/provenance.py::find_provenance_descendants` /
  `provenance_closure` (forget/purge) and `resolve_source_raises` /
  `resolve_backfill_raises` (sensitivity propagation) — walk *derived*
  concepts' `provenance` (`sources/<slug>` entries) to find what cites a
  Source; a Source's own `raw/<file>` entry never equals any concept id, so
  it never joins or seeds a closure. `resolve_backfill_raises` explicitly
  avoids `find_unresolvable_provenance` (design D8) specifically because
  every Source's raw-path entry would otherwise warn on every run.
- `bundle/provenance.py::find_unresolvable_provenance` — would flag a
  Source's own `raw/<file>` entry as unresolvable (no concept file matches
  it); callers work around this by scoping roots (`set-sensitivity`,
  `cli/main.py:7683`) or by not calling it at all (`resolve_backfill_raises`).
- `lint.check_dangling_provenance` — carries the `doc.resource == entry`
  exclusion for exactly this shape (its own docstring names it).
- `lint.check_unbacked_provenance` — reads a doc's `provenance` to check it
  backs that SAME doc's own engine-owned `relations:`; a Source is a root,
  never itself a `derived_from` target, so this never touches a Source's
  `provenance` either way.
- `application/ingest.py::family_owns_source` — reads a *derived* concept's
  `provenance` for a `sources/<slug>` membership key (collision-family
  ownership); unrelated to a Source's own field.
- `event_dates.py::resolve_event_date` — reads a *derived* concept's
  `provenance` for `sources/`-prefixed entries to find which Source(s) to
  resolve a date from, then reads that Source's own `event_date` field
  directly, never its `provenance`.
- `graph/sqlite_graph.py` — builds a `derived_from` edge only when a body
  link's target id is in the citing doc's `provenance`; a Source's body
  never links to `raw/<file>` (it isn't a bundle id), so this is unaffected
  either way.
- `application/list_service.py`, `mcp/gate.py`, `application/query.py` —
  read a *derived* concept's/citation's `provenance` for display; unrelated.

No consumer depends on a Source listing its own raw file under
`provenance`. The one place that reads it (`lint`'s dangling-provenance
exclusion, and `find_unresolvable_provenance`'s same trap) reads it only to
special-case it away.

### Why fix `ingest`, not the example

The example already matches the target shape (no `provenance` key on either
Source) and needs no change. Changing the example to match `ingest` instead
would mean intentionally shipping the reference bundle with a frontmatter
field that has no reader and that the engine's own conformance test forbids
on every other concept — the wrong side of the disagreement to keep.

### Backward compatibility

A workspace already holding `provenance: [raw/<file>]` on a Source (written
by an older `openkos`) keeps that entry untouched — no migration runs.
`lint.check_dangling_provenance`'s `doc.resource == entry` exclusion, and
`find_unresolvable_provenance`'s equivalent handling in
`resolve_backfill_raises`/`set-sensitivity`, are kept exactly as-is (now
serving only pre-existing bundles); their docstrings/comments are updated to
say so.

## Scope

### In scope

- `model/okf.py::build_source_concept`: `provenance` becomes
  `list[str] | None = None`; the frontmatter key is emitted only when
  truthy.
- `application/ingest.py`: both `build_source_concept(...)` call sites drop
  `provenance=[resource]`.
- `lint.py::check_dangling_provenance` docstring/comment: note the
  exclusion is now legacy-only (kept for bundles ingested before this
  change).
- Delta specs: `ingestion` domain ("OKF-Native Provenance" and "Ingest Raw
  Copy and Source Concept Generation").

### Out of scope

- No change to `examples/good-life-demo` (already matches the target
  shape).
- No migration/`repair` step for existing `provenance: [raw/<file>]`
  entries.
- No change to a derived concept's own `provenance` handling.
- No change to `find_unresolvable_provenance`/`resolve_backfill_raises`'s
  existing raw-resource workaround — still needed for pre-existing
  bundles.

## Rollback

Revert the `model/okf.py`/`application/ingest.py` diff; `ingest` resumes
writing `provenance: [resource]` on every Source. No data migration to
reverse, since none was performed.
