# Does the answer's language cost it its attribution line? (#871)

**Result: the issue's language hypothesis did not survive measurement —
omission is QUESTION-shaped, not language-shaped. One anchor sentence was
measured to move every cell non-negatively and shipped; a question-shaped
English residual remains and is stated below.**

Needs Ollama for both the embedding model and the chat model.

```bash
uv run python -u evals/query_attribution/run_query_attribution_probe.py --self-test
uv run python -u evals/query_attribution/run_query_attribution_probe.py --arm baseline --runs 3
uv run python -u evals/query_attribution/run_query_attribution_probe.py --arm treatment --runs 3
```

## Why this probe exists

The 0.2.9 E2E reported: English short answer carries its `USED:` line, both
Spanish answers (medium, long structured) do not — so every Spanish `--save`
funnels into the unverified-provenance consent path. The stored
`evals/query_citation/` runs could not adjudicate this: they are 60/60
`reported` on Spanish questions, but their answers are SHORT (median 305
chars, max 897), and the wild failures live in a length regime those runs
never entered.

So this probe crosses the two confounded variables: **language** (mirrored
ES/EN corpora — translations of each other, each bundle queried only in its
own language, so a rate difference between language cells is attributable to
language, not content) and **length regime** (`short` pointed questions vs
`long` comprehensive structured requests). All questions are grounded;
`sufficiency_check` is off (it precedes synthesis and can only produce
NO_MATCH — it never touches the reply the attribution is parsed from, but a
false refusal would silently shrink one cell's n).

## What was measured (`qwen3:8b`, two pooled 3-run sweeps per arm, n=30/cell)

| cell | baseline compliance | treatment compliance |
| --- | --- | --- |
| `es-short` | 1.00 | 1.00 |
| `es-long` | 0.83 | **1.00** |
| `en-short` | **0.63** | 0.77 |
| `en-long` | 0.77 | 0.83 |
| overall | 0.81 | **0.90** |

Stamps: baseline `20260825T030540Z` + `20260825T031731Z`, treatment
`20260825T031149Z` + `20260825T032216Z`. Never compare arms measured on
different corpora or question sets; single 3-run sweeps of one arm differed
by whole questions flipping, which is why both arms were pooled to n=30
before any verdict was read.

### The language hypothesis is refuted

The worst baseline cell is ENGLISH short answers (0.63), while Spanish short
answers sit at 1.00 — the wild report's "2 of 2 Spanish absent, 1 of 1
English present" was a 3-answer sample, and the issue itself said a sample
of that size is a signal, not a rate. Absence concentrates in QUESTIONS, not
languages: one-line answers ("who was assigned as owner of the migration?" —
81 chars) and the longest structured enumerations ("list and elaborate every
architecture principle…") drop the line; the Spanish long summaries that do
drop it are the longest answers in their cell. The reported `--save` case
(Spanish long) is real — es-long 0.83 — just not language-caused.

### The adopted treatment

One sentence appended to the attribution instruction
(`retrieval/answer.py`), the `_LANGUAGE_ANCHOR` one-sentence shape, revised
after the baseline to name the MEASURED omission regimes (length extremes)
as well as language: *"Close with that line every time, in exactly that
form, however short or long the answer and whatever language it is written
in: the USED line is machinery, not prose — never translate it, never omit
it."* Every cell moved non-negatively; es-long — the reported `--save`
regime — went to 30/30.

### What remains, stated rather than implied

- **A question-shaped English residual** (en-short 0.77, en-long 0.83):
  "why are verbatim citations required?" and "list and elaborate…" still
  drop the line in some runs. The conservative fallback holds for them —
  `absent` cites the whole retrieval set, the CLI notices it, and `--save`
  routes through the unverified-provenance consent gate — so the residual
  costs disclosure friction, not silent bad provenance.
- **Per-question rates at n=6 are unstable.** Individual questions flipped
  between single sweeps of the SAME arm; read the cell and overall rates,
  not per-question ones.
- **One model.** Everything above is `qwen3:8b`.

## Fixture provenance

The Spanish corpus half is imported from
`evals/query_grounding/grounding_corpus.py`, never copied; the English half
is a document-by-document translation of it, and the runner's `--self-test`
pins the mirroring (cell counts, corpus sizes, bundle materialization) plus
the post-adoption invariants (production carries the anchor verbatim; the
treatment arm equals production, keeping the stored treatment runs
reproducible).
