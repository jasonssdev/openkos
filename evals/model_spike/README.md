# Model spike: derived-object extraction comparison

A **manual** evaluation harness that measures which local 7-8B model gives the
best derived-object extraction for openkos. It is the MVP-1 roadmap deliverable
for choosing the default model (`config.DEFAULT_MODEL`).

This is a **spike**, not a test. It is NOT pytest, NOT wired into CI, and NOT
part of the shipped `openkos` package. It lives under `evals/` (sibling to
`examples/`, `tests/`, `src/`) precisely because AGENTS.md sec. 46 says to
"spike-then-test the fuzzy extraction parts": extraction quality is
non-deterministic model behavior, so we sample and score it here rather than
asserting on it in the deterministic test suite.

## What it measures

For each candidate model, it drives the **real** extraction pipeline
(`openkos.extraction.concept.extract_concept` over
`openkos.llm.ollama.OllamaClient`) across ground-truth fixtures, `--runs` times
each (models are non-deterministic), and scores:

- **schema_valid_rate** — fraction of runs that returned a non-empty list of
  valid `ExtractionResult`s (empty replies and backend errors count against it).
- **avg_object_count** — mean produced-object count vs. the target count
  (over-/under-extraction).
- **type_accuracy** — multiset recall of the target types.
- **anti_enumeration_score** — penalty for over-producing shallow stubs (the
  `call-with-maria` fixture is the probe: a good model extracts the rich
  `Decision` + the one salient `Person`, not a `Person`/`Entity` stub for every
  name mentioned).
- **avg_latency_s** — mean extraction latency per run.

It writes a markdown comparison report (`report.md`, plus a timestamped copy in
`results/`) with a per-model summary table, per-fixture per-run raw
`[type:title]` lists for human eyeballing, and a **recommendation** for the
default model.

### Ground-truth fixtures

Two `good-life-demo` raw sources with known-correct derived objects:

| Fixture | Raw source | Target |
| --- | --- | --- |
| `call-with-maria` | `examples/good-life-demo/raw/call-with-maria-2026-07-14.txt` | 2 → `Decision` + `Person` |
| `notes-on-enchiridion` | `examples/good-life-demo/raw/notes-on-the-enchiridion-2026-07-05.txt` | 2 → `Concept` × 2 (Stoicism, Epicureanism) |

## How to run

Requires a **running Ollama** with the candidate models pulled
(`ollama pull qwen3:8b`, etc.). Models not installed on the host are skipped and
noted in the report — the spike never crashes on a missing model or a backend
failure.

```sh
# Full spike with defaults (qwen3:8b, mistral:7b, gemma4:e4b; 3 runs each):
uv run python evals/model_spike/run_spike.py

# Override the candidate set and sample count:
uv run python evals/model_spike/run_spike.py --models qwen3:8b,llama3.1:8b --runs 5

# Prove the scoring/report logic on synthetic data (NO Ollama needed):
uv run python evals/model_spike/run_spike.py --self-test
```

Other flags: `--host` (Ollama host, else `OLLAMA_HOST`/default), `--timeout`
(per-call seconds, default 120), `--output` (report path).

## Scoring formulas

**type_accuracy** — multiset recall of the target types:

```
type_accuracy = sum_t min(produced[t], target[t]) / sum_t target[t]
```

Range `[0, 1]`. Missing target types lower it; wrong/extra types cannot raise
it. Target `{Concept: 2}`: `{Concept: 2}` → `1.0`; `{Concept: 1, Person: 1}` →
`0.5`; `{Person: 2}` → `0.0`.

**anti_enumeration_score** — over-production penalty:

```
over  = sum_t max(0, produced[t] - target[t])
score = 1.0                                if over == 0
      = target_count / (target_count + over)   otherwise
```

Range `(0, 1]`, strictly decreasing as excess or wrong-type stubs pile up.
Under-production is not penalized here (that is `type_accuracy`'s job). Target
`{Decision: 1, Person: 1}` (`target_count = 2`): ideal `{Decision, Person}` →
`1.0`; flood `{Decision, Person×3, Entity, Event}` has `over = 2 + 1 + 1 = 4` →
`2 / 6 = 0.33`.

The **composite** used to pick the default is the equal-weight mean of
`schema_valid_rate`, `type_accuracy`, and `anti_enumeration_score`, with
`avg_latency_s` as the tie-breaker.

## Notes

- The harness imports and drives the real pipeline; it does **not** reimplement
  extraction. `extract_concept` is a config-free leaf, so the harness constructs
  `OllamaClient` directly and never touches `openkos.config`.
- `evals/` is outside mypy's configured scope (`files = ["src", "tests"]`), so
  run `uv run mypy evals/` explicitly to type-check it.
