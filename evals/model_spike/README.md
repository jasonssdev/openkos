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
  `Decision`, the apatheia `Concept`, and the one salient `Person`, not a
  `Person`/`Entity` stub for every name mentioned).
- **avg_latency_s** — mean extraction latency per run.

It writes a markdown comparison report (`report.md`, plus a timestamped copy in
`results/`) with a per-model summary table, per-fixture per-run raw
`[type:title]` lists for human eyeballing, and a **recommendation** for the
default model.

### Ground-truth fixtures

Two `good-life-demo` raw sources with known-correct derived objects:

| Fixture | Raw source | Target |
| --- | --- | --- |
| `call-with-maria` | `examples/good-life-demo/raw/call-with-maria-2026-07-14.txt` | 3 → `Decision` + `Person` + `Concept` (apatheia) |
| `notes-on-enchiridion` | `examples/good-life-demo/raw/notes-on-the-enchiridion-2026-07-05.txt` | 2 → `Concept` × 2 (Stoicism, Epicureanism) |

**The reference bundle is the ground truth, not this table.** An object belongs
to a fixture's target when extracting *that raw alone* should produce it. Read
the `provenance:` of `examples/good-life-demo/bundle/` to check.

`call-with-maria` read `2 → Decision + Person` until #377. That was
under-specified by exactly one object, `concepts/stoicism.md`, which cites this
call as `[2]` twice in its body for the two apatheia paragraphs. The
consequence was not cosmetic: the harness **penalized the correct answer**, since
a run producing all three scored `anti_enumeration_score = 2/3` instead of
`1.0`.

The mirror-image trap is `notes-on-enchiridion`. The decision object lists that
raw in its `provenance:` too, so it looks like it should also target a
`Decision`. It must not: provenance means "this object's content draws on that
source", not "extracting that source alone yields this object". The decision is
*made* in `call-with-maria`; the Enchiridion notes only supply the
dichotomy-of-control background it cites.

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

## Sibling harness: the title-anchor A/B (`run_title_ab.py`)

`run_spike.py` answers "which model?". `run_title_ab.py` answers a different
question, for [#377](https://github.com/jasonssdev/openkos/issues/377): **is the
`SOURCE TITLE:` value what collapsed extraction to one object per source?**

It holds corpus, model, `_SYSTEM_PROMPT` and sample count constant and varies
exactly one thing, the title handed to the user turn:

| Arm | Title sent | Reproduces |
| --- | --- | --- |
| `h1` | `derive_source_title(raw)` → `"Call with Maria Salazar — 2026-07-14"` | v0.2.1 (today) |
| `stem` | `titleize(path.stem)` → `"call with maria 2026 07 14"` | v0.2.0 |
| `none` | no `SOURCE TITLE:` line at all | the control |

Why those arms: `git diff v0.2.0 v0.2.1 -- src/openkos/extraction/` is **empty**,
so the prompt cannot explain a 3→1 regression between them. What landed in
v0.2.1 alone is `7f29cdd` (#248), which changed that title and feeds it to the
extraction prompt.

```sh
uv run python evals/model_spike/run_title_ab.py                       # all three arms
uv run python evals/model_spike/run_title_ab.py --model qwen3:8b --runs 5
uv run python evals/model_spike/run_title_ab.py --arms h1,none
uv run python evals/model_spike/run_title_ab.py --self-test           # no Ollama needed
```

### Measuring an external corpus (`--corpus`)

The two `good-life-demo` fixtures **never reproduced the 3→1 regression** — in
the first run, `call-with-maria` yielded exactly one object in all three arms,
including the arm that reproduces v0.2.0's title. The regression #377 documents
lives on its 15-source tutorial corpus, which is not in this repository.

`--corpus DIR` loads every `.md`/`.txt` under `DIR` as an **unlabeled** source:

```sh
uv run python evals/model_spike/run_title_ab.py --corpus ~/path/to/raw/
uv run python evals/model_spike/run_title_ab.py --corpus ~/path/to/raw/ --with-fixtures
```

No target types are invented for those sources. #377's evidence is *counts*
("3 objects under v0.2.0, 1 under v0.2.1"), so counts are what gets measured:
`avg_objects`, `twin_rate`, the per-source count table, and the type-conditional
probe all apply; `type_acc` and `anti_enum` exclude unlabeled sources rather
than reporting a meaningless number. Declaring guessed targets would manufacture
a ground truth nobody verified — the exact defect this harness already had to
fix once.

A run over 100 calls prints a time forecast before it starts.

### The type-conditional probe

Every report ends with one extra line, pooled across arms:

> runs landing on a named-entity type average **N** objects; runs landing on
> `Concept`/`Entity` average **M**

It exists because the collapse is **not global**. Seven of the nine types read
*"the source is fundamentally about ONE specific, named X"*; `Concept` and
`Entity` are exempt (`concept.py:38-63`, and the code's own comment at
`concept.py:68`).

The corpus run measured a **hard cap on the named-entity side**: n=24, mean
exactly 1.00, max 1 — every single run produced one object, zero variance. The
exempt side averaged 1.54 with spikes to 5.

So the probe reports **spread, not a difference of means**. That 1.00-vs-1.54
gap failed an earlier `≥1.0` mean threshold and printed "does NOT split", when
zero variance on one side *was* the finding. It also refuses to read the cap as
the cause while the exempt side still mostly yields one object — the wording
explains those capped runs, not the collapse as a whole. The probe declines
entirely when only one side of the line is present.

Two **independent** signals decide it, because the anchor turned out to act on
more than one thing:

- **`multi_obj_rate`** — fraction of runs producing ≥2 objects. How *often* the
  model enumerates. The primary signal.
- **`twin_rate`** — fraction of produced objects whose title merely restates the
  SOURCE TITLE the arm sent. *What* it produces. `none` reads `0.00` by
  construction, not by merit.

Either one moving implicates the anchor; both flat clears it.

**`avg_objects` is reported but is not the criterion**, and the first corpus run
is why. The distribution is bimodal — the mode is 1, the tail spikes to 3–5 — so
the mean barely moves when the rare event doubles in frequency:

| Arm | mean | multi-object rate | twin_rate |
|---|---|---|---|
| `h1` (v0.2.1) | 1.22 | 0.08 (3/36, 2 sources) | **0.30** |
| `stem` (v0.2.0) | 1.50 | **0.17** (6/36, 5 sources) | 0.11 |

An earlier version of this harness applied a 0.5 threshold to that 0.28 mean
spread and reported the anchor innocent, while its own `twin_rate` said the
opposite. The clearest single case:

```
[h1]   05-workflow → Concept:Workflow
[stem] 05-workflow → Concept:Explore, Plan, Code, and Commit Workflow
```

Same document, same prompt. Under `h1` the model echoes the heading; under
`stem` it has to read the document and name what it found. A mean cannot see
that.

The report ends with a **Verdict** that reads both signals, prints the raw event
counts so small `n` stays visible, and flags itself **underpowered** when fewer
than 10 multi-object runs are pooled across arms. Writes `report-title-ab.md`
plus a timestamped copy under `results/`.

The `none` arm swaps `concept._build_messages` for a title-free builder and
restores it in a `finally`, because proposal slice 1 writes no production code.
It removes the *anchor*, not the information: both raws open with their own
title line, which still reaches the model inside `SOURCE TEXT:`.

## Notes

- The harness imports and drives the real pipeline; it does **not** reimplement
  extraction. `extract_concept` is a config-free leaf, so the harness constructs
  `OllamaClient` directly and never touches `openkos.config`.
- `evals/` is outside mypy's configured scope (`files = ["src", "tests"]`), so
  run `uv run mypy evals/` explicitly to type-check it.
