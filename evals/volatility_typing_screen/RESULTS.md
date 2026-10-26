# Results: the volatility-typing model screen (#1563)

**Verdict, by the registered rule: no model clears J.** The `volatility_typing`
task key is **not justified on this fixture**, no candidate is selected over
`qwen3:8b`, no confirmation run is owed, and nothing is adopted. This is a
measurement of each model against the resolved labels of two adjudicators
(kappa 0.544); it is not evidence that the registry default is right. Refs #1563,
part of #1556. No code, default, prompt, task key or ADR changes.

## Registration and run

- Registered at commit `b526c476` ([`PREREGISTRATION.md`](PREREGISTRATION.md),
  [`ADJUDICATION.md`](ADJUDICATION.md)). Arms, items, models, metrics, bars,
  vetoes and selection rule are as registered; nothing was changed after a scored
  call.
- Run window: pipeline start 2026-10-26 19:38:20Z, first scored call (`qwen3:8b`)
  19:40:13Z, `score` at 20:29:11Z, about 49 minutes of scored wall-clock against
  a 2.5 h budget. Models ran one at a time, server defaults, one Ollama job.
- 162 calls per model (27 items x 2 arms x 3 seeds), 810 in all. Stored runs live
  outside the repository (`openkos-eval-runs/volatility-screen/runs-*.json`).
- Digests, as registered (the harness stamps the full digest in-band and voids a
  run that differs):

| tag | digest (short ID) |
| --- | --- |
| `qwen3:8b` | `500a1f067a9f` |
| `gemma4:26b-a4b` | `001e5dafc3c7` |
| `qwen3:14b` | `bdbd181c33f2` |
| `qwen3.6:27b` | `bcbdbd4bcf86` |
| `qwen3.6:35b-a3b` | `a7eb95c53bcf` |

## Stage 0 (unscored)

The registration names Stage 0 (three timed calls per model, setting the
per-call timeout and replacing the assumed latencies) but gave it no harness
command, so it ran from a scratch script, reproduced verbatim in the appendix.
Per model: one untimed warm-up call, then three timed calls on the `default` arm
over three fixture items (first, middle, last). Nothing was stored.

| model | warm-up s | timed s | median s | failures | forecast min |
| --- | --- | --- | --- | --- | --- |
| `qwen3:8b` | 6.84 | 1.23, 1.19, 1.34 | 1.23 | 0 | 4.3 |
| `gemma4:26b-a4b` | 11.90 | 1.16, 1.76, 1.85 | 1.76 | 0 | 5.7 |
| `qwen3:14b` | 12.28 | 2.30, 2.61, 2.45 | 2.45 | 0 | 7.6 |
| `qwen3.6:27b` | 25.11 | 7.28, 8.56, 10.94 | 8.56 | 0 | 24.1 |
| `qwen3.6:35b-a3b` | 9.08 | 1.52, 1.46, 1.87 | 1.52 | 0 | 5.1 |

Per-call timeout: 60 s (the floor; 4x the baseline median is 4.9 s). Measured
screen forecast: 0.78 h, inside the 2.5 h budget, so no candidate was dropped
before a scored call. The registration's 1.2 h forecast assumed #1560's answer
latencies; a volatility reply is a short JSON object and ran faster, as the
registration expected.

## Score (as printed by `score`)

`run_volatility_screen.py score` was re-run on the stored runs for this document
and its output is byte-identical to the report produced at the end of the run.
The report follows, with its headings demoted one level.

#### Volatility-typing screen

Labels are the RESOLVED ones of two adjudicators (kappa 0.544): read
every number as a measurement against them, not as accuracy against a
truth. `strict` and `lenient` decide; `strict (1st)` is the sensitivity
analysis against the first author's labels and decides nothing.

floors (strict): registry default 0.481, commonest label 0.407; registry default against the first author's labels 0.778

| model | strict | lenient | strict (1st) | failures | median s | p95 s | anchor follow | stability |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| qwen3:8b | 0.481 | 0.778 | 0.568 | 0.000 | 1.20 | 1.74 | 0.383 | 0.667 |
| gemma4:26b-a4b | 0.593 | 0.901 | 0.617 | 0.000 | 1.50 | 2.10 | 0.210 | 0.630 |
| qwen3:14b | 0.370 | 0.568 | 0.346 | 0.000 | 2.17 | 3.41 | 0.346 | 0.593 |
| qwen3.6:27b | 0.457 | 0.716 | 0.481 | 0.000 | 10.49 | 15.47 | 0.235 | 0.481 |
| qwen3.6:35b-a3b | 0.346 | 0.593 | 0.395 | 0.000 | 1.87 | 2.59 | 0.296 | 0.148 |

### qwen3:8b: per-class recall and confusion (default arm)

recall: static 0.148, slow 0.848, volatile 0.333
| gold \ answered | static | slow | volatile | none |
| --- | --- | --- | --- | --- |
| static | 4 | 14 | 9 | 0 |
| slow | 2 | 28 | 3 | 0 |
| volatile | 1 | 13 | 7 | 0 |
agrees with the registry default: 0.605

### gemma4:26b-a4b: per-class recall and confusion (default arm)

recall: static 0.630, slow 0.848, volatile 0.143
| gold \ answered | static | slow | volatile | none |
| --- | --- | --- | --- | --- |
| static | 17 | 7 | 3 | 0 |
| slow | 3 | 28 | 2 | 0 |
| volatile | 0 | 18 | 3 | 0 |
agrees with the registry default: 0.691

### qwen3:14b: per-class recall and confusion (default arm)

recall: static 0.704, slow 0.182, volatile 0.238
| gold \ answered | static | slow | volatile | none |
| --- | --- | --- | --- | --- |
| static | 19 | 1 | 7 | 0 |
| slow | 26 | 6 | 1 | 0 |
| volatile | 6 | 10 | 5 | 0 |
agrees with the registry default: 0.284

### qwen3.6:27b: per-class recall and confusion (default arm)

recall: static 0.741, slow 0.303, volatile 0.333
| gold \ answered | static | slow | volatile | none |
| --- | --- | --- | --- | --- |
| static | 20 | 0 | 7 | 0 |
| slow | 18 | 10 | 5 | 0 |
| volatile | 5 | 9 | 7 | 0 |
agrees with the registry default: 0.444

### qwen3.6:35b-a3b: per-class recall and confusion (default arm)

recall: static 0.407, slow 0.303, volatile 0.333
| gold \ answered | static | slow | volatile | none |
| --- | --- | --- | --- | --- |
| static | 11 | 10 | 6 | 0 |
| slow | 14 | 10 | 9 | 0 |
| volatile | 7 | 7 | 7 | 0 |
agrees with the registry default: 0.395

### Decision (registered rule, applied mechanically)

- qwen3:8b: gain over the registry default -0.000 [-0.210, 0.198] -> key NOT justified
- gemma4:26b-a4b: gain over the registry default 0.111 [-0.037, 0.259] -> key NOT justified
  vs qwen3:8b: delta 0.111 [-0.074, 0.296], vetoes none -> not selected
- qwen3:14b: gain over the registry default -0.111 [-0.383, 0.148] -> key NOT justified
  vs qwen3:8b: delta -0.111 [-0.346, 0.136], vetoes none -> not selected
- qwen3.6:27b: gain over the registry default -0.025 [-0.247, 0.198] -> key NOT justified
  vs qwen3:8b: delta -0.025 [-0.259, 0.198], vetoes ['V2'] -> not selected
- qwen3.6:35b-a3b: gain over the registry default -0.136 [-0.346, 0.074] -> key NOT justified
  vs qwen3:8b: delta -0.136 [-0.346, 0.074], vetoes none -> not selected

## Reading

Everything here is read off the table above and the confusion matrices.

- **No model clears J.** Every 95% lower bound of the gain over the registry
  predictor is below 0. The best, `gemma4:26b-a4b`, is +0.111 with an interval of
  [-0.037, 0.259]: a point estimate in its favour that the 27-item fixture cannot
  distinguish from zero. `qwen3.6:27b` also trips V2 (median 10.49 s against 1.20
  s, 8.7x the baseline against a 2.0x limit); no other veto tripped.
- **The J bar was easier than it first looked, and still nothing cleared it.**
  The adjudication left the registry predictor wrong on 14 items (not 6), so a
  model that wins four of them with no loss clears J. That the bar was lowered
  before any data and no model reached it strengthens the null.
- **`volatile` recall is low everywhere**: 0.333 (`qwen3:8b`), 0.143
  (`gemma4:26b-a4b`), 0.238 (`qwen3:14b`), 0.333 (`qwen3.6:27b`), 0.333
  (`qwen3.6:35b-a3b`), on 21 volatile calls per model. For `gemma4:26b-a4b`, 18
  of those 21 were answered `slow`.
- **`qwen3:8b` matches the registry floor exactly, without being the registry.**
  It agrees with the registry default on 0.605 of calls yet scores 0.481, the
  registry floor (gain -0.000). The answers differ; their accuracy coincides.
- **Models err in different directions.** `qwen3:14b` and `qwen3.6:27b` answer
  `static` for most `slow` calls (26 of 33 and 18 of 33) and fall below the
  registry floor; `qwen3:8b` and `gemma4:26b-a4b` lean to `slow`. Lenient
  accuracy is higher for all, but the registry predictor is 1.000 lenient by
  construction (it matches one adjudicator on every item), so lenient cannot
  separate a model from it.
- **The first-author sensitivity does not change the verdict.** `strict (1st)`
  ranges 0.346 to 0.617 and no model reaches the registry's 0.778 on those
  labels, so under either labelling no model beats the registry predictor. That
  column carries no bootstrap and decides nothing.
- **Zero failures** for all five models on all 162 calls: no timeout, no
  unparseable reply, no off-vocabulary tier. The differences are accuracy, not
  reliability.
- **Stability across seeds is modest** (0.148 to 0.667); `qwen3.6:35b-a3b` is the
  least stable at 0.148.

### What this does not show

It does not show that the registry default is right: the screen measures a model
against the resolved labels, and the registry against the same labels, nothing
more; the labels carry kappa 0.544 and an unresolved anchoring question
([`PREREGISTRATION.md`](PREREGISTRATION.md)). It does not show that no model
could do better on another fixture: with 27 items and a paired interval about 0.3
wide, a real gain of the size seen for `gemma4:26b-a4b` could not be detected
here. Place, Entity and Insight remain uncovered. The registry default stays the
better-evidenced choice only in the registered sense: no alternative beat it on
this fixture. No adoption, config default or ADR follows.

## Appendix: the Stage 0 script, verbatim

Run once as `python stage0_volatility.py <repo>`, outside the repository. It
contacts the model.

```python
"""Stage 0 of the #1563 volatility screen (PREREGISTRATION.md, "Forecast and budget").

Per model: one untimed warm-up call (absorbs the load), then three timed,
unscored calls on the default arm. Nothing is stored in the runs directory.
Prints JSON: per-model latencies, median, the registered timeout
(4x the baseline median, floor 60 s) and the measured forecast.
"""

from __future__ import annotations

import json
import pathlib
import statistics
import sys
import tempfile

REPO = pathlib.Path(sys.argv[1])
HERE = REPO / "evals/volatility_typing_screen"
sys.path.insert(0, str(HERE))
import run_volatility_screen as rvs  # noqa: E402

core = rvs.core
items = core.load_fixture()
picks = [items[0], items[len(items) // 2], items[-1]]
models = [rvs.BASELINE, *rvs.CANDIDATES]
out: dict = {"models": {}}
for model in models:
    llm = rvs.OllamaClient(
        model=model,
        max_generation_tokens=rvs.DEFAULT_MAX_GENERATION_TOKENS,
        context_window=rvs.DEFAULT_CONTEXT_WINDOW,
        timeout=600.0,
        seed=0,
    )
    with tempfile.TemporaryDirectory() as tmp:
        warm, _ = rvs.run_item(llm, picks[0], "default", pathlib.Path(tmp) / "w")
        lat = []
        fails = []
        for n, item in enumerate(picks):
            row, _ = rvs.run_item(llm, item, "default", pathlib.Path(tmp) / f"t{n}")
            lat.append(row["latency_s"])
            fails.append(row["failure"])
    med = statistics.median(lat)
    out["models"][model] = {
        "warmup_s": warm["latency_s"],
        "timed_s": lat,
        "failures": fails,
        "median_s": med,
        "forecast_min": (162 * med + rvs.LOAD_S) / 60,
    }
    print(model, out["models"][model], flush=True)

base = out["models"][rvs.BASELINE]["median_s"]
out["timeout_s"] = max(60.0, 4 * base)
out["screen_forecast_h"] = sum(m["forecast_min"] for m in out["models"].values()) / 60
print(json.dumps(out, indent=2))
```
