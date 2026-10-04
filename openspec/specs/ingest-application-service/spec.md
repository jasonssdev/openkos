# Ingest Application Service Specification

## Purpose

The ingest bounded context's application service composes the
orchestration directly around the already-pure `extraction/concept.py`
leaf — de-presented derived-object staging and the plan-composition core
of the single-file ingest path — into callables usable by any adapter
without importing from `openkos.cli`. Workspace layout, configuration, an
`LLMBackend`, and decoded source text arrive as parameters, so the layer
binds no concrete backend and performs no filesystem I/O of its own. It is
the second artifact in the `application/` layer (ADR-0018), following the
shipped `application/query.py`.

A second module, `application/ingest_service.py`, sequences that core with
the effects around it — the confirmation gate, the drift guard, the writes,
the auto-commit and the post-commit derived-index step — into one callable
that ingests a single source into a workspace named by an explicit root
(see "Single-Source Ingest Is A Service Over An Explicit Root").

## Non-Goals

Interactive confirmation and TTY detection; stdout/stderr rendering;
process exit-code selection; the `Console(...).status` spinner and
`observability.phase_callback`; the auto-commit and the derived-index
refresh implementations; LLM backend construction; any change to
`extract_concept`'s contract, the on-disk format, or the CLI surface;
`_ingest_batch`, `_expand_batch_sources`, the batch cost gate and the
batch's non-TTY `--auto` rule; the `api`/`mcp` adapters themselves; the
headless-consent protocol.

## Requirements

### Requirement: Non-CLI Callable Ingest Composition

The service MUST expose a synchronous callable that composes derived-object
staging with the plan-composition core of the single-file ingest path,
importable and callable by code that imports nothing from `openkos.cli`.
It MUST receive its workspace layout, configuration, an `LLMBackend`, and
decoded source text as parameters rather than constructing or reading them
itself.

#### Scenario: A non-CLI caller stages derived objects

- GIVEN a module that imports nothing from `openkos.cli`
- WHEN it imports and calls the ingest application service with decoded
  source text, a workspace layout, a configuration, and an `LLMBackend`
- THEN it receives a result and no import of `openkos.cli` is triggered

#### Scenario: No concrete backend is bound inside the service

- GIVEN the ingest application service module
- WHEN its imports and call signatures are inspected
- THEN the `LLMBackend` arrives as a parameter and the module names no
  concrete backend implementation of its own

### Requirement: Extraction Disclosure Data Is Returned, Not Rendered

The service MUST return typed data for every notice, per-candidate drop,
and degrade condition it currently renders — the ordered set covered by
the existing `_<name>_notice(report)` helpers, per-candidate drop reasons
(empty slug, in-batch collision, on-disk exists, build failure), and
degrade reasons (`no-extractable-text`, `blocked-by-sensitivity`,
`failed`, including the caught `BackendError`) — and MUST NOT call
`typer.echo` or any other presentation call to render them. The adapter
MUST render this data using the relocated `_notice` helpers, in the same
order and with the same wording as the ingest notices always print.

#### Scenario: The service module renders nothing

- GIVEN the ingest application service module
- WHEN its source is inspected for calls to `typer`, `rich`, or
  `openkos.cli.observability`
- THEN none are found

#### Scenario: A degrade condition is returned as typed data

- GIVEN an extraction call that returns no extractable text
- WHEN the service composes the result
- THEN it returns a degrade reason distinguishing that case from
  `blocked-by-sensitivity` and `failed`, and prints nothing itself

### Requirement: Progress Reporting Is Injected, Never Owned

The service MUST accept an `on_progress` callable and forward it to the
extractor; it MUST NOT construct a `rich.Console` spinner or call
`observability.phase_callback` itself. The adapter builds both and passes
`on_progress` in.

#### Scenario: The adapter supplies progress reporting

- GIVEN a caller that passes an `on_progress` callback
- WHEN the service invokes the extractor
- THEN that callback is forwarded unchanged, and the service constructs no
  spinner or phase callback of its own

### Requirement: Decoded Text Arrives As A Parameter

The service MUST receive decoded source text (concept, index, and log
text) as parameters rather than performing any `_snapshot_read` itself;
the adapter performs every read that also feeds `guarded_targets`.

#### Scenario: The service reads no files

- GIVEN the ingest application service module
- WHEN its imports are inspected
- THEN it references no filesystem read primitive; text-dependent
  computations operate only on the parameters passed in

### Requirement: The Convergence Short-Circuit Is A Typed Outcome

WHEN the plan-composition core reaches the byte-identical re-ingest
convergence case, the service MUST return a typed outcome
distinguishing it from every other outcome, rather than terminating via an
internal early return. The adapter MUST map that outcome to the same
CLI-observable behavior (no model call, no write, exit 0, the disclosure
line naming `--re-extract`).

#### Scenario: Convergence returns a typed outcome, not a raw return

- GIVEN a byte-identical re-ingest whose prior extraction ran to
  completion
- WHEN the service composes the plan
- THEN it returns a typed convergence outcome, and the adapter alone exits
  the command from that outcome

### Requirement: Shared Write Mechanics And Client Construction Arrive As Ports

The plan-composition core MUST NOT hold a second definition of the
snapshot read, the drift baseline, the auto-commit or the derived-index
refresh, and MUST NOT construct an LLM backend. The orchestration service
MUST receive the chat-client factory, the auto-commit, the post-commit
derived-index step, the snapshot read and the clock as injected ports, and
MUST NOT import `openkos.cli`, `typer`, `rich` or `openkos.vcs`. The drift
decision itself is one pure function (`application/drift.py`) that returns
the refusal message or nothing; the CLI's drift guard and the service both
call it.

#### Scenario: Committing a plan uses the adapter's own helpers

- GIVEN a run of the orchestration service with an adapter's ports
- WHEN it reaches the commit and the derived-index step
- THEN it calls the ports it was given, in that order, and no duplicate
  implementation of either lives inside the service

#### Scenario: One drift decision serves every caller

- GIVEN a target that changed after its snapshot was read
- WHEN the CLI drift guard or the orchestration service checks the plan
- THEN both derive the refusal from the same pure function

### Requirement: Single-Source Ingest Is A Service Over An Explicit Root

The orchestration service MUST expose one synchronous callable that ingests
one source into the workspace at an explicit `root` and returns a typed
outcome. It MUST NOT read the current directory, prompt, render output,
inspect whether stdin is a terminal, or raise `typer.Exit`. Every condition
that ends a run without a write MUST be a typed refusal carrying the
user-facing message: an unreadable source, a directory that is not a
workspace, a raw copy whose bytes differ from a matched source, an
inconsistent workspace, a failed check, a failed preparation, a failed
write, post-confirm drift, a declined confirmation, and a confirmation that
could not be asked. A write MUST NOT begin before the drift guard has
passed. The convergence short-circuit MUST be a distinct outcome from a
written run. A confirmation that is required but has no answering callback
MUST refuse, never proceed.

The confirmation gate applies exactly when the caller has not asked to skip
it and the workspace configuration says `review: true`; everything the user
reads is reported through an observer as typed data or as advisory lines,
so an unattended caller that passes no observer gets silence.

#### Scenario: A non-CLI caller ingests from outside the workspace

- GIVEN a process whose current directory is not the workspace
- WHEN the service is called with the workspace root and a source
- THEN the source is copied into that workspace's `raw/`, a Source concept
  is written, and a written outcome is returned

#### Scenario: A required confirmation with no answer refuses

- GIVEN a workspace whose configuration requires review
- WHEN the service is called without skipping confirmation and without a
  callback
- THEN it raises a confirmation-unavailable refusal and writes nothing

#### Scenario: Drift is refused before any write

- GIVEN a target that changes after the plan read it
- WHEN the service reaches the guard
- THEN it raises a drift refusal, writes nothing, and never calls the
  auto-commit

### Requirement: The Extraction Preserves Observable CLI Behavior

For every input covered by the existing `tests/unit/cli/test_ingest.py`
suite, `openkos ingest <file>` MUST produce the same exit code, stdout,
and stderr — including every output-text assertion — through the
service, identical to the direct CLI implementation.

#### Scenario: A previously-passing CLI scenario is unchanged

- GIVEN any scenario `test_ingest.py` covers
- WHEN the same CLI invocation runs through the service
- THEN its exit code, stdout, and stderr are unchanged
### Requirement: compose_source_document Accepts An Optional Event Date

`compose_source_document` MUST accept an optional `event_date` parameter
and forward it to `okf.build_source_concept` unchanged. It MUST NOT
resolve the value itself (no flag parsing, no file-name inference, no
stored-value read-back) — the caller supplies the value the `ingestion`
capability's precedence rules already resolved. WHEN `event_date` is
`None`, the generated Source concept's frontmatter MUST omit the
`event_date` key, and the rest of the generated document MUST be
byte-identical to `compose_source_document`'s output without the
parameter.

#### Scenario: A None event_date produces a byte-identical Source

- GIVEN a call to `compose_source_document` with `event_date=None` and
  inputs identical to a call that omits the parameter
- WHEN the two calls' output is compared
- THEN the generated Source concept document is byte-identical, and
  neither carries an `event_date` key

#### Scenario: A given event_date reaches the generated document

- GIVEN a call to `compose_source_document` with
  `event_date=date(2026, 7, 14)`
- WHEN the Source concept is composed
- THEN its frontmatter carries `event_date: '2026-07-14'`

#### Scenario: The service performs no resolution of its own

- GIVEN the ingest application service module
- WHEN its handling of the `event_date` parameter is inspected
- THEN it contains no flag parsing, no file-name inference, and no read of
  any on-disk stored value — it only forwards the value it received

### Requirement: compose_catalog_update Preserves event_date On Marker-Only Rebuilds

`compose_catalog_update` MUST also preserve a Source's `event_date` on a
conditional rebuild triggered solely to record a new `extraction_status` or
`extraction_notice` marker. A marker-only rebuild MUST NOT drop an
`event_date` the Source already carries.

#### Scenario: A marker-only catalog rebuild keeps the resolved event_date

- GIVEN a Source whose resolved `event_date` is `2026-07-14`, and a catalog
  update that rebuilds the Source concept solely to record a new
  `extraction_status` or `extraction_notice`
- WHEN `compose_catalog_update` performs that rebuild
- THEN the rebuilt Source concept's frontmatter still carries
  `event_date: '2026-07-14'`
### Requirement: compose_source_document Accepts Parsed Incoming Frontmatter Lift Results

`compose_source_document` MUST accept optional parameters carrying the
incoming source's parsed frontmatter mapping (for `source_frontmatter`)
and its lifted tag candidate (for the Source's tag union), and forward
them to `okf.build_source_concept` unchanged. It MUST NOT parse the
incoming source's frontmatter itself, MUST NOT decide the tag lift or
union outcome itself, and MUST NOT read any on-disk tags for the union —
the caller supplies the already-parsed mapping and the already-resolved
tag list that the `ingestion` capability's parse and lift rules produced.
WHEN both parameters are absent (`None`/empty), the generated Source
concept's frontmatter MUST omit `source_frontmatter`, and its `tags` MUST
be byte-identical to `compose_source_document`'s output before these
parameters existed.

#### Scenario: Absent frontmatter parameters produce a byte-identical Source

- GIVEN a call to `compose_source_document` with no incoming frontmatter
  mapping and no lifted tags, and inputs otherwise identical to a call made
  before these parameters existed
- WHEN the two calls' output is compared
- THEN the generated Source concept document is byte-identical, and
  neither carries a `source_frontmatter` key

#### Scenario: A given frontmatter mapping and tag list reach the generated document

- GIVEN a call to `compose_source_document` with a parsed incoming
  frontmatter mapping and a resolved tag list
- WHEN the Source concept is composed
- THEN its frontmatter carries `source_frontmatter` equal to the given
  mapping and `tags` equal to the given list

#### Scenario: The service performs no parsing or lift decisions of its own

- GIVEN the ingest application service module
- WHEN its handling of the incoming-frontmatter parameters is inspected
- THEN it contains no YAML parsing, no tag-shape normalization, and no
  on-disk tag read — it only forwards the values it received
### Requirement: Staging Plans Distinguish A New Object From An Attachment

`stage_derived_objects` MUST return, in `StagedDerivedObjects.plans`, plans
that state whether they CREATE a new object or ATTACH to an existing one. An
attach plan MUST carry the existing concept id, the composed revised text,
and the new `version`; a create plan MUST be unchanged. Every attach MUST also
appear as a `StagingDrop` of a distinct kind so the adapter renders its own
wording; an attach MUST NOT increment `lost_in_staging`, because nothing
extracted was lost.

The lookup MUST arrive as a parameter (a `find(type, title)` that returns the
ids of existing, non-deprecated concepts of a non-excluded type whose title has
the same normalized key, and a `read(id)` that returns one concept's decoded
text), built by the caller; staging MUST NOT read the bundle itself. The
caller's `read` MUST be memoised and MUST be invoked only after extraction
returns, taking the target's baseline bytes from that same read, so the bytes a
revision is composed from are the bytes the drift guard compares and a concept
edited while the model was extracting is not overwritten. A `None` lookup MUST
reproduce the pre-attach behavior exactly.

`ingest_source` MUST register every attach target as a guarded target
(re-validated by the drift guard after the confirm gate, baseline bytes taken
from the read the revised text was composed from) and MUST NOT register it as
a create-only target. It MUST write an attach target atomically, never with
the create-only primitive. `IngestOutcome` MUST report the number of attached
concepts, and the auto-commit message MUST name them separately from created
ones while remaining byte-identical when there are none.

#### Scenario: A null lookup reproduces today's plans

- GIVEN staging called with no attach lookup
- WHEN it stages a candidate whose slug is taken by a foreign source
- THEN it returns a create plan at `<slug>-2` and no attach plan

#### Scenario: An attach plan carries the revision

- GIVEN a lookup holding an existing concept and a matching candidate
- WHEN staging runs
- THEN the plan is an attach to that concept id carrying the composed text
  and `version + 1`, and the drops contain one attach entry

#### Scenario: An attach target is drift-guarded

- GIVEN an attach target that changes on disk between Phase A and the commit
  phase
- WHEN `ingest_source` runs its commit phase
- THEN it raises `DriftDetected` and writes nothing

#### Scenario: The commit message is unchanged when nothing attached

- GIVEN an ingest with only create plans
- WHEN the auto-commit runs
- THEN the commit message equals the pre-attach message byte for byte

#### Scenario: The baseline is read after extraction

- GIVEN an attach target edited while the extractor was running
- WHEN the ingest completes
- THEN the edit is preserved in the revised concept
