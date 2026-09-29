# Archive Report: reextract-send-gate

**Date archived:** 2026-09-29
**Issue:** #1086
**Status:** archived in the same PR/branch as the implementation (`fix/1086-reextract-send-gate`)

## Delta merged into `openspec/specs/ingestion/spec.md`

| Section | Requirement | Canonical count after merge |
|---|---|---|
| MODIFIED | Default Sensitivity from Config | 53 |

Composed with `gentle-ai sdd-archive-compose` (exit 0). Requirement count
unchanged (53 before and after); only the target requirement's body and its
"Extraction gate ..." scenario were rewritten.

## Delta merged into `openspec/specs/sensitivity-aware-llm/spec.md`

| Section | Requirement | Canonical count after merge |
|---|---|---|
| MODIFIED | Extract Gates on the Workspace Sensitivity Floor | 11 |

Composed with `gentle-ai sdd-archive-compose` (exit 0). Requirement count
unchanged (11 before and after); the target requirement's body was rewritten
and four scenarios were added (two new blocking/escape scenarios, and two
regression scenarios for the non-blocking directions).

## What shipped

- `_ingest_single`'s call to `stage_derived_objects` (`src/openkos/cli/main.py`)
  now passes `workspace_floor=source_plan.source_sensitivity` instead of
  `workspace_floor=cfg.default_sensitivity`. This is the SAME value already
  passed as `stamp_sensitivity`, so the extraction gate and the
  derived-object sensitivity stamp can no longer diverge.
- On a fresh ingest this is a no-op (`source_sensitivity` already equals
  `cfg.default_sensitivity` when there is no on-disk Source). On a
  re-extract it becomes `combine_sensitivity(on_disk_value,
  cfg.default_sensitivity)` — the high-water mark of the two — so a Source
  raised to `confidential` (via `set-sensitivity`, the high-water mark, or a
  prior raise) now blocks the extraction LLM send regardless of how
  permissive the workspace default is, without `--include-confidential`.
- Two pre-existing tests that pinned the buggy behavior as correct were
  rewritten to the corrected contract
  (`test_extract_gate_tracks_the_same_resolved_value_as_the_stamp`,
  `test_reingest_resolved_sensitivity_gates_extraction`). Three more tests
  whose forged-sensitivity fixtures landed on `confidential` (now blocking)
  were adjusted to a non-blocking forged level so they still exercise their
  original intent. Four new regression tests cover: the
  `--include-confidential` escape (not vacuous), a private Source on a
  private workspace still extracting, and a confidential Source still
  blocking under the most permissive (`public`) workspace default.

## Other send gates audited (not changed)

- `retrieval/answer.py`, `resolution/{contradiction,edge_typing,
  adjudication}.py`: each already re-checks a concept's OWN freshly re-read
  frontmatter via `sensitivity.should_block`/`blocks_llm_send` at the actual
  `llm.chat` send point — not a stale scalar floor — so they do not share
  this bug class.
- `application/query.py`'s `stage_filed_answer`: its `default_sensitivity`
  parameter computes a write-time sensitivity STAMP for the filed answer
  document (a high-water mark over cited concepts, raised further by
  `type_birth_sensitivity`), never a gate deciding whether to call the LLM.
  The answer itself was already generated, through the per-concept-checked
  path above, before this function runs.
- `_ingest_batch` has no gate of its own; it invokes `_ingest_single` once
  per file, so this fix covers the batch/folder path too.

## Out of scope

No local-exemption path was added to the `extract` gate (it has never had
one); that remains a separate, unrelated feature if ever proposed.
