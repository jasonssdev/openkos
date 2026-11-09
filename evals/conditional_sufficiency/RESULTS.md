# Results: a conditional sufficiency check (#1740)

Registration: `PREREGISTRATION.md` in this directory. The owner confirmed it on 2026-11-09
and it merged in PR #1746 (commit `eb287703`) before any model call. Every stored file
carries the harness commit `eb287703` with a clean tree. Models:

- check, primary configuration: `gemma4:26b-a4b`, digest
  `001e5dafc3c77684c2307ebc6ab8e336e10c9b18eca52acf547d72fc83c3ca8c`;
- answer, and check in configuration B: `qwen3:8b`, digest
  `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`;
- embedder: `bge-m3` (`790764642607`).

Prompt hashes are `answer/sufficiency` `3e2b44fcf3e8477f` and `answer/system`
`e0ba5833e90054fa`. The window is 12288. The stored contexts, checks and syntheses are in
`~/Dev/Projects/openkos-eval-runs/conditional-sufficiency/` and `-B/`, outside the
repository. Configuration B reuses the primary configuration's `contexts.json` and
`synth.json` byte for byte, as registered.

## Verdict

**Every treatment is REJECTED in both configurations.** The engine keeps the shipped
always-on check, run before synthesis.

| Treatment | Primary (check on `gemma4:26b-a4b`) | Configuration B (check on `qwen3:8b`) |
|---|---|---|
| `cond_unattributed` | REJECTED on B1, B2, B4 | REJECTED on B1, B2 |
| `cond_lowrank` | REJECTED on B4 | REJECTED on B4 |
| `reduced` | REJECTED on B4, B5 | REJECTED on B3 |

The registered AMI rule applies as confirmed: because every treatment is REJECTED here, no
AMI registration follows.

## Run

Phase 1 ran `contexts` (20 of 20 questions assembled) and `checks` (100 rows, 0 failed).
The phase-1 stop was `None`, so neither NO-BASELINE nor NO-EXPOSURE fired. Phase 2 ran
`synth` (100 rows, 0 failed). Configuration B produced 100 check rows with 0 failed. Cells
read: 100 of 100 in each configuration, so the INCONCLUSIVE floor was not approached.

## Metrics

Seconds are replayed from warm, phase-separated calls (see the registration). "Grounded"
means the 10 grounded questions x 5 runs.

**Primary configuration.** The mean full check on grounded cells took 1.67 s and the mean
synthesis 3.43 s.

| Arm | Survivors | Adjacent | False refusals | Delivered | Mean grounded s | p50 grounded | p95 grounded |
|---|---|---|---|---|---|---|---|
| `baseline` | 3 | 10 | 1 | 0.90 | 4.57 | 4.34 | 6.76 |
| `cond_unattributed` | 1 | 8 | 0 | 1.00 | 3.43 | 3.17 | 5.47 |
| `cond_lowrank` | 3 | 10 | 1 | 0.90 | 4.82 | 4.44 | 6.76 |
| `reduced` | 3 | 10 | 1 | 0.90 | 4.45 | 4.29 | 8.76 |

**Configuration B.** The mean full check on grounded cells took 2.19 s and the mean
synthesis 3.43 s.

| Arm | Survivors | Adjacent | False refusals | Delivered | Mean grounded s | p50 grounded | p95 grounded |
|---|---|---|---|---|---|---|---|
| `baseline` | 3 | 10 | 0 | 1.00 | 5.62 | 5.00 | 9.85 |
| `cond_unattributed` | 1 | 8 | 0 | 1.00 | 3.43 | 3.17 | 5.47 |
| `cond_lowrank` | 3 | 10 | 0 | 1.00 | 5.26 | 4.71 | 9.85 |
| `reduced` | 3 | 10 | 1 | 0.90 | 4.04 | 3.95 | 7.68 |

"False refusals" counts grounded questions refused in at least one run. "Delivered" is the
delivered rate over grounded cells.

## Bars, per treatment

B4 is the grounded latency ratio (arm over baseline, pooled), which must be at most 0.85,
together with its one-sided 95% cluster-bootstrap upper bound, which must be at most 0.95.

| Configuration | Treatment | B1 | B2 | B3 | B4 ratio / upper | B5 p95 vs baseline |
|---|---|---|---|---|---|---|
| primary | `cond_unattributed` | fail (1 of 3) | fail (8 vs 10) | pass | fail (0.750 / 0.982) | pass (5.47 vs 6.76) |
| primary | `cond_lowrank` | pass | pass | pass | fail (1.054 / 1.328) | pass (6.76 vs 6.76) |
| primary | `reduced` | pass | pass | pass | fail (0.973 / 1.204) | fail (8.76 vs 6.76) |
| B | `cond_unattributed` | fail (1 of 3) | fail (8 vs 10) | pass | pass (0.610 / 0.641) | pass (5.47 vs 9.85) |
| B | `cond_lowrank` | pass | pass | pass | fail (0.937 / 1.000) | pass (9.85 vs 9.85) |
| B | `reduced` | pass | pass | fail (1 vs 0) | pass (0.719 / 0.822) | pass (7.68 vs 9.85) |

## Reading

- **`cond_unattributed` fails B1 as the registration predicted.** It caught only 1 of the 3
  survivors and refused 8 of the 10 adjacent questions the baseline refuses; #760 had
  attribution alone refusing 7. Most of the check's value is invisible to `USED:` attribution, so skipping the
  check whenever a `USED:` line is present loses refusals the shipped engine makes.
- **`cond_lowrank` keeps every refusal but saves nothing.** The gate fired on 80% of
  grounded cells (`checks_grounded` 0.8 in both configurations), so it pays almost every
  check and adds the synthesis on cells the baseline refuses.
- **`reduced` is the near miss, and only in configuration B.** With the `qwen3:8b` check,
  checking the top 3 of 5 blocks kept all 3 survivors and all 10 adjacent questions and
  cut grounded latency to 0.719, with an upper bound of 0.822. It was rejected on B3: one
  grounded question was refused in at least one run where the baseline refused none. With
  the `gemma4:26b-a4b` check, the reduced context saved almost nothing (0.973) and widened
  the tail.
- **On this corpus the check is cheap.** It took 1.67 s on the primary configuration, so
  the issue's figure of about 9.6 s of check per query is not reproduced. The contexts here
  are far shorter than an AMI series. The registration disclosed this limit: the saving on
  long meeting contexts is not measured here.
- **Two findings fall outside the bars.**
  - The shipped `gemma4:26b-a4b` check refused one grounded question in at least one of 5
    runs (baseline delivered rate 0.90). The `qwen3:8b` check refused none.
  - The `qwen3:8b` check was slower than the `gemma4:26b-a4b` check (2.19 s against
    1.67 s on grounded cells), consistent with the 26B model being a mixture of experts
    with about 4B active parameters. A smaller check model is therefore not a latency win
    on this machine.

## Cost

| Phase | Forecast (typical) | Observed |
|---|---|---|
| `contexts` | (embedder only) | 6 s |
| `checks`, primary | (in the 0.6 h total) | 4 min 14 s |
| `synth` | (in the 0.6 h total) | 5 min 15 s |
| `checks`, configuration B | 0.3 h | 3 min 49 s |
| **Total** | **0.9 h** | **about 0.22 h** |

The 2 h hard cap was not approached. Models were already present, so no pull time was
spent.
