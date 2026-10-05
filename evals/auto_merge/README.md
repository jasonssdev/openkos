# auto-merge eval (#1054)

Measures whether a confidence threshold exists under which the
entity-resolution adjudicator's `SAME` verdicts on one narrow eligible class
never auto-merge two different concepts. This is the STOP-gate harness for
`auto-merge-safe-class`
(`openspec/changes/archive/2026-09-30-auto-merge-safe-class/`, ADR-0034) --
`curate --auto-merge` is built only if this harness's pre-registered rule
PASSes.

**Verdict: FAIL.** No calibration positive exceeds the highest negative
`same` confidence (0.9500), so no separating threshold exists (`t*`
undefined) and auto-apply was not built; see
[ADR-0034](../../docs/adr/0034-identity-auto-merge-only-for-a-measured-class.md).
The canonical verdict file is
[`results/auto-merge-verdict-20260930T071242Z-qwen3-8b.md`](results/auto-merge-verdict-20260930T071242Z-qwen3-8b.md);
the other two verdict files there are near-identical duplicates of it.

## The structural class (#1298)

A second, separately pre-registered measurement reuses this harness's rule
on a different population: same-type, same-key base/`-N` families outside
Event and Person. Neither this fixture nor `evals/adjudication`'s has any
pair in that class, so it brings its own synthetic fixture. See
[`PREREGISTRATION-1298.md`](PREREGISTRATION-1298.md) (draft until the owner
approves it), [`structural_fixtures.py`](structural_fixtures.py), and the
model-free [`run_structural_class.py`](run_structural_class.py)
(`--self-test`).

## Why this harness exists

`curate` never applies an Identity merge unattended today (#702):
`--accept identity` is refused, `review: false` never reaches Identity, and
`adjudicate --apply-same` requires typing the previewed count. The owner
agreed, on #1054 (2026-09-29), to let one narrow class merge automatically,
reviewable and reversible AFTER the fact -- **but only if a measurement
committed before the feature says a safe class exists.**

The prior evidence points the other way. `evals/adjudication/README.md`
found the adjudicator's stated confidence "carries no information": every
verdict in the no-clause arms, right and wrong, was stated at 0.95, and the
wild-shape recurrence pair (`grupo-calidad-datos`) is judged `same` 13 of 15
runs "stably, at 0.95 confidence". A threshold cannot separate what the
signal does not separate -- which is exactly why the harness comes first: if
the rule fails, the change stops, and a stopped change is a successful
outcome of this plan, not a failure of it.

## The eligible class

A `CandidateGroup` of exactly two members, sharing one declared OKF `type`,
with `lifecycle.cross_type_concern` `None`, whose merge plan is not
guardrail-refused for stacked, unreconciled body content
(`application.lifecycle.StackedBodyReport.exceeds_guardrail`). N>2 groups,
cross-type pairs, and anything this rule does not admit keep today's
per-item consent (#702) unchanged.

## The fixture

`auto_merge_fixtures.py` -- committed, invented content only, no private
corpus. Ten labelled-pair classes:

| class | expected | min pairs | what it guards |
|---|---|---|---|
| `week-apart` | different | 3 | **the owner's named negative**: one series title, disjoint provenance, dates seven days apart, different attendees or decisions |
| `recurrence` | different | 2 | #796's reported class (imported) |
| `asym-recurrence` | different | 3 | #869's wild shape, including `grupo-calidad-datos`, judged `same` 13/15 at 0.95 today (imported) |
| `namesake-person` | different | 2 | two different people with one name, disjoint affiliations |
| `aspect-or-part` | different | 2 | same-type part-whole / aspect-of (imported where D3 admits) |
| `reingest-dup` | same | 3 | the re-ingest accumulation case: one source compiled twice, shared provenance |
| `event-same` | same | 2 | one meeting recorded twice from two sources (imported) |
| `person-same` | same | 2 | identical name IS identity for a Person |
| `alias-same` | same | 2 | one entity, two names |
| `asym-same` | same | 1 | sparse-but-genuine duplicate (imported) |

Totals: at least 12 negative pairs across at least 5 classes, at least 10
positive pairs across at least 4 classes. Labels are CONSTRUCTED, not
adjudicated -- read as rubric-consistency, not agreement with a human on a
real bundle. `recurrence`, `asym-recurrence`, `event-same`, `asym-same`, and
same-type `part-whole`/`aspect-of` pairs are imported BY REFERENCE from
`evals/adjudication/adjudication_fixtures.py` (filtered by `.probe`, never
copied), so they cannot drift out of step with what that harness already
measured. `--self-test` asserts, for every labelled pair, that
`find_candidates` yields exactly one 2-member group, both members declare
one type, `cross_type_concern` is `None`, and the pinned `prepare_one_merge`
plan is not guardrail-refused -- an ineligible pair is a FIXTURE defect,
caught in CI, never a silent exclusion from the measured population.

`alias-same`'s two pairs use a short title and a more qualified variant of
the same title (e.g. "Rate Limiter" / "API Rate Limiter"), not two
unrelated names: candidate discovery is title-based
(`resolution.similarity.near_match_score`), so a wholly different alias,
however true to life, would never be NOMINATED as a candidate in production
and has no place in a fixture meant to prove structural eligibility.

## The decision rule (frozen)

Written in `openspec/changes/archive/2026-09-30-auto-merge-safe-class/design.md`
§"Decision rule", in the planning commit (`525393d`), before any live run.
It MUST NOT be edited after the first live number is seen; a later change
that wants a different rule records it as a new, separately pre-registered
rule and re-runs both arms. Copied here verbatim for reference:

> **Step 1 -- threshold, from the calibration arm only.**
>
> - `B` = the HIGHEST `s` over calibration negative trials with `same`
>   (`B = -inf` when there are none).
> - `t*` = the LOWEST `s` over calibration positive trials with `same` such
>   that `s > B`.
> - If no such positive exists, the verdict is **FAIL (no separator)**. The
>   lowest good must sit strictly above the highest bad.
> - If `B = -inf`, `t*` is still computed and the verdict file MUST label
>   the threshold **non-binding** (no negative ever reached it, so it was
>   never tested); the bars below still decide.
>
> **Step 2 -- bars, on the confirmation arm with `t*` fixed.**
>
> | Bar | Metric | Pass when |
> |---|---|---|
> | R0 | Validity | zero trials missing a result; fixture digest and model identical across arms |
> | R1 | Exposure | negative trials reaching the gate ≥ 12 × 15 = 180 |
> | R2 | False auto-merges | **0** negative trials with `same` and `s >= t*` |
> | R3 | Owner's named negative | **0** `week-apart` trials auto-merged at `t*` in EITHER arm |
> | R4 | Retention | ≥ 0.50 of positive trials auto-merge at `t*` |
> | R5 | Stability | mean over positive pairs of the modal share of the auto-merge decision ≥ 0.80 |
>
> **Verdict.** `PASS` iff Step 1 yields `t*` and R0-R5 all hold. `INVALID`
> if R0 fails (re-run; not a result). Otherwise `FAIL`, naming every failed
> bar.

**A note on R3's "either arm" check.** Given Step 1's own invariant
(`t* > B` always, and `B` is the max over ALL calibration negative
`same`-trials, week-apart included), a week-apart trial cannot ever reach
`t*` from calibration data alone -- any calibration negative trial's
confidence is, by construction, `<= B < t*`. The cross-arm check still does
real work: it is exactly what catches a week-apart violation in a lucky
CONFIRMATION run. `--self-test`'s `FAIL R3` case is therefore constructed in
the confirmation arm (see the code comment on that case).

## Usage

```
uv run python evals/auto_merge/run_auto_merge_eval.py --self-test
uv run python evals/auto_merge/run_auto_merge_eval.py --arm calibration --runs 15
uv run python evals/auto_merge/run_auto_merge_eval.py --arm confirmation --runs 15
uv run python evals/auto_merge/run_auto_merge_eval.py --decide \
    results/runs-calibration-<stamp>-<model>.json \
    results/runs-confirmation-<stamp>-<model>.json
```

`--self-test` is model-free (discovered by `evals/run_self_tests.py`, run
under a poisoned `OLLAMA_HOST`): it materializes the fixture, asserts D3 for
every pair and the class minimums, asserts the fixture digest is stable
across two materializations, and runs `decide()` on synthetic arms that
reach `PASS`, `FAIL (no separator)`, `FAIL R2`, `FAIL R3`, `FAIL R4`,
`FAIL R5`, `INVALID`, and the `B = -inf` non-binding case.

`--arm {calibration,confirmation}` runs the REAL production path --
`find_candidates` then `adjudicate_candidates` on a temp bundle, no
`findings.db`, so no verdict is ever served from cache -- production
sampling (no seed or temperature pinned) and production client settings
(`config.DEFAULT_CONTEXT_WINDOW`, `config.DEFAULT_MAX_GENERATION_TOKENS`).
Needs Ollama serving `qwen3:8b` locally. Budget: roughly 22 pairs x 15 runs
x ~19s, about 1.7 hours per arm.

`--decide CAL.json CONF.json` is pure and offline: it never calls a model.
Calibration and confirmation are always two SEPARATE `--arm` invocations
(design D4) -- mixing them, or re-scoring one arm's runs as if they were the
other's, would make the eventual verdict circular.

## Results file format

All under `evals/auto_merge/results/`, committed (`tasks.md` Phase 2: each
arm's JSON and report, and the verdict file, are committed as soon as they
land):

- `runs-<arm>-<UTCstamp>-<model>.json` -- schema `openkos.eval.auto_merge/v1`:
  git sha, fixture digest, client settings, and every trial (`pair_id`,
  `probe`, `expected`, `tier`, `cross_source`, `verdict`, `confidence`,
  `rationale` verbatim, `latency_s`), grouped by run.
- `auto-merge-<arm>-<UTCstamp>-<model>.md` -- the per-arm report: run count,
  fixture digest, git sha, per-probe verdict distribution.
- `auto-merge-verdict-<UTCstamp>-<model>.md` -- `--decide`'s output: both
  input file names, `B`, `t*` (and whether non-binding), every bar's
  measured value, the verdict, the secondary cross-source-excluded result,
  and "What a PASS does not establish".

## What a PASS does not establish

Constructed labels are rubric-consistency, not agreement with a human on a
real bundle; one model; de-identified analogues may be easier than real
documents. This section is carried verbatim in every verdict file.
