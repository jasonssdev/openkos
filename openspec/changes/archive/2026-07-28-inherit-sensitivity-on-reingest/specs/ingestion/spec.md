# Delta for ingestion

## MODIFIED Requirements

### Requirement: Default Sensitivity from Config

On a FRESH ingest (no prior `bundle/sources/<slug>.md`), the generated
Source concept's `sensitivity` MUST equal the workspace config's
`default_sensitivity`; no `--sensitivity` flag is offered in this slice.
This is a narrowing of a previously unconditional guarantee, not new
behavior: on a RE-INGEST (`regenerate=True`), the Source's `sensitivity`
MUST instead be resolved as `okf.combine_sensitivity(on_disk_value,
cfg.default_sensitivity)` — the high-water mark of the two — and that
resolved value MUST be both written to `concept_path` and passed as
`stamp_sensitivity` to derived-object staging, so re-ingest can only raise
or preserve a Source's sensitivity, never lower it. The only sanctioned
downgrade path remains `set-sensitivity --allow-downgrade`. The extraction
gate's `workspace_floor` parameter MUST keep tracking `cfg.default_sensitivity`
literally, unrelated to the resolved or on-disk value (`sensitivity-aware-llm`
Requirement 4 is unaffected). An on-disk `sensitivity` value that is
unrecognized or non-string MUST rank as `confidential` under the existing
`_rank` fallback, so resolution fails closed toward the MORE restrictive
level rather than escalating silently; a missing key or a blank/whitespace-only
string instead ranks as `private` -- the config default floor -- per `_rank`'s
existing behavior, never `confidential`. `timestamp`,
`description`, `resource`, `provenance`, and the body MUST continue to
refresh exactly as before this change; only the `sensitivity` field is
carried forward, as a merge into the freshly built metadata, never a
restore of the prior document. WHEN a regenerated Source's resolved
`sensitivity` exceeds `cfg.default_sensitivity`, the re-ingest preview line
for that Source MUST name the preserved level.
(Previously: stated unconditionally that the Source's `sensitivity` equals
`cfg.default_sensitivity`, with no distinction between a fresh ingest and a
re-ingest, so a re-ingest silently reset any level a human had raised via
`set-sensitivity`.)

#### Scenario: Fresh ingest still stamps the config default

- GIVEN a workspace config with `default_sensitivity: private` and no prior
  `bundle/sources/<slug>.md` for this source
- WHEN `openkos ingest <path>` completes
- THEN the generated Source concept's `sensitivity` field is `private`

#### Scenario: Re-ingest preserves an on-disk value raised above the config default

- GIVEN a Source previously raised to `confidential` via `set-sensitivity`,
  and `default_sensitivity: private` in config
- WHEN `openkos ingest <path>` re-ingests that same source (`regenerate=True`)
- THEN the Source's `sensitivity` remains `confidential`, and any derived
  object newly written on that same re-ingest is stamped `confidential`

#### Scenario: Re-ingest raises to a config default above the on-disk value

- GIVEN a Source on disk at `public`, and config `default_sensitivity`
  raised to `confidential`
- WHEN `openkos ingest <path>` re-ingests that same source
- THEN the Source's `sensitivity` is raised to `confidential`

#### Scenario: Re-ingest with equal values is byte-identical to today

- GIVEN a Source on disk whose `sensitivity` already equals
  `cfg.default_sensitivity`
- WHEN `openkos ingest <path>` re-ingests that same source
- THEN the resolved `sensitivity` is unchanged and the Source's write is
  byte-identical to the pre-existing regenerate behavior for that field

#### Scenario: Existing derived objects are untouched by re-ingest regardless of resolved level

- GIVEN a Source with one existing derived object on disk, and a re-ingest
  that resolves the Source's `sensitivity` to a higher level
- WHEN `openkos ingest <path>` completes
- THEN the existing derived object's file, including its `sensitivity`
  field, is left byte-unchanged (create-only reconciliation still applies)

#### Scenario: Missing on-disk sensitivity floors to private

- GIVEN a Source's on-disk `sensitivity` frontmatter key is missing
  entirely, and config `default_sensitivity: private`
- WHEN `openkos ingest <path>` re-ingests that source
- THEN the on-disk value ranks as `private` under `_rank`'s missing-key
  handling, and the resolved `sensitivity` that gets written and staged is
  `private`

#### Scenario: Blank on-disk sensitivity floors to private

- GIVEN a Source's on-disk `sensitivity` frontmatter value is a blank or
  whitespace-only string, and config `default_sensitivity: private`
- WHEN `openkos ingest <path>` re-ingests that source
- THEN the on-disk value ranks as `private` under `_rank`'s blank-string
  handling, and the resolved `sensitivity` that gets written and staged is
  `private`

#### Scenario: Unrecognized or non-string on-disk sensitivity fails closed to confidential

- GIVEN a Source's on-disk `sensitivity` frontmatter value is either
  non-string (e.g. an `int` or `list`) or a string that does not match any
  `SENSITIVITY_ORDER` member
- WHEN `openkos ingest <path>` re-ingests that source
- THEN the resolved `sensitivity` ranks as `confidential` under the
  existing `_rank` fallback, and that value is what gets written and staged

#### Scenario: Extraction gate still reads the workspace default, not the resolved value

- GIVEN a Source whose resolved `sensitivity` differs from
  `cfg.default_sensitivity` after re-ingest resolution
- WHEN extraction's LLM-send gate (`blocks_llm_send`) evaluates whether to
  call the LLM
- THEN it reads `workspace_floor` (`cfg.default_sensitivity`) literally,
  never the resolved or on-disk value

#### Scenario: Preview reports a preserved level

- GIVEN a Source on disk whose `sensitivity` (`confidential`) exceeds
  `cfg.default_sensitivity` (`private`)
- WHEN the re-ingest preview is shown before Phase B writes
- THEN the preview line for the regenerated Source states the resolved
  level (`confidential`) with the trailing clause "preserved from the
  existing Source"

#### Scenario: Preview reports a raised level

- GIVEN a Source on disk whose `sensitivity` (`private`) is below
  `cfg.default_sensitivity` (`confidential`)
- WHEN the re-ingest preview is shown before Phase B writes
- THEN the preview line for the regenerated Source states the resolved
  level (`confidential`) with the trailing clause "raised by the
  workspace default"

#### Scenario: Preview reports an unchanged level

- GIVEN a Source on disk whose `sensitivity` already equals
  `cfg.default_sensitivity`
- WHEN the re-ingest preview is shown before Phase B writes
- THEN the preview line for the regenerated Source states the resolved
  level with the trailing clause "unchanged"

#### Scenario: Preview reports the workspace default after `forget`

- GIVEN a source whose Source concept was removed via `openkos forget`
  (`had_prior_source` is `False`), so there is no on-disk `sensitivity` to
  read
- WHEN the re-ingest preview is shown before Phase B writes
- THEN the preview line for the regenerated Source states the resolved
  level (`cfg.default_sensitivity`) with the trailing clause "from the
  workspace default"
