# Sensitivity-Aware LLM Specification

## Purpose

The `sensitivity` frontmatter field (`public`/`private`/`confidential`,
default floor `private`) is written by ingest but has no reader today except
merge's high-water-mark recompute. No verb or `llm.chat` call site gates on
it. This spec makes sensitivity govern which concepts may reach `llm.chat`,
via one shared fail-closed predicate applied uniformly across all six call
sites: `adjudicate`, `contradictions`, `suggest-relations`,
`suggest-volatility`, `query`, `extract`.

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
- WHEN any of the six call sites processes them
- THEN both are sent unchanged

### Requirement: Uniform Enforcement Across All Six Call Sites

Every call site sending concept content to `llm.chat` — `adjudicate`,
`contradictions`, `suggest-relations`, `suggest-volatility`, `query`,
`extract` — MUST exclude any concept resolving to confidential before the
send. No call site MAY bypass this gate.

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

### Requirement: Extract Gates on the Workspace Sensitivity Floor

`extract` runs on raw source content prior to concept-bundling and has no
per-doc `sensitivity` value. The system MUST instead gate `extract`'s
`llm.chat` call on `cfg.default_sensitivity`: WHEN the floor is
`confidential`, `extract` MUST NOT call `llm.chat` at all; WHEN the floor is
`private` or `public`, `extract` proceeds unchanged.

#### Scenario: Confidential floor skips extract's llm.chat call
- GIVEN a workspace with `default_sensitivity: confidential`
- WHEN `extract` runs
- THEN it does not call `llm.chat`; this is a documented skip, not an error

#### Scenario: Private floor proceeds unchanged
- GIVEN a workspace with `default_sensitivity: private`
- WHEN `extract` runs
- THEN it calls `llm.chat` exactly as before this change

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
- WHEN any of the six commands run without `--include-confidential`
- THEN confidential concepts are excluded

### Requirement: Exclusion, Not Redaction

The system MUST exclude confidential concepts from `llm.chat` payloads
entirely; it MUST NOT send a redacted, truncated, or masked version of a
confidential concept's content.

#### Scenario: No partial confidential content is sent
- GIVEN a confidential concept
- WHEN any of the six call sites builds its `llm.chat` payload without
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
