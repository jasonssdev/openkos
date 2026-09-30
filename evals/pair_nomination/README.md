# `pair_nomination` — the chunked-vector pair-nomination gate (#888)

A document longer than the embedder's window used to be represented solely
by its first ~8192 tokens, so `VectorProximitySource.pairs()`
(`graph/proximity.py`) nominated candidate edges from a truncated prefix.
Chunk-backed vectors fix the representation; this probe is the gate that
proves the fix rather than the absence of a crash. Manual tool, not pytest,
not shipped. The full rationale is the docstring of
`run_pair_nomination_probe.py`.

## What decides PASS/FAIL

`margin = best_unrelated_distance - worst_related_distance` over the
hand-labelled pairs in `pair_labels.json` (ids only), using vec0's L2
distance (lower = more similar). **PASS = post-change margin >= pre-change
margin**: chunking must not make the labelled pairs harder to separate. A
changed candidate-pair set is descriptive, never a verdict by itself. An
empty `unrelated` set reports `UNFALSIFIABLE`, not a PASS.

A truncation witness, `cos(doc_vector, first_chunk_vector)` for every
multi-chunk document, ties the gate to the defect: it is `1.0000` before the
change by construction and must be below `1.0000` after it.

## Run

The probe needs a workspace already indexed by `openkos reindex`; it reads
`vectors.db` directly and issues no embedding calls of its own.

```bash
uv run python -u evals/pair_nomination/run_pair_nomination_probe.py --self-test
uv run python -u evals/pair_nomination/run_pair_nomination_probe.py \
    --bundle /path/to/workspace --baseline pre.json
uv run python -u evals/pair_nomination/run_pair_nomination_probe.py \
    --bundle /path/to/workspace --compare pre.json
uv run python -u evals/pair_nomination/run_pair_nomination_probe.py \
    --rescore pre.json
```

`--baseline` records the pre-change measurement, `--compare` measures again
and compares against it, and `--rescore` re-applies the rule to a stored
measurement.

## Files

- `pair_labels.json` — the labelled `related` / `unrelated` pairs (ids only).
- `pre.json`, `post.json` — the stored pre- and post-change measurements.
- `compare-pre-vs-post.txt` — the recorded outcome. It states
  `verdict: PASS` (post margin -0.0298 >= pre margin -0.0328) and discloses
  that both margins stay negative: the signal does not separate the classes,
  and the gate asserts only that chunking did not make it worse. It also
  discloses the revised label set the run scored against.
