# Delta for Entity Resolution Merge

> **Conditional delta.** Built ONLY if the pre-registered decision rule in
> `auto-merge-safe-class/design.md` passes. On FAIL this file is deleted
> from the change before archive. On PASS, archive also rewords this
> spec's Non-Goals entry "automatic no-confirm merge" to say `merge` itself
> stays confirm-gated and the only automatic path is `curate --auto-merge`
> (curate-command), since Purpose/Non-Goals are not delta-addressable.

## ADDED Requirements

### Requirement: The Automatic-Merge Run Bullet Never Resembles A Merge Bullet

A `curate --auto-merge` run that applied at least one merge MUST append
exactly one `**Auto-merge**` bullet to `log.md`, after every per-merge
`**Merge**` bullet of that run, listing each merge as survivor, absorbed
and confidence, plus the threshold and the model. Each automatic merge
MUST still write its own `**Merge**` bullet exactly as `merge_log_entry`
spells it, because `unmerge`'s surgical reversal locates that bullet.

The `**Auto-merge**` bullet's text MUST NOT contain a substring equal to
any `merge_log_entry(...)` output, so `unmerge` can neither mistake it for
a merge's own bullet nor refuse over a duplicate. `unmerge` MUST leave the
`**Auto-merge**` bullet in place as history.

#### Scenario: Unmerge of an automatic merge keeps the run bullet

- GIVEN a run that automatically merged (S, A) and (T, B)
- WHEN `openkos unmerge S A` runs
- THEN the `**Merge**` bullet for (S, A) is removed, the one for (T, B)
  and the run's `**Auto-merge**` bullet remain, and the unmerge audit line
  is appended

#### Scenario: A run that merged nothing writes no bullet

- GIVEN `curate --auto-merge` with no eligible pair
- WHEN the run completes
- THEN `log.md` gains no `**Auto-merge**` bullet
