# Pre-registration: hosted providers vs the local default on the concept screen (#1767)

**Status: registered on merge to `main`.** No hosted call may be sent before
that merge. A change after the merge is a new registration, not an edit to
this one. The owner settled every decision on 2026-11-23; they are recorded in
"Owner decisions" below and bind the run.

## Question

Do hosted chat models extract concepts better than the local default
`qwen3:8b` on the concept-extraction screen (#1557)? Which of the two defects
the 2026-11-23 smoke test showed (#1767, #1768) is real at this n:

- **Unevidenced objects.** A final object whose written text quotes no line of
  the source (production's own `_unevidenced_titles` predicate, #801). Hosted
  models paraphrase: 7 of 7 hosted objects in the smoke test carried no
  verbatim source line.
- **Within-run near-duplicates.** Two objects of one meeting-run for the same
  subject (the Anthropic arm wrote two apatheia Concepts).

This is a **measurement**. It adopts nothing and changes no default
(`DEFAULT_MODEL`, `DEFAULT_TASK_MODELS`, any prompt). OpenKOS stays local-first:
a hosted model is an opt-in backend, and this result can only inform #1768 and
the provider guide (#1769).

## What already exists, and what is new

The #1557 harness already expresses a hosted arm: `run --backend
openai-compatible --base-url --served-model --digest` drives the production
`OpenAICompatibleClient` (`screen_pipeline.openai_compatible_sender`). Four
things it could not do are added, each with a model-free self-test:

1. **Authentication.** The sender never passed a key. It now takes it from
   `OPENKOS_OPENAI_API_KEY`, only for a non-loopback, non-private host, and the
   key is never written to a cache entry, a record, a stamp or the ledger.
2. **No seed.** `--no-seed` omits `seed` from the request (some providers
   reject it; #1766 verified it only for some models). The seed still enters
   the cache key.
3. **A spend guard** (`SpendGuard`, `--max-calls`, `--max-usd`, `--price-in`,
   `--price-out`). Calls and spend are counted before each send from a ledger
   that survives restarts. At the ceiling the run stops with `BudgetExceeded`,
   a `SystemExit`, so no `except Exception` can score it as an empty window.
   The overshoot is at most one call. A hosted run refuses to start without all
   four flags, `--timeout-from`, a key in the environment, and without
   `--prose`.
4. **Provider errors are not cached** on a hosted arm, so a re-run sends the
   call again instead of replaying a 503 forever. The record also carries
   `final_unevidenced` (per final object), and `hosted_screen.py` adds the
   forecast, the cache-only `replay`, and the `metrics` below.

Lift, G1, V1, V4, V5 and the bootstrap are the unchanged #1557 code
(`run_concept_screen.py score`).

## Arms

The reference is `qwen3:8b`, its #1557 records for runs 1 and 2 on pool DA,
read from cache and not re-run (digest `500a1f067a9f`, harness `ebbde16f`). A
cache-only replay at the current head reproduced all ten recorded final lists
(titles, in order) with Ollama's host poisoned, so the production prompts on
the screen path have not drifted since.

Every hosted arm sends the same production calls through the OpenAI-compatible
client: `stream: false`, `max_completion_tokens` 8192 (the shipped ceiling,
#1774), **no `temperature`, no `seed`, no reasoning-effort field** (provider
defaults), the registered timeouts of `qwen3:8b` (146 s per window call, 438 s
per whole-source call, `--timeout-from qwen3:8b`), and the client's own bounded
retry of 429/5xx (#1771, 3 attempts). The selector judge, re-ask and
participant capture run on the arm's own model, as they do for every local arm.

| Arm | Served model | Endpoint | Kind | Note |
|---|---|---|---|---|
| `qwen3:8b` | `qwen3:8b` | local Ollama | reference | cached runs 1 and 2 |
| `openai-gpt-4.1-mini` | `gpt-4.1-mini` | `https://api.openai.com` | non-reasoning | in the smoke test |
| `openai-gpt-5-mini` | `gpt-5-mini` | `https://api.openai.com` | reasoning | #1774 makes it callable |
| `anthropic-claude-haiku-5-5` | `claude-haiku-5-5` | `https://api.anthropic.com` | non-reasoning | in the smoke test |
| `openai-gpt-6-luna` | `gpt-6-luna` | `https://api.openai.com` | unknown, forecast as reasoning | on the owner's key |
| `openai-gpt-5.5` | `gpt-5.5` | `https://api.openai.com` | reasoning | optional, 9x the others |
| `google-gemini-3.8-flash` | `gemini-3.8-flash` | `https://generativelanguage.googleapis.com/v1beta/openai` | thinking | conditional on availability |

Cache identity of a hosted arm: label above, `--digest
<provider>:<served-model>:<date of run>`. A provider alias can move under a
fixed name, so a hosted arm is pinned by date, not by content, and is
reproducible only from its cache.

## Items

Pool DA, as #1557 round 2: ES2015a, IS1004b, TS3009c, ES2015d, IS1004d; 41
windows, 37 holding at least one assigned gold sentence; runs 1 and 2 (n = 2).
The registered window hashes in `windows.json` are checked before every run.
The prose fixtures are **not** sent: four of the five are real-world material
that is not a public corpus (the harness refuses `--prose` on a hosted host).
AMI is CC BY 4.0, so no user data leaves the machine. V3 (Spanish title
language) is therefore not evaluated for any hosted arm.

## Metrics

Embeddings (`bge-m3`) and the G1 judge (`gpt-oss:20b`) stay local.

- **Primary: window-level coverage lift**, the paired delta against `qwen3:8b`
  per window, averaged over runs, two-stage bootstrap (meetings, then windows;
  10,000 resamples; seed 42). Unchanged from #1557.
- **G1 precision guardrail**: pooled precision difference against `qwen3:8b`,
  same bootstrap; the 95% lower bound must be at least -0.05.
- **Vetoes as registered in #1557**: V1 failures (arm rate at most the
  reference's + 0.02 and at most 0.05 absolute; the reference is 2 of 82),
  V4 volume (objects per meeting-run within [0.5, 1.5] x the reference's 5.00,
  so [2.50, 7.50]), V5 verbatim copying (at most the reference's + 0.10).
- **Unevidenced-object rate**: final objects flagged `final_unevidenced` over
  all final objects, pooled, n of TOTAL. Difference against the reference, with
  a bootstrap that draws the five meetings with replacement, the same draw for
  both arms.
- **Within-run near-duplicate rate**: final objects with another final object
  of the same meeting-run at bge-m3 cosine >= 0.85 (the `cut_redundancy.py`
  threshold; 0.80 and 0.90 are sensitivity only), over all final objects, n of
  TOTAL. Difference and interval as above.
- **Reported, not decided on**: window outcomes by cause, spend and calls per
  arm from the ledger, tokens per call, wall time, objects per meeting-run,
  judged-out counts.

The reference's unevidenced rate, read from its cache-only replay before
registration, is 5 of 50 (0.100). Its near-duplicate rate was not computed
(needs the embedder).

## Completeness and failure handling

A hosted failure has two sources, and they are kept apart.

- **Provider failure**: a window whose calls ended in `error` or `not_called`
  after the client's retry. It says nothing about the model and is never
  treated as zero recall. Provider errors are not cached. After a pass, delete
  the record of each affected meeting-run and run again: cached calls replay
  free, failed ones are sent again. At most **two** such re-runs per arm. An
  arm that still has any provider-failure window is **INCOMPLETE**: its numbers
  are reported, flagged, and it cannot be labelled BETTER.
- **Model failure**: `timeout`, `capped`, `unparseable`, as #1557. These are
  cached, count in V1, and are not re-sent. A reasoning model that spends the
  8192 tokens before answering is a `capped` model failure, and is billed.
- If the first 10 calls of an arm all end in provider failure, stop the arm and
  report it **UNAVAILABLE on <date>**; this is the expected outcome for Google
  today and is not a result about the model.
- Hitting the spend or call ceiling stops the run. Windows not yet run are
  reported as not run, never as failures; the arm is INCOMPLETE.

## Decision rule (fixed before any hosted call)

Per hosted arm, in this order, on pool DA at n = 2:

1. **INCOMPLETE** if any provider-failure window remains or the ceiling
   stopped the run.
2. **VETOED** if V1, V4, V5 or G1 fails (named in the result).
3. **BETTER than local** if the window lift delta's 95% lower bound is above 0.
4. **WORSE than local** if its 95% upper bound is below 0.
5. **NOT DISTINGUISHABLE** otherwise.

For the two defects, per arm that is not INCOMPLETE or UNAVAILABLE:

- **Paraphrase confirmed** if the unevidenced-rate difference has a 95% lower
  bound above +0.10. **Not confirmed** otherwise.
- **Duplicates more than local** if the near-duplicate difference has a 95%
  lower bound above 0. **Not shown** otherwise.

Consequences, none of which changes a default:

- A BETTER arm earns a follow-up pre-registration of a confirmation on pool F
  at n = 2 (the `qwen3:8b` pool-F runs would be made locally). Nothing is
  adopted here, and a hosted model can never become a default under the
  local-first principle.
- #1768 is decided from the paraphrase column: confirmed on at least one
  complete arm means the issue proceeds with this data; confirmed on none means
  the premise is not supported at this n and the owner decides whether to
  close it.
- Any wording recommending a model in the provider guide (#1769) cites the
  results file and its label, and states the date and the n.

## Cost forecast

Method, all model-free (`hosted_screen.py forecast`, reproducible):

- **Calls.** 51 per DA run, from the `qwen3:8b` run-1 records (41 windows, 5
  participant captures, 5 selector judges). Hosted arms may re-ask more or
  less; the call ceiling covers it.
- **Input tokens.** The prompt tokens the local server counted for those 51
  calls, 173,820 per run, times a per-provider factor: 1.0 for OpenAI and
  Google (assumed, not measured), **1.4 for Anthropic (UNCONFIRMED**:
  Anthropic states its 4.7-and-later tokenizer yields about 30% more tokens
  than earlier ones; its relation to the local tokenizer was not measured).
- **Output tokens.** The completion tokens the five local models produced on
  the same 51 calls: low 12,509, central 13,925 (median), high 34,092 per run.
  Reasoning arms add an assumed 0 / 1,500 / 4,000 billed reasoning tokens per
  call for low / central / high. **No measurement backs the reasoning figures.**
- **Error.** Take the central figure as good to about +/-25% for non-reasoning
  arms; for reasoning arms the high column, not the central, is the planning
  figure. "Worst" bills every call at the full 8,192-token ceiling and is the
  most a registered run can cost, whatever the model does.
- **Price.** Published standard-tier rates per 1M tokens, read 2026-11-23:
  Anthropic from [platform.claude.com/docs/en/about-claude/pricing](https://platform.claude.com/docs/en/about-claude/pricing)
  (read as the page text; Haiku 5.5 is $0.10 in / $0.50 out for prompts up to
  100,000 tokens, ours are about 3,400);
  OpenAI from [developers.openai.com/api/docs/pricing](https://developers.openai.com/api/docs/pricing);
  Google from [aistudio.google.com/docs/pricing](https://aistudio.google.com/docs/pricing)
  (Gemini 3.8 Flash $0.75 / $3.75 through 2026-12-31, then double). The OpenAI
  rates were then confirmed on the raw page (see "Owner decisions"); the Google
  rate was not, and Google is not a registered arm.
  Prompt caching and batch discounts are not assumed.

Two runs per arm (102 calls), US dollars:

| Arm | In $/M | Out $/M | Central | High | Worst | Ceiling to set |
|---|---|---|---|---|---|---|
| `openai-gpt-4.1-mini` | 0.40 | 1.60 | 0.18 | 0.25 | 1.48 | 1.50 |
| `openai-gpt-5-mini` | 0.25 | 2.00 | 0.45 | 1.04 | 1.76 | 2.00 |
| `anthropic-claude-haiku-5-5` | 0.10 | 0.50 | 0.06 | 0.08 | 0.47 | 0.50 |
| `openai-gpt-6-luna` | 0.10 | 0.50 | 0.13 | 0.27 | 0.45 | 0.50 |
| `openai-gpt-5.5` | 5.00 | 30.00 | 7.16 | 16.02 | 26.81 | 17.00 |
| `google-gemini-3.8-flash` | 0.75 | 3.75 | 0.94 | 2.05 | 3.39 | 3.50 |
| **All six** | | | **8.92** | **19.71** | **34.35** | **25.00** |
| **Recommended four (first four rows)** | | | **0.82** | **1.64** | **4.15** | **4.50** |
| **Cheaper subset (4.1-mini, haiku, luna)** | | | **0.37** | **0.60** | **2.39** | **2.50** |

A third run per arm multiplies each figure by 1.5. The `gpt-5.5` ceiling sits
below its worst case on purpose: it is a decision to accept a stop before a
fully capped run. Re-running the forecast after changing a price is one
command; the ceilings are set from the high column and are what the harness
enforces.

Local cost, free but not nothing: bge-m3 embeddings and the `gpt-oss:20b` G1
judge over 41 windows per arm-run, a few minutes of GPU per arm.

## Procedure

One arm at a time, the provider's own key exported for that arm only:

```sh
export OPENKOS_OPENAI_API_KEY=...
uv run python evals/concept_model_screen/run_concept_screen.py run \
  --models openai-gpt-4.1-mini --pool DA --runs 1 2 \
  --backend openai-compatible --base-url https://api.openai.com \
  --served-model gpt-4.1-mini --digest openai:gpt-4.1-mini:<date> \
  --timeout-from qwen3:8b --no-seed \
  --max-calls 200 --max-usd 1.50 --price-in 0.40 --price-out 1.60
```

Then `judge --pool DA --runs 1 2` for the arm, `score --pool DA --runs 1 2
--g1 --prose-runs` (no prose runs) with `qwen3:8b` and the finished arms,
`hosted_screen.py replay --models qwen3:8b --runs 1 2`, and `hosted_screen.py
metrics --models <arms> --runs 1 2`. The `decide` subcommand is not used: the
rule above is applied to the stored scores.

## Not concluded

- Nothing about private or non-English sources: the prose fixtures are not sent.
- Nothing about the pipeline's other tasks (answering, edge typing,
  contradiction), only concept extraction on meeting transcripts.
- Not a statement about a provider: a model name on a date. Aliases move.
- Power is low. The #1557 intervals on 37 windows were about +/-0.03 wide
  against a reference lift of 0.03, and the unevidenced and near-duplicate
  intervals rest on five meetings. A NOT DISTINGUISHABLE label means
  "not resolved at n = 2", not "equal".
- Reasoning and thinking budgets are provider defaults, so a reasoning arm's
  result mixes the model with its default effort.
- The selector judge is the arm's own model; the judge-coupling effect (#1571)
  is not separated.
- No hosted model becomes a default, and nothing here weakens the local-first,
  no-mandatory-cloud principle.

## Owner decisions

Settled by the owner on 2026-11-23, accepting every recommendation below:

- **Arms:** `openai-gpt-4.1-mini`, `openai-gpt-5-mini`,
  `anthropic-claude-haiku-5-5`, `openai-gpt-6-luna`. `openai-gpt-5.5` and
  `google-gemini-3.8-flash` are not registered arms.
- **Ceilings:** `--max-usd` 1.50, 2.00, 0.50 and 0.50 respectively (4.50 total).
- **Runs per arm:** 2, paired with the reference's cached runs 1 and 2.
- **Prices:** confirmed on the raw provider pages on 2026-11-23.
  [developers.openai.com/api/docs/pricing](https://developers.openai.com/api/docs/pricing),
  Standard tier: gpt-4.1-mini $0.40 / $1.60, gpt-5-mini $0.25 / $2.00,
  gpt-6-luna $0.10 / $0.50 (short context, up to 272K input tokens).
  [platform.claude.com/docs/en/about-claude/pricing](https://platform.claude.com/docs/en/about-claude/pricing):
  Claude Haiku 5.5 $0.10 / $0.50 for prompts up to 100,000 tokens. OpenAI's
  [reasoning guide](https://developers.openai.com/api/docs/guides/reasoning)
  states reasoning tokens are billed as output tokens. The Anthropic token
  factor of 1.4 stays an assumption; the ceiling, not the forecast, bounds the
  spend.
- **Local GPU:** authorized for the G1 judge and the embeddings.

The original recommendations, kept for the record:

1. **Which hosted arms.** Recommendation: the first four rows (`gpt-4.1-mini`,
   `gpt-5-mini`, `claude-haiku-5-5`, `gpt-6-luna`): one non-reasoning and one
   reasoning OpenAI model, the Anthropic model from the smoke test, and the
   cheapest new OpenAI model. Leave `gpt-5.5` out: it is 9x the cost of the
   other four combined for one more point on the curve, and can be added later
   by its own small registration. Fallback if the budget is tighter: the
   cheaper subset.
2. **Budget ceiling.** Recommendation: **$4.50 total** for the recommended
   four, enforced per arm by `--max-usd` (1.50 / 2.00 / 0.50 / 0.50), against a
   central forecast of $0.82 and a worst case of $4.15. Add $17 to include
   `gpt-5.5`.
3. **Google.** Recommendation: **exclude it from the registered arms** while
   its endpoint answers 503 on every call; the harness's stop rule
   (UNAVAILABLE after 10 failed first calls) costs at most a few client retries
   and zero tokens, so including it as a conditional arm is nearly free, but it
   would likely end UNAVAILABLE and prove nothing about the model. Include it
   only if the owner sees it answer first.
4. **Runs per arm.** Recommendation: **2**, matching the reference's cached runs
   1 and 2 so the paired comparison needs no new local extraction. Three runs
   would cost 1.5x and add local work for `qwen3:8b` run 3.
5. **Prices.** Confirm the OpenAI and Google rates on the provider pages (read
   through a summarising fetch), and the Anthropic token factor of 1.4, before
   setting `--price-in` and `--price-out`; a wrong price weakens the guard.
6. **Local GPU.** The G1 judge and embeddings need Ollama; confirm the GPU is
   free for a few minutes per arm.

## Disclosed before registration

- The reference's token counts per call (all five local models), its stored
  window outcomes, and its unevidenced rate (5 of 50) were read from cached
  records while preparing this file. These are #1557 records, already
  published in aggregate, and no hosted outcome exists.
- No hosted API was called, no extraction or judge model was run, and no key
  was available, while preparing this file. The 2026-11-23 smoke test figures
  come from #1767 and #1766 as filed.
