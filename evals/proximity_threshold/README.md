# `proximity_threshold` — calibrating the candidate-edge floor (#1052)

`graph/proximity.py`'s `CANDIDATE_SIMILARITY_THRESHOLD = 0.70` decides which
concept pairs are nominated as untyped candidate edges, and those candidates
are the only concept-to-concept input `suggest-relations` and
`contradictions` get before a human runs `relate`. The floor was calibrated
on an embed shape `reindex()` no longer produces. A 9-pair smoke check on
the current `chunk-v1` shape scored related pairs 0.5842–0.8043 and
unrelated pairs 0.2705–0.3996: no overlap, but the weakest related pair sits
below 0.70, and nine pairs cannot say where the two classes actually meet.

This harness drives the real `state.reindex.reindex` over a temporary
bundle and reads document vectors back through
`VectorStoreDB.document_vectors` — the read `VectorProximitySource` makes in
production — then applies the rule below. The rationale behind every number
in it is in [`DESIGN.md`](DESIGN.md).

```bash
uv run python -u evals/proximity_threshold/run_proximity_threshold_probe.py --self-test
uv run python -u evals/proximity_threshold/run_proximity_threshold_probe.py --live --model bge-m3
uv run python -u evals/proximity_threshold/run_proximity_threshold_probe.py --rescore <results .json>
```

`--self-test` and `--rescore` make no model calls. `--live` needs a
reachable Ollama with the embedding model pulled.

## Decision rule

<!-- rule:begin -->
**Pre-registered decision rule (#1052).** Frozen before the first live run
of the calibration fixture; the harness applies it mechanically and never
substitutes another.

A pair is *nominated* at floor `t` when its document-vector cosine is
`>= t` (production keeps a neighbor whose L2 distance is
`<= MAX_NEIGHBOR_DISTANCE`, the same condition). Candidate floors are the
grid `t = 0.40, 0.41, ..., 0.90`. `H` is the number of hard negatives.
`recall(t)` is the share of ALL related pairs nominated at `t`.

1. **INCOMPLETE** if any labelled pair was not scored (`n of TOTAL` must
   be equal for related, unrelated, hard and easy pairs). No conclusion.
2. **INVALID_FIXTURE** if the fixture has fewer than 40 related pairs,
   40 unrelated pairs, 20 hard negatives or 10 hard positives. No
   conclusion.
3. **INSUFFICIENT_EXPOSURE** if `median(hard negatives) <= median(easy
   negatives)`: the "hard" negatives are not measurably harder, so any
   zero-false-positive result would be vacuous. Keep 0.70.
4. A floor `t` is *admissible* when at most `floor(0.05 * H)` hard
   negatives AND zero easy negatives are nominated at `t`. `t_min` is the
   lowest admissible grid floor, and the candidate is
   `t* = t_min + 0.02` (safety margin: a hand-written negative set
   under-samples the negative tail).
5. **OVERLAP** if no grid floor is admissible, if `t* > 0.90`, or if
   `recall(t*) < 0.50`: no single floor both respects the false-nomination
   budget and keeps most related pairs. Keep 0.70; cosine proximity
   alone cannot serve, which is a design question, not a constant.
6. **MOVE (raise) to `t*`** if 0.70 itself is not admissible: today's
   floor already over-nominates hard negatives.
7. **KEEP 0.70** if 0.70 is admissible and `t* >= 0.70`.
8. If `t* < 0.70`: **KEEP 0.70** when `recall(t*) - recall(0.70) < 0.10`
   (the gain does not justify the churn); otherwise
   **INSUFFICIENT_EXPOSURE** when fewer than 5 hard negatives score
   `>= t* - 0.10` (nothing tested the new floor from below, so its
   zero-false-positive claim would be vacuous; keep 0.70 and extend the
   hard negatives); otherwise **MOVE (lower) to `t*`**.

A MOVE verdict supports changing `CANDIDATE_SIMILARITY_THRESHOLD`; the
change itself lands in its own reviewed PR, never in the measurement's.
Labels are frozen with this rule: a label changed after scores are seen
must be reported with the verdict under BOTH label sets.
<!-- rule:end -->
