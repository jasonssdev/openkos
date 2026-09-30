# Sensitivity-Aware LLM Specification

## Purpose

The `sensitivity` frontmatter field (`public`/`private`/`confidential`,
default floor `private`) governs what may leave the process. One shared
fail-closed predicate decides which concepts may reach `llm.chat`, and the
same rule governs embedding against a backend that is not verifiably this
machine and disclosure to non-LLM consumers such as the MCP read surface.
The `--include-confidential` flag and the local-backend exemption release
the `llm.chat` send only. The `llm.chat` call sites are enumerated in
"Uniform Enforcement Across The Chat Call Sites".

## Non-Goals

Redaction (exclusion only); a new `max_send_sensitivity` config key
(rejected — threshold is fixed at confidential-only); per-source
`ingest --sensitivity` input; S4 export exclusion; any change to how
`sensitivity` is written or to merge's high-water-mark recompute.

## Requirements

### Requirement: Fail-Closed Sensitivity Resolution

The system MUST resolve each concept's effective sensitivity from its own
`sensitivity` frontmatter field. A concept MUST resolve to confidential
(blocked) WHEN the field is `"confidential"`, OR is missing, OR its
frontmatter fails to parse, OR the file cannot be read, OR the value is not
one of `public`/`private`/`confidential`. None of these fallback conditions
MAY raise an uncaught exception.

#### Scenario: Explicit confidential is blocked
- GIVEN a concept with `sensitivity: confidential`
- WHEN its effective sensitivity is resolved
- THEN it resolves to confidential (blocked)

#### Scenario: Missing, malformed, or unreadable fails closed
- GIVEN a concept file with no `sensitivity` field, OR unparseable
  frontmatter, OR a file that cannot be opened/read
- WHEN its effective sensitivity is resolved
- THEN it resolves to confidential (blocked), never an uncaught exception

#### Scenario: Unknown sensitivity value fails closed
- GIVEN a concept with `sensitivity: top-secret` (not one of the three
  known ranks)
- WHEN its effective sensitivity is resolved
- THEN it resolves to confidential (blocked)

### Requirement: Private and Public Pass Through Unchanged

A concept resolving to `private` or `public` MUST be sent to `llm.chat`
exactly as it would be without this filter.

#### Scenario: Private and public concepts reach llm.chat
- GIVEN concepts with `sensitivity: private` and `sensitivity: public`
- WHEN any gated call site processes them
- THEN both are sent unchanged

### Requirement: Uniform Enforcement Across The Chat Call Sites

Every verb that sends concept content to `llm.chat` and offers
`--include-confidential` MUST exclude any concept resolving to confidential
before the send, with no bypass other than that flag (or the local-backend
exemption). These verbs are `ingest` (extraction, the judge and the
re-ask, gated on the resolved sensitivity floor per "Extract Gates on the
Workspace Sensitivity Floor"), `adjudicate`, `contradictions`,
`suggest-relations`, `suggest-volatility`, `revisions`, `query`, and
`curate` (whose stages apply the same gate through the shared services).
Known limitation, not intended behavior: the merged-body reconciliation
call made by `merge`, `adjudicate --apply`, `adjudicate --apply-same` and
`curate` applies no confidential gate. It is bounded only by the merge's own
consent and the `--no-reconcile` opt-out (`entity-resolution-merge`), so a
merge involving a `confidential` concept against a backend that is not
local sends its bodies off the device. Closing this gap is outside this
requirement's enumerated call sites.

#### Scenario: Confidential excluded from adjudicate/contradictions/suggest-relations
- GIVEN a confidential concept is a candidate for `adjudicate`,
  `contradictions`, or `suggest-relations`
- WHEN the command runs without `--include-confidential`
- THEN it is excluded from the `llm.chat` payload

#### Scenario: Confidential excluded from suggest-volatility
- GIVEN a confidential concept is under consideration for
  `suggest-volatility`
- WHEN it runs without `--include-confidential`
- THEN it is excluded from the `llm.chat` payload

#### Scenario: Confidential excluded from query/answer
- GIVEN a confidential concept matches a question
- WHEN `query`/`answer` runs without `--include-confidential`
- THEN it is excluded from the fused hits fed to `llm.chat`

#### Scenario: Confidential Decisions excluded from revisions
- GIVEN a confidential Decision concept
- WHEN `revisions` runs without `--include-confidential` on a non-local
  backend
- THEN its body is excluded from the judge's `llm.chat` payload

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
### Requirement: `--include-confidential` Escape Flag

Every `llm.chat`-calling command MUST offer an opt-in
`--include-confidential` flag that restores pre-filter, sensitivity-blind
behavior byte-for-byte. When absent, exclusion is the default — the
filtering resolution MUST still execute.

#### Scenario: Flag restores excluded concepts
- GIVEN a confidential concept that would otherwise be excluded from
  `query`
- WHEN `query --include-confidential` runs
- THEN it participates exactly as a private/public concept would

#### Scenario: Flag is opt-in, default is exclusion
- GIVEN a mixed bundle of public, private, and confidential concepts
- WHEN any gated verb runs without `--include-confidential`
- THEN confidential concepts are excluded

### Requirement: Exclusion, Not Redaction

The system MUST exclude confidential concepts from `llm.chat` payloads
entirely; it MUST NOT send a redacted, truncated, or masked version of a
confidential concept's content.

#### Scenario: No partial confidential content is sent
- GIVEN a confidential concept
- WHEN any gated call site builds its `llm.chat` payload without
  `--include-confidential`
- THEN none of that concept's content — full or partial — appears in the
  payload

### Requirement: Per-Entry Merged-Content Gate, Never Per-Survivor

`sensitivity.merged_content_blocked` MUST be invoked ONCE PER LEDGER ENTRY
read from a survivor's `bundle/.state/ledger/` sidecar, ranking fail-closed
over `current_sensitivity`, `entry.sensitivity_before`, and
`entry.sensitivity_after` for that entry alone. It MUST NOT be invoked once
per survivor across the whole sidecar: a survivor whose current sensitivity
was lowered via `set-sensitivity` (ADR-0008) after absorbing entries
written at a higher sensitivity MUST still block those specific entries
individually, even when other entries in the same sidecar are not blocked.

#### Scenario: One high-sensitivity entry blocks while a sibling entry in the same sidecar does not
- GIVEN a survivor's sidecar with two entries, one whose
  `sensitivity_before`/`sensitivity_after` exceed the survivor's current
  (lowered) sensitivity and one that does not
- WHEN merged-body candidates are evaluated for that survivor
- THEN `merged_content_blocked` is called once for each of the two entries
  and returns different outcomes for them

#### Scenario: A call hoisted to per-survivor is detected as wrong
- GIVEN a survivor sidecar with 3 entries, only 1 of which should block
- WHEN the gate is invoked exactly once for the whole survivor instead of
  once per entry
- THEN the test asserting per-entry invocation count fails, distinguishing
  a per-survivor implementation from the required per-entry one

### Requirement: Walk-Incompleteness Observability

The system MUST detect when the directory walk underlying the fail-closed
sensitivity filter is provably incomplete (`okf._walk_errors` reports one or
more unlistable subdirectories) and MUST emit a warning to STDERR identifying
the incomplete-walk condition, for each of the five sensitivity-filter verbs:
`query`, `contradictions`, `adjudicate`, `suggest-relations`,
`suggest-volatility`. This detection MUST cover BOTH the concept walk under
`bundle/**.md` AND the ledger-sidecar walk under `bundle/.state/`: an
unlistable subdirectory in either location MUST trigger the warning. The
command MUST still exit 0 (WARN, not refuse). The warning MUST be skipped
when `--include-confidential` is passed, since the filter is then
deliberately disabled.
(Previously: the walk-incompleteness check covered only `bundle/**.md`;
`bundle/.state/` did not exist as a scanned location.)

#### Scenario: Incomplete concept walk warns and still exits 0
- GIVEN a bundle where `okf._walk_errors` reports at least one unlistable
  subdirectory under `bundle/**.md`
- WHEN `query`, `contradictions`, `adjudicate`, `suggest-relations`, or
  `suggest-volatility` runs without `--include-confidential`
- THEN the command prints a warning to STDERR identifying the incomplete
  walk and exits 0

#### Scenario: Incomplete ledger-sidecar walk also warns
- GIVEN a bundle where `bundle/.state/` contains an unlistable
  subdirectory, with the concept walk otherwise clean
- WHEN any of the five verbs runs without `--include-confidential`
- THEN the command prints a warning to STDERR identifying the incomplete
  walk and exits 0

#### Scenario: Clean bundle produces no warning
- GIVEN a bundle where `okf._walk_errors` reports no unlistable
  subdirectories anywhere, including `bundle/.state/`
- WHEN any of the five verbs runs
- THEN no incomplete-walk warning is printed to STDERR

#### Scenario: `--include-confidential` suppresses the warning
- GIVEN a bundle where either walk reports an unlistable subdirectory
- WHEN any of the five verbs runs WITH `--include-confidential`
- THEN no incomplete-walk warning is printed, since the filter is
  deliberately off

### Requirement: Defense-in-Depth Sensitivity Re-Check at Load

Each of `contradictions`, `adjudicate`, `suggest-relations`, and
`suggest-volatility` MUST apply an independent fail-closed re-check — via
`sensitivity.blocks_llm_send` against that document's own frontmatter — at
the point a candidate/member/pair document is loaded by direct path, before
its content enters the `llm.chat` payload. This re-check MUST NOT depend on
whether the document was present in the precomputed blocked set built during
the directory walk: a confidential document absent from that set (e.g.
because its subtree became unlistable, or a permission change occurred,
after the walk but before the load) MUST still be excluded.
`--include-confidential` MUST bypass this re-check identically to how it
bypasses walk-based exclusion, restoring byte-identical pre-filter behavior.
`query` already implements this re-check (S3 FIX-2, answer.py:211-214) and
requires no behavior change.

#### Scenario: Confidential doc absent from the precomputed blocked set is caught at load
- GIVEN a confidential document that was NOT added to the precomputed
  blocked set (its containing subtree lost read permission after indexing,
  but the doc is still reachable and loaded by direct path)
- WHEN `contradictions`, `adjudicate`, `suggest-relations`, or
  `suggest-volatility` loads that document without `--include-confidential`
- THEN the independent per-doc re-check excludes it before it enters the
  `llm.chat` payload

#### Scenario: `--include-confidential` bypasses the re-check
- GIVEN the same confidential document as above
- WHEN any of the four verbs runs WITH `--include-confidential`
- THEN the document is loaded and sent exactly as pre-filter behavior would

#### Scenario: Query is already conformant
- GIVEN `query`'s existing send-time `sensitivity.blocks_llm_send` re-check
  (S3 FIX-2, answer.py:211-214)
- WHEN this change ships
- THEN `query`'s behavior is unchanged — it already independently re-checks
  each candidate at load, satisfying this requirement without modification

### Requirement: Embedding Is Gated As Egress, Like `llm.chat`

An embed call against a backend that is not verifiably this machine puts the
document's text on the wire exactly as an `llm.chat` payload does, so the
same rule MUST govern it. Every embed seam — the `reindex` command,
`ingest`'s per-file embed, and the write-time derived-store refresh every
mutating verb runs — MUST resolve the exemption from the client that will do
the sending (`client.locality.is_local AND cfg.confidential_local_exemption`,
the same `_resolve_local_exemption` the chat seams use) and MUST pass it to
`state.reindex.reindex`, which delegates the meaning to
`sensitivity.should_block`. `reindex` MUST NOT re-derive either term.

The parameter MUST default to withholding, so a caller that fails to thread
it withholds a document rather than sending one. Every embed seam MUST also
emit the non-local embedding-host advisory.

A withheld document MUST be reported as its own outcome, never conflated
with `skipped` (a read failure). It MUST NOT change the exit code, MUST NOT
be pruned from the vector store (a vector computed earlier against a local
backend never left the machine), and MUST join the `skipped`/`embed_failed`
union that withholds the embedding-model tag, for that gate's own stranding
reason. A document served from the content-hash cache MUST NOT be reported
as withheld: no send was going to occur.

The lexical FTS index is NOT gated: it is built locally and no document text
leaves the machine, so a withheld document stays lexically searchable.

#### Scenario: A confidential document is not embedded against a remote backend
- GIVEN a workspace whose embedding host is not verifiably this machine
- WHEN any embed seam runs over a `confidential` document
- THEN its text is never passed to the embedder, the run reports it as
  withheld on stderr and in `reindex`'s summary, and the exit code is
  unchanged

#### Scenario: The same document is embedded against a local backend
- GIVEN the same document and a verifiably local embedding host with the
  workspace exemption enabled
- WHEN the same seam runs
- THEN the document is embedded and nothing is withheld

#### Scenario: An absent or blank sensitivity fails closed
- GIVEN a document whose `sensitivity` key is absent, blank, or whitespace
- WHEN an embed seam runs against a non-local backend
- THEN the document is withheld

#### Scenario: A withheld document's existing vector survives
- GIVEN a document embedded earlier against a local backend
- WHEN a later run withholds it against a remote backend
- THEN its stored vector is neither pruned nor overwritten

#### Scenario: A withheld document withholds the model tag
- GIVEN a run whose embedding-model tag changed
- WHEN any document is withheld
- THEN the new tag is NOT persisted, so the next run forces the re-embed again
### Requirement: Disclosure To A Non-LLM Consumer Is Gated Fail-Closed

A second predicate, distinct from `blocks_llm_send`, MUST decide whether a
concept or bulk row set may be disclosed to a non-LLM consumer — a program
receiving a structured tool result rather than an LLM prompt. It MUST reuse
the same STRICT fail-closed rank `blocks_llm_send` applies: a value that is
absent, blank, unrecognized, unreadable, or unparseable counts as
confidential, exactly as an explicit `confidential` value does.

This predicate MUST take exactly one policy input — whether the consuming
surface's launch-time opt-in is enabled — and MUST NOT accept an
`include_confidential` or `local_exemption` parameter of any kind: those
are LLM-egress escapes, and neither carries an honest meaning for
disclosure to a non-LLM consumer. A per-value check and a set-producing
sibling for bulk rows MUST both exist and MUST apply the same rank.

#### Scenario: An explicit confidential value is withheld by default

- GIVEN a concept with `sensitivity: confidential`
- WHEN the disclosure predicate evaluates it with the launch opt-in off
- THEN it returns not-disclosable

#### Scenario: A resolved-confidential value is disclosable once the opt-in is on

- GIVEN a concept whose effective sensitivity resolves to confidential —
  whether by an explicit value, a missing field, or unparseable frontmatter
- WHEN the disclosure predicate evaluates it with the launch opt-in on
- THEN it returns disclosable

#### Scenario: Private and public values are always disclosable

- GIVEN concepts with `sensitivity: private` and `sensitivity: public`
- WHEN the disclosure predicate evaluates them, regardless of the opt-in
- THEN both return disclosable

#### Scenario: The predicate accepts no LLM-egress escape parameters

- GIVEN the disclosure predicate's signature
- WHEN it is inspected
- THEN it accepts no `include_confidential` and no `local_exemption`
  parameter

#### Scenario: The bulk sibling applies the identical rank

- GIVEN a mixed set of rows spanning public, private, and every fail-closed
  fallback condition
- WHEN the set-producing sibling evaluates them with the opt-in off
- THEN it returns exactly the disclosable subset the per-value check would
  return for each row individually

#### Scenario: An id with no walked document is withheld even under the opt-in

- GIVEN a concept id referenced by a relation or provenance entry, for
  which no corresponding document exists on disk to walk
- WHEN the set-producing sibling evaluates the walked set, with the launch
  opt-in on or off
- THEN that id is absent from the returned set in both cases, since there
  is no document behind it whose rank the opt-in could ever disclose
