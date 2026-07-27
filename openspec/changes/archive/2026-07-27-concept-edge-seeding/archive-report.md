# Archive Report: concept-edge-seeding (Closes #183)

**Date Archived**: 2026-07-27
**Status**: ARCHIVED
**GitHub Issue**: #183 (Closed 2026-07-27T15:11:14Z)

| Slice | PR | Merged as |
|---|---|---|
| PR1 — three-state message vocabulary | [#194](https://github.com/jasonssdev/openkos/pull/194) | `8ce57c8` |
| PR2 — candidate edges from embedding proximity | [#198](https://github.com/jasonssdev/openkos/pull/198) | `d08a9e9` |
| PR3 — CLI/ingest wiring | [#200](https://github.com/jasonssdev/openkos/pull/200) | `0735114` |

## Summary

`ingest` produced edges, but every one was a `derived_from` Concept→Source
provenance mirror. The graph held zero concept-to-concept edges until a human
ran `relate`, so `suggest-relations` and `contradictions` — both of which
consume concept-to-concept edges — starved. A user's first `suggest-relations`
after ingesting always reported an empty graph.

Concepts whose embeddings sit within a similarity floor now become **candidate**
edges: pairs worth asking a human about. `relation_type` stays `NULL`,
`suggest-relations` still asks an LLM for a type, and `relate` still requires a
human to accept it. Proximity opens the conversation; it never concludes it.

Candidates are **projection-ephemeral** — recomputed on every `build_graph`,
never written to the bundle. That is what makes the threshold safe to change
with no migration, and it is pinned by tests asserting a build with candidates
leaves every bundle byte untouched.

## Verification

The end-to-end test was written first and failing, reproducing the issue's exact
symptom (`ingest never called embed()`), and now passes:

```
ingest → embeds what it wrote → proximity nominates candidates
      → suggest-relations types them → relate accepts → contradictions judges
```

Final gates: 2242 tests passing, branch coverage 97.5% against a 90% floor,
mypy and ruff clean. The suite passes both with and without a reachable Ollama —
verified in both directions, not assumed.

## Specs merged

| Spec | Change |
|---|---|
| `candidate-edge-seeding` | NEW |
| `graph-projection` | +2 requirements (third pass, and its degradation without embeddings) |
| `ingestion` | +1 (ingest triggers candidate-edge computation, degrading gracefully) |
| `llm-edge-production` | +1 (three-state empty-result messaging, later extended to a fourth) |
| `status` | +1 (needs-attention reports concept-to-concept edge state) |
| `contradiction-detection` | MODIFIED (empty-graph message becomes three-state) |

## Review history

Three receipts, and the curves differed sharply.

| Slice | Receipt | Outcome |
|---|---|---|
| PR1 | `review-32bac7a32309ceb8` | approved after one bounded correction |
| PR2 | `review-08cd50afe1faf02a` | approved first pass, zero blocking findings |
| PR3 | `review-5633b4b58afa660d` | approved on the **fourth** round |

PR1's CRITICAL is worth recording: `suggest-relations` printed
`"N relation(s) exist; none are untyped."` from a raw row total that applied
neither the pair-level exclusion nor the confidentiality filter — factually
false in the state `relate` leaves behind. A fourth message state was added.

PR3's history is more instructive. Rounds 1–3 each surfaced a CRITICAL:

1. a test that silently required a live Ollama — green locally, red in CI
2. a crash after the commit on a malformed `OLLAMA_HOST`
3. a plaintext credential from `OLLAMA_HOST` echoed to stderr

Rounds 2 and 3 both came from a non-local-backend warning that **was not part
of this issue's fix**. Withdrawing it (`fea9602`, −157/+9) is what made the PR
converge; the wiring itself reviewed clean in all four rounds. It is now tracked
as [#199](https://github.com/jasonssdev/openkos/issues/199), whose body records
all four of its failure modes as a specification.

The generalizable lesson: when a change will not converge, ask whether the code
producing the findings is the code the issue actually asked for.

## Known limitations

Recorded rather than fixed:

- The similarity floor (0.70) was calibrated on 7 documents / 21 pairs. It shows
  a real separation exists and that 0.70 sits inside it; it does not justify
  more precision than that. Recalibrate against a real fixture bundle before
  treating the constant as settled. Calibrating on bare titles instead of full
  OKF documents gives a materially different and wrong distribution.
- `neighbors()` breaks distance ties in Python because vec0 rejects a secondary
  SQL sort key. That fixes the ORDER of returned rows, not WHICH rows the `k`
  cut returns — exact ties need byte-identical embeddings, so the residue is
  narrow but not zero.
- `ingest` now pays an O(bundle) walk per run, with no bypass flag and a silent
  success path, and `report.skipped` is never surfaced.

## Follow-ups filed

[#195](https://github.com/jasonssdev/openkos/issues/195),
[#196](https://github.com/jasonssdev/openkos/issues/196),
[#197](https://github.com/jasonssdev/openkos/issues/197),
[#199](https://github.com/jasonssdev/openkos/issues/199).
