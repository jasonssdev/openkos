# Pre-registration: an `evidence` instruction in the concept-extraction prompt (#1768)

**Status: registered on merge to `main`.** No treatment call, local or hosted,
may be sent before that merge. A change after the merge is a new
registration, not an edit to this one. The owner decisions listed at the end
are open and must be settled before the first call.

## Question

Should the shipped concept-extraction prompt ask the model for a verbatim
`evidence` quote?

#1767 measured that hosted models paraphrase: 19 of 27 final objects of
`openai-gpt-4.1-mini` and 156 of 156 of `anthropic-claude-haiku-5-5` quote no
line of their source, against 5 of 50 for `qwen3:8b` ([`HOSTED_RESULTS.md`](HOSTED_RESULTS.md)).
#1784 made the engine accept an optional `evidence` field and count it as
evidence only when `evidence.verified_quote` finds it verbatim in the source
(at least four words, one-directional containment). The prompt does not ask
for the field yet, so no model supplies it. This A/B measures what asking does.

The ship decision rests on the local default `qwen3:8b`; the hosted arms
measure whether the instruction fixes the defect it was written for. No
hosted model becomes a default, and nothing here weakens the local-first,
no-mandatory-cloud principle.

## Treatment

**Control:** the shipped system prompt `extraction/system`
(`src/openkos/prompts/extraction/system.md` with its transcript clause), as
`concept._SYSTEM_PROMPT` holds it at the registration head: 8,320 characters,
sha256 `6514c14bc12ec1d8645cc0bee8343b2a102082b81341c334297f194e49e961cb`.

**Treatment:** the same text with exactly two splices
(`evidence_prompt.treatment_prompt`), 9,019 characters, sha256
`9dbb82be4ede1522fa83cd7544677735edb374c52ce65dda838aad1d4230e58b`.

1. This paragraph, inserted before the paragraph that begins
   `Return ONLY a JSON array,`:

   > One further field, for EVERY object: "evidence", ONE sentence or line copied VERBATIM from the SOURCE that supports the object -- the line a reader would search the source for to check it. Copy it character for character, exactly as the source writes it: do not paraphrase, shorten, translate, correct, or merge two lines, and never put in it text the source does not contain. Choose a line of at least four words. If no single line of the source supports the object, omit "evidence" for that object rather than writing one. This field does not change your answer: choose, type and describe each object exactly as you would have otherwise.

2. In the reply-schema line, `"body": "...", "type_alternative"` becomes
   `"body": "...", "evidence": "<one line copied verbatim from the SOURCE>", "type_alternative"`.

Each anchor must occur exactly once in the shipped text, or the splice is
refused. `run --prompt-arm evidence` also refuses to send a treatment whose
sha256 is not the one above (`evidence_prompt.REGISTERED_SHA256`), so a shipped
prompt that moves after this merge cannot be measured under this
registration. The re-ask and participant-capture prompts are not changed, so
objects those calls add never carry the field; the same holds in production
if the instruction ships. The shipped prompt is not changed by this PR: the
harness swaps the module constant for the run and restores it.

## Arms

| Pair | Control (cached) | Treatment label | Endpoint |
|---|---|---|---|
| local, primary | `qwen3:8b`, #1557 runs 1 and 2 | `qwen3:8b+evidence` | local Ollama |
| hosted | `openai-gpt-4.1-mini`, #1767 runs 1 and 2 | `openai-gpt-4.1-mini+evidence` | `https://api.openai.com`, `gpt-4.1-mini` |
| hosted | `anthropic-claude-haiku-5-5`, #1767 runs 1 and 2 | `anthropic-claude-haiku-5-5+evidence` | `https://api.anthropic.com`, `claude-haiku-5-5` |

`openai-gpt-4.1-mini` is the only #1767 arm that cleared every veto;
`anthropic-claude-haiku-5-5` is the cheapest high-volume arm and the one with
156 of 156 objects unevidenced.

Everything except the system prompt is #1767's: pool DA (ES2015a, IS1004b,
TS3009c, ES2015d, IS1004d; 41 windows, the hashes in `windows.json` checked
before every run), runs 1 and 2 (n = 2), the selector judge on, the arm's own
model for the selector judge, re-ask and participant capture, the
`qwen3:8b` stage-0 timeouts (146 s per window call, 438 s per whole-source
call), `max_completion_tokens` / `num_predict` 8192. Seeds: `qwen3:8b` sends
seed r + k x 1000 (run r, attempt k) as #1557 did; the hosted arms send no
seed, temperature or reasoning field (`--no-seed`), as #1767 did. Prose
fixtures are not run on any arm.

A treatment is a new record label, not a new model: `run --prompt-arm` sends
the control's model name and digest, and writes records, stamp and spend
ledger under `<model>+evidence`, so no control record, cached call or ledger
is touched. The treatment's chat-cache keys differ from the control's because
the system prompt differs.

## Controls are reused, not re-run

The extraction path at the registration head is byte-identical to the one
the controls ran under for any reply that carries no `evidence` key. #1784
changed only two things: `_validate` reads an optional `evidence` string
(absent becomes `""`), and `_unevidenced_titles` consults
`verified_quote(evidence)` before the body check, which returns `None` for
`""` (zero words), so the body check decides exactly as before. The control
prompts are unchanged, so every control cache key is unchanged.

Checked model-free before registration (Ollama host poisoned, every call
answered from cache, nothing written): a replay of all 30 control
meeting-runs at the registration head reproduced every final title list
(10 of 10 per arm), the unevidenced counts of `HOSTED_RESULTS.md` (5 of 50,
19 of 27, 156 of 156), and found no control object carrying an `evidence`
field (0 of 233). The controls are therefore reused from cache.

Two conditions keep that true at run time:

- **Local digest.** The `qwen3:8b` treatment must run on the control's model,
  digest `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`. If
  Ollama serves a different digest, stop before any call: the local pair is
  INCOMPLETE and a new registration decides whether to re-run the control.
  `evidence_ab.py report` marks the pair INCOMPLETE if the digests differ.
- **Hosted dates.** A hosted control was run on 2026-11-23 and its treatment
  will run later. A provider alias can move between those dates; this is
  disclosed, not controlled (see "Not concluded" and the owner decisions).

The control metrics are read from `hosted_screen.py replay` output at the run
head, which adds `final_evidence` to each control record without any model
call.

## Metrics

All per pair, treatment against its own control, pooled over the two runs.

- **Primary: unevidenced rate.** Final objects whose `final_unevidenced` is
  true (production's `_unevidenced_titles`, which now counts a verified
  `evidence` quote as evidence), over all final objects, n of TOTAL. Delta =
  treatment minus control, with the meeting-level bootstrap of
  `HOSTED_RESULTS.md` (`hosted_screen.rate_delta`: the five meetings drawn
  with replacement, the same draw for both arms, 10,000 resamples, seed 42).
- **Evidence verified share.** Final objects whose `evidence` passed
  `verified_quote`, over all final objects.
- **Fabricated-quote share.** Final objects that supplied an `evidence` the
  source does not contain (`not_in_source`), over all final objects; also
  reported over the objects that supplied the field. A quote that is in the
  source but under four words is `too_short` and counted apart, not as
  fabricated. Every record carries `final_evidence` per object
  (`none | verified | not_in_source | too_short`, `screen_pipeline.evidence_status`).
- **Reported, not decided on:** window outcomes by cause, calls, tokens and
  spend from the ledger, wall time, judged-out counts, objects added by
  re-ask and participant capture.

## Guardrails (each against the arm's own control)

- **Window lift non-inferiority:** the paired window-level lift delta
  (two-stage bootstrap, meetings then windows, 10,000 resamples, seed 42;
  `score --reference <control>`) has a 95% lower bound of at least -0.03.
- **G1 precision:** the pooled precision delta (`gpt-oss:20b` judge, same
  bootstrap) has a 95% lower bound of at least -0.05.
- **V1 failures** as in #1557: the treatment's failed-window rate is at most
  the control's + 0.02 and at most 0.05 absolute.
- **V4 volume:** objects per meeting-run within [0.5, 1.5] x the arm's own
  control (`qwen3:8b` 5.00, `gpt-4.1-mini` 2.70, `haiku` 15.60). #1783
  questions V4 when it compares one model against another; against the same
  model it guards the instruction's side effects (an instruction that halves
  or inflates the object count).
- **V5 verbatim copying** as in #1557: at most the control's rate + 0.10.
- V3 (Spanish title language) is not evaluated: no prose fixtures are run.

## Completeness

As #1767, a provider failure is not a result about the model.

- **Provider failure:** a hosted window whose calls ended in `error` or
  `not_called` after the client's bounded retry. Not cached. Delete the
  affected meeting-run records and run again (cached calls replay free); at
  most **two** such re-runs per arm.
- **Model failure:** `timeout`, `capped`, `unparseable`. Cached, counted in
  V1, not re-sent.
- If the first 10 calls of a hosted arm all end in provider failure, stop the
  arm and report it **UNAVAILABLE on <date>**.
- A pair is **INCOMPLETE** if, after the re-runs, any provider-failure window
  remains, the spend or call ceiling stopped the run (meeting-runs missing),
  any control or treatment record lacks the per-object flags, or the local
  digests differ. Its numbers are reported and flagged; it cannot qualify.

## Decision rule (fixed before any treatment call)

Applied by `evidence_ab.py report` (`decide`) to the stored records and
scores; the rule is not re-tuned after the data.

**SHIP** the instruction as the default prompt if both hold:

1. on `qwen3:8b`, the pair is complete, every guardrail holds, and the
   unevidenced delta's 95% upper bound is at most 0 (the instruction does not
   make the local default worse); and
2. on at least one complete hosted arm whose own guardrails hold, the
   unevidenced delta's 95% upper bound is at most -0.10 (the interval
   excludes a reduction smaller than 0.10).

**INCOMPLETE** if the `qwen3:8b` pair is incomplete, or if condition 1 holds,
no hosted arm qualifies, and at least one hosted arm is incomplete (it could
still have qualified). Nothing is shipped; the owner decides whether to
complete the missing arm under this registration's ceilings.

**DO NOT SHIP** otherwise. The result records why: which guardrail failed on
which arm, or which unevidenced bound missed its threshold.

A hosted arm that is complete but fails a guardrail is named in the result
whether or not the decision ships: the prompt is shared by every backend, so
a side effect on an opt-in backend is reported even though it does not by
itself block the local default.

SHIP means a follow-up implementation change edits
`src/openkos/prompts/extraction/system.md` to the registered treatment text,
through the ordinary spec and review process. It does not change a default
model, and this registration does not edit the prompt.

## Cost forecast and ceilings

From the #1767 ledger actuals for two runs (112 calls per arm):
`openai-gpt-4.1-mini` 395,491 prompt / 17,681 completion tokens, 0.186 USD;
`anthropic-claude-haiku-5-5` 605,596 / 156,180, 0.139 USD. Every treatment
call is new (the system prompt differs, so nothing replays from cache).

- **Input** grows by the 699-character instruction on every extraction call,
  about 175 tokens per call (about 270 at haiku's measured 1.53 factor):
  under 5% of the prompt tokens. Central +5%, high +10% (also covering a few
  more re-ask calls).
- **Output** grows by one quote per candidate, before the selector judge.
  Central x1.5 of the control's completion tokens, high x3. No measurement
  backs these factors; the ceiling, not the forecast, bounds the spend.
- **Worst** bills 200 calls (the call ceiling) at the full 8,192-token output
  ceiling.
- Prices as registered and confirmed in #1767 (2026-11-23): `gpt-4.1-mini`
  $0.40 / $1.60, `claude-haiku-5-5` $0.10 / $0.50 per 1M tokens, standard
  tier, no caching or batch discount assumed. Re-confirm them on the
  provider pages before the run (owner decision 1).

Two runs per arm, US dollars:

| Arm | #1767 actual | Central | High | Worst | `--max-usd` | `--max-calls` |
|---|---|---|---|---|---|---|
| `openai-gpt-4.1-mini+evidence` | 0.186 | 0.21 | 0.26 | 2.93 | 1.00 | 200 |
| `anthropic-claude-haiku-5-5+evidence` | 0.139 | 0.18 | 0.30 | 0.94 | 1.00 | 200 |
| **Total** | 0.325 | **0.39** | **0.56** | 3.87 | **2.00** | |

The `gpt-4.1-mini` ceiling sits below its worst case on purpose: it accepts a
stop (an INCOMPLETE pair) before a fully capped run, at about 4x the high
forecast. Haiku spent 1.7x its #1767 high forecast; its ceiling covers its
worst case.

Local cost, free in money but not in time, on the owner's GPU: the
`qwen3:8b` treatment (10 meeting-runs, about 50 minutes at #1557's 292 s per
meeting-run), the `gpt-oss:20b` G1 judge over the three treatment arms, and
`bge-m3` embeddings for scoring and the report.

## Procedure

The registration head's worktree needs the four gitignored
`examples/extraction-corpus/sources` files copied in (`run` refuses to start
without 5 of 5, though no prose is sent); they are never committed. Keys are
read only from `OPENKOS_OPENAI_API_KEY`, exported for one hosted arm at a
time, and never written, echoed or logged.

```sh
S=evals/concept_model_screen
# 0. Controls: cache-only replay, no model call (adds final_evidence).
OLLAMA_HOST=http://127.0.0.1:9 uv run python $S/hosted_screen.py replay \
  --models qwen3:8b openai-gpt-4.1-mini anthropic-claude-haiku-5-5 \
  --runs 1 2 --timeout-from qwen3:8b
# 1. Local treatment. First: `ollama list` must show ID 500a1f067a9f for
#    qwen3:8b; if it does not, stop (see "Local digest" above).
uv run python $S/run_concept_screen.py run --models qwen3:8b --pool DA \
  --runs 1 2 --prompt-arm evidence
# 2. Hosted treatments, one at a time, that provider's key exported.
uv run python $S/run_concept_screen.py run --models openai-gpt-4.1-mini \
  --pool DA --runs 1 2 --prompt-arm evidence \
  --backend openai-compatible --base-url https://api.openai.com \
  --served-model gpt-4.1-mini --digest openai:gpt-4.1-mini:<date> \
  --timeout-from qwen3:8b --no-seed \
  --max-calls 200 --max-usd 1.00 --price-in 0.40 --price-out 1.60
uv run python $S/run_concept_screen.py run --models anthropic-claude-haiku-5-5 \
  --pool DA --runs 1 2 --prompt-arm evidence \
  --backend openai-compatible --base-url https://api.anthropic.com \
  --served-model claude-haiku-5-5 --digest anthropic:claude-haiku-5-5:<date> \
  --timeout-from qwen3:8b --no-seed \
  --max-calls 200 --max-usd 1.00 --price-in 0.10 --price-out 0.50
# 3. G1 judge on the treatments (the controls' judge records are cached).
uv run python $S/run_concept_screen.py judge --pool DA --runs 1 2 \
  --models qwen3:8b+evidence openai-gpt-4.1-mini+evidence \
  anthropic-claude-haiku-5-5+evidence
# 4. One score per pair, paired against that pair's control.
for c in qwen3:8b openai-gpt-4.1-mini anthropic-claude-haiku-5-5; do
  uv run python $S/run_concept_screen.py score \
    --name "evidence-ab-${c//:/_}" --models "$c" "$c+evidence" \
    --reference "$c" --pool DA --runs 1 2 --g1 --prose-runs
done
# 5. The registered metrics, guardrails and decision.
uv run python $S/evidence_ab.py report --runs 1 2
```

The results go to `EVIDENCE_RESULTS.md`, with every deviation from this
procedure listed.

## Not concluded

- Power is low: n = 2, five meetings, 37 scored windows. The `qwen3:8b`
  control has 5 of 50 objects unevidenced, so its delta can fall by at most
  0.10, and "upper bound at most 0" asks only that no meeting gets worse
  beyond resampling noise. A DO NOT SHIP on the local arm can mean "not
  resolved at n = 2".
- Hosted controls and treatments run on different dates under a moving
  alias, so part of a hosted delta can be the provider, not the instruction.
  The hosted arms' `final_unevidenced` rates of 0.70 and 1.00 leave room for a
  large effect; a small hosted effect is not interpretable.
- A verified quote proves a source line was copied, not that it supports the
  object. G1 precision guards support; the quote's relevance is not judged.
- Nothing about private or non-English sources (no prose fixtures, V3 not
  evaluated), or about the pipeline's other tasks.
- The selector judge runs on each arm's own model and also reads the longer
  replies; its interaction with the instruction (#1571) is not separated.
- The re-ask and participant-capture prompts do not ask for the field;
  objects they add stay evidenced or not by their body alone.
- Reasoning budgets and sampling are provider defaults on the hosted arms.

## Owner decisions needed

1. **Budget.** Recommendation: **$2.00 total**, enforced per arm by
   `--max-usd` 1.00 and 1.00 with `--max-calls` 200 each, against a central
   forecast of $0.39 and a high of $0.56; re-confirm the two prices on the
   provider pages before setting `--price-in` and `--price-out`.
2. **Local GPU.** Confirm the GPU is free for this lane: about 50 minutes of
   `qwen3:8b` extraction, then the `gpt-oss:20b` G1 judge and `bge-m3`
   embeddings, a few minutes per arm.
3. **Hosted controls.** Recommendation: **reuse the 2026-11-23 controls**
   (above), accepting the disclosed alias drift. The alternative is to re-run
   both hosted controls on the treatment's date under new labels, about $0.33
   more; that needs a new label pair in this registration before merge.

## Disclosed before registration

- The control records, their cached replies and their stored aggregates were
  read while preparing this file (the replay check above). No treatment
  output exists.
- No model was called, local or hosted, and no key was used, while preparing
  this file. The replay ran with Ollama's host poisoned and every call
  answered from the chat cache.
- Harness support added with this registration, each with a model-free
  self-test: `run --prompt-arm` (installs the registered treatment, refuses an
  unregistered hash, records under `<model>+<arm>`), `score --reference`,
  per-object `final_evidence` in every record, and `evidence_ab.py report`.

## Deviations from the brief this registration was written from

Recorded so the owner can see where the registered rule differs from the
design it was asked to register, and why:

1. **A qualifying hosted arm must also pass its own guardrails.** The brief
   required only its unevidenced bound. A reduction bought by losing coverage
   or precision, or by collapsing the object count, is not the fix #1768
   asks for.
2. **An INCOMPLETE outcome for the decision**, not only for an arm, so a
   provider failure can never produce DO NOT SHIP by itself.
3. **The local digest must match the control's**, or the pair is INCOMPLETE:
   reusing a cached control is only valid on the same model.
4. **Fabricated quotes are split from short ones.** A real source span under
   four words fails verification but is not invented, so it is counted as
   `too_short`, apart from `not_in_source`.
