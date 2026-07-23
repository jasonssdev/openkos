# Proposal: Directory-Walk Observability Hardening (S3 follow-up)

## Intent

The S3 fail-closed filter resolves confidential docs via
`sensitivity.sensitive_concept_ids`, which walks the bundle through
`okf._iter_docs` (rglob). That walk **silently drops** any subdirectory it can
traverse (x-bit) but not list (r-bit): the subtree vanishes with no signal
(`okf._walk_errors` docstring, okf.py:882-896). A `confidential` doc under such
a subdir is never added to the blocked set — so the fail-closed filter silently
fails **open**. This is the S3 4R resilience review's CRITICAL finding,
explicitly deferred as "MUST harden before any cloud-backend slice." Query
already closed the leak at send time (S3 FIX-2, answer.py:211-214); the other 4
verbs still leak in a narrow window (doc indexed in graph.db by a prior reindex,
but hidden from the live walk after a subtree lost its r-bit post-indexing).

## Scope

### In Scope
- **Observability signal** in all 5 sensitivity-filter verbs (query,
  contradictions, adjudicate, suggest-relations, suggest-volatility): a shared
  CLI-layer helper calls `okf._walk_errors(bundle_dir)`, WARNs to STDERR on
  incompleteness, exit 0. Skipped when `--include-confidential` is active.
- **Leak closure**: port query's send-time `sensitivity.blocks_llm_send`
  per-doc re-read into the 4 still-leaking load paths
  (`contradiction._load_doc`, `adjudication._load_members`,
  `edge_typing._load_doc`, volatility_typing's load), so a doc loaded by direct
  path is independently fail-closed re-checked before entering the prompt.
- Helper carries a `mode` param (`warn`|`refuse`); this slice ships `warn` only.

### Out of Scope / Non-Goals
- **Cloud-egress refuse mode** (future slice): flips the helper's `mode` to
  `refuse` at the cloud send seam. Built for, not delivered here.
- **extract**: its gate reads the workspace `default_sensitivity` floor value —
  no bundle walk, nothing to be incomplete.
- Any I/O in `sensitivity.py` — it stays a pure, canonical no-I/O leaf.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `sensitivity-aware-llm`: adds two requirements to the existing fail-closed
  invariant — (1) **walk-incompleteness observability** (the filter MUST WARN
  when its directory walk is incomplete), and (2) **defense-in-depth at load**
  (each of the 5 verbs re-checks a directly-loaded doc's sensitivity before the
  `llm.chat` send, not only during the walk).

**New-vs-modified justification**: this strengthens the SAME fail-closed
invariant S3 established (which concepts may reach `llm.chat`), so it belongs as
a delta on `sensitivity-aware-llm`, not a new capability. It hardens an existing
guarantee rather than introducing an orthogonal one; a separate spec would
fragment one invariant.

## Approach

Follow the established precedent: `state/reindex.py:285`
(`prune_skipped = bool(okf._walk_errors(bundle_dir))` → self-explaining warning,
cli/main.py:3723-3734) and `survey_bundle` → status/doctor. Build one shared
CLI-layer helper that runs `_walk_errors` (metadata-only re-walk), emits a
self-explaining STDERR warning keyed on the boolean, and returns exit 0. Wire it
into the 5 verbs. Separately, mirror query's send-time `blocks_llm_send` re-read
(answer.py:211) into the 4 verbs' direct-path load functions — a redundant
per-doc gate that converts them to leak-closed like query.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| new CLI-layer helper (`cli/` or shared) | New | `_walk_errors`-based warn/refuse observability helper (`mode` param) |
| `cli/main.py` (5 verb commands) | Modified | Wire helper into query, contradictions, adjudicate, suggest-relations, suggest-volatility |
| `resolution/contradiction.py` (`_load_doc`) | Modified | Per-doc `blocks_llm_send` re-check at load |
| `resolution/adjudication.py` (`_load_members`) | Modified | Per-doc re-check at load |
| `resolution/edge_typing.py` (`_load_doc`) | Modified | Per-doc re-check at load |
| `resolution/volatility_typing.py` (load path) | Modified | Per-doc re-check at load |
| `sensitivity.py` | Unchanged | Stays pure — no I/O, no printing |

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Warning noise on healthy bundles | Low | Keyed on `bool(_walk_errors)`; silent when walk is complete |
| Double `_walk_errors` cost per verb | Low | Metadata-only re-walk; matches reindex/status precedent |
| Leak-closure re-read drifts from query's semantics | Med | Reuse the same `sensitivity.blocks_llm_send`; mirror test_answer.py:2161 |
| Refuse-mode dead code until cloud slice | Low | `mode` param defaults to `warn`; explicit Non-Goal |
| Warning suppressed with `--include-confidential` hides real breakage | Low | Filter deliberately off; status/doctor still report bundle health |

## Rollback Plan

Additive. Reverting removes the helper wiring and the 4 load-path re-checks;
`sensitivity.py`, the walk, and query's existing FIX-2 re-check are untouched. No
data or schema migration.

## Dependencies

- `okf._walk_errors` (shipped) — consumed.
- `sensitivity.blocks_llm_send` (shipped, S3 FIX-2) — reused in the 4 load paths.
- S3 `sensitivity-aware-llm` capability (shipped) — extended.

## Success Criteria

- [ ] Each of the 5 verbs WARNs to STDERR (exit 0) when `okf._walk_errors` is non-empty; silent when empty.
- [ ] Warning is suppressed under `--include-confidential`.
- [ ] A confidential doc loaded by direct path in contradictions/adjudicate/suggest-relations/suggest-volatility is re-checked and excluded before `llm.chat`, even if the live walk missed its subtree.
- [ ] `sensitivity.py` gains no I/O.
- [ ] Helper exposes a `mode` (warn|refuse) param, shipped as `warn`.

## Arc Note

S3 follow-up ("harden before cloud"). Closes the S3 4R resilience CRITICAL. The
future cloud-egress slice flips the helper to `refuse` mode at the cloud send
seam — an explicit Non-Goal here.
