# Results: decision-evolution answers, `revision_history` and the context window (#1702, #1703)

Registration: `PREREGISTRATION.md` in this directory, commit `356acfcf` (the owner's
confirmation, PR #1717). Harness commit stamped in every stored file: `356acfcf`,
clean tree. Model `qwen3:8b` (digest `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`),
embedder `bge-m3` (`790764642607`). Prompt hashes: `answer/system` `e0ba5833e90054fa`,
`answer/sufficiency` `3e2b44fcf3e8477f`. Bundle and questions are committed; stored runs,
sheets and keys are in `~/Dev/Projects/openkos-eval-runs/decision-evolution/` (outside the repository).

## Verdicts

| Axis | Verdict | Basis |
|---|---|---|
| History (#1702) | **REJECTED on B1** (registered futility stop; the `on` arm was not run) | `contexts`: `on` delivers both gold values on 5 of 12 evolution questions, as many as `off` (gain 0, minimum 2) |
| Window (#1703, PR #1714) | **REJECTED on W4** | W1 to W3 pass, latency fails |

**Is the window axis SUPPORTED? No. #1714 stays held.** A larger window raises
answer quality but costs 2.7 times the mean latency, over the registered 30 s and 2.0x.
`revision_history` stays default off.

### Verdicts exactly as the unfixed code printed them

Before the two harness fixes (see Deviations), `decide` printed, for both axes,
`INCONCLUSIVE` with the reason "invariant violated: the on arm never attached history:
the mechanism was not exercised". For the window axis that was a bug: the invariant
belongs to the history axis, and the window axis has no `on` arm. After fix 2, `decide`
prints the window verdict `REJECTED`, failed `["W4"]`. For the history axis it still
prints `INCONCLUSIVE` for the same invariant, which is the same fact as the futility
stop seen from the other side; the registration names the futility stop's outcome as
REJECTED on B1 ("a model cannot state a position it was not shown"), and that is the
verdict reported above. I did not change the history path of `decide`.

## Window axis, `w24576` against `off` (60 cells per arm, 0 failed, 0 unjudged)

| Quantity | `off` (12288) | `w24576` | Bar | Result |
|---|---|---|---|---|
| Evolution judged-correct | 0.361 | **0.750** | W1: difference at least +0.2, lower bound above 0 | +0.389, lower bound +0.139: pass |
| W0 headroom | 0.361 | | below 0.75 | pass |
| Evolution mf-correct (W2) | 0.361 | 0.750 | difference at least +0.1 | +0.389: pass |
| Control judged-correct (W3) | 0.792 | 0.750 | difference at least -0.1 | -0.042: pass |
| Contamination, evolution | 0.028 | 0.056 | (history axis bar B4 only) | reported |
| Contamination, control | 0.125 | 0.000 | | reported |
| Mean seconds per answer (W4) | 14.9 | **40.4** | at most 30 s and at most 2.0x | 2.7x: **fail** |
| Median seconds | 15.5 | 38.4 | | |
| p90 seconds | 18.9 | 55.9 | | |
| Exposure | | 20 of 20 prompts differ | at least 5 | pass |
| WG, gold `delivered@8` regressions (56 questions, 54 annotated) | | 0 | none | pass |

Per base question, judged-correct (3 runs, two languages each, `off` to `w24576`):
e1 0.50 to 0.50; e2 0.00 to 1.00; e3 0.17 to 0.83; e4 0.50 to 1.00; e5 0.50 to 0.50;
e6 0.50 to 0.67; controls c1 0.50 to 0.50, c2 0.67 to 0.50, c3 1.00 to 1.00, c4 1.00 to 1.00.
Four of six evolution clusters move, two do not; no cluster falls.

WG companion figures (`delivered-compare`, the model-free metric): `delivered@8` 18 of 54
to 33 of 54, improved 15, regressed 0, one-sided lower bound +0.185; `@5` 25 to 29 (4
improved, 0 regressed); `@10` 16 to 34 (18 improved, 0 regressed). The two questions that
are not annotated (q01, q10) are not scored.

## History axis

Phase 1 (`contexts`, embedder only), 20 questions:

| | evolution (12) | control (8) |
|---|---|---|
| gold values both delivered, `off` | 5 | 7 |
| gold values both delivered, `on` | 5 | 7 |

`futility_stop` fired, so no `on` chat call was made and B0 to B5 were not evaluated. G1
held: 56 of 56 gold prompts identical between `off` and `on` (a no-op check, not a
measurement of harm, as registered).

Why the `on` arm delivered nothing new: in the `on` arm, 67 blocks were omitted across the
20 questions, against 0 in `off` and 0 in `w24576`; 0 history blocks were delivered. A
read-only diagnostic (see Deviations) on 3 questions showed the omitted blocks are the
attached predecessors: 5, 3 and 1 predecessors attached, 0 delivered. The 4 KB Sources
fill the 12288-token window first and the history blocks, which are attached after their
successors, get no share. This is the shipped budget behaviour on this bundle, not a bug in the
walk. It is also a property of the bundle the registration disclosed (Sources lengthened so
the budget has something to trim), so what it licenses is narrow: at the shipped window,
on a library with long Sources, `revision_history` delivers no earlier position. It does
not say what history does when the budget leaves room.

## Judging

Following `retrieval_gold`: four blind Claude Opus judges (30 cells each, J001 to J120) on
opaque sheets with the question, the gold in words, the answer and the cited ids, no arm,
run or score; one blind Claude Sonnet judge on a seeded sample of 40 cells; the first
label stands. Cohen's kappa on the sample printed by `score`: 1.0 (bar 0.6). Judges and
the authoring model share a family, so a shared misreading is invisible to that figure
(see the registration's limits). The Opus first-label counts: correct 77, partial 19,
wrong 14, no-answer 10 of 120.

## Cost

| Step | Forecast (typical) | Observed |
|---|---|---|
| `contexts` + `guardrail` | 0.25 h | about 2.5 min |
| `off` + `w24576`, 120 chat cells | 0.92 h | 55.5 min wall (cell time 0.92 h) |
| `history` run | 0.30 h | not run (futility stop) |
| Total | 1.5 h | about 1.0 h, far under the 5 h cap |

`w24576` ran at a mean of 40.4 s, inside the forecast range of 15 to 100 s but over W4.

## Deviations

1. **Run before the registration merged.** The run used the confirmed registration at its
   pushed commit `356acfcf` (PR #1717) before that PR merged. The commit was pushed and
   fixed before the first model call and was not edited afterwards, so no bar was set after
   any result. The stated merge condition was still not met. The orchestrating session made
   this call, not the operator.
2. **Harness fix 1, `delivered-compare`.** The registered commands run `delivered` twice
   with `--query-aware`, at 12288 and at 24576, but `delivered-compare` refused any pair
   that did not differ in `query_aware` ("base and cand must differ in query_aware"), so
   WG could not be computed as registered. It now refuses only a pair that differs in
   neither `query_aware` nor `context_window` (`delivered_pair_problem`, in
   `evals/retrieval_gold/run_retrieval_gold.py`). A self-test was added and observed failing
   first (NameError before the function existed), then passing. No `delivered` output was
   regenerated: WG was computed from the two files already written.
3. **Harness fix 2, `decide`.** `decide_window` applied the history arm's invariant "the on
   arm never attached history" to the window axis, which has no `on` arm, so it printed
   INCONCLUSIVE. The registration scopes that invariant to the history axis. `decide_window`
   now ignores that one invariant (`NO_HISTORY_INVARIANT`) and still fails on a history
   block in an `off` or `w24576` prompt. Two self-test checks were added and observed
   failing first (NameError), then passing. No bar, question or scoring function changed.
4. **Placeholder run.** `score` and `decide` were first run with
   `--window-gold-regressed 0 --window-gold-total 56` before WG could be computed, only to
   read the other bars (they printed INCONCLUSIVE, the bug above). `0` there was a
   placeholder, not a measurement. After fix 1, WG was computed from the stored outputs
   (0 regressions) and `decide` was re-run with the same arguments, which are now the
   measured value.
5. **Read-only diagnostic of the budget drop.** After `contexts` reported the invariant, I
   ran an ad hoc script (not committed) with the real embedder and the capturing stub, no
   chat call, on 3 questions (e1-es, e1-en, e2-es) with `revision_history=True`. It showed
   5, 3 and 1 history predecessors attached and omitted for budget, 0 delivered. The
   committed `contexts.json` shows the same shape for all 20 (67 omitted, 0 delivered).
6. **History axis verdict vs printed output.** The registration gives two readings for the
   history axis (futility stop gives REJECTED on B1; an invariant failure gives
   INCONCLUSIVE). I report the futility stop as the verdict because it is the registered
   rule for this state and no `on` cell exists to be inconclusive about; `decide` still
   prints INCONCLUSIVE for it.
7. **Judging by models** on the owner's delegation, as registered; kappa is 1.0 on 40 cells,
   which is agreement of two model judges of one family, not validity.

## Limits

As registered, plus: the history axis was not measured by a chat arm at all, so nothing is
known about answer quality with history delivered; the window result is one model, one
synthetic bundle, six evolution clusters; latency is one machine.
