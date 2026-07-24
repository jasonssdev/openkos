# Design: duplicates / adjudicate output ergonomics (#139, Slice 1)

## Technical Approach

Additive, display-only. Insert three `typer.echo` outputs (tally, legend, `Next:`) into
each command's non-empty render path, backed by two pure sibling helpers next to
`_format_type_tally`. Existing per-item detail lines stay byte-identical, so every
current CliRunner substring assertion keeps passing. No verdict logic, scoring, tier
bucketing, or `merge` change.

## Verified Facts (file:line)

- `duplicates` loop: `main.py:3586-3591`. Iterates `groups: list[CandidateGroup]`;
  tier via `group.tier is Tier.HIGH` -> `"HIGH"`/`"LOW"`; line `f"[{tier_label}] {group.okf_type} -- {group.trigger}"`, members `f"  - {member_id}"`.
- `CandidateGroup` (`candidates.py:55-71`): a HIGH group may have >2 `member_ids`; a LOW group is always a pair. `trigger` = normalized key (HIGH) or 3-dp near-match score (LOW).
- `adjudicate` loop: `main.py:3721-3733`. `results: list[AdjudicatedCandidate]`; `displayed` filters by `result.verdict is Verdict.SAME` when `--same-only`; renders `result.verdict.value.upper()`.
- `Verdict` enum (`adjudication.py:70-77`): `SAME="same"`, `DIFFERENT="different"`, `UNCERTAIN="uncertain"`. Matches proposal — no contradiction.
- Empty guards: adjudicate has TWO — `if not results:` -> `"No candidates found."` (`:3710-3712`) AND `if not displayed:` -> `"No SAME-verdict candidates to display (--same-only)."` (`:3717-3719`). Both must stay suppression points.
- `merge` (`main.py:2259`): positional `survivor_id` then `absorbed_id` — hint order confirmed.
- `Next:` style (`main.py:3898`, `:4013`): inline `typer.echo("Next: ...")` as last statement, literal angle-bracket placeholders (`<source> <type> <target>`).
- Tests: BOTH files use only `in result.stdout` substring asserts on render output. Every `==` equality is on `result.stderr` (refuse/error paths only). Additive stdout lines are safe — confirmed definitively.

## Architecture Decisions

### Two purpose-specific helpers, not one general helper
**Choice**: `_format_group_tally(high, low)` and `_format_verdict_tally(same, different, uncertain)`, both pure, both `""`-on-empty (`total == 0`), both reuse `_plural`.
**Rejected**: one parameterized helper (prefix + counts + conditional-noun). It would need branching for the UNCERTAIN-only-when-nonzero rule and divergent nouns, undermining the pure/decoupled shape of `_format_type_tally`.
**Rationale**: mirrors `_format_type_tally`'s purpose-built, primitive-in / string-out contract; each helper stays trivially testable in isolation.

### `Next:` hint uses literal placeholders, printed once
**Choice**: `typer.echo("Next: openkos merge <survivor> <absorbed>")` after the loop, angle brackets literal (teaches command shape, NOT computed ids).
**Rejected**: per-group `merge a b` lines.
**Rationale**: `merge` is pairwise; a HIGH group can have >2 members, so computed ids would be dishonest/wrong. A generic one-time hint is accurate for any group size (>2-member groups resolve via repeated pairwise merges). Matches `:3898`/`:4013` convention exactly.

### Tally source & placement
**Choice**: adjudicate tally counts FULL `results` via `collections.Counter(r.verdict for r in results)`, indexing (`counts[Verdict.SAME]` -> 0 if absent). Both tallies/legend/`Next:` sit AFTER all empty guards, before the loop (`Next:` after). UNCERTAIN segment appended only when `uncertain > 0`.
**Rationale**: `--same-only` is display-only; locked decision counts full results. Placing after guards keeps empty / no-candidate / same-only-empty paths byte-clean (no new lines).

## Interfaces / Contracts

```python
def _format_group_tally(high: int, low: int) -> str:
    total = high + low
    if total == 0:
        return ""
    return f"{total} candidate group{_plural(total)} ({high} exact, {low} near)"

def _format_verdict_tally(same: int, different: int, uncertain: int) -> str:
    total = same + different + uncertain
    if total == 0:
        return ""
    parts = f"{same} SAME, {different} DIFFERENT"
    if uncertain > 0:
        parts += f", {uncertain} UNCERTAIN"
    return f"adjudicated {total}: {parts}"
```

Legend (once, before loop, both commands):
`Legend: [tier] type -- trigger (HIGH = exact normalized key, LOW = near-match score)`

Requires `from collections import Counter` in `main.py` (add if absent).

## Data Flow

    groups/results ──► count (tier / Counter[Verdict]) ──► pure helper ──► echo tally
                                                              │
                       legend echo ──► existing loop (unchanged) ──► echo Next:

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `src/openkos/cli/main.py:356-373` | Modify | add two pure sibling helpers by `_format_type_tally` |
| `src/openkos/cli/main.py:3582-3591` | Modify | duplicates: tally + legend before loop, `Next:` after |
| `src/openkos/cli/main.py:3708-3733` | Modify | adjudicate: verdict tally (full results) + legend + `Next:` |
| `tests/unit/cli/test_duplicates.py` | Modify | RED tests: tally text, legend, `Next:`, empty-suppression |
| `tests/unit/cli/test_adjudicate.py` | Modify | RED tests: verdict tally, UNCERTAIN on/off, legend, `Next:`, both empty paths |

## Testing Strategy

| Layer | What | Approach |
|-------|------|----------|
| Unit (helpers) | `_format_group_tally`, `_format_verdict_tally` | direct calls: empty->"", singular/plural, UNCERTAIN suppressed at 0, present at >0 |
| Unit (CLI duplicates) | tally + legend + `Next:` present; empty path suppresses all three | CliRunner substring asserts; existing `test_..._fresh_bundle` guards empty path |
| Unit (CLI adjudicate) | tally counts full results under `--same-only`; UNCERTAIN segment; both empty notices unchanged | build `AdjudicatedCandidate` lists via existing `_adjudicated()` + `Verdict.*`, mock `adjudicate_candidates`; assert `"adjudicated 3: 1 SAME, 1 DIFFERENT, 1 UNCERTAIN"`, and absence of tally/`Next:` on `not results` / `not displayed` paths |

Verdicts are constructed exactly as existing tests do: `_adjudicated(group, verdict=Verdict.DIFFERENT, ...)` with `find_candidates`/`adjudicate_candidates` monkeypatched.

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or process-integration boundary. Pure `typer.echo` to stdout.

## Migration / Rollout

No migration. Single display-only PR, well under the 400-line budget; rollback = revert.

## Open Questions

None blocking. Exact blank-line spacing between tally/legend/loop/`Next:` is an implementation detail for tasks; detail lines remain byte-identical.
