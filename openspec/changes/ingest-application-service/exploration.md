# Exploration: ingest-application-service

Issue [#918](https://github.com/jasonssdev/openkos/issues/918), ingest slice.

Verified against `src/openkos/cli/main.py` on branch
`feat/918-ingest-application-service` (main @ `2c851cc`). Every line number and
count below was re-measured against the tree, not carried over from the brief.

## Current state — confirmed measurements

| Unit | Lines | Size |
| --- | --- | --- |
| `ingest` command body | L5700-5809 | ~109 |
| `_ingest_single` | L5872-6554 | 683 |
| `_stage_derived_objects` | L4203-4750 | 548 |
| `_ingest_batch` | L5390-5688 | ~299 |
| Candidate plan-building block (inside `_ingest_single`) | L6094-6399 | 305 |

The candidate block's coupling — 13 inputs from the enclosing scope, 14 outputs
consumed after it — was verified line by line and is accurate, with one
correction: `plan` in the outputs list is not a leaked variable but a loop
variable re-bound in a *later, separate* `for plan in derived_plans:` loop at
L6442. `derived_plans` is the real output.

Three in-block IO calls, two of which are control flow rather than presentation:

1. `observability.stage_notice(...)` before the extraction call — presentation.
2. `typer.echo` + `return _SingleIngestOutcome(...)` at L6225-6250 — the #773
   convergence short-circuit. **Terminates `_ingest_single` from inside the
   block.**
3. The wrapping `except (OSError, ValueError)` at L6395-6399 → `typer.echo` +
   `raise typer.Exit(code=1)`.

## The seam is more entangled than the query precedent

### 1. `_stage_derived_objects` is not presentation-free

Unlike `retrieval/answer.py::answer()` — query's clean base — this function
contains **24 `typer.echo(..., err=True)` calls** for extraction notices
(wrong-language, re-ask, judge, cap, staging-drop), a
`rich.Console(stderr=True).status(...)` spinner, and a call to
`observability.phase_callback("ingest", status.update)`, where
`openkos.cli.observability` is a CLI-only module.

**This function, not the outer 305-line block, is the harder unit.** Extracting
the block while leaving `_stage_derived_objects` echo-laden would relocate a
"service" that still calls `typer.echo` transitively — breaking ADR-0018's
"services never render" rule the moment it is called from `application/`.

### 2. Dual-purpose snapshot reads

`_snapshot_read(concept_path)` (L6167-6168) and `_snapshot_read(index_path)` /
`_snapshot_read(log_path)` (L6334-6335) each serve two masters. The **bytes**
feed `guarded_targets`, the drift-guard baseline consumed later at L6468 by
`_reject_drifted_targets` — write infrastructure, which ADR-0018 D3 keeps
adapter-side. The **text** feeds pure business logic: on-disk sensitivity and
title parsing, and building `new_index_text` / `new_log_text`.

Query's `stage_filed_answer` has no analogue — `application/query.py` contains
zero references to `_snapshot_read` or `guarded_targets`. This is a genuine open
design question, deliberately unresolved here.

### 3. LLM client construction sits inside the block

`_chat_client(cfg, task="extraction")` is called at L6313, inside the candidate
region. Per ADR-0018's disposition table the adapter constructs the backend and
passes it in, exactly as `run_query` receives `llm: LLMBackend`.
`_stage_derived_objects` is already written that way (it takes an `llm`
parameter); only the *construction call site* needs to move up.

## Batch path — no rework needed

`_ingest_batch` calls `_ingest_single` **wholesale, once per matched file**,
with `auto=True` forced, inside `try: ... except typer.Exit`, reading only the
typed `_SingleIngestOutcome` return value. It already treats `_ingest_single` as
an opaque unit, so whatever adapter-facing shape the single-file path takes, the
batch can call it identically. `_expand_batch_sources` and
`_estimate_batch_calls` are independent pre-flight helpers, not entangled with
the candidate block.

## The already-exists seam

**`extraction/concept.py` (`extract_concept`, `extract_concept_union`) is
already the pure, non-CLI leaf.** Its imports contain zero references to
`typer`, `cli`, or `config`, and `_stage_derived_objects`' own docstring states
it: "the extraction leaf stays config-free".

This is ingest's exact analogue to `retrieval/answer.py::answer()` for query —
the goal that is already done. **Do not scope work to "extract the LLM call".**
What is *not* done, unlike query, is the orchestration wrapper directly around
it, which is itself presentation-heavy.

## Test surface

- `tests/unit/cli/test_ingest.py`: **307 test functions**, one of the largest
  modules in the repo. ~290 output-text assertions, so most tests pin exact CLI
  wording — a safety net for behaviour, a cost for any reflow.
- ~40 sites call `main._stage_derived_objects(...)` directly (white-box), and at
  least one (`test_stage_derived_objects_forwards_concurrent_extraction_from_config`)
  monkeypatches `main.extract_concept` / `main.extract_concept_union` **by
  name** — these repoint if the extractor-selection line leaves `main.py`.
- `tests/unit/test_documented_ingest_cost.py`: 13 tests on the batch cost gate.
- Lighter, CLI-runner-only coupling: `test_write_time_refresh.py`,
  `test_embed_host_advisory.py`, `test_confidential_local_exemption.py`,
  `test_next.py`, `test_chat_timeout_wiring.py`, `test_lint*.py`,
  `test_doctor.py`, `test_candidate_edges_e2e.py`.

**Precedent for the migration cost:** ADR-0018 records that moving the
`answer()` call site broke **124** `monkeypatch.setattr("openkos.cli.main.answer", ...)`
sites across 6 files. Ingest's equivalent is real but far smaller.

## Layering violations in the candidate region

Everything below is CLI-only and must not be reachable from `application/`:

- `openkos.cli.observability` (`stage_notice`, `phase_callback`,
  `progress_callback`) — used in the block *and* inside `_stage_derived_objects`.
- `rich.Console(stderr=True).status(...)` inside `_stage_derived_objects`.
- `typer.echo` — throughout `_stage_derived_objects` and at the block's two
  control-flow points.
- `_chat_client` / `OllamaClient` construction at L6313.
- `_snapshot_read`, `guarded_targets`, `_reject_drifted_targets`,
  `fsio.write_*`, `_autocommit`, `_refresh_derived_after_write` — write and
  drift infrastructure ADR-0018 keeps adapter-side.

## Open questions for proposal and design

Deliberately unresolved here:

1. Does `_stage_derived_objects` move into `application/ingest.py` in the **same
   slice** as the outer plan-building block, or is its de-presentation a
   separate prior slice?
2. How do the dual-purpose `_snapshot_read` calls split — does the CLI perform
   all raw reads and pass text in, or does the service read and return bytes for
   the guard?
3. Confirm the slice boundary: single-file `_ingest_single` only (batch reuses
   it unchanged, confirmed above), or narrower still given the presentation
   entanglement?

## Risks

- **Test churn**: ~40 direct-call sites plus monkeypatch-by-name sites repoint if
  `_stage_derived_objects` or its extractor-selection line leaves `main.py`.
  Strict TDD means those repoints ship in the same commits as the move.
- **ADR-0018 violation by construction**: a literal "extract the block" plan
  breaks the "services never render" rule the moment `_stage_derived_objects` is
  called from a service. Notice *detection* has to be separated from notice
  *rendering* first — real work beyond the 305-line block.
- **Wording sensitivity**: ~290 output-text assertions mean any accidental
  whitespace or punctuation drift surfaces as dozens of failures.
