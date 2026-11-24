# Results: an `evidence` instruction in the concept-extraction prompt (#1768)

**Decision: DO NOT SHIP.** The instruction does not make the local default
better, and no hosted arm that fixes the defect also holds its guardrails.
The shipped prompt is not changed and no default changes.

Rules: [`EVIDENCE_PREREGISTRATION.md`](EVIDENCE_PREREGISTRATION.md), merged in
#1786 (`7c72a0cb`) before any treatment call. The rule was applied by
`evidence_ab.py report` to the stored records and scores, not re-tuned. Pool
DA, runs 1 and 2 (n = 2). Controls reused from cache (`qwen3:8b`: #1557
records; both hosted arms: #1767 records of 2026-11-23). Treatments run on
2026-11-23.

No pair is INCOMPLETE: every treatment has 10 of 10 meeting-runs and 82 of 82
windows answered, no provider-failure window, no ceiling stopped a run, and
the local digest equals the control's.

## Decision

| Arm | Unevidenced delta [95%] | Guardrails | Verdict for the arm |
|---|---|---|---|
| `qwen3:8b+evidence` (primary) | **+0.1871 [+0.1072, +0.2846]** | G1 and V4 fail | The upper bound is above 0 (condition 1 fails) and two guardrails fail |
| `openai-gpt-4.1-mini+evidence` | -0.2704 [-0.5263, -0.0965] | G1 and V1 fail | Does not qualify |
| `anthropic-claude-haiku-5-5+evidence` | -0.8571 [-0.9045, -0.7686] | G1 fails | Does not qualify |

Why SHIP is not reached:

1. **Condition 1 (local) fails.** On `qwen3:8b` the unevidenced rate went up,
   5 of 50 to 29 of 101, and the interval excludes 0 on the wrong side.
   G1 precision delta lower bound is -0.0741 (below -0.05) and V4 volume is
   10.10 objects per meeting-run against the control's 5.00 (outside
   [2.50, 7.50]).
2. **Condition 2 (hosted) fails.** Neither hosted arm holds its own
   guardrails. Haiku has the large effect the instruction was written for, and
   fails only G1 (lower bound -0.0864, below -0.05). `gpt-4.1-mini` fails G1
   and V1.

## The primary metric and the quote shares

Final objects, pooled over the two runs, n of TOTAL. Delta = treatment minus
control, meeting-level bootstrap (five meetings with replacement, same draw
for both arms, 10,000 resamples, seed 42).

| Arm | Unevidenced control | Unevidenced treatment | Delta [95%] | Verified quote | `not_in_source` | `too_short` | Supplied the field |
|---|---|---|---|---|---|---|---|
| `qwen3:8b+evidence` | 5 of 50 (0.100) | 29 of 101 (0.287) | +0.1871 [+0.1072, +0.2846] | 66 of 101 (0.653) | 13 of 101 (0.129) | 3 of 101 (0.030) | 82 of 101 (0.812) |
| `openai-gpt-4.1-mini+evidence` | 19 of 27 (0.704) | 13 of 30 (0.433) | -0.2704 [-0.5263, -0.0965] | 9 of 30 (0.300) | 4 of 30 (0.133) | 0 of 30 | 13 of 30 (0.433) |
| `anthropic-claude-haiku-5-5+evidence` | 156 of 156 (1.000) | 22 of 154 (0.143) | -0.8571 [-0.9045, -0.7686] | 132 of 154 (0.857) | 2 of 154 (0.013) | 0 of 154 | 134 of 154 (0.870) |

- Every control has 0 supplied quotes (the prompt never asked).
- Fabricated (`not_in_source`) over the objects that supplied the field:
  `qwen3:8b` 13 of 82 (0.159), `gpt-4.1-mini` 4 of 13 (0.308), haiku 2 of 134
  (0.015). `too_short` is counted apart: 3 `qwen3:8b` objects, none elsewhere.
- Haiku quotes a verifiable source line for 86% of its objects, against 0%
  before. This is the effect #1768 was after, and it is large.
- `gpt-4.1-mini` supplies a quote on fewer than half of its objects
  (13 of 30), and 4 of those 13 are not in the source. Its upper bound
  (-0.0965) is also above the -0.10 threshold of condition 2, by 0.0035.
- `qwen3:8b` doubled its objects (50 to 101) and supplied a quote for 82 of
  them, but 13 of the quotes are invented and 19 objects supplied none. The
  rise in the unevidenced rate comes with that volume change; this run does
  not separate the instruction's effect on what is extracted from its effect
  on what is quoted.

## Guardrails (each against the arm's own control)

| Arm | Window lift delta [95%] (need lower >= -0.03) | G1 precision (control, treatment) | G1 delta [95%] (need lower >= -0.05) | V1 failed windows (control, treatment; need <= control + 0.02 and <= 0.05) | V4 objects per meeting-run (control, treatment; need 0.5x to 1.5x) | V5 verbatim (need <= control + 0.10) |
|---|---|---|---|---|---|---|
| `qwen3:8b+evidence` | +0.0084 [-0.0094, +0.0254] holds | 38 of 41, 76 of 82 | +0.0000 [-0.0741, +0.0870] **fails** | 2 of 82, 1 of 82 holds | 5.00, **10.10 fails** | 0 of 50, 0 of 101 holds |
| `openai-gpt-4.1-mini+evidence` | +0.0010 [-0.0102, +0.0159] holds | 11 of 11, 12 of 13 | -0.0769 [-0.2727, +0.0000] **fails** | 0 of 82, **6 of 82 (0.073) fails** | 2.70, 3.00 holds | 0 of 27, 0 of 30 holds |
| `anthropic-claude-haiku-5-5+evidence` | -0.0052 [-0.0228, +0.0138] holds | 127 of 134, 125 of 134 | -0.0149 [-0.0864, +0.0363] **fails** | 0 of 82, 0 of 82 holds | 15.60, 15.40 holds | 0 of 156, 0 of 154 holds |

- V3 is not evaluated: no prose fixtures are run.
- Window lift is not hurt on any arm; the three intervals all contain 0.
- The G1 failures are lower bounds, not point losses: the points are +0.0000,
  -0.0769 and -0.0149. The `gpt-4.1-mini` interval rests on 13 judged
  objects, the haiku one on 134, and the `qwen3:8b` one compares 41 judged
  control objects with 82. A failure here means "not shown to hold at
  n = 2", which is what the registered rule asks.
- `gpt-4.1-mini` V1: 6 `unparseable` windows (control 0). The longer reply
  with a quote on each object is a plausible cause; this run does not test it.

Window outcomes by cause (82 windows per arm, treatment; control in
parentheses): `qwen3:8b` 67 ok, 1 unparseable, 14 empty (69, 2, 11);
`gpt-4.1-mini` 75 ok, 6 unparseable, 1 empty (81 ok, 0, 1 empty); haiku 82 ok
(82 ok). All 112 calls per arm ended `ok` at the client level, so there is
no provider failure and no re-run was used.

## Calls and spend

The ledger bills 102 calls per hosted arm: the 10 participant-capture calls
per arm replay from the control's cache, because that prompt is unchanged.
Records show 112 calls per arm. Spend is the harness ledger at the registered
prices.

| Arm | Billed calls (ceiling 200) | Prompt tokens | Completion tokens | Spend USD (ceiling 1.00) | Forecast central / high USD | #1767 control USD |
|---|---|---|---|---|---|---|
| `openai-gpt-4.1-mini+evidence` | 102 | 350,722 | 27,924 | 0.185 | 0.21 / 0.26 | 0.186 |
| `anthropic-claude-haiku-5-5+evidence` | 102 | 542,054 | 159,173 | 0.134 | 0.18 / 0.30 | 0.139 |
| **Total** (ceiling 2.00) | 204 | | | **0.319** | 0.39 / 0.56 | 0.325 |

Both arms spent below their central forecast and at under 19% of their
ceiling. Local: 112 calls (102 not served from cache), no money, 1,040 s of
recorded meeting-run wall time in total (mean 104 s per meeting-run, against
the 292 s the registration used from #1557). Hosted wall time: 419 s
(`gpt-4.1-mini`) and 776 s (haiku).

## Deviations from the procedure

None changes a number or a rule.

1. **Prices not re-confirmed on the provider pages.** The registration asks
   for it; the runner did not open them and used the registered prices
   (`gpt-4.1-mini` 0.40 / 1.60, haiku 0.10 / 0.50 per 1M tokens), which were
   confirmed in #1767 on the same date (2026-11-23).
2. **Sequential arms.** The local arm ran first and to completion, then the
   two hosted arms one after the other, then the judge and the scores. The
   registration did not say they could overlap, so they did not.
3. **Hosted controls and treatments share the date.** The registration
   expected the treatment to run later than the control; both ran on
   2026-11-23, so alias drift between them is not expected, though not
   proven. The digests carry that date.
4. **Gitignored fixtures copied into the worktree** (the four files under
   `examples/extraction-corpus/sources`), as the procedure says; none is
   committed.
5. **Step 0 replay** ran as written and added `final_evidence` to the
   control records; the cached chat entries were not touched.
6. **Local wall time** was lower than the registration's estimate (about
   17 minutes against about 50). Seeds, timeouts and the digest were as
   registered; the cause was not investigated.

## Not concluded

- Whether the instruction helps a model that is not `qwen3:8b`. Haiku shows
  the effect on one date and one alias; `gpt-4.1-mini` is inconclusive (13
  quotes). Neither is a default and neither qualifies under the rule.
- Whether the local result is the instruction or the volume change. The
  `qwen3:8b` arm doubled its objects, which alone moves the unevidenced rate
  and trips V4; this A/B does not isolate the two.
- Power is low (n = 2, five meetings, 37 scored windows). The `qwen3:8b`
  upper bound above 0 is a measured increase on these meetings, but a
  different pool could read differently.
- A verified quote proves a source line was copied, not that it supports the
  object; G1 guards support and its intervals are wide.
- Nothing about private or non-English sources (no prose fixtures, V3 not
  evaluated), or about the pipeline's other tasks.
- The selector judge ran on each arm's own model and read the longer
  replies; its interaction with the instruction (#1571) is not separated.
- The re-ask and participant-capture prompts do not ask for the field, so the
  objects they add are not covered by the quote.
- Hosted reasoning budgets and sampling are provider defaults.
- #1768 stays open: the prompt decision lands in its own change. A follow-up
  would need a new registration (for example a quote-only variant that does
  not change the object count, or a per-backend prompt), not an edit to this
  one.
