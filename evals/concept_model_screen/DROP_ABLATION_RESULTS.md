# Results: which deterministic drop costs window lift (#1572)

**Verdict: the framing-object drop (`_drop_framing_objects`) accounts for
the whole L7 loss.** Turning it off restores 100% of the loss on each of the
three models the issue names. The other L7 rules dropped nothing:
`_drop_source_title_twins` dropped 0 objects and `_drop_wrong_language_titles`
dropped 0. The framing drop is **sound for its target class**. Of the 69
objects it dropped in round 1, 68 name the meeting itself: 67 `Event`s and
one `Project`. One was a false positive, a `Decision` whose title mentions
"next meeting". That
false positive is filed as #1575. No default changes.

The rules were registered in
[`DROP_ABLATION_PREREGISTRATION.md`](DROP_ABLATION_PREREGISTRATION.md),
merged in #1574 before scoring.

## Run

- The harness is `drop_ablation.py` (`list`, `score`, `--self-test`). The
  pre-registration merged at `4231ce13`. Scoring ran on the harness commit
  `90563cab`.
- **No model call.** Every run had `OLLAMA_HOST` pointed at a closed port,
  and the embedding function raises on a cache miss. Every text needed a
  vector, and every vector was already in the #1557 `bge-m3` cache. Nothing
  was deferred inside the registered scope.
- **Validity check:** the all-rules-on replay reproduced the recorded
  `dedup` and `drops` stages in all 35 records (25 in round 1, 10 in run 2).
  0 were excluded.
- **Reconciliation:** the L7 loss recomputed from the recorded stages
  matches the published table to rounding. For example, `qwen3:8b` gives
  -0.0089 here, against 0.0478 → 0.0390 in #1559.

## Primary: round 1 (n = 1, pool DA)

"Lift delta" is the window lift with the rule off minus the window lift with
every rule on. A positive delta means the rule costs lift. The interval is
the paired two-stage bootstrap (10,000 resamples, seed 42).

| Model | Rule | lift delta [95% CI] | objects dropped | windows emptied | false positives (their delta) |
|---|---|---|---|---|---|
| `qwen3:8b` | framing | +0.0089 [-0.0115, +0.0393] | 9 | 9 | 0 |
| `qwen3:8b` | dedup | +0.0000 | 0 | 0 | 0 |
| `qwen3:8b` | language, twins | +0.0000 | 0 | 0 | 0 |
| `qwen3.6:35b-a3b` | framing | +0.0110 [-0.0103, +0.0425] | 21 | 8 | 0 |
| `qwen3.6:35b-a3b` | dedup | +0.0000 | 0 | 0 | 0 |
| `qwen3.6:35b-a3b` | language, twins | +0.0000 | 0 | 0 | 0 |
| `gemma4:26b-a4b` | framing | +0.0157 [-0.0044, +0.0472] | 20 | 5 | 0 |
| `gemma4:26b-a4b` | dedup | +0.0030 [+0.0000, +0.0102] | 2 | 0 | 0 |
| `gemma4:26b-a4b` | language, twins | +0.0000 | 0 | 0 | 0 |
| `qwen3:14b` | framing | +0.0036 [-0.0012, +0.0106] | 9 | 0 | 0 |
| `qwen3:14b` | dedup | -0.0008 [-0.0024, +0.0000] | 3 | 0 | 0 |
| `qwen3:14b` | language, twins | +0.0000 | 0 | 0 | 0 |
| `qwen3.6:27b` | framing | -0.0017 [-0.0126, +0.0118] | 10 | 6 | 1 (+0.0005) |
| `qwen3.6:27b` | dedup | +0.0012 [+0.0000, +0.0047] | 1 | 1 | 0 |
| `qwen3.6:27b` | language, twins | +0.0000 | 0 | 0 | 0 |

| Model | L7 loss | framing share of it | L6 (dedup) change | ceiling (L8) change, objects cut | backstop (L11) change, objects cut |
|---|---|---|---|---|---|
| `qwen3:8b` | -0.0089 | 100% | +0.0000 | +0.0000, 0 | +0.0000, 0 |
| `qwen3.6:35b-a3b` | -0.0110 | 100% | -0.0000 | +0.0000, 0 | +0.0000, 0 |
| `gemma4:26b-a4b` | -0.0157 | 100% | -0.0028 | +0.0000, 0 | +0.0000, 0 |
| `qwen3:14b` | -0.0036 | 100% | +0.0005 | -0.0066, 51 | +0.0000, 0 |
| `qwen3.6:27b` | +0.0017 | — (L7 gains lift) | -0.0012 | +0.0000, 0 | +0.0000, 0 |

The caps are read from the recorded stages, not replayed. The ceiling cut
only on `qwen3:14b`. The backstop never cut.

## Secondary: runs 1 and 2 (reported only)

| Model | L7 loss | framing delta [95% CI] | dropped | windows emptied | false positives (their delta) | dedup delta |
|---|---|---|---|---|---|---|
| `qwen3:8b` | -0.0107 | +0.0107 [-0.0077, +0.0401] | 21 | 20 | 0 | +0.0000 |
| `qwen3.6:27b` | -0.0058 | +0.0058 [-0.0041, +0.0204] | 21 | 10 | 1 (+0.0002) | +0.0006 |

## Decision rules applied

1. **Responsible rule: framing.** Its toggle restores at least 50% of the
   L7 loss on all three of the issue's models, and the bar needed 2 of 3.
   No other rule restores anything in L7. On every model, the L7 object
   count difference equals the framing drops, so no L7 drop is shared
   between rules.
2. **Benefit audit.** Every dropped object was classified from its type,
   title and description before any lift was computed. Over all 35 records
   there were 98 drops:
   - **framing**: 92 drops in all. 91 are target, an object naming the
     gathering itself: 90 `Event`s ("… Meeting", "Meeting Discussion") and
     one `Project` describing the meeting. 1 is a false positive: a `Decision` from `qwen3.6:27b`
     (IS1004b) that assigns the tasks due before the next meeting.
   - **dedup**: 6 drops, all exact duplicates, so all target.

   The titles are model output derived from AMI transcripts. Under the
   repository's rule for AMI-derived text, they are kept in the runs
   directory (`scores/drop_ablation_classes.json`), not here. That is a
   deviation from the registration's "published with the results"; only
   the counts are published.
3. **Verdicts.**
   - **framing, target class: sound.** The lift it costs is the metric
     crediting a meeting container's summary-like description.
   - **framing, false positive: fix issue #1575.** The one false positive
     has a positive lift delta (+0.0005 in round 1, +0.0002 at n = 2), so
     the registered bar is met. The drop matches the gathering word in any
     non-`Procedure` title, and a `Decision` that names a meeting is not a
     container. The fix is proposed, not made.
   - **twins, language: sound by absence.** Each dropped 0 objects on these
     English meetings, so neither costs lift here. This says nothing about
     the sources they were built for.
   - **dedup: sound.** All of its drops are exact duplicates, and the change
     is under ±0.003 on every model.

## Where the framing cost comes from (exploratory, not registered)

The framing cost was split by whether the drop empties its window, which
means the container was the window's only object. An empty window scores
lift 0.

| Model | framing delta | from emptied windows | from windows that keep other objects |
|---|---|---|---|
| `qwen3:8b` (r1) | +0.0089 | +0.0089 | +0.0000 |
| `qwen3.6:35b-a3b` (r1) | +0.0110 | +0.0108 | +0.0003 |
| `gemma4:26b-a4b` (r1) | +0.0157 | +0.0123 | +0.0034 |
| `qwen3:14b` (r1) | +0.0036 | +0.0000 | +0.0036 |
| `qwen3.6:27b` (r1) | -0.0017 | -0.0013 | -0.0004 |
| `qwen3:8b` (r1-r2) | +0.0107 | +0.0105 | +0.0003 |
| `qwen3.6:27b` (r1-r2) | +0.0058 | +0.0027 | +0.0031 |

Most of the L7 loss is the **one-object attractor (L2)**: a window whose
whole reply was the meeting container. The drop then leaves that window
empty, which #522 documents as the honest outcome. On `qwen3:8b`, all 9
round-1 framing drops, and 20 of 21 at n = 2, emptied their window.
Restoring the container would buy lift by storing framing as knowledge. The
lever is the extraction reply that returned only the container, not the
drop.

## Deferred

- **Downstream of the judge.** Every toggle changes the selector judge's
  input, and the judge's reply to that input was never generated. The effect
  of any rule on the judge, final and backstop stages needs judge calls, so
  it is not measured. Ollama was in use by #1558, and this issue's
  constraint was no model call.
- Nothing else was deferred. No embedding was missing from the cache.

## Aside

`_title_language("ES2099a")` votes `es`, so a title restating an `ES…`
meeting id would be dropped by the wrong-language rule before the twin rule
sees it. This never happened here (0 drops) and is not filed.

## Reproduce

```sh
uv run python evals/concept_model_screen/drop_ablation.py --self-test
uv run python evals/concept_model_screen/drop_ablation.py list
# classify scores/drop_ablation_list.json into scores/drop_ablation_classes.json
uv run python evals/concept_model_screen/drop_ablation.py score
```
