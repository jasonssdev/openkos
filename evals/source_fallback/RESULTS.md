# Results: a bounded Source fallback on a sufficiency refusal (#1741)

Registration: `PREREGISTRATION.md` in this directory. Item 1 was confirmed by the owner and
the rest delegated to the author's recommendations on 2026-11-09, with B3 made strict. It
merged in PR #1748 (commit `c465b987`) before any model call. The run used a detached
checkout at that commit. The store stamps the harness commit `c465b987` with a clean tree,
model `qwen3:8b` (digest
`500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`) and embedder `bge-m3`
(`790764642607`). Prompt hashes are `answer/system` `e0ba5833e90054fa` and
`answer/sufficiency` `3e2b44fcf3e8477f`. The fixture hash is
`7c1be799ffcdf20e54bca5448d58d1bd46754f363db947faa3110c0baaaa41d3`, the same as
registered. The window is 12288. The stored cells are in
`~/Dev/Projects/openkos-eval-runs/source-fallback/`, outside the repository.

## Verdict

**NO-EXPOSURE. This axis ends after pass 1, and no fallback call was made.** Only 3 of the
36 `loss` cells were refused, below `FIRE_MIN` (6). With every Decision removed, the
shipped engine refused almost nothing; it answered from the Source transcripts already in
its context. There was nothing for the fallback to recover on this fixture.

The registration's stop order puts NO-EXPOSURE before NO-HEADROOM (B0). B0 would also have
stopped the run: the `base` correct rate on `loss` questions is 0.81, above
`HEADROOM_MAX` (0.6).

## Pass 1

114 of 114 base cells ran, 0 failed and 0 missing. The pass took 10 min 31 s against a
typical forecast of 0.8 h for both passes.

| Readout (`base`) | Value |
|---|---|
| `loss` cells refused (`insufficient_context`) | 3 of 36 |
| `loss` correct rate | 0.81 |
| `intact` correct rate | 0.90 |
| Foreign-series contamination | 0.06 |
| Unanswerable questions answered (false answers) | 0 of 42 |
| Mean latency | 5.35 s |

Every refusal on the `loss` bundle was the same question: `c4-en`, the English control,
in all 3 runs, asked over the Spanish bundle. Its fallback plan would have read
`consejo-2026-02-10`, `ingenieria-2026-06-11` and `consejo-2026-03-10`, and the gold value
is not in the assembled fallback prompt (`gold_in_prompt` false, reach 0.0). So even the one refused
question was out of the fallback's reach. NO-REACH would have fired had exposure been
met.

All 42 cells of the 14 unanswerable questions were refused by the shipped check. That
includes the 4 absent-fact questions, whose topic is in the Sources without a value.

## Reading

- **The issue's failure mode is not reproduced on this fixture.** Its 10 Source
  transcripts are about 4 KB each, so with the Decisions gone, the Sources the fused
  ranking admits (up to 4 of the 8 slots) carry the facts nearly whole, and the shipped
  answer finds them. #1741 comes from an AMI evaluation where a Source is a full meeting
  transcript, much longer than its share of the window. There the query-aware excerpt can
  miss the fact and the check refuses. That regime was not measured here.
- **What this establishes:** on short Sources, total extraction loss does not make the
  shipped engine refuse; it answers from the Source at 0.81 correct, with no false answer
  on unanswerable questions. A fallback adds nothing there.
- **What this does not establish:** whether the fallback helps when Sources are long and
  excerpting loses the fact. Answering that needs a fixture whose Source bodies exceed
  their share of the window, and it would be a new registration with its own exposure
  floor.

## Cost

| Phase | Forecast | Observed |
|---|---|---|
| Pass 1 (`run`) | in the 0.4 to 1.6 h total | 10 min 31 s |
| Pass 2 (`fallback`) | in the same total | not run (stopped) |
| **Total** | **0.8 h typical, 2.5 h cap** | **about 0.18 h** |
