# Proposal: Surface embed-failed in the reindex stdout summary

## Intent

`openkos reindex` prints an at-a-glance stdout tally of what happened:
`embedded / cache-hit / pruned / skipped` (`cli/main.py:2590-2594`). It omits
`embed_failed`, even though the report already carries it
(`state/reindex.py:84`). A run of `embedded=9, embed_failed=1` prints
`9 embedded, 0 cache-hits, 0 pruned, 0 skipped.` — the failed count is absent
from the primary summary. Today the operator learns of failures only via the
stderr re-run notice (a call-to-action) or logs, forcing a cross-reference to
answer a factual question the summary should already answer. This closes that
gap so the tally is complete.

## Scope

### In Scope
- Extend the stdout summary line to append `{report.embed_failed} embed-failed`
  after `skipped`, always shown (matching the always-shown counter convention).
- Widen the governing spec requirement + scenario
  (`openspec/specs/reindex-command/spec.md:60-75`) to include `embed_failed`.
- Update stdout counter assertions in `tests/unit/cli/test_reindex_cmd.py`
  (both summary-print sites: ~65-82 and ~807-836; extend the embed-failed notice
  test ~227-247 to assert the new count on stdout).

### Out of Scope
- Changing counter semantics, `embed_failed` computation, or the atomic commit.
- The stderr actionable re-run notice (`main.py:2642-2649`) and the success gate
  (`main.py:2619`) — both keep their current behavior; they are distinct signals.
- Pluralization cleanup of existing counters (embedded/pruned/skipped stay
  unpluralized; this is not the change to "fix" that inconsistency).

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `reindex-command`: the successful-run summary requirement/scenario widens to
  state the summary reports `embed_failed` alongside embedded/cache-hit/pruned/
  skipped.

## Approach

Append `{report.embed_failed} embed-failed` after `skipped` in the single
`typer.echo` at `main.py:2590-2594`, hyphenated like `cache-hit`, unpluralized,
terminal period kept. Resulting line:
`... {pruned} pruned, {skipped} skipped, {embed_failed} embed-failed.`

**Always-show vs only-when-nonzero**: always show. Every existing counter prints
regardless of value (`0 skipped` is test-locked); a complete factual tally is
consistent, deterministically assertable, and matches the established
convention. A conditional suffix would break that convention for marginal
noise reduction on healthy runs. Display-only change — the CLI verb stays thin
wiring; behavior remains in the state layer (AGENTS.md non-negotiable respected).

**ADR**: Not required. The ADR gate needs a hard-to-reverse decision; this is
display-formatting text, trivially reversible — it fails the hard-to-reverse
limb explicitly.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/cli/main.py` (:2590-2594) | Modified | Append `embed-failed` to the stdout summary echo |
| `openspec/specs/reindex-command/spec.md` (:60-75) | Modified | Widen requirement + scenario to include embed_failed |
| `tests/unit/cli/test_reindex_cmd.py` (~65-82, ~227-247, ~807-836) | Modified | Assert the new stdout count |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Two test sites share the one echo; a stray edit breaks both | Low | Single production edit feeds both; update both assertions in lockstep |
| Spec-vs-code drift if spec text not widened with the edit | Med | Widen `spec.md:60-75` in the same change; verify convention |

## Rollback Plan

Trivial: revert the one `typer.echo` line and the spec requirement/scenario
text. No data, schema, or migration impact — display-only.

## Dependencies

- Follows up on `reindex-embedding-resilience` (deferred item #3), which
  introduced and populates `ReindexReport.embed_failed`. No new external deps.

## Success Criteria

- [ ] `reindex` stdout summary always includes `{n} embed-failed` after skipped.
- [ ] Governing spec requirement/scenario names embed_failed; no spec-vs-code drift.
- [ ] `uv run pytest` and `uv run mypy .` pass; well under the 400-line budget.
