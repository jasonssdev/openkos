# Generation-ceiling measurement — #714

`qwen3:8b`, 3 runs per fixture per arm, 18 runs total, 2026-08-15.
Raw: `results/generation-ceiling-20260815T134239Z-qwen3-8b.jsonl`.

## Result

| fixture | arm | threshold | runs failed | some reply cut off | objects | worst generated |
| --- | --- | --- | --- | --- | --- | --- |
| `ami-ts3005a-full` (16 440) | baseline | 18 000 | 0/3 | 0 | 4 | 235 |
| `ami-ts3005a-full` | chunked | 12 000 | 0/3 | 0 | 7 | 1 248 |
| `ami-ts3005a-full` | **treatment** | 18 000 | **2/3** | 2 | — | **8 192** |
| `ami-ts3005a-full` | **treatment-chunked** | **12 000** | **0/3** | 0 | 6 | 1 731 |
| `prose-large-03` (16 948) | baseline | 18 000 | 0/3 | 0 | 8 | 671 |
| `prose-large-03` | chunked | 12 000 | 0/3 | 0 | **17** | 902 |

`treatment` = the shipped prompt plus #715's additive clause, imported from
`evals/stage_attrition`.

## The lever works, and it is aimed at the right call

Both cut-offs landed in `extracting pass 1/2` and `extracting pass 2/2` —
whole-document extraction, which is exactly what `_CHUNK_THRESHOLD` governs.
Chunked, the same source's worst window generated **1 731 of 8 192**. The
headroom is not marginal, and #715's blocker goes from 2/3 to 0/3.

This was worth measuring rather than assuming. `judge_mod.select` receives the
**whole** `source_text` plus the candidate list on *both* sides of the
threshold, so a threshold change cannot shrink that prompt at all. Had the
ceiling been hit there, the authorized lever would have changed nothing.

It is not hit there, by a wide margin — and the reason is the opposite of the
one I expected. The judge's prompt is consistently **smaller** than an
extraction prompt, not larger (4 994 against 6 240; baseline run 1 records
4 799 against 6 164). What protects it is that the ceiling caps **replies**:

| phase | calls | worst generated | max prompt |
| --- | --- | --- | --- |
| `extracting pass 1/2` | 9 | **8 192** | 6 240 |
| `extracting pass 2/2` | 8 | **8 192** | 6 240 |
| `extracting chunk 4/5` | 9 | 1 731 | 2 826 |
| `capturing further participants` | 10 | 282 | 4 792 |
| every `judging N candidates` | 15 | **97** | 4 994 |

The judge's largest reply across 15 calls is **97 tokens**. Selection is a short
answer over a long prompt; extraction is a long answer. Only the second kind can
reach a reply ceiling, whatever the prompt sizes are — which is the invariant to
carry forward, not "the judge's prompt is bigger".

## Why the fix branches on shape instead of lowering one number

`prose-large-03` is **16 948 chars — larger than the transcript that fails** —
and was never cut off in any arm, at either threshold. It does not have the
problem. But chunking it is not free: the retained set went from **8 objects to
17** (16 / 17 / 20 across three runs).

Two of those three runs sit below `_UNION_BACKSTOP` (20), so this is not the cap
truncating a longer list — it is genuine fragmentation of one subject across
windows, which is the open **#699** defect. The 13–17 KB prose band is the path
every existing extraction measurement was taken against (`_CHUNK_THRESHOLD`'s
docstring: the #379 gate's documents produced 5–10 objects each, and this
measurement reproduces that at 7 / 9 / 8).

So a single lowered constant buys the transcript fix and pays for it with a
measured regression in a band that never asked for one. Shipping two thresholds
selected by `_is_meeting_shaped` buys the fix and pays nothing.

That detector is why this option exists at all. `_CHUNK_THRESHOLD`'s docstring
justified the single number with "no cheap detector for transcript-shaped exists
yet" — **stale since #673**, which shipped `_transcript_shaped_text` and measured
it at 0 false positives over a 785-file sweep. The docstring has been corrected.

## Two things the ledger surfaced that the exception cannot

**Only `_extract_once` propagates a cut-off.** The judge (`judge.select`'s D7
fail-closed rule), the re-ask and the participant capture all wrap their
`llm.chat` in a broad `except` and degrade to "found nothing". So #714's
`OllamaGenerationCapped` is *necessarily* an extraction cut-off — which is what
makes the authorized lever the right one, and is now asserted by the probe's own
self-test rather than argued from reading.

**The corollary is a silent defect class.** A cut-off judge reply becomes
`judge_status="failed"` and the run keeps its **full unfiltered candidate set**;
a cut-off participant pass silently finds nobody. Both read as clean runs. That
is why the table separates `runs failed` from `some reply cut off` — a probe
counting only raised exceptions would report those as zero. In this sweep the two
columns agree, so nothing was silently degraded; the columns exist so that a
future sweep where they *disagree* is visible.

## Bounds

One model (`qwen3:8b`), one transcript, one prose control, 3 runs per arm. Only
12 000 was measured as the meeting-shaped threshold — a different value is
another run of this probe, not a judgement call. The baseline transcript arm did
not reproduce #714's own 1-in-3 failure in 3 runs, which is consistent with an
intermittent tail: the treatment arm is what makes it reliable enough to measure,
and is also the arm #715 needs.
