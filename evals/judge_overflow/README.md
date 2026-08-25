# judge_overflow — where the failing judge call's tokens went (#866)

**Verdict: the 3-of-3 `unparseable: no-json` judge failure on chunked
sources is PROMPT OVERFLOW, not #830's thinking runaway — and it was
deterministic. The judge (and the #584 re-ask, and the #668 participant
capture) send the WHOLE source in one prompt while extraction itself fans
out over windows; on the reported 54K-char transcript that prompt is
16,091 real tokens against the shipped `num_ctx` 12,288, and Ollama does
not raise on an oversized prompt — it silently truncates it.**

[#866](https://github.com/jasonssdev/openkos/issues/866) mandated one
targeted measurement before any fix: record `prompt_eval_count`,
`eval_count`, `done_reason`, and the thinking/content split for the
failing judge calls.

```
uv run python -u evals/judge_overflow/run_judge_overflow_probe.py --self-test
uv run python -u evals/judge_overflow/run_judge_overflow_probe.py \
    --corpus <dir-with-large-transcripts> --runs 1
```

The corpus is **not committed** — the motivating sources are the private
transcripts from the 0.2.9 E2E walkthrough. Committed results carry
counters, statuses, short cause strings, and a **digest** of each source's
filename (never the filename itself — a transcript's name can carry a
person's name or a meeting title); the self-test pins the scrub
structurally (`committed_string_violations`), and the probe refuses to
write a results file that violates it. The table below still names
`transcription1`/`transcription3` because issue #866 itself published
those two names; the stored rows carry their digests (`9b9ff878ee97`,
`a5d1134ec410`).

## 1. The measurement — where the tokens went

`transcription1` (53,578 chars, ~13 windows, Spanish), `qwen3:8b`, the
shipped `num_predict` 8192 / `num_ctx` 12288 — the exact configuration the
failing E2E ran under:

| call | prompt_eval | eval | done_reason | thinking chars | content chars | outcome |
| --- | --- | --- | --- | --- | --- | --- |
| true size (`num_ctx` 32768) | **16,091** | 1 | length (probe cap) | — | — | the prompt's REAL size |
| production (`num_ctx` 12288) | **6,146** | 1,995 | **stop** | **0** | 7,884 | `unparseable: no-json` |

Two facts in that table close the issue's open questions:

- **`thinking_chars = 0`.** `think: false` (which `OllamaClient.chat` has
  sent on every call since the first commit) held. This is NOT #830's
  runaway: the model did not deliberate past its budget — it finished
  normally (`done_reason=stop`) and answered in Spanish prose (*"Aquí
  tienes una estructura de conocimiento…"*).
- **`prompt_eval = 6,146` against a true size of 16,091.** The server log
  names the mechanism: `truncating input prompt: limit=6146 prompt=16091
  keep=4`. llama.cpp keeps 4 head tokens plus the LAST
  `(num_ctx − keep) / 2 = 6,142` — so the **system prompt** (the JSON
  reply-shape instructions) **and the first ~60% of the source were
  silently cut**. The model was handed a decapitated transcript tail and a
  numbered list with no instructions, and answered it helpfully.

That also explains the retry arithmetic: `select` re-sends the IDENTICAL
prompt (#754), so both attempts were truncated identically and the failure
was deterministic — `no-json ×2` on every source of this class, and never
on the short ones. The E2E's "chunked × Spanish" signature is really
"chunked × large": Spanish is what the user's large transcripts are
written in.

Refuted on the way:

- **The thinking-runaway hypothesis** (#830's signature): zero thinking
  chars on the failing call.
- **The judge-specific varied retry** the issue floated: no retry
  variation can fix arithmetic. The prompt cannot fit; the fix is to make
  it fit. (The existing identical retry stays, for the stochastic
  prose-one-call, JSON-the-next class #644 measured.)

## 2. The fix the counters implicate — and its verification

`_bounded_prompt_source` (`extraction/concept.py`): when a whole-source
prompt cannot fit the planning window, the SOURCE portion is replaced by a
deterministic even-coverage excerpt built from the existing `_chunk_lines`
windows — always keeping the first and last windows, joined with an
explicit elision marker — while the instructions and candidate list
survive intact. Applied at all three whole-source seams (judge, re-ask,
participant capture); grounding checks keep reading the FULL source. A
prompt that fits ships byte-identical to before. Disclosed per run via
`ExtractionReport.bounded_prompt_calls` and one stderr advisory.

The planning budget is conservative by construction:
`(window − reserve) / 0.40` chars, where 0.40 tokens/char sits above every
whole-prompt ratio this repo has measured (0.277 on this judge prompt,
0.341 on the extraction prompt at `_CHUNK_THRESHOLD`, falling with length)
and below the point that would excerpt prompts that fit today.

Verified live on the real failing corpus (stored:
`results/runs-20260824T231108Z-qwen3-8b.json`) — the post-fix pipeline run
beside the unbounded arm reproducing the defect on the SAME captured
candidates:

| source | true prompt | post-fix judge call | unbounded judge call |
| --- | --- | --- | --- |
| transcription1 (53,578 chars, 14 windows) | 15,871 tok | prompt_eval **7,226** · reply 115 tok · stop · 21.4s · **judge ok** | prompt_eval **6,146** · 1,923 tok of prose · **no-json** |
| transcription3 (55,296 chars, 15 windows) | 16,419 tok | prompt_eval **7,489** · reply 64 tok · stop · 21.5s · **judge ok** | prompt_eval **6,146** · 1,401 tok of prose · **no-json** |

Both sources that failed 3-of-3 in the E2E now get a working judge
(`judge_prompt_fits_window=True`, `bounded_prompt_calls=['judge']`), the
judge call costs ~21s instead of minutes, and the unbounded arm still
fails at exactly the truncation constant (`keep + (num_ctx − keep) / 2 =
6,146`) — the defect is reproducible on demand and closed in production.
The bounded prompts' measured ratio (0.246 and 0.244 tokens/char) confirms
the 0.40 planning ratio is conservative, as designed.

## n

The defect and the fix are both ARITHMETIC (a prompt either fits the
window or it does not), measured deterministic across the E2E's 3-of-3 and
this probe's runs — so the stored evidence is one run per source plus the
counter table above, not a rate sweep. The judge's selection QUALITY on an
excerpt vs. the full source is deliberately not scored here: on the
failing class the pre-fix judge read garbage (no instructions, tail-only
source), so any instructed selection is an improvement, and a quality A/B
would need ground truth this corpus does not have.
