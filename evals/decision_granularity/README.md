# `decision_granularity` — decision splitting and a twice-named person (#1231)

Drives the real `extract_concept_union` (production generation ceiling 8192
and context window 12288) over synthetic sources and scores the retained
objects. A treatment is a one-sentence edit applied to
`concept._SYSTEM_PROMPT` by exact replacement; production is never edited.

```bash
uv run python -u evals/decision_granularity/run_granularity.py --self-test   # no model
uv run python -u evals/decision_granularity/run_granularity.py --arm baseline --runs 15
uv run python -u evals/decision_granularity/run_granularity.py --arm persons --runs 15
uv run python -u evals/decision_granularity/run_granularity.py --arm decisions --runs 15
uv run python -u evals/decision_granularity/run_granularity.py --rescore
```

Every text in `granularity_fixtures.py` is synthetic. The decision rule is pre-registered
in `run_granularity.py`'s docstring (gain of at least 0.25 and larger than the baseline's
own block spread on the fixtures the baseline fails; no recall, precision or
over-split regression elsewhere).

## Result (`qwen3:8b`, 15 runs per arm and fixture, 2026-10-02)

Nothing was shipped for either sub-issue.

### Exposure: what the baseline actually fails (`n of TOTAL` baseline runs)

| fixture | shape | baseline failures |
| --- | --- | --- |
| `es-notes-5-decisions` | five decisions, Spanish meeting notes | 2 of 15 |
| `es-gemini-notes-5-decisions` | five bold-lead decisions, export layout, non-meeting title | 1 of 15 |
| `en-review-3-decisions` | three decisions, English review notes | 8 of 15 |
| `en-review-new-engineer` | engineer named twice (target Person missed) | 7 of 15 |
| `es-meeting-new-engineer` | same, Spanish meeting notes | 0 of 15 |
| `en-note-single-decision` | control, one decision | 0 of 15 over-split |

The field failure (b), five decisions folded into one object, **did not
reproduce**: both five-decision fixtures split in 13 of 15 and 14 of 15 runs.
The only fixture with real exposure (`en-review-3-decisions`) fails in the
opposite way: the run collapses to a single `Event` and no `Decision` at all
(7 of 15 split, block spread 0.60), which is the collapse #522 measured, not
the lumping the issue describes. The missed Person (c) reproduced on
`en-review-new-engineer` (8 of 15 hits, spread 0.20).

### Treatments

`persons` adds to the anti-enumeration sentence: "a person the source names
more than once and describes (for example a new team member) is about that
person, not a passing mention."

| fixture | metric | baseline | `persons` |
| --- | --- | --- | --- |
| `en-review-new-engineer` | person hit | 8 of 15 | 12 of 15 |
| `es-meeting-new-engineer` | topic recall | 0.64 | 0.51 |
| `es-meeting-new-engineer` | Decisions/run | 0.73 | 0.13 |
| `en-review-3-decisions` | split | 7 of 15 | 1 of 15 |

The target gain (+0.27) clears the 0.25 bar and the baseline spread (0.20),
but recall on `es-meeting-new-engineer` fell 0.13 and `en-review-3-decisions`
lost most of its splits: **rejected** (rule 2).

`decisions` appends to the Decision definition: "A source that records
several choices yields one Decision per choice, never one object listing them
all."

| fixture | metric | baseline | `decisions` |
| --- | --- | --- | --- |
| `en-review-3-decisions` | split | 7 of 15 (spread 0.60) | 9 of 15 (spread 0.80) |
| `es-notes-5-decisions` | split | 13 of 15 | 15 of 15 |
| `es-gemini-notes-5-decisions` | split | 14 of 15 | 15 of 15 |
| `en-note-single-decision` | over-split | 0 of 15 | 1 of 15 |

The only fixture with exposure moved +0.13, below both the 0.25 bar and the
arm's own spread: **rejected** (rule 1). The five-decision fixtures had
almost no failures to remove (3 of 30 combined).

### Reading

- Both edits are within or beyond run-to-run noise on a model whose dominant
  instability here is collapse-to-`Event`, which neither edit addresses.
- `es-meeting-new-engineer` was not measured under `decisions`: that arm ran
  on five fixtures only (`--fixture`), so rule 2 for that fixture rests on the
  baseline and `persons` arms.
- Stored runs: `results/runs-*.json`.

## Second pass on (c): three narrow clauses, none adopted (2026-10-06)

Pre-registered in `PREREGISTRATION-1231c.md` before any treatment run: two
baseline arms pooled (30 runs per fixture), the target must beat the pooled
rate (17 of 30) by more than the 0.40 block spread, which only 15 of 15
meets. A re-run baseline on current main gave 9 of 15 (the first gave 8 of
15).

The stored baseline outputs show that (c) is not a Person-recall gap: every
miss is a whole-run collapse to one `Event` (`produced = 1`, judge skipped).
When the run does not collapse, the Person is emitted.

| arm | edit | target person hit | guard verdict |
| --- | --- | --- | --- |
| `role` | after the Person definition, "A newcomer introduced by role ... even when named only in a decision or staffing line." | 7 of 15 | fails target; `es-meeting-new-engineer` split 3 of 15 (< 4) |
| `attendees` | after "not five Person stubs", "; but a person the decisions or staffing lines are about ... is a subject, not an attendee." | 9 of 15 | fails target (no gain) |
| `newcomer` | after the Person definition, "(including a newcomer's role on a team)." | 6 of 15 | fails target |

Guards held for every arm (recall, `es-meeting-new-engineer` person hit 15 of
15, the control's over-split 0 of 15, no errored runs); the targets simply did
not move. **Nothing shipped.** Wording that targets the Person cannot fix a
failure that happens before Persons are considered; a fix would have to
address the collapse itself (#522), which a single-clause prompt edit has not
done on this model. Run-to-run noise is large: `en-review-3-decisions` split
7 of 15 in one baseline arm and 4 of 15 in the next, which is why the rule
pools both baselines.

Stored runs: `results/runs-{baseline,role,attendees,newcomer}-2026100*.json`.

## Third pass: the collapse was a deterministic gap, and it shipped (#1318, 2026-10-07)

Pre-registered in `PREREGISTRATION-1318.md` before any treatment run. The
diagnosis, from code and live traces, is in that file: both passes return the
model's single object (model behaviour), the #584/#642 re-ask that exists for
exactly this shape recovers it when called by hand (5 of 5 on each fixture),
and it never fired (`reask_runs 0`) because token containment read the file
stem `2026-02-10-architecture-review` and the object title `Architecture
review, 10 February` as two topics. `_title_tokens` now drops date tokens
(all-digit tokens and full English/Spanish month names).

`datefold` arm vs three baseline arms (`qwen3:8b`, 15 runs per arm and
fixture; the third baseline was run on this branch before the treatment):

| fixture | metric | baseline arms | pooled | `datefold` | one-sided Fisher p vs pool |
| --- | --- | --- | --- | --- | --- |
| `en-review-new-engineer` | person hit | 8, 9, 5 of 15 | 22 of 45 | 13 of 15 | 0.0095 (0.043 vs the two-arm pool of the registration) |
| `en-review-3-decisions` | split | 7, 4, 4 of 15 | 15 of 45 | 13 of 15 | 0.0004 (0.0016 vs the two-arm pool) |
| `en-review-new-engineer` | runs with `produced = 1` | 6, 6, 10 of 15 | 22 of 45 | 0 of 15 | |
| `en-review-3-decisions` | runs with `produced = 1` | 6, 8, 7 of 15 | 21 of 45 | 0 of 15 | |
| `en-note-single-decision` (control) | over-split | 0, 0, 0 of 15 | 0 of 45 | 0 of 15 | |

Guards held: topic recall 1.00 on both targets (pooled 1.00 and 0.90); person
stubs 0.00; no errored runs; mean latency 1.16x (`en-review-new-engineer`),
1.40x (`en-review-3-decisions`), 1.01x (control). Both targets met the
registered bar (13 of 15 and 11 of 15), the first exactly at its edge. The
control was never at risk: on the stored runs the new trigger flips in 0 of
104 control runs, and in the treatment arm it spent no re-ask on the control
(`reask_runs` 0 in 15 of 15), so it confirms the note is left alone and is
no evidence of safety on other sources.

Shipped, so the arm that remains is the ablation, `undated`, which restores
the pre-#1318 tokens and reproduces the collapse. Stored runs:
`results/runs-baseline-20261007T162623Z-qwen3-8b.json`,
`results/runs-datefold-20261007T163815Z-qwen3-8b.json`.

Not explained by this fix: on `en-review-3-decisions` some single-object runs
come from the judge keeping one of several candidates, which a trigger read
before the judge cannot reach.
