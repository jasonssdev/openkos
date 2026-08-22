# `generation_ceiling` — which call gets cut off, and does chunking fix it? (#714)

`#714` reports that a 16.4 KB real meeting transcript hits the shipped
`max_generation_tokens` ceiling of 8192 on 1 run in 3, and on 3 runs in 3 under
`#715`'s treatment clause. The owner's ruling is to **lower `_CHUNK_THRESHOLD`,
with the value coming from measurement**. This probe produces that measurement.

```bash
uv run python -u evals/generation_ceiling/run_generation_ceiling_probe.py --self-test
uv run python -u evals/generation_ceiling/run_generation_ceiling_probe.py --runs 3
uv run python -u evals/generation_ceiling/run_generation_ceiling_probe.py --arm baseline --arm chunked
uv run python -u evals/generation_ceiling/run_generation_ceiling_probe.py --rescore results/<file>.jsonl
```

**Use `-u`.** Piping through `tee` makes Python buffer and a long run looks hung.

## Why it measures the call and not the run

Both #714's evidence and #715's blocked treatment arm report
`OllamaGenerationCapped` out of a whole `extract_concept_union` run. That
exception does not say **which** of the run's calls was truncated, and the run
makes several: two whole-document extractions, an optional re-ask, an optional
participant capture, and the judge.

That distinction decides whether the authorized fix can work at all.
`_CHUNK_THRESHOLD` governs the extraction fan-out only. The judge is called as
`judge_mod.select(source_text, …)` — with the **whole** source text plus the
candidate list, on both sides of the threshold, so lowering that constant does
not shrink this prompt at all. If the ceiling were being hit there, the
authorized lever would change nothing, and a probe that measured only
`_extract_once` would have handed back a clean bill of health for a constant
with no effect.

Measured, it is not hit there — see `report.md`. Note the reason is *not* that
the judge's prompt turned out to be bigger: it is consistently **smaller**
(4 994 tokens against extraction's 6 240). What saves the judge is that its
*reply* is short. The ceiling caps replies, not prompts.

So the probe drives the shipped pipeline and **attributes** each cut-off to the
phase that issued the call, rather than narrowing to the call a reader would
guess.

## Raised vs. cut off — they are not the same column

The probe reports two different things, and the gap between them is a finding in
its own right:

| column | meaning |
| --- | --- |
| `raised` | the cut-off **escaped** and failed the run — the failure #714 reports |
| `cut` | **some** reply in the run was cut off, escaped or not |

Only `_extract_once` can produce a `raised`. Every other call swallows its own
backend failures by contract — `judge.select`'s D7 fail-closed rule, and the
broad `except Exception` in `_reask_for_further_subjects` and
`_capture_further_participants`. A cut-off judge reply becomes
`judge_status="failed"` and the run keeps its **full unfiltered candidate set**;
a cut-off participant pass finds nobody.

Since #828 the last two name the cause on `ExtractionReport.optional_call_failures`
instead of discarding it, so a cut-off bonus call is no longer silent outside
this probe. It still does not raise, so nothing below changes.

Those runs read as clean. A probe that counted only raised exceptions would
report them as zero — so the ledger records `done_reason` per call, off the raw
response, and never infers it from the exception.

The useful corollary: because the swallowing calls cannot raise, #714's
`OllamaGenerationCapped` is *necessarily* an extraction cut-off, which is exactly
what `_CHUNK_THRESHOLD` governs.

## The arms are the decision

`chunk_threshold` is an **arm axis, not a constant**. The `*-chunked` arms run
the identical fixture with `_CHUNK_THRESHOLD` lowered to `_CANDIDATE_THRESHOLD`
(12 000), so the same source takes the chunked path.

| arm | prompt | threshold | question it answers |
| --- | --- | --- | --- |
| `baseline` | shipped | 18 000 | does #714 reproduce? |
| `chunked` | shipped | 12 000 | does the authorized lever fix it? |
| `treatment` | +#715 clause | 18 000 | does #715's clause reproduce its 3/3 blocker? |
| `treatment-chunked` | +#715 clause | 12 000 | **does the lever unblock #715?** |

The last row is the one #714 exists for. A threshold that gives the baseline
headroom but not the treatment would unblock nothing.

#715's clause is **imported** from
`evals/stage_attrition/run_stage_attrition_probe.py`, never copied — that
module's own splice assertion fires before anything reaches here, and two copies
would drift into two clauses with one name.

## The prose control

`_CHUNK_THRESHOLD`'s docstring records a cost on the *other* side of the
boundary: below it, the whole-document call is the path every existing
measurement was taken against, and it works on prose — the #379 gate's 13–17 KB
documents produced 5–10 objects each. A threshold lowered far enough to protect a
16.4 KB transcript moves that measured-working band onto a path nobody measured
for it.

`prose-large-03` is **16 948 chars — larger than the transcript that fails** —
and runs both the baseline and chunked arms, so the lever's collateral is
measured beside its benefit instead of assumed away.

That docstring also says no cheap transcript-shape detector exists, and
therefore treats a shape-conditional boundary as unavailable. **That sentence is
stale**: #673 shipped `_transcript_shaped_text` and `_is_meeting_shaped`, already
used at four sites in `concept.py` and measured at 0 FP over a 785-file sweep. If
these numbers say the cut-off tracks shape rather than size, the shape-conditional
lever is available and measured.

## How it reads the counters

`OllamaClient` accepts `urlopen` as a constructor argument, so the probe passes a
recorder that drains each response, keeps `eval_count` / `prompt_eval_count` /
`done_reason`, and replays the bytes to the client unchanged. **No production
file is modified and no attribute is monkeypatched** — the request that goes out
is byte-identical to a real `ingest`, and the client's own parsing, its
`done_reason` guard and its error mapping all stay in the loop.

Each call is tagged with the phase in flight, taken from
`extract_concept_union`'s own `on_progress` hook. That hook fires immediately
*before* the call it describes at every seam, so the label in flight when a
request goes out is that request's phase — the pipeline's own public reporting
seam, not a guess reconstructed from prompt sizes.

## The self-test

`--self-test` drives the real `OllamaClient` and the real pipeline over a
scripted transport, in two scenarios:

1. **the judge is cut off** — the run must SUCCEED (`raised` false) while the
   ledger still names the judge;
2. **extraction is cut off** — the run must FAIL (`raised` true), attributed to
   an extracting phase.

Every failure it guards is a *silent-success* failure. A recorder that dropped
the counters would print empty headroom and the table would still render. One
that consumed the body without replaying it would turn every run into a
malformed-reply error and #714's rate would read as zero. A phase tag lagging its
call by one would blame the wrong stage — and blaming extraction instead of the
judge is precisely the mistake that ships a constant which changes nothing.

It also asserts the arm's threshold patch was restored, so a sweep cannot leak a
patched constant into a later arm.

## Reading the result

`render_verdict` states which reading the data supports, because they lead to
opposite code changes: cut off in extraction (the lever is aimed correctly), cut
off in the judge only (the lever does not reach it, and the defect is silent), or
cut off in both. It then reports whether the chunked arm actually removes the
failure, and points at the prose control's object count for collateral.

It is deliberately a *reading*, not a pass/fail gate. #714 sets a constant, and
the honest output of a measurement feeding a constant is the evidence plus the
reading — not a boolean that hides which case it saw.
