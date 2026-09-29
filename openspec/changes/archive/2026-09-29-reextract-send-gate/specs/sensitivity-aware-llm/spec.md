# Delta for Sensitivity-Aware LLM

## MODIFIED Requirements

### Requirement: Extract Gates on the Workspace Sensitivity Floor

`extract` runs on raw source content prior to concept-bundling. On a FRESH
ingest (no prior `bundle/sources/<slug>.md`) it has no per-doc `sensitivity`
value of its own, so the system MUST gate `extract`'s `llm.chat` call on
`cfg.default_sensitivity` in that case. On a RE-INGEST of an EXISTING
Source, the system instead already resolves that Source's own sensitivity
as the high-water mark `combine_sensitivity(on_disk_value,
cfg.default_sensitivity)` (`ingestion`'s "Default Sensitivity from Config"
requirement) before extraction runs -- the system MUST gate `extract`'s
`llm.chat` call on THAT resolved value, not on `cfg.default_sensitivity`
alone. In both cases this floor is the SAME value the run also stamps onto
the Source and onto any derived object it writes. WHEN the resulting floor
is `confidential`, `extract` MUST NOT call `llm.chat` at all; WHEN it is
`private` or `public`, `extract` proceeds unchanged.

(Previously: stated that `extract` "has no per-doc `sensitivity` value" in
all cases and gated unconditionally on `cfg.default_sensitivity`, so a
re-extract of a Source already raised to `confidential` on disk -- via
`set-sensitivity`, the high-water mark, or a prior raise -- sent its text to
a non-local `llm.chat` backend whenever the workspace default alone was
`private` or `public`, without `--include-confidential` (issue #1086).)

#### Scenario: Confidential floor skips extract's llm.chat call

- GIVEN a workspace with `default_sensitivity: confidential`
- WHEN `extract` runs
- THEN it does not call `llm.chat`; this is a documented skip, not an error

#### Scenario: Private floor proceeds unchanged

- GIVEN a workspace with `default_sensitivity: private`
- WHEN `extract` runs
- THEN it calls `llm.chat` exactly as before this change

#### Scenario: A re-extract of a Source raised to confidential blocks the send

- GIVEN a Source previously raised to `confidential` on disk (via
  `set-sensitivity`, the high-water mark, or a prior raise), and a
  workspace with `default_sensitivity: private`
- WHEN `openkos ingest <path> --re-extract` runs without
  `--include-confidential`
- THEN it does not call `llm.chat`; the run resolves to
  `blocked-by-sensitivity`

#### Scenario: The same re-extract proceeds with --include-confidential

- GIVEN the same Source and workspace as above
- WHEN `openkos ingest <path> --re-extract --include-confidential` runs
- THEN it calls `llm.chat`, proving the block above is not vacuous

#### Scenario: A private Source on a private workspace still extracts

- GIVEN a Source whose resolved sensitivity is `private`, and a workspace
  with `default_sensitivity: private`
- WHEN `openkos ingest <path> --re-extract` runs
- THEN it calls `llm.chat` exactly as before this change

#### Scenario: A confidential Source blocks even under the most permissive workspace default

- GIVEN a Source previously raised to `confidential` on disk, and a
  workspace with `default_sensitivity: public` (the least restrictive
  value)
- WHEN `openkos ingest <path> --re-extract` runs without
  `--include-confidential`
- THEN it does not call `llm.chat`; the gate's floor is the high-water mark
  of the two, never the workspace value alone
