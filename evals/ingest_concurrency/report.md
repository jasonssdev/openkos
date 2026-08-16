# #700 lever 4 — concurrency measured

**Question.** Does issuing ingest's independent per-window extraction calls
concurrently buy wall clock, and does it cost quality?

**Answer.** The speedup is real, smaller than predicted, saturates immediately,
and costs no quality — but it is **not a lever openkos can pull on its own.**
At a default Ollama the gain is exactly zero, and the setting that unlocks it
belongs to a server the product does not own.

Levers 1 and 2 were measured and rejected (#728, #699) and lever 3 measured and
not adopted (#738). This one is different in kind: it is **not refuted**. It is
blocked on a decision about a dependency, not on evidence.

---

## 1. A default Ollama serializes, so client threading buys nothing

`probe_server_capacity.py`, qwen3:8b at `num_ctx=12288`, solo request 5.1s.

| concurrent | wall (s) | per-call (s) | speedup |
| --- | --- | --- | --- |
| 2 | 10.2 | 5.1, 10.2 | **1.01x** |
| 3 | 15.2 | 5.1, 10.2, 15.2 | **1.01x** |
| 4 | 20.3 | 5.2, 10.2, 15.2, 20.3 | **1.01x** |

The per-call column is the proof, not the speedup column: each request finishes
exactly one solo-duration after the one before it. That is a queue, not
contention.

With `OLLAMA_NUM_PARALLEL=4` on the same machine and the same prompts:

| concurrent | wall (s) | per-call (s) | speedup |
| --- | --- | --- | --- |
| 2 | 7.8 | 7.7, 7.8 | 1.33x |
| 3 | 11.1 | 11.1, 11.1, 11.1 | 1.39x |
| 4 | 24.9 | 24.8, 24.8, 24.9, 24.9 | 0.83x |

**Threading `extract_concept_union`'s window loop against a stock Ollama would
produce identical wall clock and a more complicated codebase.** openkos cannot
set this variable on the user's behalf: `ollama serve` is a separate,
user-managed process, usually long-running and often started by the desktop app.

(The 0.83x at 4 is a micro-benchmark artifact of 512-token generations and does
NOT reproduce at real extraction sizes — see §3. It is reported because it is
why concurrency 4 was carried into the main table rather than assumed away.)

## 2. What raising it costs in memory

`probe_server_capacity.py`, resident size Ollama attributes to the loaded model
after a real generation.

| `OLLAMA_NUM_PARALLEL` | resident (GB) | context |
| --- | --- | --- |
| 1 | **7.2** | 12288 |
| 2 | **9.1** | 12288 |
| 3 | 11.1 | 12288 |
| 4 | 12.9 | 12288 |

7.2 GB at one slot is exactly the figure #739 quotes from #691 — the issue's
number is right, and it is the single-slot number. Each further slot costs
~1.9 GB of KV cache.

**This does not block the lever, because of §3.** An earlier reading of this
directory said it did: 12.9 GB at four slots exceeds the ~11 GB #739 budgets for
a 16 GB machine. That generalized from the wrong level. The speedup saturates at
**2**, and two slots cost 9.1 GB — inside the budget. The memory objection
dissolves precisely because more parallelism buys nothing.

## 3. The speedup on the real fan-out, and what it costs in quality

`run_ingest_concurrency_probe.py`, 15 runs per arm, fixture
`medium-10-reunion-plataforma` (the #694 oracle's, four windows),
`OLLAMA_NUM_PARALLEL=4`.

| concurrency | wall clock (s) | speedup | recall | precision |
| --- | --- | --- | --- | --- |
| 1 (serial) | 156.9 ±17.5 | 1.00x | 0.42 ±0.11 | 0.92 ±0.12 |
| 2 | 124.1 ±20.6 | **1.26x** | 0.47 ±0.11 | 0.89 ±0.11 |
| 3 | 124.4 ±13.7 | **1.26x** | 0.43 ±0.13 | 0.91 ±0.12 |
| 4 | 124.6 ±17.4 | **1.26x** | 0.43 ±0.11 | 0.91 ±0.13 |

Welch's t against the serial arm:

- **Wall clock is a real effect**: t = 4.72 / 5.69 / 5.07 at concurrency 2 / 3 /
  4, saving 32.9 / 32.6 / 32.3s. The arm ranges overlap, which is why the test
  matters — reading the `[min-max]` columns alone would have called this noise.
- **The three concurrent arms are indistinguishable from each other**: t = -0.04,
  -0.08, -0.05. Going past 2 buys *nothing measurable*. The GPU saturates
  immediately, which is what "concurrent requests share the same compute" looks
  like at its limit.
- **Quality does not move** on either axis at any level: |t| < 1.21 for recall,
  < 0.61 for precision. The mechanism predicted this — the calls are
  independent and the client holds no per-call state — and unlike the last three
  levers, the measurement agreed with the mechanism.

1.26x is **below** #700's predicted 1.3–1.6x. The prediction was optimistic.

**The #694 band is not the pass mark for those quality columns.** It scores the
complete pipeline, whose merge, re-ask, participant capture and judge
re-admission all recover subjects after the fan-out; this probe stops at the
fan-out, so absolute recall sits below the band on every arm including the
shipped serial one. The serial arm is the control. Reading the gap as a
regression would manufacture a finding out of a measurement boundary.

## 4. What that is worth on a whole ingest

`probe_fanout_share.py`, 5 runs, same fixture, phase timestamps from
`on_progress`.

| | |
| --- | --- |
| full ingest | 160.7s ±14.1 |
| window fan-out | **91%** of it |
| phases | 4 × `extracting chunk`, `capturing further participants`, `judging 7 candidates` |

The fan-out is a larger share than a call count suggests (4 of 6 calls, but 91%
of the time): participant capture and the judge send short prompts and generate
little.

**So a 32.5s fan-out saving is 20% off a whole ingest** — 160.7s → ~128s on this
fixture.

## 5. Verdict

| | |
| --- | --- |
| speedup, whole ingest | **~20%** (1.26x on the 91% that is fan-out) |
| optimal concurrency | **2** — 3 and 4 add nothing measurable |
| quality cost | **none detected**, both axes, every level |
| memory cost | **+1.9 GB** (7.2 → 9.1), inside #739's ~11 GB budget |
| blocker | **`OLLAMA_NUM_PARALLEL` ≥ 2, which openkos does not own** |

Not adopted, and **not refuted either**. The evidence supports the lever; what
is missing is a decision this probe cannot make: whether openkos is willing to
depend on a server setting it can only document and detect, never set.

If that decision is yes, the implementation is small and its shape is already
constrained by what was measured here:

- concurrency **2**, not a tunable — the measurement gives no reason for more;
- `ThreadPoolExecutor.map`, never `as_completed`. `_dedup_merged` keeps the
  FIRST occurrence of a title and chunk order carries meaning ("earlier context
  named the subject first"), so completion order would silently change which
  duplicate wins;
- it must degrade to serial when the server is not configured, which means
  detecting that — and `/api/ps` does not report `OLLAMA_NUM_PARALLEL`, so
  detection is itself an open question;
- it would be the **first concurrency in this codebase** — there is no
  `ThreadPoolExecutor`, `asyncio` or `threading` anywhere in `src/`, `tests/` or
  `evals/` today — and it lands on the path that already carries the #441
  partial-batch contract and #701's progress reporting.

## Method notes

- 15 runs per arm, not 5. `evals/contradictions/` measured one arm against
  **itself** at 0.44 and 0.19 on five runs each, and `evals/edge_typing/`
  reversed its ranking between n=3 and n=15.
- Arms interleaved, not blocked: all of arm 1 then all of arm 2 would confound
  the arm with anything drifting across a two-hour session.
- Concurrency 1 runs production's own serial comprehension, not a one-worker
  pool, so the baseline is the shipped shape.
- Every probe server runs on a scratch port that this directory starts and
  stops. The user's own server was never restarted or reconfigured.
- Production is unchanged by this directory.

### One correction, disclosed

The stored run carries its per-call durations under
`per_call_latencies_completion_order`, not `per_window_latencies`. The probe
that produced it appended each duration when its call **finished**, so on the
concurrent arms the list is in completion order while only the serial arm's is
positional. The stored data shows it: a 12.9s entry sits where the
3,988-character window — the largest of the four — should be.

Three of the four review lenses found this independently. The probe now pairs
each duration with its own result, so later runs carry `per_window_latencies`
and mean it; the stored values are untouched and only the key was renamed.

**No conclusion above reads that field.** Wall clock is timed around the whole
fan-out and results are reassembled by `map` in window order, so the speedup
and quality tables are unaffected.
