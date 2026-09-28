# Exploration: decision-revision-detection (#1014 piece a)

> **Orchestrator correction:** the exploring agent claimed `reconcile` has no test coverage. That is false: `tests/unit/cli/test_reconcile.py` holds 37 tests. Every other claim below was spot-checked or is cited to file:line.

## Human decision already taken
A **reversal** is recorded as `supersedes`, which hides the old decision as current through `lifecycle.deprecated_concept_ids`. A **refinement** is recorded as a new `revises` relation, which hides nothing. REAFFIRMS and UNRELATED write nothing (see open decision 2).

## Findings

- **Decision extraction today**
  - `ExtractionResult` (`extraction/concept.py:292-313`) has only `type`, `title`, `description`, `body` and `type_alternative`. There is no subject, value or speaker.
  - #801's `extraction/evidence.py` is a read-only check that looks for a quoted source line in the written text. Nothing stores a structured evidence field.
- **Where subject, value, evidence and speaker could live:** a frontmatter extension read and written through `model/okf.py`, following the tolerant `read_event_date`/`StoredEventDate` pattern (`okf.py:224-271`).
- **Subject derivation options**
  1. Lexical, from the title (no LLM). Free, but coarse.
  2. A post-hoc LLM pass over Decisions only. It leaves the shared classification prompt untouched and needs its own small harness.
  3. Extending the shared classification prompt. This has the highest blast radius and needs an A/B measurement first.
- **Contradiction detection** (`resolution/contradiction.py`)
  - Candidates are edge-driven (`_candidate_pairs` 278-360 walks typed edges, excludes `derived_from`, caps at `_MAX_PAIRS=200` after the deprecation filter). This is structurally the wrong shape for piece (a), which needs Decision×Decision pairs from different Sources, blocked by subject and independent of edges. That part must be new.
  - Reusable as a template (copy, don't import): the verdict and batch shapes (203-266), the fail-closed JSON judge built on `llm/parsing.py`, the per-pair loop with partial batch on failure (1008-1150), and the plan/probe/run triad with its zero-LLM cost gate in `curate.py` (1615-1868).
- **Findings store** (`state/findings.py`): `verdict` is a free-text column, so new verdict values need no migration. `_run_reconcile_from_findings` (`cli/main.py:10138-10267`) is TTY-only with per-item consent.
- **Vectors:** `vectors.db` holds whole-document embeddings. `graph/proximity.py:30-42` warns that calibrating on short strings gives a different, wrong distribution. For the MVP, lexical blocking (`resolution/similarity.py`, `SequenceMatcher` over `normalize_key`) is lower risk.
- **Reconcile** (`cli/main.py:9651-10135`)
  - Roles today: symmetric `reconciled_with` and directional `supersedes`. Neither is in `model/relations.py:REGISTRY`; they are written directly as `okf.Relation`.
  - `revises` should follow that precedent: a third role, a third note sentence, and a third mode in `_existing_reconciliation_state`, which is a two-way `Literal` today.
  - `lifecycle.py:78` special-cases only `"supersedes"`, so `revises` hides nothing with no code change.
- **Event date for a Decision:** no helper resolves it through `provenance` → Source → `okf.read_event_date`, so one must be written. There is no precedent for a merged concept whose provenance spans Sources with different dates.
- **Entry points:** a `curate` stage plus a standalone verb sharing one core, the same shape as `contradictions`. TTY and `--auto` conventions are copied as they are.
- **Harness template:** `evals/contradictions/` (fixture, arms, results, model-free scoring self-test). Candidate-stage recall follows the margin method of `evals/pair_nomination/run_pair_nomination_probe.py`. Every harness needs a model-free `--self-test`.
- **Specs likely touched:** `reconcile-command`, `typed-relationships`, `status-aware-retrieval`, `contradiction-detection`, `curate-command`. The next ADR number is 0024.

## Size and split
This is large. Recommended split into sub-changes, in dependency order:

1. **`revises` relation + reconcile plumbing:** no LLM, useful on its own for recording refinements by hand.
2. **Detector:** subject derivation, candidate generation, judge, `curate` stage and verb, findings.
3. **Harness:** adjudicated fixture of dated cross-source pairs; REVISES/REFINES precision and recall, direction accuracy, candidate-stage recall; model-free self-test.

## Open product decisions
1. Subject derivation method (lexical from the title, post-hoc LLM pass, or inline prompt).
2. Whether REAFFIRMS leaves any record.
3. The event date of a merged concept whose provenance has multiple dates (min, max, or undirected).
4. Whether a pair already resolved as `revises`/`supersedes` stays a contradiction candidate.
5. The production subject-similarity backend (lexical for the MVP, embeddings later after calibration).
6. Confirmation of the three-sub-change split.

## Risks
- **No fixture exists** of dated cross-source Decision pairs. It must be authored and adjudicated.
- **The judge prompt is new and unmeasured.** Baseline it in the harness before trusting its wording.
- **The third reconcile mode** needs explicit tests for every transition between modes.

## Decisions recorded by the orchestrator (2026-09-25)

- **Split confirmed:** sub-change 1 `revises-relation` (relation + reconcile plumbing, no LLM), then sub-change 2 (detector), then sub-change 3 (harness).
- **Open decision 1 answered by the human:** subject and value come from a **separate post-hoc LLM pass over Decisions only**. The shared extraction prompt stays untouched, and the pass gets its own harness.
- **Open decision 4 taken by the orchestrator (conservative):** a pair already joined by `revises`, `supersedes` or `reconciled_with` is no longer offered as a contradiction candidate. This is scoped in sub-change 1.
- **Still open, for sub-change 2:** decision 2 (a REAFFIRMS record), decision 3 (the event date of a merged concept with several dates) and decision 5 (lexical for the MVP, confirmed by the harness).

- **Open decision 2 answered by the human (2026-09-25):** a REAFFIRMS verdict is kept as a detector finding and shown in that decision's history ("reaffirmed in the meeting of <date>"). It writes no relation and changes nothing in the bundle.
- **Open decision 3 answered by the human (2026-09-25):** when a Decision's provenance reaches Sources with more than one distinct `event_date` (for example after a merge), the pair is still judged but gets **no automatic direction**. The finding asks the human for it, the same rule as a missing date.
- **Open decision 5 (orchestrator default):** lexical subject blocking for the MVP. Revisit with embeddings only if the harness shows low candidate-stage recall.

- **Open decision 5 superseded (2026-09-28, PR #1050):** the harness measured lexical subject blocking at 14 of 24 candidate recall (bar B2 needs 18), so the revisit this default anticipated was triggered. Candidates are now blocked by bge-m3 embedding similarity (`EMBEDDING_SIMILARITY_THRESHOLD` 0.65), which found 19 of 24. The threshold must be re-measured on full OKF documents before Phase B.
