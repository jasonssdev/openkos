# Contradiction Detection Specification (delta)

## MODIFIED Requirements

### Requirement: Degrade-On-No-Model Mirrors `adjudicate`'s 3-Tier Catch

The verb MUST report each of `BackendUnavailable`, `BackendModelNotFound`,
and generic `BackendError` (checked in that order) with an actionable
message, write nothing, and exit non-zero — mirroring `adjudicate`'s
degrade contract. A mid-loop failure from `llm.chat` reaches
the verb as `ContradictionBatch.failure` (with the completed verdicts
preserved and reported first), not as a raise; the 3-tier catch around the
call itself remains only for a failure raised outside the guarded chat
seam.

#### Scenario: Each tier degrades cleanly with zero writes

- GIVEN `find_contradictions` returns a batch whose `failure` is one of the
  three `BackendError` tiers (or, for a failure outside the guarded chat
  seam, raises one)
- WHEN `contradictions` runs
- THEN the completed verdicts (if any) report first, the matching message
  prints, no bundle write occurs, and the process exits non-zero

The `BackendModelNotFound` message MUST name the model the `contradiction`
task resolved (`config.resolve_task_model`), together with its pull command,
never the global `model:` when the two differ; the verb MUST NOT fall back
to the global model.

#### Scenario: A missing packaged judge is named by its own tag

- GIVEN `cfg.backend == "ollama"`, no `models:` key, and the model is not
  installed
- WHEN `contradictions` runs
- THEN stderr names `gemma4:26b-a4b` and `ollama pull gemma4:26b-a4b`, not
  the global `model:`, the process exits 1, and nothing is written
