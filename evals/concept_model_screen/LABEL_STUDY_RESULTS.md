# Results: is what the selector judge cuts worth a page? (#1579, step 3)

Registered in `LABEL_STUDY_PREREGISTRATION.md` (merge commit `518ddf94`, PR
#1613) before any label call was sent. Run with `label_study.py` at main commit
`41c18ed9` (clean tree; the stamp in `label-study/stamp.json` records it), on
2026-10-26 from 03:53:45 to 04:16:42 UTC. The only model called was
`gpt-oss:20b`, digest
`f38aa0c53da5f8c49d08c43a99df24ff53167fe68e24664a7777288e7656fdfe`, with the
registered prompt (`label-study/label`, sha256_16 `3a5ae5acc4a7c38b`). Scores:
`~/Dev/Projects/openkos-eval-runs/label-study/scores/label_study.json`.

## Verdict

**Mixed or inconclusive by the registered rule. Both arms are inconclusive.**
The validity control passed, so the study is not void; no window was
unlabelled, so it is not degraded. Nothing is adopted.

## Population, control and timings

- Population: 303 windowed ceiling objects in 154 windows, sha256
  `76a9d42c3d70445ae4899d9b21c4a1c08299a4030f4c3d3867d8462a052aef84`
  (matches the registration).
- Control: 11 dedup-removed objects in 8 windows, sha256
  `2cfad2ef0510e8c95b9b1d9846ab26c83bfe8a41b7f7d962669d00aac3bd6f73`
  (matches the registration).
- Calls: 162 (154 label + 8 control), the forecast count; every call produced a
  usable reply on the first attempt (162 stored call records, no retry).
- Time: 1365 s summed over the 162 calls (stored `wall_s`), **8.43 s per call**
  (median 6.9 s, maximum 39.6 s), against the forecast 9.9 s per call
  (1604 s). Wall time 22 min 57 s (03:53:45 to 04:16:42 UTC) against about 28
  min forecast and a 1 h budget; one 10.8 s warm-up call is not counted. The
  first 20 calls were nowhere near the 25 s stop line.
- The score was recomputed from the stored labels in a fresh worktree
  (`OLLAMA_HOST=http://127.0.0.1:1`, no model call) and its output is
  identical to the run's, line for line.

## Scorer output (verbatim)

```
## Clutter share (merge or drop), seed 0, primary pooled

| arm | kept | cut | D = cut - kept [95% CI] | cut labelled keep |
|---|---|---|---|---|
| self | 23 of 165 (0.139) | 25 of 138 (0.181) | +0.0418 [-0.0932, +0.1794] | 113 of 138 (0.819) |
| fixed | 24 of 160 (0.150) | 24 of 143 (0.168) | +0.0178 [-0.0920, +0.1249] | 119 of 143 (0.832) |

## Coverage

- self: windows labelled 154 of 154; objects labelled 303 of 303; records skipped 0
- fixed: windows labelled 154 of 154; objects labelled 303 of 303; records skipped 0

## By type and side (descriptive only)

- self Concept/cut: {'keep': 18, 'merge': 5}
- self Concept/kept: {'keep': 28, 'merge': 8, 'drop': 1}
- self Decision/cut: {'keep': 42, 'merge': 15}
- self Decision/kept: {'keep': 64, 'merge': 8}
- self Entity/cut: {'keep': 1}
- self Entity/kept: {'keep': 1}
- self Event/cut: {'keep': 11, 'drop': 3}
- self Event/kept: {'keep': 10, 'drop': 1}
- self Person/kept: {'drop': 3, 'merge': 1, 'keep': 4}
- self Procedure/kept: {'keep': 17, 'merge': 1}
- self Project/cut: {'keep': 41, 'merge': 2}
- self Project/kept: {'keep': 18}
- fixed Concept/cut: {'keep': 22, 'merge': 5, 'drop': 1}
- fixed Concept/kept: {'keep': 24, 'merge': 8}
- fixed Decision/cut: {'keep': 28, 'merge': 13}
- fixed Decision/kept: {'keep': 78, 'merge': 10}
- fixed Entity/cut: {'keep': 1}
- fixed Entity/kept: {'keep': 1}
- fixed Event/cut: {'drop': 3, 'keep': 15}
- fixed Event/kept: {'keep': 6, 'drop': 1}
- fixed Person/kept: {'drop': 3, 'merge': 1, 'keep': 4}
- fixed Procedure/kept: {'keep': 17, 'merge': 1}
- fixed Project/cut: {'keep': 53, 'merge': 2}
- fixed Project/kept: {'keep': 6}

## Validity control

8 of 11 duplicates labelled merge or drop (0 unlabelled); labels {'keep': 3, 'merge': 7, 'drop': 1}; passes: True

arms: {'self': 'inconclusive', 'fixed': 'inconclusive'}; verdict: mixed_or_inconclusive
```

## The rules applied mechanically

Rules 0 (degraded, void) and 1 (fewer than 30 labelled cut objects) do not
fire: 0 of 154 windows unlabelled, the control is 8 of 11 (the bar), and the
cut sides hold 138 and 143 labelled objects. That leaves the two substantive
rules, and both fail in both arms:

| Arm | Lower bound of D | Upper bound of D | Cut labelled keep |
|---|---|---|---|
| self | -0.0932 | +0.1794 | 113 of 138 (0.819) |
| fixed | -0.0920 | +0.1249 | 119 of 143 (0.832) |

- **Rule "the cut buys clutter reduction" fails.** It needs the lower bound of
  D above +0.10. The lower bounds are -0.0932 and -0.0920, below zero and
  about 0.19 under the bar. The point estimates (+0.0418, +0.0178) are not
  above +0.10 either.
- **Rule "the cut removes keepable objects" fails.** It needs the upper bound
  of D below +0.10 **and** at least 70% of cut objects labelled keep. The
  second condition holds (0.819 and 0.832, well above 0.70). The first does
  not: the upper bounds are +0.1794 and +0.1249, both above +0.10, by 0.079
  and 0.025. This is the only reason the arms are not rule 3.
- Both arms therefore fall to "inconclusive", and the overall verdict is mixed
  or inconclusive (the arms agree, but on the inconclusive branch).

## Reading

- The point estimates are near zero: cut objects are labelled merge or drop
  at 0.181 and 0.168, kept objects at 0.139 and 0.150. The cut barely prefers
  clutter. The direction is the one the cut would want, and the size is
  4 and 2 points against a 10 point bar.
- The intervals (+-0.14 and +-0.11 around the point) are too wide to close the
  "removes keepable objects" rule at this n, as the registration predicted
  ("+-0.10 or wider, rule 2 or rule 3 may well not be reachable"). The 303
  objects come from 25 meeting-runs of 5 meetings, so the cluster bootstrap
  has few independent units.
- Most of what the cut removes is labelled worth keeping: 81.9% and 83.2%.
  That is a descriptive fact about the labeller, not a decision: the rule needs
  the interval to rule out a 10 point clutter benefit and it does not.
- The control passed only at the bar (8 of 11, the least that passes; 3
  duplicates were labelled keep). The rubric sees exact-title duplicates most
  of the time but not reliably, so its sensitivity to subtler clutter is
  modest, which weakens a "not clutter" reading of the cut objects further.
- By type (descriptive only): Project cut objects are 41 of 43 and 53 of 55
  keep, Decision cut objects 42 of 57 and 28 of 41 keep. Cell counts are small.

## What this means for #1579

Across steps 1 to 3 the cut has shown no measured benefit on any of the three
axes tested, and its cost is measured:

| Step | Axis | Result for the cut |
|---|---|---|
| 1 | Source support | Cut objects as supported as kept (0.928 vs 0.927 self); inconclusive |
| 2 | Redundancy | Cut objects as non-redundant as kept; about 94% not near-duplicates; inconclusive |
| 3 | Standalone value | Cut objects barely more clutter than kept (+0.042, +0.018); inconclusive |
| Cost | Window lift (`STAGE_LOSS_RESULTS.md`, `JUDGE_STUDY_RESULTS.md`) | -0.0161 [-0.0276, -0.0081], 38 of 450 gold sentences lost |

No step shows the cut buys anything, and no step shows (at its registered
bar) that it removes something valuable on a non-lift axis either. Each step is
inconclusive on its own, which is the weakest of the possible findings: the
evidence neither supports the cut nor condemns it, except for the lift cost.

This results PR adopts nothing; the epic forbids adoption here, and any change
needs its own pre-registration, merged before its first run, and an ADR. The
options the owner faces, without a recommendation (the table in
`NEXT_STEP.md` section 3 has the user-facing description of each):

1. **Leave the judge as is** (option A) and record the three inconclusive
   steps as the finding that the cut is unproven, at a measured lift cost.
2. **Register a drop-only or cap-only judge arm** (options C or B) for a
   lift comparison. The data here (80%+ of cut objects labelled keep) is the
   motivation the registration named, but the rule that would have licensed it
   did not fire.
3. **Remove the judge** (option D) or **make it opt-in** (option E), each
   through its own pre-registration, a spec delta to `extraction-union-judge`,
   and an ADR.
4. **Measure the downstream cost** first (edge typing, contradictions on the
   two bundles), or a **human-labelled sample** to replace the LLM labeller,
   since further LLM labelling at this n is unlikely to narrow the interval
   enough to decide.

Still open and owner-only (`NEXT_STEP.md` section 5): whether a smaller bundle
is worth lost gold content and how much; whether the Project cut (43 of 61 and
55 of 61) and the Decision cut (57 of 129 and 41 of 129) are acceptable
defaults; whether an LLM-labelled value judgment suffices or a human sample is
required (decision 1 of the registration accepted the LLM label for this
study only); whether an opt-in switch is acceptable product surface; and
whether the downstream stages are priced before any change.

## Deviations and limits

- No deviation from the registration: population, hashes, judge digest,
  prompt, seed, bootstrap and rules are as committed. Timing came in under the
  forecast.
- One LLM labeller, one label per object, window-only source, the labeller sees
  the whole window's candidates; 303 objects are not independent; the control
  is 11 objects, passed at the bar. The result is a statement about this
  labeller at this n, not about a curator.
