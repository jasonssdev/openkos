# Proposal: duplicates / adjudicate output ergonomics (#139, Slice 1)

## Intent

The read-only advisory commands `duplicates` and `adjudicate` dump a long, flat
list with no orientation: no count, no "what do I do next", and a `[tier] type --
trigger` column that reads as self-contradictory (`[LOW] Concept -- 1.000`)
because `group.trigger` is a normalized key for HIGH tier but a numeric near-match
ratio for LOW. Users cannot judge scale at a glance or find the next action.
This slice is **additive display only** — no behavior, state, verdict logic,
scoring, tier bucketing, or `merge` changes.

## Scope

### In Scope
1. **Leading summary tally** (additive first line):
   - `duplicates` (`main.py:3532-3591`): `N candidate group(s) (X exact, Y near)`
     — X = HIGH count, Y = LOW count over the full `groups` list.
   - `adjudicate` (`main.py:3595-3733`): `adjudicated N: x SAME, y DIFFERENT`
     (+ `, z UNCERTAIN` when z>0), counted over the FULL `results` set, not the
     `--same-only` display subset.
2. **One-time legend line** before the group loop explaining the bracket columns:
   trigger = exact normalized key for HIGH, near-match score for LOW. Printed once,
   never per group. Verdict/group order unchanged (lowest-risk branch of the issue's
   either/or).
3. **Trailing `Next: openkos merge <survivor> <absorbed>` hint** on both commands,
   inline `typer.echo(...)` as the last statement, matching `main.py:3898`/`:4013`.
   No shared helper.
4. **Sibling tally helper(s)** near `_format_type_tally` (`main.py:356-373`) —
   same pure `dict[str,int] -> str`, `""`-on-empty shape, reusing `_plural`.
   `_format_type_tally` itself is extraction-specific and NOT reused.

### Out of Scope
- **Pager handling (issue item 4): RESOLVED / DROPPED, not deferred.** Grep of all
  `src/` for `pager`/`PAGER`/`echo_via_pager` = zero matches; openkos invokes no
  pager. The collision was the user's own shell pager. No code exists to change.
- **#137** (adjudicate → merge: `--json`, interactive apply, guarded batch) — Slice 2.
- Any change to verdict logic, similarity scoring, tier bucketing, or `merge`.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `entity-resolution`: `duplicates` output gains a leading tally, a one-time
  legend line, and a trailing `Next:` hint (display-only requirement).
- `entity-resolution-adjudication`: `adjudicate` output gains a verdict tally
  (full `results`), a legend line, and a trailing `Next:` hint.

## Approach

Both commands get three additive `typer.echo` insertions (tally before render,
legend before the group loop, `Next:` after) plus 1-2 pure formatting helpers.
Existing per-item detail lines stay byte-identical so current CliRunner substring
tests keep passing. Honest wording: "groups"/"members", never "pairs" — a HIGH
`CandidateGroup` can have >2 members (`candidates.py:65-66`), so the issue's
"19 candidate pairs" example is corrected to group/member accounting.

## Locked product decisions
- **Empty state**: keep existing sole lines — `"No candidates found."` (duplicates)
  and `"No SAME-verdict candidates to display (--same-only)."` (adjudicate). No
  tally, no legend, no `Next:` on empty / no-candidate / same-only-empty paths
  (nothing to merge yet; mirrors `suggest-relations` `total==0`).
- **Tally source**: `adjudicate` counts full `results` (`--same-only` is display-only).
- **UNCERTAIN**: surfaced in the tally only when nonzero, so a degraded all-UNCERTAIN
  run is not silently reported as `0 SAME, 0 DIFFERENT`.
- **Additive-only**: no existing asserted output substring is altered.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/cli/main.py:3532-3591` | Modified | `duplicates`: tally + legend + `Next:` |
| `src/openkos/cli/main.py:3595-3733` | Modified | `adjudicate`: verdict tally + legend + `Next:` |
| `src/openkos/cli/main.py:350-373` | New | sibling tally helper(s) |
| `tests/unit/cli/test_duplicates.py`, `test_adjudicate.py` | Modified | new TDD assertions |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Hidden exact-stdout assertion breaks on additive lines | Med | Re-read both test files fully before implementation |
| "pairs" wording misleads on >2-member HIGH groups | Med | Use "groups"/"members"; correct issue's example |
| all-1.000 near scores look wrong vs exact split | Low | Legend clarifies the two trigger axes |
| Legend adds a line vs pager-shortening intent | Low | Tally net-shortens; legend replaces confusion |
| Non-TTY output | Low | Plain `typer.echo` to stdout, no TTY-conditional formatting |

## Rollback Plan

Single-PR, display-only. Revert the PR commit — no schema, state, or data
migration involved.

## Dependencies

- `merge <survivor> <absorbed>` is already implemented (`main.py:2259`); `Next:`
  references live functionality.

## Success Criteria

- [ ] `duplicates` prints a leading group tally, one legend line, and a `Next:` hint.
- [ ] `adjudicate` prints a verdict tally over full `results`, legend, and `Next:` hint.
- [ ] Empty / same-only-empty paths print only the existing message (no new lines).
- [ ] All pre-existing CliRunner substring tests pass unchanged.
- [ ] Delivery: small-to-medium single PR (well under the 800-line budget).
