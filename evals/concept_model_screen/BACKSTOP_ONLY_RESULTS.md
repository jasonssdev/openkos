# Results: can the selector judge's cut be removed, keeping only the positional backstop? (#1579, step 5)

Registered in `BACKSTOP_ONLY_PREREGISTRATION.md` (PR #1636) and
`BACKSTOP_ONLY_AMENDMENT-1.md` (confirmation set only), before the registered
run. Run with `backstop_only.py` at main commit `78bcbf76` (clean tree; the
stamp in `restored/labels.json.stamp` records it), on 2026-10-27 from 18:20:11
to 18:28:29 by the machine clock (the log labels it EST). Models called:
`gemma4:26b-a4b` (digest `001e5dafc3c7...`, the fixed judge), `gpt-oss:20b`
(digest `f38aa0c53da5...`, the G1 labeller), and `bge-m3` (digest
`7907646426...`, embeddings), each matching its registered pin. Stored outputs:
`~/Dev/Projects/openkos-eval-runs/backstop-only/` (`scores/score.json`,
`scores/cost.json`, `scores/confirmation.json`, `restored/labels.json`).
Part of #1556; refs #1579.

## Verdict

**ADOPT-CANDIDATE; every registered gate holds in both arms** (the harness's
own line). V1 found no problem and no window was unlabelled, so the run is
neither void nor degraded. This adopts nothing and changes no default.

## Gates

Comparison is `backstop-only` minus judge, paired per window; one-sided 95%
lower bound against the margin, with the two-sided 95% interval beside it.
10,000 resamples, seed 42, as registered.

| Gate | Arm | Point | One-sided bound | Two-sided 95% CI | Margin | Result |
|---|---|---|---|---|---|---|
| P1 window lift | self | +0.0153 | lower +0.0069 | +0.0054, +0.0277 | -0.005 | pass |
| P1 window lift | fixed | +0.0118 | lower +0.0039 | +0.0025, +0.0211 | -0.005 | pass |
| P2 strict G1 precision | self | +0.0001 | lower -0.0269 | -0.0325, +0.0323 | -0.05 | pass |
| P2 strict G1 precision | fixed | +0.0097 | lower -0.0190 | -0.0243, +0.0479 | -0.05 | pass |

- P1 detail: 37 windows, 0 records skipped. Lift: backstop-only 0.0518;
  judge 0.0366 (self) and 0.0400 (fixed); the ceiling with no backstop 0.0527.
  The positional backstop therefore gives up 0.0009 of lift (13 of 303
  windowed objects, 23 of 352 objects, cut in total).
- P2 detail: 149 windows. Backstop-only 268 of 290 supported (0.924). Judge:
  146 of 158 (0.924, self), 139 of 152 (0.914, fixed). The restored objects
  are about as supported as the kept ones. The lower bounds sit about 0.023
  (self) and 0.031 (fixed) inside the margin.
- S1 clutter share (non-gating as registered; +0.05 if it had gated):

  | Arm | Point | One-sided upper | Two-sided 95% CI | Backstop-only | Judge |
  |---|---|---|---|---|---|
  | self | +0.0165 | +0.0749 | -0.0503, +0.0840 | 47 of 290 (0.162) | 23 of 158 (0.146) |
  | fixed | +0.0042 | +0.0487 | -0.0545, +0.0580 | 47 of 290 (0.162) | 24 of 152 (0.158) |

  The self arm's upper bound is above +0.05. Had S1 gated, self would not have
  cleared it and the outcome would have been INCONCLUSIVE, as the registration
  expected. It did not gate. The step 3 control behind this label passed only
  at its bar (8 of 11), so S1 is a weak signal either way.
- Restored objects by type (descriptive). Self: Concept 21 of 60, Decision 56
  of 129, Event 13 of 25, Project 41 of 61, Entity 1 of 2. Fixed: Concept 27
  of 60, Decision 40 of 129, Event 17 of 25, Project 53 of 61, Entity 1 of 2.
  No Person, Organization or Procedure object is restored in either arm.

### C1, cost (deterministic counts, budgets as registered)

| Count | Self | Fixed | Budget | Result |
|---|---|---|---|---|
| Objects (329 vs 214 / 209) | 1.537x | 1.574x | at most 1.65x | pass |
| Embedding calls | 1.537x | 1.574x | at most 1.65x | pass |
| Identity rows per 25 sources (12 vs 4 / 2) | +8 | +10 | at most +12 | pass |
| Identity rows per workspace (26 vs 7 / 3) | +19 | +23 | at most +25 | pass |
| Cap of 50 | max 8 produced | max 8 produced | must not bind | pass |
| Revision pair pool (reported only) | 1308 vs 481 (2.72x) | 1308 vs 692 (1.89x) | not gated | n/a |

The fixed arm is closest to its budgets (object ratio 1.574 of 1.65, +23 of
+25 rows per workspace).

### V1, validity

No problems. The backstop replay of the `ceiling` stage reproduced the
recorded `final` stage: 23 of 352 objects cut (13 of 303 windowed). The step 1
and step 3 label files used hash to
`624789b62d94e1a4b7d00a692111ab2c3ea32bb04fdb560d99ef7e0f34483397` (G1) and
`d69840d613ceee4688f630ddc13da25ff356587ee56d05e2471aae5b51cde41b` (clutter).
Coverage: windows labelled 154 of 154 for self and fixed on both steps; 0
records skipped; 0 files missing.

## Confirmation set

Six free-pool meetings (IS1002b, IS1002c, IS1002d, IS1005a, IS1005b,
IS1005c), extractor `qwen3:8b`, runs 1 to 3: 18 meeting-runs. Status `ran`;
nothing excluded. TS3012 is refused as a meeting and as a control. Null
controls follow Amendment 1: IS1002b to IS1005b, IS1002c to IS1005c, IS1002d
to ES2015d, IS1005a to ES2015a, IS1005b to IS1002b, IS1005c to IS1002c. The
confirmation lift is comparable only with its own two arms, not with a
stock-control number.

| Arm | Lift, backstop-only minus judge | Interval (as stored, descriptive) | Required point |
|---|---|---|---|
| self | +0.0120 | +0.0038, +0.0217 | not below -0.005 |
| fixed | +0.0157 | +0.0049, +0.0300 | not below -0.005 |

Both points clear the rule. The registration says this set can show only "not
clearly worse"; both interval lower ends are also above zero, which the rule
did not require. Restored objects (G1, `gpt-oss:20b`, descriptive only):
**64 of 69 supported** (0.928; 4 partial, 1 unsupported). That is in line with
the primary population's 0.924 backstop-only share, from a far smaller sample.

## Budget and time

- Registered budget: 45 min of measured model time plus 5 min of embedding.
  Measured model time: **454 s (7.6 min)**; wall time 8 min 18 s.
- Embeddings: 235 texts in 8 calls, 4 s (the preflight reported 235 in 8).
- Fixed-judge replays: 18 meeting-runs, about 95 s after a 17 s warm-up.
- G1 labels: 44 calls (preflight upper bound 72 windows; forecast at most
  200). Sum 360 s, mean 8.2 s, median 6.7 s, maximum 27.8 s. The first 20
  calls averaged 8.1 s against the 25 s stop line, which is what the line
  tests; the single 27.8 s call is not a breach.
- The primary-population scoring called no model.

## Deviations from the registration

1. **Harness-validation peek (disclosed in Amendment 1).** While validating the
   harness, `score` was run once on the primary population around the time the
   registration merged, and both judge arms appeared to clear P1 and P2. No
   gate, margin, budget or rule was set or changed on those numbers. The
   registered run, reported here, is a separate run.
2. **The primary population is the hypothesis-generating data.** Steps 1 to 3
   and the cost measurement read the same 25 meeting-runs that suggested H.
   The primary gates are therefore a re-analysis, not an independent test, and
   the confirmation set carries the independent weight. That weight is small:
   6 meetings, one extractor, a lift interval about 0.02 wide, and 69 labelled
   restored objects. The two populations point the same way; neither alone is
   strong.
3. **Amendment 1 was applied as written:** the confirmation vectors were
   embedded (235 texts, pinned digest, confirmation texts only) and four
   controls were substituted for TS3012.
4. **Nothing else deviates.** Margins, budgets, bootstrap, arms, digest pins
   and the decision rule are as registered. Seed indices 1 and 2 of the judge
   arms are not decisive and are not reported here.

## Reading P1

P1 is positive: the backstop-only set has more window lift than the judge's
set (+0.015 self, +0.012 fixed) on the primary population, and the
confirmation set agrees in sign. That is expected from the construction (the
registration's framing item 2: restoring objects adds coverage, and the
backstop loses almost none of it). It means the judge's cut costs gold
coverage and that the positional backstop is not what loses it. It does **not**
mean the restored objects are better knowledge: P2 shows them about as
supported as the kept ones, and S1 and human value remain open. The lift gate
was a sanity check, not a hard test.

## What this cannot show

- Whether a person wants the restored objects: P2 is source support from an
  LLM label and S1 a weak LLM label, with no human curator.
- Text that is not an English AMI meeting, including the prose notes the judge
  also runs on in production.
- Large sources: the backstop binds in 8 of 25 sources and is positional; the
  loss on sources with many more than 20 objects is not bounded.
- Revision candidates, proximity edges, edge typing and contradictions (the
  revision pool is reported, not priced).
- Extractors outside the five, other languages, other hardware.

## What this licenses

**ADOPT-CANDIDATE licenses writing an ADR, and a spec delta to
`extraction-union-judge` (which requires a judge-selects contract), each in its
own PR as epic #1556 requires, proposing to drop the judge's cut.** It does not
adopt anything, changes no default, and is not an approval. This results PR
changes nothing in `src/`.

## Discrepancies between the log and the stored outputs

The log's figures for P1, P2, S1, coverage, per-type counts, C1, V1 and the
confirmation match `scores/*.json` exactly. Two bookkeeping notes. The log
records four `exit=0` lines (confirm, score, cost, decide) and holds no output
for `population` or `replay`, so "every subcommand exited 0" is established
here for those four only. And the cache holds 17 chat and 41 judge files
against 18 replays and 44 label calls, which is consistent with identical
prompts sharing one content-addressed file but was not checked.
