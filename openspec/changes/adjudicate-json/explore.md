# Exploration: adjudicate-json (#137, Slice 2a)

Non-destructive machine-readable output for `adjudicate`. First slice of the
#137 "path to merge" work. Scope chosen by maintainer: `adjudicate --json`
ONLY (interactive/batch apply deferred to later slices).

Full arc exploration (covering merge-core, apply modes, guardrails) is in Engram
`sdd/adjudicate-merge-path/explore`. This file captures the 2a-relevant subset.

## Current State (src/openkos/cli/main.py)
- `adjudicate` (`main.py:3632-3780`) is fully read-only post-#139: workspace gate
  → `find_candidates` → `adjudicate_candidates` → verdict tally, legend,
  per-group verdict+rationale, static `Next:` hint (line 3780). No `--json`.
- `AdjudicatedCandidate` (`resolution/adjudication.py:82-95`): `candidate`
  (a `CandidateGroup`), `verdict` (SAME/DIFFERENT/UNCERTAIN),
  `confidence` (parsed, clamped, NEVER rendered — issue #138), `rationale`.
- `CandidateGroup` (`resolution/candidates.py:55-71`): `member_ids` is an
  alphabetically-sorted tuple; `okf_type`, `tier` (HIGH/LOW), `trigger`.
  HIGH groups can have >2 members. No survivor/absorbed heuristic exists.
- `--same-only` is a display-only filter; `results` holds the full set.

## Key facts for 2a
- **`--json` has ZERO precedent** in the codebase; `json` is not imported in
  `cli/main.py`. Every structured-capable read command's docstring explicitly
  says it has no `--json`. This slice DEFINES the convention.
- Confidence is stored but NEVER thresholded anywhere; the only machine-checkable
  guardrail today is `verdict == SAME`.

## Open design decisions (for the proposal)
1. **Include `confidence` in the JSON?** Recommended: EXCLUDE, consistent with
   #138 (hidden from humans because uncalibrated; a JSON number revives the
   misleading precision). Proposal to decide explicitly.
2. **`--json` output shape**: pure JSON to stdout, suppressing the human
   tally/legend/detail/Next so it is cleanly pipeable. One object per adjudicated
   group. Suggested fields: `member_ids` (list), `okf_type`, `tier`, `verdict`,
   `rationale`. No survivor/absorbed (no heuristic exists) — the consumer decides.
3. **`--same-only` + `--json` interaction**: default recommendation — `--json`
   emits the FULL results (every verdict) so the consumer filters by the `verdict`
   field; if `--same-only` is also passed, filter the array to SAME for
   composability. Proposal to lock.
4. Empty state under `--json`: emit `[]` (valid empty JSON array), not the plain
   "No candidates found." text, so consumers always get parseable output.

## Risks
- New convention: schema/flag choices set precedent for future `--json` commands.
- Must not alter existing human output when `--json` is absent (additive flag).
- Determinism: array order should be stable (results order) for scriptability.
