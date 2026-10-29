# Results: the unattended-apply gate for directed revision findings (#1350)

**Verdict: Stage 0 FAIL. The mode is not built; Stage 1 is not run.** Registered in [PREREGISTRATION-1350.md](PREREGISTRATION-1350.md) (merged as `b6bb7dce`, #1685) before this verdict file existed; the expected outcome stated there was FAIL, no separator.

Command: `uv run python evals/decision_revisions_autoapply/run_autoapply_gate.py decide --out evals/decision_revisions_autoapply/results/stage0-verdict.json` (registered defaults, 0 model calls). Machine-readable verdict: [results/stage0-verdict.json](results/stage0-verdict.json).

```text
# Unattended-apply gate verdict (#1350): FAIL

model `qwen3:8b`, judge prompt `d8238af6410ac31d`
calibration ['runs-20260928T173355Z-qwen3-8b.json']; confirmation ['runs-20260928T204537Z-qwen3-8b.json', 'runs-20260929T011322Z-qwen3-8b.json']

- PASS R0 validity: ok
- FAIL S1 separator: no right-edge confidence strictly above the highest wrong-edge confidence (1.0); confidence does not separate

calibration (descriptive): {"applicable_calls": 322, "wrong": 25, "precision": 0.9224, "wrong_confidences": [[0.9, 8], [0.95, 9], [1.0, 8]], "right_confidences": [[0.95, 138], [1.0, 159]], "wrong_pairs": ["r04", "r13", "r27"]}

confirmation (descriptive): {"applicable_calls": 650, "wrong": 56, "precision": 0.9138, "wrong_confidences": [[0.9, 11], [0.95, 24], [1.0, 21]], "right_confidences": [[0.9, 1], [0.95, 288], [1.0, 305]], "wrong_pairs": ["r04", "r13", "r27", "r46"]}
```

## Reading

- S1 fails: the highest wrong-edge confidence is 1.0, which is also the highest right-edge confidence, so no cutoff `t*` exists. Confidence does not separate a right directed edge from a wrong one.
- The wrong edges are systematic: `r27`, `r13` and `r04` carry them in both arms. Repeating the call would write the same wrong edge every time.
- Consequence: `openkos revisions` and `reconcile --from-findings` keep their per-item consent. A different signal can qualify only through its own pre-registered measurement (ADR-0034's closing rule, by analogy).

## Deviations

None.

## Limitations

Stage 0 labels are one drafter's, with a blind second labeller and a third-model adjudication, not the owner's. The library fixture is one domain, one voice, English only, tuned for `qwen3:8b`. Stage 1 labels are only as good as two blind readings of short Decisions; a PASS shows agreement with those labels on three development series, not that the mode is correct on a user's bundle. The result covers one judge model and one prompt hash. This section is carried into every verdict file.
