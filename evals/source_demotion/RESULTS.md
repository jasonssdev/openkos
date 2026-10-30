# Results: Source-block demotion under the context budget (#1703)

Registration: `PREREGISTRATION.md` in this directory, confirmed by the owner and merged
in PR #1732 (commit `228ec5b2`) before any model call. Harness commit stamped in every
stored file: `228ec5b2`, clean tree. Model `qwen3:8b` (digest
`500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`), embedder `bge-m3`
(`790764642607`). Prompt hashes: `answer/system` `e0ba5833e90054fa`,
`answer/sufficiency` `3e2b44fcf3e8477f`. Window 12288. Stored runs, sheets, keys and
judgments are in `~/Dev/Projects/openkos-eval-runs/source-demotion/` (outside the
repository).

## Verdict

**REJECTED on B1, B2 and B6.** Demoting a Source that has a derived page in the same
context changed no answer's judged correctness, and it lowered how often the demoted
Source is cited. The engine keeps sharing the budget as shipped (`_bound_bodies`).

## Phase 1 (embedder only)

- 11 of 20 prompts differ between the arms (6 evolution and 2 control base questions);
  13 Source blocks demoted; mean prompt shrink 3.2%. No stop fired.
- No block omitted in either arm (`omitted_base` 0, `omitted_demote` 0); 98 blocks
  excerpted in `base`. As the registration states, the shipped engine already cuts
  Sources to excerpts rather than dropping them whole.
- Both gold values in the prompt: evolution 5 of 12 in both arms, controls 7 of 8 in
  both; 0 questions lost a gold value.
- Guardrail G1: 56 of 56 gold prompts keep every block.

## Phase 2: 11 treated questions x 3 runs x 2 arms = 66 cells, 0 failed, 0 unjudged

| Bar | `base` | `demote` | Threshold | Result |
|---|---|---|---|---|
| B0 headroom (evolution judged-correct, `base`) | 0.556 | | below 0.75 | pass |
| B1 benefit (evolution judged-correct) | 0.556 | 0.556 | difference at least +0.2, lower bound above 0 | +0.000, lower bound 0.000: **fail** |
| B2 corroboration (mf-correct) | | | difference at least +0.1 | +0.000: **fail** |
| B3 controls (judged-correct) | 1.000 | 1.000 | difference at least -0.1 | 0.000: pass |
| B4 contamination | 0.000 | 0.000 | rise at most 0.05 | pass |
| B5 latency (mean s per answer) | 10.70 | 9.17 | ratio at most 1.1 | 0.86: pass |
| B6 citation integrity | 10 of 20 (0.500) | 7 of 21 (0.333) | drop at most 0.1, at least 10 pairs per arm | -0.167: **fail** |
| B7 new empty answers | 0.000 | 0.000 | at most 0.05 | pass |
| Agreement (Cohen's kappa, 40-cell second sample) | | | at least 0.6 | 1.00: pass |

The cost was about 0.4 h of GPU, under the typical forecast of 0.7 h.

## Reading

- The answers did not change in quality: the derived Decision pages already state the
  whole answer on this bundle, so the characters freed from the Source added nothing
  the model used.
- The one thing demotion bought is speed, 14% lower mean latency. The registration
  has no neutral-and-faster verdict, so that does not rescue it.
- The cost is provenance: with a shorter Source in front of it, the model cites the
  meeting a Decision came from less often (0.500 to 0.333).

## Judging

Two blind Claude Opus judges labeled the first sheet, J001 to J033 and J034 to J066.
One blind Claude Sonnet judge labeled the seeded 40-cell second sample. Each judge read
only its sheet: question, gold, reference text, answer and cited ids, under opaque
codes, and nothing about arm, run, checks or verdict. The first judges' labels stand.
Labels: 44 correct, 16 partial, 6 wrong, 0 no-answer.

## Deviations

None from the registered commands. The two Opus judges each filled a copy of the same
sheet for their range; the copies were joined by entry code before `judge-ingest`,
which the registration's "one per range of cells" allows.
