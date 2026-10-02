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
