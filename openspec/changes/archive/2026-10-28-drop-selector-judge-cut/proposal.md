# Proposal: drop-selector-judge-cut

Refs #1579. Governing decision: ADR-0080 (Accepted).

## Intent

The union extractor merges the candidates of its runs, cuts them to a ceiling of
24, asks a selector judge which to keep, and applies a positional backstop of 20
last. The pre-registered backstop-only comparison
(`evals/concept_model_screen/BACKSTOP_ONLY_RESULTS.md`) came back
ADOPT-CANDIDATE with every registered gate held in both arms, while the judge's
cut showed no measured benefit on support, redundancy or standalone value and
cost 0.0161 of window lift. ADR-0080 therefore makes the cut opt-in.

Success: by default a source is extracted with no judge call; the set is the
ceiling-bounded union cut positionally to 20; `selector_judge: true` restores
today's behaviour exactly.

## Scope

### In Scope

1. A config key `selector_judge`, default `false`, validated as a boolean
   (`config.py`, the packaged template, the reference example's `openkos.yaml`).
2. `extract_concept_union` skips the judge when the key is off and reports
   `judge_status="skipped"`; the ceiling of 24 and the backstop of 20 are
   unchanged.
3. `estimate_extraction_calls`, `max_extraction_calls`, the batch cost gate in
   `cli/main.py`, the consent bound in `application/ingest_consent.py` and its
   settings digest follow the key.
4. Delta specs for `extraction-union-judge`, `ingestion` and `stable-python-api`
   (the consent bound names the settings that move it), archived with this
   change.
5. `docs/cli.md`, the CHANGELOG.

### Out of Scope

- Deleting the judge code (a later decision, after a release at the new
  default).
- Any change to `union_judge`'s name, meaning or default.
- Re-running or editing any registered eval result.

## Approach

See ADR-0080. `union_judge` keeps meaning "union-of-runs extractor"; the new key
gates only the judge inside it, so `union_judge: false` still restores the
single-run path byte-for-byte.

## Rollback

`selector_judge: true` in `openkos.yaml` restores the previous default
behaviour with no code change; reverting the change restores the old default.
