# Archive Report: drop-selector-judge-cut

**Archived**: 2026-10-28
**Archive folder**: `openspec/changes/archive/2026-10-28-drop-selector-judge-cut/`
**Store mode**: filesystem merge (a script matched each heading against the living spec) and move.
**Tracking issue**: #1579. Governing decision: ADR-0080 (Accepted).

## Final State At Close

- A new config key `selector_judge`, default `false`, boolean-only like the other flags. With it off,
  `extract_concept_union` makes no judge call and reports `judge_status="skipped"`, so the positional
  backstop of 20 cuts the ceiling-bounded set (ceiling of 24 unchanged). With it on, behaviour is
  exactly what it was.
- `union_judge` keeps its name, meaning and default (`true`).
- `estimate_extraction_calls`, `max_extraction_calls`, the batch cost gate in `cli/main.py`, the
  consent bound and `_settings_digest` (which now includes `selector_judge`) follow the key.
- The shipped `openkos.yaml.template` and `examples/good-life-demo/openkos.yaml` (regenerated from a
  fresh init; a test pins it byte-identical) document the key; `docs/cli.md` and the CHANGELOG say so.
- Four eval harnesses that measure the judge (`generation_ceiling`, `generation_runaway`,
  `participant_anchor`, `stage_attrition`) and `concept_model_screen/screen_pipeline.py` pin
  `selector_judge=True` so their self-tests and their stored records keep meaning what they measured.
  No registered result was edited.

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| extraction-union-judge | Updated | 8 MODIFIED requirements replaced in place (Judge Selection Over a Closed Candidate List; Judge Failure Fails Closed to the Backstopped Union; Backstop Cap Applied Once, After Judge Selection; Run and Judge Bookkeeping on the Extraction Report; Opt-Out Configuration Flag; the two judge re-admission requirements; Extraction Exposes An Upper Bound On Its Calls). 1 ADDED appended: The Selector Judge Is Opt-In. |
| ingestion | Updated | 4 MODIFIED requirements replaced in place (Ingest Raw Copy and Source Concept Generation; Bounded, Deduplicated Derived-Object Staging; Judge-Failure Degrade Is Reported, Ingest Still Succeeds; Batch Cost Gate Announces A Fan-Out-Aware Estimate). |
| stable-python-api | Updated | 1 MODIFIED requirement replaced in place (Files Are Added Through A Quote And A Run Against It): the bound counts judge attempts only with the key on, and `selector_judge` is a setting that moves the bound. ADR-0080 named only the first two domains; this third one is what keeps the consent-bound requirement true. |

Two Purpose paragraphs (`ingestion`, `extraction-union-judge`) were edited directly because a Purpose
is not a requirement and has no heading to match.

### Heading Name-Match

Every MODIFIED heading was matched as an exact line in its living spec before the replace (one
occurrence each), and after the merge every `### Requirement:` and `#### Scenario:` heading of each
delta appears exactly once in its living spec (the merge script asserts it). No REMOVED or RENAMED
headings. `tests/unit/test_config_keys_specified.py` finds `selector_judge` in the living specs.

## Requirement-vs-Code Check

The requirements describe what shipped: `extraction/concept.py` (`extract_concept_union`,
`estimate_extraction_calls`, `max_extraction_calls`), `config.py`, `application/ingest.py`,
`application/ingest_consent.py`, `cli/main.py`. One nuance the spec states and the code keeps: with the
judge off, `judge_status="skipped"` is also a status the backstop applies under (the cap is skipped
only for `"failed"`).

## Left Open

- Removing the judge code is a later decision, after a release at the new default (ADR-0080).
- Eval harnesses that call `extract_concept_union` with the default and whose stored results assumed
  the judge were not changed (their self-tests pass): see the PR description.
