# Design: Surface embed-failed in the reindex stdout summary

## Technical Approach

A single-line, display-only edit to the one `typer.echo` stdout tally at
`src/openkos/cli/main.py:2590-2594`. Append `{report.embed_failed} embed-failed`
as a fifth counter after `skipped`, always shown. `ReindexReport.embed_failed`
already exists (`state/reindex.py:84`) and is already read in this same command
by the success gate (`main.py:2619`) and the stderr re-run notice
(`main.py:2642-2649`). No new data flow, no new computation, no new field — the
tally simply stops omitting a value the report already carries.

### Precise target form

```python
typer.echo(
    f"openkos reindex: {report.embedded} embedded, {report.cache_hits} "
    f"cache-hit{_plural(report.cache_hits)}, {report.pruned} pruned, "
    f"{report.skipped} skipped, {report.embed_failed} embed-failed."
)
```

`embed-failed` is hyphenated (matching `cache-hit`) and unpluralized (matching
`pruned`/`skipped`, which never take `_plural`). The terminal period stays on
the final counter. Insertion point is between `skipped` and the period.

## Architecture Decisions

| # | Decision | Choice | Rejected | Rationale |
|---|----------|--------|----------|-----------|
| D1 | Placement | Fifth counter, after `skipped`, before the period | Separate echo line; front position | One tally is one factual line; ordering mirrors report field order (`embedded, cache_hits, pruned, skipped, embed_failed`). |
| D2 | Visibility | Always shown, even when `0` | Conditional (`if embed_failed`) | A complete tally is deterministically assertable and consistent with the four peers, which all print at `0`. `0 skipped` is already test-locked. |
| D3 | Pluralization | Unpluralized `embed-failed` | `_plural(...)` like cache-hit | Matches `pruned`/`skipped` register; proposal-scoped (no pluralization cleanup of existing counters). |
| D4 | Layer | Read-only in the thin CLI verb | Add derivation/logic in CLI | AGENTS.md: canonical behavior lives in `state`; entry layer stays thin wiring (`engine.py` thin; canonical layer never depends on derived). This only formats an existing report field. |

## Thin-engine confirmation

The CLI verb adds no behavior. `state/reindex.py` remains the sole producer of
`embed_failed`; the command already consumes that field for the gate and the
stderr notice. Appending it to stdout is pure presentation, honoring the
AGENTS.md "canonical layer / thin entry" principle — no computation crosses into
the entry layer.

## Observable behavior

| Case | Before | After |
|------|--------|-------|
| `embed_failed == 0` (e.g. embedded=3) | `...0 pruned, 0 skipped.` | `...0 pruned, 0 skipped, 0 embed-failed.` |
| `embed_failed > 0` (e.g. embedded=9, embed_failed=1) | `9 embedded, 0 cache-hits, 0 pruned, 0 skipped.` | `9 embedded, 0 cache-hits, 0 pruned, 0 skipped, 1 embed-failed.` |

## Relation to the stderr re-run notice

Same field (`report.embed_failed`), different stream and purpose — no
duplication:

- **stdout tally** (this change): a factual, always-present count of what
  happened this run, sitting alongside the other four counters.
- **stderr notice** (`main.py:2642-2649`, unchanged): an actionable
  call-to-action that fires only when `embed_failed > 0`, telling the user to
  re-run.

Different streams (stdout vs stderr), different trigger (always vs conditional),
different intent (tally vs remediation). They are complementary signals, not a
duplicated one; the stderr notice stays exactly as-is.

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `src/openkos/cli/main.py` (:2590-2594) | Modify | Append `{report.embed_failed} embed-failed` to the stdout summary f-string. |
| `openspec/specs/reindex-command/spec.md` (:60-75) | Modify | Widen the successful-run summary requirement + scenario to name `embed_failed` alongside the four existing counters. |
| `tests/unit/cli/test_reindex_cmd.py` (~65-82, ~227-247, ~807-836) | Modify | Assert the new stdout `embed-failed` count at both print sites; extend the notice test to also assert the stdout count. |

## Testing Strategy

| Layer | What | Approach |
|-------|------|----------|
| Unit (cli) | stdout summary includes `{n} embed-failed` after skipped; `0 embed-failed` when none; `1 embed-failed` when one doc failed transiently | Existing `typer` runner assertions at both print sites; extend the notice test to assert stdout count too |

No integration/E2E change — display-only, deterministic string.

## Data Flow

No sequence diagram — this is not a flow. One field already on `report` is read
and rendered into an existing echo. `report.embed_failed → f-string → stdout`.

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file
classification, or process-integration boundary. A single formatted stdout line.

## ADR gate

**NO ADR.** The two-limb gate requires both architectural weight and
hard-to-reverse commitment. This is display-formatting text, trivially reverted
by deleting one f-string fragment (rollback = revert the line + spec text). It
fails the hard-to-reverse limb explicitly, so no ADR is warranted.

## Migration / Rollout

No migration required. Display-only; no data/schema/config impact.

## Open Questions

None.
