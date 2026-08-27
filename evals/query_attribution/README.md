# Does the answer lose its attribution line? (#871, #887)

Two questions, measured in two passes over the same harness.

**#871 — does the answer's LANGUAGE cost it the line?** No. The language
hypothesis did not survive measurement; omission is QUESTION-shaped. One
anchor sentence was measured to move every cell non-negatively and shipped.

**#887 — does the CONTEXT SIZE cost it the line?** No, and the direction is
the opposite of the report's. Retrieving whole `Source` documents raises the
prompt about 3x and clips most of the retrieval set away, and compliance is
HIGHER there than on small whole documents. Post-#882 the size regime is not
where this defect lives.

Needs Ollama for both the embedding model and the chat model.

```bash
uv run python -u evals/query_attribution/run_query_attribution_probe.py --self-test
uv run python -u evals/query_attribution/run_query_attribution_probe.py --arm baseline --runs 3
uv run python -u evals/query_attribution/run_query_attribution_probe.py --arm treatment --runs 3
```

`--context small|large` restricts a sweep to one rung. `--unbounded`, paired
with a small `--context-window`, reproduces the PRE-#882 send; see
"What actually broke in the wild" below.

---

## Part 1 — #871: the language hypothesis (2026-08-25)

The 0.2.9 E2E reported: English short answer carries its `USED:` line, both
Spanish answers (medium, long structured) do not — so every Spanish `--save`
funnels into the unverified-provenance consent path. The stored
`evals/query_citation/` runs could not adjudicate it: 60/60 `reported`, but
on SHORT answers (median 305 chars, max 897), while the wild failures live in
a length regime those runs never entered.

So the probe crossed the two confounded variables: **language** (mirrored
ES/EN corpora, each bundle queried only in its own language) and **length
regime** (`short` pointed questions vs `long` comprehensive requests).

### What was measured (`qwen3:8b`, two pooled 3-run sweeps per arm, n=30/cell)

| cell | baseline | treatment |
| --- | --- | --- |
| `es-short` | 1.00 | 1.00 |
| `es-long` | 0.83 | **1.00** |
| `en-short` | **0.63** | 0.77 |
| `en-long` | 0.77 | 0.83 |
| overall | 0.81 | **0.90** |

Stamps: baseline `20260825T030540Z` + `20260825T031731Z`, treatment
`20260825T031149Z` + `20260825T032216Z`.

The worst baseline cell is ENGLISH short answers (0.63) while Spanish short
answers sit at 1.00. Absence concentrates in QUESTIONS, not languages: one-line
answers and the longest structured enumerations drop the line. The reported
`--save` case (Spanish long) is real — es-long 0.83 — just not language-caused.

**The adopted treatment** is one sentence appended to the attribution
instruction in `retrieval/answer.py`, following
`extraction.concept._LANGUAGE_ANCHOR`'s shape and naming the MEASURED
omission regimes (length extremes) as well as language: *"Close with that
line every time, in exactly that form, however short or long the answer and
whatever language it is written in: the USED line is machinery, not prose —
never translate it, never omit it."*

> **These four numbers are not comparable with Part 2's.** They were measured
> on a corpus that silently held ten documents rather than fourteen; see
> "The corpus was smaller than its own docstring said" below.

---

## Part 2 — #887: the context-size regime

`es-long` measured 30/30 above, and then BOTH wild Spanish long answers
dropped the line. Not variance — a regime the probe had never entered. Its
documents are constructed concepts, median 170 chars, max 1,656. The wild
retrieval set held two `Source` documents of 55,403 and 57,116 chars, 33x the
largest here. #871 closed the ANSWER-length regime and left the
CONTEXT-length one open: the same methodological miss, one level up.

### The regime is no longer what the issue described

#887 was filed before #882 shipped. #882 bounds the retrieval context to the
model's window, so a big document can no longer overflow the prompt. What it
produces now is a THIN, ELIDED EXCERPT. That is the regime this rung
reproduces, and it is the one that still exists.

### The `large` rung

`small` is the original corpus untouched. `large` replaces the three
`sources/*` bodies — and only those — with full-length transcripts of the SAME
meetings (`attribution_large_sources.py`). Concepts and decisions are
byte-identical across rungs, so every question stays grounded in both and the
question set is unchanged.

Measured through the production bound, worst-case retrieval set:

| rung | context sent | blocks excerpted | elision markers |
| --- | --- | --- | --- |
| `small` | 2,234–2,363 chars | 0 of 5 | 0 |
| `large` | 7,760–8,179 chars | 3 of 5 | 8–9 |

### Why ~7-9 KB transcripts and not 55 KB

Because the excerpt CONVERGES. Every block's excerpt length and
elision-marker count stop moving at 8x document size and are then identical at
16x, 32x and 64x. The wild sources are 6x to 7.7x these transcripts, so they
sit in that converged regime and would produce the same prompt.

The transfer has a price and the self-test states it rather than waving it
away: at 1x these documents are just BELOW the settling point, so the authored
rung sends 1.2% more context than the converged prompt in Spanish and 19.3%
more in English. `_CONVERGENCE_MARGIN` is a ceiling on that drift.

A first draft claimed the excerpt was size-INVARIANT. The self-test refuted
it twice — window boundaries move as a document grows, and tiling rotates
which repetition a middle window lands on, so neither the size nor the bytes
are invariant. Convergence is the weaker, true statement.

### What was measured (`qwen3:8b`, two pooled 3-run sweeps, n=30/cell)

Stamps `20260827T122923Z` + `20260827T124244Z`, arm `baseline` (production
untouched), identical pins in both, 0 failures and 0 no-matches anywhere.

| cell | `small` | `large` |
| --- | --- | --- |
| `es-short` | 1.00 | 1.00 |
| `es-long` | 0.93 | **0.97** |
| `en-short` | 0.80 | **0.87** |
| `en-long` | 0.80 | **1.00** |
| **rung** | **0.883** | **0.958** |

| rung | prompt tokens (median) | context sent (median) | blocks excerpted |
| --- | --- | --- | --- |
| `small` | 777 | 3,184 chars | 0.0 |
| `large` | 2,348 | 9,704 chars | 2.4 |

**The context-size hypothesis is refuted, and the direction is the opposite
of the report's.** Three times the prompt, most of the retrieval set clipped
away, 8-9 elision markers in the context — and no cell loses ground. Three of
four gain, `en-long` most of all.

The non-reported answers stay QUESTION-shaped, exactly as #871 found: on the
large rung they concentrate in "what was decided about the decision history?"
(2 of 6), "why are verbatim citations required?" (1 of 6) and "is the bundle
or the index the source of truth?" (1 of 6) — English short questions, #871's
own residual, unchanged by the context regime around them.

`nomatch` is 0 in all eight cells, so no cell traded attribution for
groundedness; the control below held.

### The groundedness control, and its cost

`bounded_text` keeps window 0 of every excerpt. Each transcript therefore
opens with an `Acuerdos alcanzados:` / `Agreements reached:` summary, the way
minutes are written — and that is load-bearing. Without a surviving statement
of the decisions, the excerpt drops the decisive turns, every large cell
collapses into refusals, and the probe measures groundedness under the name of
attribution. A pre-flight over the real budget showed exactly that: two of
three closing-recap anchors were excerpted away. `--self-test` now asserts
that every decisive anchor survives the bound, and every run reports
`nomatch` per cell so a collapse cannot hide.

The cost is worth stating twice, because it has two halves.

First, this rung measures attribution under a thin elided excerpt **whose
answer is present**. Whether even-coverage excerpting preserves the answer in
the wild is a separate question this harness does not answer.

Second, the axis is not size in a vacuum. A document cannot be made eight
times larger without its content changing, and these transcripts are real
minutes — agenda, discussion arcs, a leading agreements summary, a closing
recap — where the small rung's are eight-line sketches. So `large` means "a
full-length `Source` document of the same meeting", which is the thing the
wild retrieval set actually held, rather than "the same text, longer". The
knowledge is held constant and the question set is identical; the prose around
it is not, and no design could hold it constant.

---

---

## What actually broke in the wild

The measurement above says context size does not cost the attribution line
POST-#882. It does not explain the wild 2-of-2, which happened on the
UNBOUNDED prompt. #887 named the missing evidence precisely — *"proving that
needs to know where the cut falls relative to the attribution instruction"* —
so `--unbounded` was added to send it and find out.

Ollama does not refuse an oversized prompt. llama.cpp keeps a few head tokens
plus the LAST half of the window and discards the rest. The system prompt is
the FIRST message, and it is where the `USED:` instruction lives — so under
overflow the model is never told to emit the line at all.

### Measured (`--context large --unbounded --context-window 4096 --max-generation-tokens 2048`)

Stamp `20260827T125644Z`. The Spanish half ran to completion; the English
half was stopped at n=9 once both halves agreed — the checkpoint records
`complete: false`, and finishing it is one `--unbounded` run away.

| | bounded, `es` | unbounded, `es` (n=28) | unbounded, `en` (n=9) |
| --- | --- | --- | --- |
| compliance | 1.00 / 0.97 | **0.21** | **0.22** |
| context sent (median) | 9,704 chars | 26,671 chars | ~26,000 chars |
| prompt tokens (median) | 2,348 | **2,050** | **2,050** |
| chars per prompt token | 4.13 | **13.01** | **12.90** |
| blocks cited (mean) | — | 4.68 of 5 | 4.56 of 5 |
| `nomatch` | 0 | 0 | 0 |

Both languages land on the same numbers, which is what the mechanism
predicts: discarding the first message is not a property of the language the
answer is written in.

`prompt_tokens` pinned at 2,050 while `sent_chars` rose to 26,671 is the
truncation itself: the backend stopped reading and said nothing. That is what
`chars/ptok` is in the report for — 4.13 when the prompt is read, 13.01 when
two thirds of it are thrown away.

`nomatch` is 0, so this is not a groundedness collapse. The model answered,
at length, and dropped the machinery — and the conservative fallback then
cited **4.68 of 5 blocks on average, including the documents it never read**.
That is #882's false-provenance defect, reproduced.

### Two honest qualifications

**The unbounded rate is an over-estimate.** Two `es-long` probes raised
`OllamaGenerationCapped` and were counted as failures rather than as rows. A
capped reply is one that stopped before its closing line, so those two are
answers that could not have complied, dropped from the denominator. The real
collapse is worse than 0.21. That the truncated prompt also makes the model
run past its ceiling is its own small finding: it was answering without the
instructions that tell it how to stop.

**The two arms are not at matched pins.** The unbounded arm runs at
`num_ctx 4096` so the corpus overflows it; the bounded column is the
shipped-pins large rung from the sweeps above. A bounded arm at 4096 was run
first and returned 60 of 60 no-matches with every block omitted — not a
result about attribution but a defect, filed as #896: `OllamaClient` never
exposes `max_generation_tokens`, so `prompt_budget.reply_reserve` always falls
back to 8192 and the budget floors to zero below the default ceiling. Until
that is fixed, a matched-pins bounded arm cannot be measured. Its emission is
kept as `runs-baseline-20260827T125631Z-qwen3-8b.json`; it has no rendered
report because it crashed the renderer on a rung with no token counts at all,
which is now fixed and guarded.

---

## The corpus was smaller than its own docstring said

Four of the fourteen documents had never been in the index — not in this
probe's runs and not in the stored 2026-08-25 ones. `_write_corpus` built its
frontmatter with an f-string that interpolated the title unquoted, and four
documents are titled `Decisión: ...` / `Decision: ...`. A colon there is
invalid YAML: `_iter_docs` recorded a `parse_error`, `reindex` counted the
document `skipped`, and nothing read that number against an expected total.
The corpus was ten documents, and the missing four were the entire
`decisions/*` document type.

Production was never affected — `okf.dump_frontmatter` quotes correctly. The
harness had hand-rolled a second renderer beside it, which is the drift #883
closed elsewhere in this codebase. `_write_corpus` now calls the shipped one,
and `--self-test` asks the shipped READER whether every materialized document
parses. Counting files on disk cannot catch this and did not.

The same defect is live in three other harnesses (`query_citation`,
`query_entailment`, `query_sufficiency`) and is filed as #895. It is why
Part 1's numbers and Part 2's are not comparable: they describe different
corpora.
