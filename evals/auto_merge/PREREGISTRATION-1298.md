# Pre-registration: the structural auto-merge class (#1298)

**Status: Run 2026-10-05 -- PASS on both rules** ([`results/auto-merge-verdict-1298-20261005T181724Z-gemma4-26b-a4b.md`](results/auto-merge-verdict-1298-20261005T181724Z-gemma4-26b-a4b.md)). One disclosure: an unrelated `pytest --cov` run (no model) shared the machine during part of the gemma4 arms, which can only inflate L1's primary latency; L1 read 1.20x against a 3.0x bar. The owner decided the open questions on 2026-10-05 on [#1298](https://github.com/jasonssdev/openkos/issues/1298) (see "Decisions" below), and the bars are binding as written. From now on the rule is frozen: a later change that wants a different rule pre-registers it separately and re-runs every arm. Values marked **measured** cite a committed file; values marked **estimate** are not measurements.

Model-free parts are already encoded and self-tested: the class predicate, the fixture, its exposure counts, and both decision rules live in [`run_structural_class.py`](run_structural_class.py) and [`structural_fixtures.py`](structural_fixtures.py) (`--self-test`, discovered by `evals/run_self_tests.py`; 15 of 15 targeted mutations are killed). The live arms are not built yet (see "What must be built before the run").

## Question

Can the shipped identity judge clear same-type, same-key base/`-N` families without per-item consent, under [ADR-0034](../../docs/adr/0034-identity-auto-merge-only-for-a-measured-class.md)? That ADR admits a class only if it passes a rule written down before its measurement, on a committed fixture with hard negatives, with the threshold chosen on one arm and judged on another. The same measurement also decides whether Identity may offer "accept recommended" (#1264 part 3), against a separate bar.

## What is already known

- **#1054 (`qwen3:8b`): FAIL.** No threshold separates right from wrong `same` verdicts, because confidence is 0.95 on both (`results/auto-merge-verdict-20260930T071242Z-qwen3-8b.md`, **measured**).
- **#1269 (`gemma4:26b-a4b`): PASS** on the same harness and fixture, as reported in the "Results, round 1" comment on #1269. That PASS is why the judge is now the default ([ADR-0047](../../docs/adr/0047-the-contradiction-and-identity-judges-default-to-gemma4-26b-a4b-and-the-engine-runs-one-chat-model-at-a-time.md)). Its raw run files, `B` and `t*` are **not committed** (the bake-off driver writes under `evals/bakeoff/results/`, which is not in the repository). So this pre-registration cannot reuse that `t*`. It must be measured again.
- **The #1269 PASS has no exposure to this class.** On the #1054 fixture, 0 of 26 labelled pairs are in the class (0 of 15 negatives). Its base/`-N` pairs are all Event (its Person pairs are not suffix families), and its Concept pairs are `-a`/`-b` or have different keys. `evals/adjudication`'s fixture is the same: 0 of 27 pairs (0 of 21 negatives). Both counts are computed by the self-test on materialized bundles, not by reading names. A PASS over those fixtures says nothing about this class.
- **Where the class still occurs.** In the binding Quiet Engine run, `main` created 0 non-excluded `-N` duplicates in 6 of 6 runs, against 3, 7 and 4 for `v0.4.0` (`evals/quiet_engine/results/report-20261005T143300Z.md`, **measured**). Attach-at-ingest removes the class on new ingests. What remains is bundles compiled before it shipped, workspaces with `attach_at_ingest: false`, and targets the attach lookup could not read. #1264 found 9 of 9 such families in one real pre-attach bundle to be true duplicates. Nine is a hint, not a sample.

## The class

A candidate group is in the class when all of these hold. Every clause reuses production code, and `run_structural_class.in_structural_class` encodes them:

1. `group.tier is Tier.HIGH`: the members' titles have the same `normalize_key` (`resolution/candidates.py`, `resolution/normalize.py`).
2. Exactly two members (ADR-0034). Families of three or more keep per-item consent.
3. One declared OKF type, not in `ATTACH_EXCLUDED_TYPES` (`application/ingest.py`: Event, Person). This is [ADR-0044](../../docs/adr/0044-the-quiet-engine-arc-precedes-interoperability.md)'s exclusion.
4. The two Concept IDs are a base/`-N` family, `is_suffix_family(a, b) or is_suffix_family(b, a)` (`resolution/normalize.py`, #1228). The survivor is the base, by `canonical_family_member`, which `lifecycle.ordered_merge_pair` already applies.

Production adds ADR-0034's eligibility checks, which the fixture self-test asserts for every pair: `cross_type_concern` is `None`, and the merge plan is not refused by the stacked-body guardrail. An automatic merge additionally needs the judge's verdict `same` at confidence `>= t*`, from a measured model.

Out of class by construction: same-key pairs that are not a suffix family (for example `-a`/`-b`, or hand-named files), and the `-1`/`-2` sibling shape, which `is_suffix_family` does not match.

## Fixture

Every bundle stays under `find_candidates`' cap of 50 groups (`_MAX_CANDIDATE_GROUPS`). Both fixtures together hold 54 pairs and, in one bundle, lose four LOW-tier groups to the cap. So each fixture is materialized in **its own bundle**. The self-test asserts the margin.

**What exists.** The #1054 fixture (`auto_merge_fixtures.py`, 26 pairs) and the adjudication fixture with the #1258-shaped hard negatives (`procedure-about`, `ui-component`). As counted above, neither has any pair in the class. The #1258 shapes are cross-type or have different keys.

**What is added** (`structural_fixtures.py`, synthetic and de-identified, 28 pairs, every one in the class):

| probe | expected | pairs | types | what it guards |
|---|---|---|---|---|
| `key-homonym` | different | 11 | Concept, Organization, Place, Procedure, Entity | one title, two unrelated referents |
| `key-part-whole` | different | 2 | Concept | the #1258 `ui-component` shape moved into the class: a product and its own app or dashboard under one name |
| `key-distinct-instance` | different | 3 | Decision, Project | **the class's named negative**, the analogue of `week-apart`: two instances of one kind, with different deciders, owners or dates |
| `key-cross-source-dup` | same | 8 | seven of the eight non-excluded buildable types (all but Insight) | one thing, two sources, consistent facts |
| `key-reingest-dup` | same | 2 | Concept, Procedure | one source compiled twice, shared `provenance` |
| `key-asym-dup` | same | 2 | Concept, Entity | a rich document and a one-line mention |

**Exposure, as n of TOTAL** (printed by the self-test):

| fixture | pairs in class | negatives in class |
|---|---|---|
| `structural_fixtures` (#1298) | 28 of 28 | 16 of 16 |
| `auto_merge_fixtures` (#1054) | 0 of 26 | 0 of 15 |
| `adjudication_fixtures` | 0 of 27 | 0 of 21 |

At n = 15, the confirmation arm exposes **240 in-class negative trials** (16 x 15). The frozen R1 floor is 180. The named negative is exposed 45 times per arm.

Labels are constructed, not adjudicated. A wrong verdict is a rubric-consistency failure, not disagreement with a human on a real bundle.

## Arms and n

| arm | model | runs | role |
|---|---|---|---|
| calibration | `gemma4:26b-a4b` | 15 | Step 1: fits `t*` |
| confirmation | `gemma4:26b-a4b` | 15 | Step 2: judges the bars with `t*` fixed |
| reference calibration + confirmation | `qwen3:8b` | 15 + 15 | latency baseline for L1, and the same rule computed for disclosure. No adoption follows from it (decision 4) |

`gemma4:26b-a4b` is the shipped judge (ADR-0047). It runs through the production path, `find_candidates` then `adjudicate_candidates`, with no `findings.db`, production client settings (`num_ctx` 12288, generation ceiling 8192, thinking off), and no pinned seed. Calibration and confirmation are separate invocations. Each run file carries the harness stamp: commit, model digest and prompt hashes (`evals/harness_stamp.py`). The two models run in separate sessions, one resident at a time (ADR-0047 layout (a)). The model-load time is recorded separately and is not part of any per-run latency.

## Decision rule (frozen on approval)

**Population.** Only the trials of the 28 in-class pairs. Trials of other pairs never decide.

**Step 1 and Step 2: the #1054 rule, unchanged.** `run_auto_merge_eval.decide` runs on the in-class population, with no edits:

- `B` is the highest calibration negative `same` confidence (`-inf` if none). `t*` is the lowest calibration positive `same` confidence strictly above `B`. No such positive: **FAIL (no separator)**. `B = -inf`: `t*` is labelled **non-binding**.
- **R0 validity:** no missing trial, and the same fixture digest and model in both arms. Otherwise **INVALID**: re-run, not a result.
- **R1 exposure:** at least 180 in-class negative trials in confirmation. 240 are expected.
- **R2 false auto-merges:** **0** in-class negative confirmation trials with `same` and confidence `>= t*`.
- **R4 retention:** at least 0.50 of in-class positive confirmation trials auto-merge at `t*`.
- **R5 stability:** the mean, over in-class positive pairs, of the modal share of the auto-merge decision is at least 0.80.

R3 (`week-apart`) has no exposure in this population, so it would pass vacuously. It is replaced by S2.

**Added bars:**

- **S2 named negative:** **0** `key-distinct-instance` trials auto-merged at `t*`, in **either** arm.
- **L1 latency:** the median per-run wall-clock of the `gemma4:26b-a4b` confirmation arm is at most **3.0x** that of the `qwen3:8b` reference confirmation arm, on the same fixture. This is #1269's identity budget. A reference arm that was not run reads **FAIL**, never pass.

**Verdict.** **PASS** if and only if Step 1 yields `t*` and R0, R1, R2, R4, R5, S2 and L1 all hold. **INVALID** if R0 fails. Otherwise **FAIL**, naming every failed bar. Precision on the negatives is the zero-tolerance R2/S2. Recall is R4 at the frozen 0.50. Neither is re-tuned to this fixture.

**Reported, never deciding:** the same rule on the cross-source-excluded in-class population (as #1054 did); the per-probe verdict distribution; the `qwen3:8b` verdict; and the #1054 fixture's secondary run under gemma4 (decision 1).

## Ship rule (if PASS)

Each item follows ADR-0034, with ADR-0044 and #1054's owner conditions:

1. **Opt-in per run and off by default.** No configuration key turns it on standing (ADR-0034 decision 2; decision 3 below: `curate --auto-merge`). Default-on would need an ADR that supersedes ADR-0034, backed by evidence from use (ADR-0044).
2. **Measured constants.** `t*` and the measured model tag and digest are code constants, not settings. A verdict from any other model (for example a workspace that opted out with `models: {adjudication: null}`) makes the class ineligible for that run. Any change to the adjudication prompt, rubric, withdrawal markers or default model invalidates the measurement until the harness is re-run (ADR-0034).
3. **Mechanical merges.** No LLM rewrite of the merged body (`--no-reconcile` semantics), so `unmerge` restores byte parity. The survivor is the base id.
4. **At most one automatic merge per survivor per run** (`unmerge` is LIFO per survivor).
5. **Committed, logged and listed with its undo.** ADR-0034 decision 4 fixes one git commit per run, one `log.md` run entry, and a disclosure that names every merge and its undo command. The shipped "what changed" digest (`application/digest.py`) lists one line per commit, with its sha, at most 4 concept ids, and `git revert <sha>` as the undo. It cannot name each merge's `openkos unmerge`, so the run's disclosure adds one line per merge (decision 2). The line must carry the concept ids or the commit sha, so the Quiet Engine's B4 matching rule ("sha or every touched document on a line with an undo command") still measures it.

If the verdict is FAIL or INVALID, nothing ships for the class. The verdict file, run files and fixture are committed, and #702's per-item consent stands.

## Identity "accept recommended"

**What it is.** In the Identity stage, the person is shown the recommended set and accepts it with one answer. The recommended set is the in-class groups the judge calls `same`, **at any confidence**. A person still confirms the list, so no threshold is fitted. That is the difference from the auto-merge class. It is still bulk consent to deletions, which #702 forbids for Identity until measured, so it gets its own bar. It reuses the auto-merge measurement's trials and needs no extra model run.

**Bars** (`decide_recommended`; both `gemma4:26b-a4b` arms pooled, 30 runs, since no threshold is chosen and nothing is circular):

| bar | rule |
|---|---|
| A0 validity | as R0 |
| A1 named negative | **0** `key-distinct-instance` trials recommended (of 90) |
| A2 false recommendations | at most **1%** of in-class negative trials recommended (at most 4 of 480) |
| A3 persistence | no single in-class negative pair recommended in more than **1** of its 30 trials |
| A4 recall | at least **0.90** of in-class positive trials recommended (at least 324 of 360) |

**Ship rule (if A0 to A4 pass).** Offered in Identity only for in-class groups, and only for the measured model. Every other Identity group keeps per-item prompts. The list shows survivor, absorbed and the undo for each item. Accept-recommended does not reduce the Quiet Engine's prompt count `D`, because an accept-all key still presents each item. Its value is time per decision, not decision count. A PASS of the auto-merge rule does not imply A0 to A4, and the reverse does not hold either: each is decided on its own bars.

## What a PASS does not establish

Constructed labels measure rubric consistency, not agreement with a human on a real bundle. The result covers one judge model and one prompt hash. Synthetic, de-identified pairs may be easier than real documents. The class's real-world frequency after attach-at-ingest is low, so the benefit is mostly to bundles compiled before it. This section is carried verbatim into the verdict file.

## What must be built before the run

These need a model, so they are tasks, not code in this change:

1. A fixture selector on `run_auto_merge_eval.py` (for example `--fixture structural`) that materializes `STRUCTURAL_PAIRS` in its own bundle, and keeps the `openkos.eval.auto_merge/v1` run-file schema and the harness stamp it already writes (`build_stamp`: commit, model digest, prompt identity).
2. `--decide` for this rule: it loads the four run files, calls `decide_structural` and `decide_recommended`, and writes `auto-merge-verdict-1298-<stamp>-<model>.md` in the existing verdict format, plus S2, L1 and A0 to A4.
3. A pilot run (1 run per model) to check that the protocol works, read by no bar. If it forces a protocol change, this document is amended before the counted runs.

## Run forecast

| arm | per run | 15 runs | source |
|---|---|---|---|
| `qwen3:8b`, 28 pairs | about 69 s | about 17 min | **measured** rate: 64 s median per 26-pair run, both #1054 arms (`results/runs-*-qwen3-8b.json`), scaled by pair count (**estimate**) |
| `gemma4:26b-a4b`, 28 pairs | about 83 to 103 s | about 21 to 26 min | **estimate**: #1269 reports an 84 s median per 27-pair adjudication run, about 1.2x `qwen3:8b`'s per-pair rate there; the range allows up to 1.5x |

- **Primary** (gemma4, calibration + confirmation): about **45 to 55 min**.
- **Reference** (qwen3:8b, two arms): about **35 min**.
- Model loads: a few minutes per switch (**estimate**; ADR-0047 records swap time separately).
- Pilot: about 5 min.
- Q1 secondary (the #1054 fixture under gemma4, two arms): about 40 to 50 min.
- **Total: about 2.3 h.**

The README's "about 1.7 hours per arm" predates the stored runs and overstates `qwen3:8b` by about 6x. A default Ollama serializes requests, so there is no parallel speed-up.

## Amendment proposal: the Quiet Engine's G1 (item 3 of #1298)

This is a proposal for the **next** Quiet Engine measurement. It does not edit `evals/quiet_engine/PREREGISTRATION.md`, whose binding run stands as recorded.

**The defect.** G1 compares total open pending rows. The decision-bearing kinds sit at their per-kind caps in almost every run of every arm: `identity` is capped by `_MAX_CANDIDATE_GROUPS` = 50 (`resolution/candidates.py`), and `relation_type` by `_MAX_CANDIDATE_EDGES` = 50 (`graph/sqlite_graph.py`). Final `identity` rows were 41, 50, 50 for `v0.4.0` and 50, 50, 20, 46, 50, 50 for `main`. `relation_type` was 51 in every run of every arm. **Measured**, from `results/runs-*.json`, `pending_final.by_kind`. A kind at its cap cannot show decisions moving into the queue, so G1 had almost no exposure. Its FAIL (four `main` runs at 106 or 107 against a `v0.4.0` median of 105) is a difference of one or two rows, within the uncapped kinds (`revision`, `volatility`).

**Proposed G1' (registered before any use):**

- **G1'a, identity:** for every `main` run, the **uncapped** identity candidate count after step 7, `find_candidates_report(bundle).produced`, is at most the `v0.4.0` median of the same count.
- **G1'b, relation_type:** the same, with the uncapped edge-candidate count (`CandidateReport.produced` from the graph build).
- **G1'c, uncapped kinds:** the open rows of every kind with no cap, summed, are at most the `v0.4.0` median of that sum. No kind is exempted, and every kind is also reported per run.
- **Exposure check:** each per-kind comparison records whether its cap bound in that run (`retained < produced`). A comparison that can only read a capped count reads **NOT_MEASURED**, never PASS.

Both counts are model-free reads on the run's bundle. They are already computed on every scan, and only need to be recorded. The alternative, a corpus small enough to stay under the caps, is not recommended. `v0.4.0` hid 476 to 568 candidates behind caps on this corpus, so staying under them would need a corpus several times smaller, and that would likely break B1's validity floor (`v0.4.0` `D / 22 >= 2.0`).

## Decisions

Each of these was an open question in the draft.

1. **Q1. Should the Event/Person base/`-N` families be measured alongside the class?** They are excluded from the class (ADR-0044), but they are now almost the only `-N` families a fresh ingest creates (`main`: 0 non-excluded against 0 to 3 Event/Person per run). **Decided by the owner, 2026-10-05:** they are excluded from the decision. The #1054 fixture is re-run under `gemma4:26b-a4b`, in its own bundle, as a **report-only secondary**: a calibration and a confirmation arm of 15 runs each. It reports the frozen #1054 rule's verdict on that fixture, and the per-probe `same` rates and confidences of its 10 Event base/`-N` pairs (`recurrence`, `asym-recurrence`, `event-same`, `asym-same`; its Person pairs are not suffix families). No bar reads it. It is evidence for a later Event/Person admission, which would need its own pre-registration.
2. **Q2. How is each automatic merge listed with its undo?** **Decided by the owner, 2026-10-05:** one commit per run, as ADR-0034 fixes. The run's disclosure prints one line per merge with survivor, absorbed, confidence and `openkos unmerge <survivor>`, beside the commit's `git revert` line. The sha and concept ids stay on the line, so the Quiet Engine's B4 rule keeps matching.
3. **Q3. Where does the mode run?** **Decided by the owner, 2026-10-05:** `curate --auto-merge`, a per-run flag (ADR-0034 decision 2). It works on a non-TTY `curate --auto`, and its output uses the line format of decision 2. The daemon does not run it.
4. **Q4. What happens if `qwen3:8b` also passes the same rule?** **Decided by the owner, 2026-10-05:** it is disclosed only. The class ships for `gemma4:26b-a4b` alone, and any `qwen3:8b` admission needs its own adoption decision.
