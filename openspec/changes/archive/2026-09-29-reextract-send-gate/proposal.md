# Proposal: Gate Re-Extraction On The Source's Own Resolved Sensitivity, Not The Workspace Default Alone (#1086)

## Intent

`stage_derived_objects`'s extraction send gate (`src/openkos/application/ingest.py`,
`if not include_confidential and blocks_llm_send(workspace_floor)`) decides
whether a Source's raw text may reach the extraction LLM from `workspace_floor`
alone. The only caller (`_ingest_single`, `src/openkos/cli/main.py`) passes
`workspace_floor=cfg.default_sensitivity` -- literally the workspace config
constant, never the Source's own resolved value.

On a RE-EXTRACT of an EXISTING Source whose on-disk `sensitivity` is
`confidential` (raised via `set-sensitivity`, the high-water mark, or a later
raise), the gate still reads only `cfg.default_sensitivity`. If the workspace
default is `private` or `public`, the gate never fires, and the Source's text
is sent to a non-local backend without `--include-confidential` -- violating
AGENTS.md's "confidential never leaves the device".

This is not merely an implementation bug: two living requirements currently
CODIFY it as correct --
`openspec/specs/ingestion/spec.md`'s "Default Sensitivity from Config" states
"The extraction gate's `workspace_floor` parameter MUST keep tracking
`cfg.default_sensitivity` literally, unrelated to the resolved or on-disk
value" and pins a scenario, "Extraction gate still reads the workspace
default, not the resolved value"; `openspec/specs/sensitivity-aware-llm/spec.md`'s
"Extract Gates on the Workspace Sensitivity Floor" states `extract` "has no
per-doc `sensitivity` value" and gates on `cfg.default_sensitivity` alone.
Two existing tests in `tests/unit/cli/test_ingest.py` pin the same behavior
(`test_extract_gate_still_reads_workspace_floor`,
`test_reingest_resolved_sensitivity_does_not_leak_into_workspace_floor`).
Both premises are now factually wrong: on a re-ingest, `ingest` already
resolves the Source's own sensitivity as `combine_sensitivity(on_disk_value,
cfg.default_sensitivity)` (`ingestion`'s "Default Sensitivity from Config"
requirement, ADR-0010) and holds it as `source_plan.source_sensitivity` --
i.e. there IS a per-doc value available to the gate, and it already reflects
the high-water mark. Both specs need a MODIFIED requirement, and both tests
need updating to the corrected behavior.

## Scope

### In Scope

- Change `_ingest_single`'s call to `stage_derived_objects` so the gate's
  `workspace_floor` argument is `source_plan.source_sensitivity` (the SAME
  value already passed as `stamp_sensitivity`) instead of
  `cfg.default_sensitivity`. On a fresh ingest the two are already equal
  (`compose_source_document` resolves `source_sensitivity` to
  `cfg.default_sensitivity` when there is no on-disk Source), so this is a
  no-op there; on a re-ingest it becomes
  `combine_sensitivity(on_disk_value, cfg.default_sensitivity)` -- the more
  restrictive of the two, never less restrictive than today.
- Update the two tests that pinned the old (buggy) contract, and add
  regression coverage for: a re-extract of a confidential Source blocking
  (with a proven-non-vacuous `--include-confidential` escape), a private
  Source on a private workspace still extracting, and a confidential Source
  still blocking under the MOST permissive (`public`) workspace default.
- Delta specs for `ingestion` and `sensitivity-aware-llm` (both currently
  state the pre-fix contract as a MUST).
- Audit every other `stage_derived_objects` caller (none besides
  `_ingest_single`; `_ingest_batch` reuses `_ingest_single` per file) and
  every other LLM-send gate reading `cfg.default_sensitivity` as a floor
  while a per-concept stored value could be more sensitive
  (`retrieval/answer.py`, `resolution/{contradiction,edge_typing,
  adjudication}.py`, `query.py`'s `stage_filed_answer`) -- all of these
  already re-check each concept's OWN freshly re-read frontmatter via
  `sensitivity.should_block`/`blocks_llm_send`, or (for `stage_filed_answer`)
  only compute a write-time sensitivity STAMP, never gate a send from a
  stale scalar floor -- so none of them share this bug class. Reported, not
  changed.

### Out of Scope

- No local-exemption path for the `extract` gate (`stage_derived_objects`
  has never had one; adding one is a separate, unrelated feature).
- No change to `blocks_llm_send`, `combine_sensitivity`, or any other
  LLM-send gate -- this narrows exactly one caller's argument.
- No ADR: this is a bug fix restoring an existing high-water-mark contract
  (ADR-0003/ADR-0010) to a call site that never picked it up, not a new
  technology, pattern, interface, or trade-off.

## Capabilities

### Modified Capabilities

- `ingestion`: the extraction gate's floor for a given Source is now the
  same resolved (high-water-mark) sensitivity already stamped onto the
  Source and its derived objects, not the workspace default alone.
- `sensitivity-aware-llm`: `extract`'s gate requirement is corrected to
  reflect that a re-extract DOES have a per-Source value available, and
  gates on it.

## Approach

One-line change at the call site (`src/openkos/cli/main.py`, inside
`_ingest_single`): `workspace_floor=cfg.default_sensitivity` becomes
`workspace_floor=source_plan.source_sensitivity`. No change to
`stage_derived_objects`'s signature or body, and no change to
`stamp_sensitivity` (already `source_plan.source_sensitivity`) -- the gate
and the stamp now read the identical field, so they can never diverge.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/cli/main.py` | Modified | `_ingest_single`'s `stage_derived_objects(...)` call: `workspace_floor` argument |
| `tests/unit/cli/test_ingest.py` | Modified | Two tests updated to the corrected contract; four new/renamed tests for the fix and its regression guards |
| `openspec/specs/ingestion/spec.md` | Modified | Delta (this change) |
| `openspec/specs/sensitivity-aware-llm/spec.md` | Modified | Delta (this change) |

## Rollback Plan

One-line revert (`git revert`); no data migration, no frontmatter shape
change. A Source that was incorrectly re-extracted before this fix already
has whatever concepts it has -- this change only affects FUTURE re-extracts.

## Success Criteria

- [x] A re-extract of a Source raised to `confidential` blocks the
      extraction LLM call under a lower workspace default, without
      `--include-confidential`.
- [x] The same setup WITH `--include-confidential` still calls the LLM
      (escape flag unchanged).
- [x] A private Source on a private workspace still extracts on re-extract
      (no regression).
- [x] A confidential Source still blocks re-extraction under the MOST
      permissive (`public`) workspace default.
- [x] Every other `stage_derived_objects` caller and every other
      `cfg.default_sensitivity`-floored LLM send audited; none share this
      bug class (documented above).
- [x] `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy .`,
      `uv run pytest --cov`, `uv run python evals/run_self_tests.py` all
      green.
