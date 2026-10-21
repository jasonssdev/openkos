# Volatility-typing screen (#1563)

A measurement harness for the `volatility_typing` task key. **No model has been
run through it.** It exists so a screen can be pre-registered and run later;
[`PREREGISTRATION-DRAFT.md`](PREREGISTRATION-DRAFT.md) is a draft, not a
registration. Part of #1556.

## What the task decides

`resolution/volatility_typing.py` (`suggest_volatility`, reached through
`suggest-volatility` and `curate`) makes **one chat call per concept type**
present in a bundle. The model sees the type name, the type's current default
tier and up to five concept bodies of that type (sorted by id, each cut at 1,000
characters), and answers one JSON object `{"tier": ..., "rationale": ...}` with
`tier` one of `static`, `slow`, `volatile`. A reply that is not that object, or
names another tier, degrades to "no suggestion" (fail closed); it is never
repaired. A suggestion is only proposed when it differs from the tier the type
is already on.

The tiers are ADR-0007's, and they set a lint window: `static` is never flagged,
`slow` 90 days, `volatile` 7 days. The registry defaults are `static` for
Place, Event, Decision and Source; `slow` for Concept, Entity, Person and
Organization; `volatile` for Procedure, Project and Insight.

This is **not** the `freshness: timeless | snapshot | pointer` axis of
`docs/knowledge-object-model.md`. That one says how a single fact is
*recorded*; this one says how fast a type's knowledge *decays*. Nothing here
touches the former.

## Method

Each fixture item is a one-type bundle written to a scratch directory and put
through production's own `suggest_volatility`, so the sampling, truncation,
prompt and parser measured are the shipped ones. Two arms, both built from
production's own arguments (no prompt is edited):

| arm | the prompt's `CURRENT DEFAULT TIER` line |
| --- | --- |
| `default` | the type's registry default, as shipped |
| `wrong-anchor` | a tier that is neither the label nor its flagged alternative |

The second arm measures anchoring: the shipped prompt names a tier next to the
question, so a model that simply echoes it scores the registry predictor's
accuracy without reading a body.

Numbers (all in `screen_core.summarize`):

- **accuracy**, strict (the primary label) and lenient (also the flagged
  alternative); the mean over items of each item's mean over runs;
- **per-class recall and the gold-by-answered confusion matrix** (with a
  `none` column for failed calls);
- **failure rate** by kind: `backend`, `timeout`, `invalid_reply`,
  `no_question`; a failed call scores wrong, never skipped;
- **latency**, median and p95, over every call;
- **anchor-follow rate** on the `wrong-anchor` arm, **agreement with the
  registry default**, and **stability** across runs;
- the two model-free floors: *always the registry default* and *always the
  commonest label*.

## The fixture (`fixture.json`)

27 items, 62 documents, all quoted verbatim from material already in the
repository (and `--self-test` fails if a quotation stops matching its source,
because a label was decided on that text):

| source | items |
| --- | --- |
| `examples/good-life-demo/` | v01-v04 |
| `evals/query_attribution/` English corpus | v05-v08 |
| `evals/edge_typing/fixtures.py` | v09-v20 |
| `examples/extraction-corpus/` medium-10 meeting (Spanish) | v21-v27 |

Class balance: **9 static, 12 slow, 6 volatile.** 16 items carry a flagged
alternative label. Seven are Spanish. The meeting's types are the corpus ground
truth's; for v15, v17 and v18 (`*` below) the source fixture typed the
documents `Concept` and **the author assigned** the type, because the documents
are plainly a standing group, a procedure and a past incident. There is no Place,
Entity or Insight material in the repository, so those types are **not
covered**; nothing was invented to fill them.

### How a label was decided

One rule, taken from ADR-0007: *how long until a reader should re-check the
facts in these bodies?*

- `static`: facts about the past or a fixed record; the passage of time does not
  make them stale (a decision taken, an event that happened, what a source says).
- `slow`: durable definitions, identities, structures and roles that change over
  months to years.
- `volatile`: current state, status, process steps, measurements or schedules,
  anything the bodies themselves date or that would plausibly be out of date
  within weeks.

The label follows the **content shown**, not the type. Where a second tier is
also defensible, `alt_label` names it and `ambiguous` is true: the item is then
scored strict (primary only) and lenient (either), and both are reported. The
per-item reasoning is in the `how` field of each item.

| item | type | lang | docs | label | alt | registry default | source |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v01 | Concept | en | 2 | slow | static | slow | good-life-demo |
| v02 | Decision | en | 1 | static | slow | static | good-life-demo |
| v03 | Person | en | 1 | volatile | slow | slow | good-life-demo |
| v04 | Source | en | 2 | static |  | static | good-life-demo |
| v05 | Concept | en | 4 | slow |  | slow | query_attribution EN |
| v06 | Concept | en | 3 | slow |  | slow | query_attribution EN |
| v07 | Decision | en | 4 | static |  | static | query_attribution EN |
| v08 | Source | en | 3 | static |  | static | query_attribution EN |
| v09 | Concept | en | 4 | slow |  | slow | edge_typing |
| v10 | Concept | en | 4 | slow | volatile | slow | edge_typing |
| v11 | Concept | en | 2 | static | slow | slow | edge_typing |
| v12 | Concept | en | 2 | slow | static | slow | edge_typing |
| v13 | Concept | en | 2 | slow | static | slow | edge_typing |
| v14 | Person | en | 2 | slow | volatile | slow | edge_typing |
| v15 | Organization* | en | 3 | slow |  | slow | edge_typing |
| v16 | Project | en | 1 | static | volatile | volatile | edge_typing |
| v17 | Procedure* | en | 3 | volatile | slow | volatile | edge_typing |
| v18 | Event* | en | 2 | static |  | static | edge_typing |
| v19 | Concept | en | 4 | slow |  | slow | edge_typing |
| v20 | Concept | en | 2 | volatile | slow | slow | edge_typing |
| v21 | Person | es | 4 | slow | volatile | slow | medium-10 |
| v22 | Event | es | 1 | static |  | static | medium-10 |
| v23 | Concept | es | 1 | volatile | slow | slow | medium-10 |
| v24 | Concept | es | 1 | volatile | slow | slow | medium-10 |
| v25 | Concept | es | 1 | slow | volatile | slow | medium-10 |
| v26 | Decision | es | 2 | static | slow | static | medium-10 |
| v27 | Procedure | es | 1 | volatile |  | volatile | medium-10 |

### What the fixture cannot show (read before any number)

- **The labels are one author's and have never been adjudicated.** A second
  adjudicator, blind to this one's labels and to the registry, must label every
  item before any adoption decision rests on this fixture. Until then a result is
  a measurement against one person's reading.
- **The rubric is the registry's own.** It comes from ADR-0007, so every item
  *without* a flagged alternative agrees with the registry default. The registry
  predictor scores **0.778 strict (21 of 27) and 1.000 lenient**; it is wrong only on
  the six items where the author's reading of the content differs from the type
  default (v03, v11, v16, v20, v23, v24), and every one of those is flagged. The fixture therefore
  discriminates a model from the registry on **six items**, and they are the six
  least certain labels.
- **The power to say "the key is justified" is thin.** With 27 items a model has
  to beat the registry predictor with a positive 95% lower bound; on this fixture
  that needs at least four of the six discriminating items and **no** loss on the
  other 21 (checked with the harness's own bootstrap: win 4 lose 0 gives a lower
  bound of +0.037; win 6 lose 1 gives 0.000). A model that loses a single item
  elsewhere cannot clear it. Expect "not justified" to be the likely result for
  any model, and read it as "this fixture cannot show it", not "the model is no
  good".
- **Types that carry a type-level prior** (Place, Entity, Insight) are absent.
- Two documents (v01 Stoicism, v02 the decision) are longer than production's
  1,000-character cut; only their `Related` / `Citations` tails are lost, and the
  labels do not depend on them (`--self-test` pins exactly these two).
- The bodies are short, constructed or meeting-derived text. The real task sees
  five compiled bodies of a type from a user's bundle, usually longer.

### A product question the labelling surfaced

The registry gives volatility **per type**, but the content decides it. A
`Person` whose body is a dated current post (v03), a completed `Project` (v16),
and a `Concept` that reports the status of an open problem (v23, v24) are each
on the wrong side of their type's default. If that is the intended behaviour,
fine: the per-concept `volatility` override and `type_tiers` exist for it. If a
model is meant to catch it, the task as shipped (one answer per *type*, from
five bodies) cannot tell a single outlier from the type, and the item-level
cases here would be measuring something production does not ask. That is a
product decision for the maintainer, not something the harness should settle.

## Usage

Always with `OLLAMA_HOST` pointing nowhere unless a run is intended:

```sh
# model-free
OLLAMA_HOST=http://127.0.0.1:1 uv run python evals/volatility_typing_screen/run_volatility_screen.py --self-test
OLLAMA_HOST=http://127.0.0.1:1 uv run python evals/volatility_typing_screen/run_volatility_screen.py forecast --runs 3
# a model (needs a local Ollama; stamped, cached per row, resumable)
uv run python -u evals/volatility_typing_screen/run_volatility_screen.py run --model qwen3:8b --runs 3
uv run python evals/volatility_typing_screen/run_volatility_screen.py score
```

`run --dry-run` prints the same forecast as `forecast`. Runs are stored under
`--runs-dir` (default `~/Dev/Projects/openkos-eval-runs/volatility-screen/`,
outside the repository), one `runs-<model>.json` each, with the identity stamp
(harness commit, model digest, prompt hash) in-band.

`screen_core.py` holds everything model-free and is unit-tested
(`tests/unit/test_volatility_screen_core.py`); `run_volatility_screen.py` owns
the model client, the stub backend of `--self-test` and the report.
