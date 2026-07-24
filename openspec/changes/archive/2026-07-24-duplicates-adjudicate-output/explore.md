# Exploration: duplicates-adjudicate-output (#139)

Slice 1 of the #139→#137 curation-output arc. Output ergonomics for the
read-only advisory commands `duplicates` and `adjudicate`. Additive display
only — no behavior/state/verdict-logic changes.

## Current State (src/openkos/cli/main.py)
- `duplicates`: `main.py:3532-3591`. Loops `CandidateGroup` list; no leading
  summary, no trailing `Next:` line.
- `adjudicate`: `main.py:3595-3733`. Loops `AdjudicatedCandidate` list; docstring
  states it never merges/writes/decides and has no `--json`; `--same-only` is
  display-only. No leading tally, no `Next:` line.
- Label root cause: `group.trigger` (`resolution/candidates.py:69-71`) is the
  normalized key string for HIGH tier, but a numeric per-token weakest-match ratio
  for LOW (`resolution/similarity.py:35-79`) — two semantically different things in
  one slot, which is why `[LOW] Concept -- 1.000` reads as contradictory.
- `Next:` convention: inline `typer.echo("Next: ...")` as the last statement
  (`main.py:3898`, `main.py:4013`) in `suggest-relations` / `suggest-volatility`.
  No shared helper. `merge` is a real command (`main.py:2259`).
- `_format_type_tally` (`main.py:356-373`, from #136) is purpose-built for
  extraction wording/ordering — NOT a direct fit; add sibling helper(s) of the
  same shape near it.
- Tests: `tests/unit/cli/test_duplicates.py`, `test_adjudicate.py` use CliRunner +
  substring assertions. Additive lines should be safe, but full files must be
  re-read before writing new tests to rule out hidden exact-stdout assertions.

## Scope decisions from exploration
1. **Pager (issue item 4): NO CODE CHANGE.** Grep of all `src/` for
   `pager`/`PAGER`/`echo_via_pager` = zero matches. Typer/Click `echo` never pages
   implicitly. The observed `ESC:...skipping...` collision was the user's own shell
   pager on long stdout. Item 4 is a no-op for openkos code — dropped (the leading
   summary + `Next:` hint already shorten the actionable surface).
2. **Label clarification**: prefer a one-time legend/header line explaining
   `[tier] type -- score` (lowest risk) over reordering verdicts.

## Open design decisions (for proposal to resolve)
1. `adjudicate` tally counts the FULL `results`, not the `--same-only`-filtered
   display subset. Recommended.
2. "candidate pairs" wording is imprecise — a HIGH `CandidateGroup` can have >2
   members. Use honest wording ("groups" / accurate member accounting) based on the
   real `CandidateGroup` structure, not "pairs".

## Summary line targets
- `duplicates`: leading `N candidate group(s) (X exact, Y near)` — exact/near split
  by tier (HIGH exact vs LOW near), wording finalized in design against the data.
- `adjudicate`: leading `adjudicated N: x SAME, y DIFFERENT`.

## Risks / edge cases
Zero pairs (empty-state wording), all-SAME / all-DIFFERENT, exact-vs-near split when
scores are all 1.000, non-TTY output, and strictly additive (no existing asserted
line altered).
