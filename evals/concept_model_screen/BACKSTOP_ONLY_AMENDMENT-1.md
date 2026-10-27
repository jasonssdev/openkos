# Amendment 1 to the backstop-only pre-registration: the confirmation set (#1579)

**Status: Registered when this file merges to `main`; no run before the merge.**
It amends `BACKSTOP_ONLY_PREREGISTRATION.md` (registered by #1636) and only the
confirmation set. The primary population, the arms, P1, P2 and S1 with their
margins, C1 and its budgets, V1, the bootstrap (10,000 resamples, seed 42) and
the decision rule are **unchanged**. This file changes nothing in `src/`. Refs
#1579.

## Why an amendment

The harness (`backstop_only.py`) found two things the registration did not
foresee, both about the confirmation set and both found by reading stored files,
not by looking at any outcome:

1. The stored `bge-m3` cache holds vectors for pool DA only. It holds none of
   the confirmation set's texts, and the registration forbids cache misses
   without budgeting any embedding call, so the confirmation could never run.
2. The harness's stock null control (`screen_data.foreign_meeting`) is TS3012
   for four of the six confirmation meetings (IS1002d, IS1005a, IS1005b,
   IS1005c). TS3012 is ineligible under epic #1556, and the control's gold is
   read like any meeting's.

## Changes

### 1. Missing confirmation vectors may be computed

For the confirmation set only, a text with no cached vector is embedded.
Embedding is deterministic and involves no model judgment; the arms, labels and
rules do not depend on it.

- **Model and digest:** `bge-m3`, digest pinned to
  `7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab`
  (`drop_ablation.EMBED_DIGEST`, the digest the #1557 cache is keyed on). The
  run refuses any other digest, exactly as it does for the two judges.
- **Scope:** only texts of the confirmation set (its gold, its control's gold,
  and the `ceiling` and `final` objects of its records). The embedder is
  wrapped so that any other text, including any primary-population text, is
  refused and a batch containing one embeds nothing. The primary scoring keeps
  its cache-only guard (zero misses, `OLLAMA_HOST` poisoned).
- **Count:** the preflight reports the exact figure from the stored data. Before
  any run it is **235 texts in 8 calls** (batches of 32), on the amended
  control below.
- **GPU budget:** at most **5 min** of embedding wall time, in addition to the
  45 min of the registration. If it is exceeded the run stops and no partial
  verdict is read.

### 2. The null control for the confirmation meetings

The control for a confirmation meeting is chosen by this rule, written before
any run and independent of any result:

> Walk the thesis cross-team control cycle (IS1002, IS1005, TS3012) from the
> series after the meeting's own, skipping TS3012 and the meeting's own series;
> take the first same-letter meeting that is on the allowlist. If there is none,
> take the first same-letter meeting of the development series in the order
> ES2015, IS1004, TS3009.

This is the stock rule with TS3012 removed, so a meeting whose stock control
was eligible keeps it. Applied to the six meetings:

| Meeting | Stock control | Amended control | Reason |
|---|---|---|---|
| IS1002b | IS1005b | IS1005b | unchanged |
| IS1002c | IS1005c | IS1005c | unchanged |
| IS1002d | TS3012d | **ES2015d** | IS1005d is not on the allowlist; ES2015 is a development series |
| IS1005a | TS3012a | **ES2015a** | IS1002a is not on the allowlist; ES2015 is a development series |
| IS1005b | TS3012b | **IS1002b** | the next eligible series in the cycle |
| IS1005c | TS3012c | **IS1002c** | the next eligible series in the cycle |

Justification: a null control must be real gold of another meeting from the same
kind of material, never the meeting's own, and eligible. Every control above is
a development series (spent on tuning, usable, already read by the primary
population) or a free-pool AMI meeting outside the thesis evaluation and
held-out sets and not TS3012. The primary population keeps its stock controls.
The harness still refuses TS3012 as a meeting and as a control, and refuses any
thesis evaluation or held-out series.

The control changes the lift baseline for four meetings, so the confirmation's
lift is not comparable to a stock-control number; it is only compared with its
own two arms, as the registration already requires.

### 3. Disclosure: the harness validation peeked at the primary numbers

While validating the harness, `score` was run once on the primary population
around the time the registration merged, and the primary numbers were seen: both
judge arms appeared to clear P1 and P2. This was a validation run, not the
registered run; the figures were not committed, and the registered run still
has to be made and reported.

- **No gate, margin, budget or rule was set, changed or tuned on those
  numbers**, then or in this amendment. Every one of them is as registered in
  #1636.
- This amendment concerns **only independent confirmation data**. It touches
  nothing the peek could have informed, and its rules (1 and 2) were derived from
  stored file contents, not from any outcome.
- The primary population remains the data that suggested H, as the
  registration's honest framing already says; the peek does not change that, and
  it is why a clean confirmation set matters.

## Unchanged

The confirmation meetings, the extractor (`qwen3:8b`), runs 1 to 3, the
`judge-fixed` replays and the G1 labels for restored objects only, the digest
pins (`gemma4:26b-a4b` `001e5dafc3c7...`, `gpt-oss:20b` `f38aa0c53da5...`), the
25 s stop line and the 45 min budget, the requirement that the confirmation ran
with a lift point not below -0.005, and the rule that without it the verdict
cannot be ADOPT-CANDIDATE.
