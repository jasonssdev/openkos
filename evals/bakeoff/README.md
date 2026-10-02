# `bakeoff` — the shared driver for the #1269 model bake-off

Executes the plan pre-registered in the binding comment on #1269
("Pre-registration: numeric bars, latency budget, run plan"): eligibility
gates, then a staged knockout per role family. It calls the existing harness
CLIs; it reimplements none of them.

```
uv run python evals/bakeoff/run_bakeoff.py --plan            # dry run: plan + time forecast, no model call
uv run python -u evals/bakeoff/run_bakeoff.py --run          # execute (resumable)
uv run python evals/bakeoff/run_bakeoff.py --evaluate-only   # recompute verdicts + report from disk
uv run python evals/bakeoff/run_bakeoff.py --self-test       # model-free
```

Every output is written under `evals/bakeoff/results/` (`eligibility/`,
`cells/<harness>/<model>/r<n>/`, `report.md`); harness artifacts are moved from
each harness's own `results/` into the cell directory, so a run never leaves
files in a harness's tree.

## Modules

| file | what it holds |
| --- | --- |
| `bakeoff_spec.py` | candidates, eligibility limits, harness plan and every bar **as data**, each bar citing the pre-registration |
| `bakeoff_bars.py` | pure bar evaluation: roles, "no worse than baseline", vetoes, no-headroom, the knockout |
| `bakeoff_metrics.py` | one extractor per harness: stored output to the numbers the bars read |
| `bakeoff_ollama.py` | the local Ollama only: license, native context, `ollama ps` memory peak, model load |
| `run_bakeoff.py` | `--plan`, the run loop, resume, evaluation and the report |

Each has its own `--self-test`; `evals/run_self_tests.py` sweeps them.

## How a run goes

1. **Eligibility** per model, no quality run: license on the allowlist
   (Apache-2.0, MIT), native context at least 12288, and the `ollama ps` peak
   at `num_ctx` 12288 and 32768 with `bge-m3` loaded against the 24 GB budget.
   Only the 12288 peak gates; 32768 is reported with a "fits beside the
   baseline" column. A model that is not pulled is **pending**: the driver
   never downloads one. The budget is judged
   arithmetically on candidate + `bge-m3` at each `num_ctx`. If `bge-m3` is
   missing from the `ollama ps` snapshot (evicted on load order, not memory
   pressure) the driver reloads it with a minimal embed call and re-snapshots,
   at most twice; if it never co-resides, `bge-m3`'s size measured when it
   loaded alone is added and the figure is marked
   `estimated: bge-m3 not co-resident`. A candidate reading under 90% of its
   on-disk size (gemma4 reports about 1 GB for 8 to 19 GB of weights) is not
   its footprint: it is estimated as on-disk size + reported size +
   `bge-m3`, marked `estimated (disk + reported)`. An estimate is judged
   against 24 GB like a measurement but is never hidden: the eligibility JSON
   carries `memory_method` and `memory_estimated`, the plan note says
   `memory estimated: ...`, and the report prefixes the GB with `~` and has a
   method column. No on-disk size, or no `bge-m3` size, is an invalid reading
   (**pending**, never eligible). Records carry an `ELIGIBILITY_VERSION`; one
   written under older rules is re-measured on the next `--run`, so nothing
   has to be deleted by hand.
2. **Baseline** (`qwen3:8b`) on every harness at n=15, twice where the
   pre-registration says no 15-run baseline exists on the current fixture. The
   second repeat only ever runs in a **later invocation** (`--session`), so the
   two are two sessions and their difference is the spread.
3. **Per family, cheapest harness first** (Label, Judge, Generate), a candidate
   **drops at its first failed gate**; a candidate whose adjudication queue is
   unworked is **blocked**, not dropped, and resumes after the queue is worked
   and `--evaluate-only` is re-run.
4. **Write last**, for the top 2 Generate survivors only, with the attribution
   and sufficiency vetoes. If neither holds, Write stays on the baseline.

A `done` cell is skipped, a `failed` one is retried. `cell.json` records the
command, exit code and wall-clock per invocation, the model swap time, and the
identity stamp (harness commit, model digest, prompt hashes) lifted from the
harness's own `runs-*.json`; the report lists every stamp and flags a cell
whose stamp could not pin its result.

## Bar roles

| role | a failure | pre-registration source |
| --- | --- | --- |
| `precondition` | blocks the candidate | "unjudged titles = 0 after adjudication" |
| `gate` | **drops** the candidate for the family | rule 6.1 "no worse than baseline", and the vetoes |
| `win` | the role is not won; no drop | rule 6.2 primary metrics |
| `budget` | **drops** the candidate for the family, at the first harness where it is known at n=15 | rule 6.4, owner clarification 3 |

A bar the baseline already fails is **waived**, never a gate (owner decision 1).
A win that would need more than the metric's ceiling is "no headroom": a scalar
bar becomes a veto at "no worse than baseline", and an element of a quorum bar
cannot contribute a win. A metric a stored run cannot produce (for example the
direction block before #1272, or the field-shape classes before #1275) reads
NOT_MEASURED: never a pass, never a fail. A role reads `MEETS BARS` only when
no gate failed, nothing is unmeasured, no budget failed and a win holds; the
human adopts.

## Interpretation choices

The pre-registration states these rules without every number the code needs.
Each choice is a constant or a data field. The ones below were accepted by the
owner in the "Clarifications before the first run" comment on #1269 (see
`CLARIFICATION` in `bakeoff_spec.py`).

- **Stage order** is cheapest-first by the pre-registration's forecast minutes
  (sufficiency, adjudication, contradictions, auto_merge, decision_revisions),
  not the order the run plan lists them in.
- **Gates and blown latency budgets drop** (owner clarification 3 on #1269). A
  missed win does not: the same candidate may still win another role.
- **"Persistently wrong" cases** (contradictions): wrong in more than half the
  runs (`PERSISTENT_WRONG_SHARE`); the three cases it means were 15, 15 and 14
  of 15.
- **`<= 22 of 150`** is encoded as the rule it comes from (#1275 R1: at most 50%
  of baseline and at least 15 cells fewer), so a re-measured baseline moves the
  bar the way the rule would.
- **Transitivity violation** is read lower-is-better (`<= baseline + 0.03`); the
  pre-registration lists it with the `>= baseline - 0.03` controls.
- **Typed TP / FP and evaluative retention** use the raw (not high-confidence)
  contradiction rates.
- **Sufficiency** counts are per check (question x run), the quote arm only;
  "adjacent refused >= 148 of 150" is "at most 2 missed" at n=15.
- **Top 2 Generate** is by mean per-fixture pre-cap recall gain over the baseline.
- **Latency** is the median per-run wall-clock (per answer for attribution,
  per non-refused check for sufficiency, per fixture and the sweep total for
  extraction); the identity budget applies to `adjudication` only, and
  `decision_revisions` has none.
- **Memory units** are decimal GB, the unit `ollama list`/`ollama ps` print.

## What is not built (see the report on #1269)

- The **same-family arm** for each judge harness (self-preference hypothesis):
  **not measured this round** (owner clarification 1 on #1269). The harnesses
  score fixed hand-written fixtures, so there is no generator model to match a
  family against. The plan and the report both say so.
- Running a Qwen3.6 candidate **with thinking on**: not done. Qwen3.6 runs with
  thinking off only, as production does (`OllamaClient` sends `think: false`);
  no client change (owner clarification 2).
- **Pulling** models, and **adjudicating** the extraction queue: both are
  yours; the driver blocks on the second and reports the first.
