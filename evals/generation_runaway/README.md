# `generation_runaway` — which call runs away, and can a lower bound cut it? (#828)

`#828` reports that `kickoff` — a **631-byte** source — hits the shipped
8192-token `max_generation_tokens` ceiling in **3 of 10 runs** on `qwen3:8b`,
two of them after **222 s and 238 s**. Successful runs on the same bytes finish
in **20–46 s** and produce **8–14 objects**.

So this is not a source too large for the ceiling. It is a generation that
occasionally fails to terminate on a source that normally takes half a minute.

The authorized scope is **diagnostic + fail-fast**: record *which* call hit the
ceiling, and cut fast instead of burning 222 s for a result that is unusable
either way. This probe produces the measurement both halves need.

```bash
uv run python -u evals/generation_runaway/run_generation_runaway_probe.py --self-test
uv run python -u evals/generation_runaway/run_generation_runaway_probe.py --runs 10
uv run python -u evals/generation_runaway/run_generation_runaway_probe.py --fixture kickoff
uv run python -u evals/generation_runaway/run_generation_runaway_probe.py --rescore results/<file>.jsonl
```

**Use `-u`.** Without it Python buffers when the output is piped, and a sweep
whose slowest run takes four minutes looks hung.

## The constraint that shapes everything below

`OllamaClient.chat` sends `"stream": False`
(`src/openkos/llm/ollama.py`, in the request body it builds for `/api/chat`).
The whole reply arrives in **one** `read()` after generation has already
finished, so the client has no token stream to watch and no partial body to
abandon. It **cannot abort a runaway mid-generation**, and it cannot even
*notice* one: the first thing it learns about a reply is that the reply is over.

That leaves exactly one fail-fast lever:

> **a lower generation ceiling** — `options.num_predict` — which makes the
> backend stop at *B* tokens instead of 8192.

Nothing else in the client can shorten a runaway. A shorter `chat_timeout` is
not an alternative: it bounds how long the client *waits*, so it converts a
slow success into a failure just as readily as it shortens a real runaway.

Two consequences the report states in as many words:

1. **Fail-fast shortens the failure; it never rescues the run.** A reply cut at
   *B* is exactly as unusable as one cut at 8192 — `extract_json_items` returns
   `[]` on a mid-JSON truncation, so there is nothing to salvage at either
   bound.
2. **The lever costs something.** Every legitimate reply that would have
   generated *B* or more tokens gets cut too, and a cut extraction reply
   *raises*. Lowering the ceiling to shorten 3 failing runs can break runs that
   work today.

So the question this probe exists to answer is whether a **separating** *B*
exists at all. If it does not, the fail-fast half of #828 is refuted and only
the diagnostic half survives — and the report says `REFUTED`, in that word.

## No arms, deliberately

This is a **distribution measurement at shipped settings**, not an A/B. There is
no treatment axis, and building one would be waste: a call that generated 1180
tokens under an 8192 ceiling would have generated 1024 and been cut under a 1024
one, so the candidate bounds are swept **arithmetically over the stored calls**
(Q3 below). A second sweep at a lower ceiling cannot tell us anything the first
sweep's own token counts do not already say.

Keeping the arm axis out is also the cheapest defence against a mistake `evals/`
has already made once: a probe that shipped an **inert** arm and reported
numbers for a treatment that never ran. An axis that does not exist cannot be
inert.

## Fixtures: in-repo, no private corpus

Both fixtures are **imported** from
`evals/section_coverage/section_fixtures.py` — the exact bytes #793 captured
from the 0.2.8 E2E workspace, committed to this repository:

| fixture | size | why it is here |
| --- | --- | --- |
| `kickoff` | 629 chars / 631 B | the source #828 was filed on |
| `helios-overview` | 531 chars / 533 B | its neighbour in the same bundle, comparable size |

So this probe **reproduces on a clean checkout**: no `build_sources.py` step, no
git-ignored third-party corpus, nothing to fetch. (`evals/generation_ceiling`,
by contrast, skips fixtures that are absent and says so — there is nothing here
to skip.)

`helios-overview` rides along because "does the runaway follow the *source* or
the *ceiling*?" is worth an answer before anyone changes a global constant. If
it appears on both, it is a property of the model and the ceiling; if only on
`kickoff`, that is a fact about the text.

## Shipped settings, read rather than restated

The client is built with the packaged defaults **read from
`src/openkos/config.py`**:

- `num_predict` = `DEFAULT_MAX_GENERATION_TOKENS`
- `num_ctx` = `max(DEFAULT_CONTEXT_WINDOW, minimum_context_window(DEFAULT_MAX_GENERATION_TOKENS))`, the same expression `config.read_config` resolves

A literal `8192` in the probe would keep measuring 8192 after the packaged
default moved, and the sweep would silently be about a value nothing ships. The
window matters for the same reason and is not decorative: `num_ctx` bounds the
prompt and the completion *together*, so an unpinned client would hand
generation a 32768-token Modelfile window and could report a cut-off — or the
absence of one — that no shipped configuration would ever produce.

Each run stores the ceiling and window it actually sent, so `--rescore` on an
old sweep cannot relabel it with today's default.

## The three questions the report answers

### Q1 — which call runs away

A per-phase ledger: calls, caps, max/median generated tokens, max prompt tokens,
worst latency. The cap column is **split in two**, and the split is a finding in
its own right:

| column | meaning |
| --- | --- |
| `raised` | the cut-off **escaped** and failed the run — the failure #828 reports |
| `swallow` | the reply was cut off and the phase's own handler ate it |

Only `_extract_once` can produce a `raised`. Every other call swallows its own
backend failures by contract — `judge.select`'s D7 fail-closed rule, and the
broad `except` in `_reask_for_further_subjects` and
`_capture_further_participants`. A cut-off judge reply becomes
`judge_status="failed"` and the run keeps its **full unfiltered candidate set**;
a cut-off participant pass finds nobody.

Since #828 the last two NAME the cause on
`ExtractionReport.optional_call_failures` instead of discarding it, so a cut-off
bonus call is no longer silent outside this probe. It still does not raise —
nothing in this table changes, and the `swallow` column measures the same thing
it measured before.

Those runs still read as clean in the exception. **A table counting only raised
exceptions reports them as zero**, which is why `done_reason` is recorded per
call, off the raw response, and never inferred from the exception.

The raising call is derived from the ledger too: `OllamaGenerationCapped` aborts
`extract_concept_union` outright, so the raiser is necessarily the **last**
recorded call of the run, and it is a cut-off one. If it ever is not, the probe
reports the cap as unattributed rather than pinning it on an innocent phase.

### Q2 — is a runaway separable from a legitimate reply?

Per phase, the generated-token distribution of **non-capped** calls beside the
capped ones, and the question in one line:

> does a bound *B* exist with every legitimate reply strictly below *B* and
> every runaway at or above it?

The runaway side is **degenerate**: every capped call generated the ceiling's
worth of tokens, by definition of the cap. So separability collapses entirely
onto the other side, and the number that decides it is the **largest legitimate
reply observed** — printed per phase, because *that number is the floor any
candidate bound must clear*. The available band is `(max legit gen, ceiling]`,
and the report prints its width rather than asserting it is wide.

### Q3 — what would a candidate bound cost and save?

For each *B* in `1024, 1536, 2048, 3072, 4096`, computed from the **stored
calls** and therefore reproducible under `--rescore` with no model calls:

- **false cuts** — legitimate replies that generated at least *B* tokens, and so
  would have been cut off had the ceiling been *B*. This is the **refutation
  criterion**. Any non-zero value is a healthy run that bound would have
  destroyed; it is reported as such, never averaged into the saving, never
  smoothed into a rate, and every offending call is listed with its phase and
  its token count.
- **saved wall clock** — derived per call from **that call's own observed tokens
  per second**, never from an assumed throughput. The runaway calls *are* the
  slow ones; borrowing a healthy call's rate would overstate the saving by
  exactly the factor that matters.

Both terms are printed beside every ratio (`499.2s / 679.0s = 0.74`), never the
quotient alone — a bare ratio hides which of its two terms moved, and this repo
has already shipped a wrong 355× from a units mismatch that both terms beside
the quotient would have exposed on sight.

`>=`, not `>`: a reply needing exactly *B* tokens stops **at** the ceiling,
which Ollama reports as `done_reason: "length"`, and the client then raises on a
reply that had in fact just finished. Counting it as a false cut is the honest
reading of the boundary, and the self-test pins it.

## The self-test

`--self-test` runs with **no model** and must pass before any GPU second is
spent. It drives the real `OllamaClient` and the real pipeline over a scripted
transport, and every property it checks guards a *silent-success* failure —
one where the report still renders, with the wrong call named or the wrong bound
blessed:

1. **phase attribution** — the tags are checked *positionally* against the calls
   that produced them. A tag lagging its call by one would still yield a
   plausible-looking list of labels, and blaming the wrong phase is the whole of
   the diagnostic half getting it wrong.
2. **a swallowed cap** — the judge's first attempt is cut off and its retry
   answers (`judge.JUDGE_ATTEMPTS`, #795). The run must **succeed**, `capped`
   must stay `False`, and `capped_phases` must still name the judge. That
   scenario also puts a runaway and a legitimate reply in the *same* phase,
   which is the case a ledger splitting its two lists by membership rather than
   by `done_reason` would get wrong.
3. **a raised cap** — an extraction cut-off must set **both** `capped` and
   `capped_phases`, and the raiser must resolve to the extracting call.
4. **the bound arithmetic** — on a hand-built ledger whose numbers are known
   exactly: a 1500-token legitimate reply must count as a false cut at
   `B = 1024` and must not at `B = 2048`; the saving must come from the
   runaway's own 8192 / 200.0 s; and a reply that generated exactly *B* must
   count as a false cut, while a *runaway* that generated exactly *B* must save
   nothing — the other half of the same boundary. The verdict must print
   `REFUTED` when the distributions overlap and must not when they do not.
5. **zero exposure** — a sweep in which every call was cut off, so no bound was
   ever tested against a legitimate reply. The verdict must print
   `UNFALSIFIABLE`, must bless no bound, must not print `REFUTED` either, and Q2
   must withhold the band.

It exits non-zero listing **every** failure, not just the first.

Each check was mutation-verified rather than trusted for passing on the first
run: inferring `capped_phases` from the exception, flipping `>=` to `>`,
shifting the phase tag by one call, crediting a runaway at the boundary with a
saving, and dropping the zero-exposure guard each turn it red.

## Reading the result

`render_verdict` states both halves separately, because they are independent:

- the **diagnostic** half is answered by any sweep that saw a cut-off at all —
  recording `done_reason` per call names the runaway without a second run;
- the **fail-fast** half is answerable only if a bound separates. When none of
  the swept candidates is clean, the verdict says so in the word `REFUTED`, and
  reports the smallest bound that *could* separate on this sample so the reader
  can see how little room there was.

A sweep that finished **zero** replies answers the fail-fast half neither way,
and the verdict says so in the word `UNFALSIFIABLE`: every candidate scores zero
false cuts there because nothing legitimate was ever exposed to it. Zero
exposure is not a clean result, and no bound is blessed on it — Q2 withholds its
floor and band for the same reason.

When a bound does separate, the verdict still carries the caveat as a line of
its own rather than a footnote: the observed maximum is a **sample** maximum
over the finished replies this sweep happened to draw, not a distribution
ceiling. A bound set just above it will falsely cut some rate of healthy replies
that did not appear here, and the margin is the only thing that shrinks it.

It is deliberately a *reading*, not a pass/fail gate — #828 sets a constant, and
the honest output of a measurement feeding a constant is the evidence plus the
reading, not a boolean that hides which case it saw.

## Output

Raw runs go to `results/generation-runaway-<stamp>-<model>.jsonl`, one JSON
object per run with every call it made, and the rendered report lands beside it
as `.md`. Every written name is built from that literal `generation-runaway-`
prefix inside `results/`, so a sweep cannot land on this file or on any other
hand-written prose — by construction, not by a check.

## What was measured (2026-08-22)

Three sweeps against a clean worktree at `701ad5f`, `qwen3:8b`, Ollama 0.32.9,
shipped settings throughout.

Two of the three are committed to `results/`. The third ran against two
off-repo sources and its artefact is NOT committed -- only the row it produced
is recorded below, on the precedent
`evals/section_coverage/results/README-transcript-arm.md` set for the same
situation: an off-repo arm contributes its numbers to the record without
shipping a file that enumerates it.

| source | bytes | path | runs | runaways | largest legitimate reply |
| --- | --- | --- | --- | --- | --- |
| `helios-overview` | 533 | unchunked | 10 | 0 | 391 |
| `kickoff` | 631 | unchunked | 10 | **3** | 1874 |
| `large-03-skills-vs-tools` | 16 948 | unchunked | 10 | 0 | 1091 |
| `transcription2` (Spanish) | 9 688 | unchunked | 1 | 0 | 1209 |
| `medium-10-reunion-plataforma` (Spanish) | 12 718 | chunked | 1 | 0 | **2315** |

### The diagnostic half is answered

Only the two extraction passes ever reached the ceiling. Across 30 runs
`capturing further participants` never exceeded 149 generated tokens and the
judge never exceeded 132: both are short answers over long prompts, and the
ceiling caps replies, not prompts. **Zero swallowed cut-offs** were recorded —
every cap escaped and failed its run.

The runaway follows the **source**, not the ceiling. `kickoff` runs away 3 in 10
while its 533-byte neighbour from the same bundle runs away 0 in 10, on the same
model, the same settings and the same day. #828 could not separate those with two
sweeps of one fixture; one sweep with the neighbour alongside separates them.

### The fail-fast half: a bound exists on this sample, and was NOT taken

`B = 2048` would have cut nothing legitimate here and saved 0.71 of the wall
clock the cut-off runs burned. It was still rejected, for two reasons the table
above makes visible.

**The sample maximum kept climbing.** 391, then 1874, then 2315 — every source
added raised it. A maximum still rising after five sources does not bound the
population, and a bound set just above it is fitted to the sample.

**The costs are asymmetric.** A fail-fast only ever SHORTENS a failure: a reply
cut at *B* is exactly as unusable as one cut at 8192. A false cut CONVERTS A
SUCCESS INTO A FAILURE, and does it systematically — a source whose legitimate
reply needs more than *B* stops extracting every time, permanently, where it
used to work.

So `DEFAULT_MAX_GENERATION_TOKENS` is unchanged, and the real fail-fast is
tracked in **#830**: detect the repetition loop in a streamed reply, which is
the thing itself rather than length as a proxy for it.

### Two findings that fell out of the arithmetic

**The ceiling's calibration is stale.** `config.DEFAULT_MAX_GENERATION_TOKENS`
records 4154 tokens as the largest legitimate completed reply, measured
2026-08-06 on 17 KB prose, with 8192 chosen for roughly 2x headroom. Today the
same regime — `large-03-skills-vs-tools`, unchunked, just under
`_CHUNK_THRESHOLD` — tops out at 1091. The pipeline moved underneath that number.

**The prompt allowance is understated for the unchunked band**, measured here at
5398 prompt tokens (English) and 4155 (Spanish, at only 9 688 chars) against a
declared 4096. That is **#829**, including the case where the reported cause of a
cut-off reply is the wrong one.
