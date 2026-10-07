# MCP Specification

## Purpose

`openkos mcp` is an adapter: a hand-rolled, stdio-only MCP server exposing
the read-only tools `query`, `get`, `navigate`, and `pending` over one
workspace. It is its own egress boundary — disclosure to a non-LLM
consumer, distinct from `llm.chat` egress — and this domain specifies both
the transport/lifecycle contract and the disclosure boundary every tool
result MUST pass through.

## Non-Goals

This spec does not define: write tools of any kind, or lock acquisition,
`WorkspaceBusyError` handling, retry, or backoff for them; protocol revision
2026-07-28 or revision auto-detection by peeking at the first message;
resources, prompts, sampling, elicitation, roots, completion, the `logging`
capability, `listChanged`, or `tools/list` pagination; HTTP or streamable
transports, a REST API, or authentication; a per-request confidential
opt-in, or any MCP meaning for `local_exemption`; redacting a disclosable
object's prose; caching the graph projection across `navigate` calls.

## Requirements

### Requirement: Stdio Transport Targets Protocol Revision 2025-11-25

`openkos mcp` MUST serve exactly one MCP protocol revision, **2025-11-25**,
over newline-delimited JSON-RPC 2.0 on stdin/stdout. It MUST implement
`initialize` and `notifications/initialized`, declaring the `tools`
capability with `listChanged: false`. WHEN a client's `initialize` request
names a different protocol version, the server MUST respond naming
`2025-11-25`, and the client alone decides whether to continue. The server
MUST support `tools/list` and `tools/call`.

#### Scenario: initialize negotiates the supported revision

- GIVEN a client sends `initialize` naming protocol revision `2025-11-25`
- WHEN the server responds
- THEN the response confirms `2025-11-25` and declares the `tools`
  capability with `listChanged: false`

#### Scenario: A different requested revision is answered with the supported one

- GIVEN a client sends `initialize` naming a protocol revision other than
  `2025-11-25`
- WHEN the server responds
- THEN the response names `2025-11-25`, and the server does not fail the
  handshake on this basis alone

### Requirement: Requests Before Initialize And JSON-RPC Batches Are Rejected

The server MUST reject any request received before a completed `initialize`
handshake with JSON-RPC error `-32600` (invalid request), except
`initialize` itself. The server MUST reject a JSON-RPC batch (a JSON array
of requests) with `-32600`, since batching is absent from the targeted
revision.

#### Scenario: A request before initialize is rejected

- GIVEN a fresh connection that has not completed `initialize`
- WHEN the client sends `tools/list`
- THEN the server responds with JSON-RPC error `-32600`

#### Scenario: A batch request is rejected

- GIVEN an initialized connection
- WHEN the client sends a JSON array of two or more requests as one frame
- THEN the server responds with JSON-RPC error `-32600`

### Requirement: Ping Is Answered

The server MUST respond to a `ping` request, as protocol revision 2025-11-25
requires of the receiver.

#### Scenario: ping receives a response

- GIVEN an initialized connection
- WHEN the client sends `ping`
- THEN the server sends a response, and the response carries no error

### Requirement: Stdout Carries Only Protocol Frames

The server MUST write nothing to stdout except newline-delimited JSON-RPC
frames belonging to this protocol. All diagnostic and error logging MUST go
to stderr; none of it MUST appear on stdout, under any tool, error, or
lifecycle path.

#### Scenario: A subprocess run emits only JSON-RPC lines on stdout

- GIVEN a real subprocess serving over stdio, exercised through
  `initialize` → `initialized` → `tools/list` → `tools/call`
- WHEN its stdout is captured for the whole session
- THEN every line is a well-formed JSON-RPC frame, and no log line or stray
  text appears

#### Scenario: An internal error still logs only to stderr

- GIVEN a tool call that triggers an internal error
- WHEN the server handles it
- THEN the diagnostic detail is written to stderr only, and stdout receives
  only the JSON-RPC error response

### Requirement: The Workspace Is Validated Before Serving

`openkos mcp` MUST accept `--workspace DIR` (defaulting to the current
directory) and MUST validate that it is an initialized OpenKOS workspace
before serving any request. WHEN the directory is not a workspace, the
server MUST exit nonzero with a message on stderr, and MUST NOT begin
serving stdio frames.

#### Scenario: A valid workspace directory serves normally

- GIVEN `--workspace` names an initialized OpenKOS workspace
- WHEN `openkos mcp` launches
- THEN it begins serving stdio frames

#### Scenario: An invalid workspace directory refuses to serve

- GIVEN `--workspace` names a directory that is not an initialized OpenKOS
  workspace
- WHEN `openkos mcp` launches
- THEN it exits nonzero, prints a message on stderr, and never emits a
  stdio protocol frame

### Requirement: Four Read-Only Tools Compose Existing Services Without A Lock

The server MUST register exactly four tools: `query(question, limit?)`
calling `run_query`; `get(concept_id)` calling the concept read path and
`list_provenance_sources`; `navigate(concept_id)` calling
`list_bundle_objects` and `build_graph`; and `pending()` calling
`next_action`. None of the four tools MUST acquire a workspace lock, retry,
or back off; each composes an existing, complete service result rather than
re-implementing its logic.

#### Scenario: Each tool calls its underlying service unmodified

- GIVEN the tool registry
- WHEN each of `query`, `get`, `navigate`, and `pending` is called
- THEN each composes the named existing service's result, and none acquires
  a workspace lock

#### Scenario: A read tool never raises WorkspaceBusyError

- GIVEN a concurrent in-flight write elsewhere in the workspace
- WHEN any of the four tools runs
- THEN it completes without raising `WorkspaceBusyError`, since it holds no
  lock

### Requirement: `get` Returns A Curated Field Set, Never A Frontmatter Passthrough

`get(concept_id)` MUST return a fixed, curated field set — id, type, title,
sensitivity, status, body, filtered relations, filtered provenance, source
ancestors from `list_provenance_sources` (including `not_run`) — and MUST
NOT return a passthrough of the concept's raw frontmatter dict. This MUST
hold even when a future frontmatter key is added, so an unrecognized key
never reaches the client through `get`.

#### Scenario: get's result names only the curated fields

- GIVEN a concept with frontmatter keys beyond the curated set
- WHEN `get(concept_id)` returns
- THEN the result carries exactly the curated field set, and no
  unrecognized frontmatter key appears anywhere in it

#### Scenario: A withheld id returns an empty object with a count, not content

- GIVEN a `concept_id` that resolves to an object the disclosure predicate
  withholds
- WHEN `get(concept_id)` is called with that id
- THEN the result is an empty curated object with `withheld: 1`, and no
  field of the withheld object's content appears

An id that resolves to a document that exists but cannot be read or
parsed is a distinct case from an id that resolves to nothing at all. WHEN
`--expose-confidential` is on and `get`'s target resolves to a document
that cannot be read or whose frontmatter cannot be parsed, the result MUST
be a success with `concept: null` and a `not_run` entry (labelled
`concept_read`) naming that the concept could not be read — never a
`read_failed` tool error. WHEN `--expose-confidential` is off, that same
unreadable target MUST instead be reported as `concept: null, withheld: 1`,
with no `not_run` entry for it, since an unreadable document is ranked
confidential and is withheld like any other confidential object. In both
cases, `concept_not_found` stays reserved for an id that does not resolve
to any file at all.

#### Scenario: An unreadable target under the opt-in is a success with a not_run entry

- GIVEN `--expose-confidential` is on and `get`'s target resolves to a
  document that cannot be read or whose frontmatter cannot be parsed
- WHEN `get(concept_id)` returns
- THEN the result is a success with `concept: null` and a `not_run` entry
  labelled `concept_read`, not a tool error

#### Scenario: The same unreadable target, opt-in off, is reported as withheld

- GIVEN `--expose-confidential` is off and the same unreadable target as
  above
- WHEN `get(concept_id)` returns
- THEN the result is `concept: null, withheld: 1`, and it carries no
  `not_run` entry for that target

### Requirement: `navigate` Returns One Concept's Neighbors In Both Directions

`navigate(concept_id)` MUST be a single tool returning the named concept's
neighbors from `build_graph` in BOTH directions — edges where the concept
is the source and edges where it is the target — covering both typed
relations and untyped links, without a separate provenance tool;
provenance stays exclusively in `get`. Each returned neighbor MUST carry a
`direction` field distinguishing an outbound edge from an inbound one.

#### Scenario: navigate returns typed and untyped neighbors

- GIVEN a concept with both a typed relation edge and an untyped link to
  other concepts
- WHEN `navigate(concept_id)` is called
- THEN the result includes both kinds of neighbor, and there is no separate
  provenance tool

#### Scenario: navigate includes inbound neighbors, not only outbound

- GIVEN a concept that is the TARGET of another concept's outbound
  relation (an inbound edge, from the named concept's perspective)
- WHEN `navigate(concept_id)` is called
- THEN that inbound neighbor appears in the result, carrying
  `direction: "in"`, alongside any outbound neighbors carrying
  `direction: "out"`

#### Scenario: A graph read that skipped edges reports a graph_build not_run entry

- GIVEN `navigate`'s underlying graph read skips one or more edges it could
  not include
- WHEN `navigate(concept_id)` returns
- THEN the result carries a `not_run` entry labelled `graph_build` with a
  count-only reason, and the tool still answers with whatever neighbors it
  did read

### Requirement: The Disclosure Predicate Gates Every Structured Channel

Every structured channel a tool result can carry — object ids, titles,
graph edges, provenance ancestors, `pending`'s recommendation/declination
subjects, and `AnswerResult`'s `omitted`/`excerpted`/`history_truncated`
title lists (each paired with its id list) — MUST be filtered through the
disclosure predicate (`sensitivity-aware-llm`'s disclosure requirement) at
the adapter boundary, in `mcp/gate.py`, before leaving the process. The
underlying services MUST remain unfiltered and complete; the CLI's behavior
MUST be unaffected. A filtered object MUST be reported as an incremented
`withheld` count, never as content of any kind — not even a redacted or
partial form.

#### Scenario: A withheld concept contributes only to the count

- GIVEN a confidential concept present in an underlying service's complete
  result
- WHEN any tool returns that result over MCP without the launch opt-in
- THEN the concept's id, title, and body are absent from the response, and
  `withheld` is incremented by exactly one for it

#### Scenario: An edge survives only when both ends are disclosable

- GIVEN a graph edge whose source is disclosable and whose target is not
- WHEN `navigate` returns its result
- THEN that edge is absent from the neighbor list

#### Scenario: A title is scrubbed by its paired id, not by title text

- GIVEN an `AnswerResult` whose `omitted_titles`/`omitted_ids` pair includes
  a confidential document's title and id
- WHEN `query` returns its result over MCP
- THEN that title is absent from the returned list because its paired id is
  not disclosable, and the list's withheld count reflects it

#### Scenario: A misaligned title/id pair drops every title in that list

- GIVEN a title list whose paired id list is a different length (a defect
  condition)
- WHEN `query` composes its disclosure-filtered result
- THEN every title in that list is dropped and counted toward `withheld`,
  fail-closed, rather than any title being kept without a verified id

#### Scenario: A `pending` subject that is not fully disclosable withholds the whole item

- GIVEN a `NextAction` recommendation whose declared subjects include one
  non-disclosable concept
- WHEN `pending()` returns
- THEN that recommendation is withheld in full, not partially disclosed

#### Scenario: Undeclared pending subjects withhold the action

- GIVEN a `NextAction` whose `subjects` is `None` (undeclared, never
  populated by its tier's call site)
- WHEN `pending()` returns
- THEN that action is withheld, fail-closed, regardless of whether the
  underlying finding is itself harmless

#### Scenario: A declared, subject-free tier is disclosed, not withheld

- GIVEN a `NextAction` whose `subjects` is the explicit empty tuple `()` —
  a fixed-text tier that names no document
- WHEN `pending()` returns
- THEN that action is disclosed normally: an explicitly declared empty set
  trivially satisfies "every subject is disclosable"

#### Scenario: Misaligned declination subjects withhold every declination

- GIVEN a `NextResult` whose `declination_subjects` list is a different
  length from its `declinations` list (a defect condition)
- WHEN `pending()` returns
- THEN every declination is withheld, fail-closed, rather than any
  declination being matched to the wrong subjects

#### Scenario: The CLI is unaffected by the gate

- GIVEN the same underlying service results
- WHEN `openkos query`, an equivalent local read, or `openkos next` run via
  the CLI rather than MCP
- THEN their output is unaffected by the MCP disclosure gate

### Requirement: `withheld` Counts Removed Entries Per Channel, And Skip Notices Are A Separate Count

`withheld` MUST count the number of entries the gate removed from a
result's structured channels — a row, an edge, a citation, a title, a
provenance id, an ancestor, or a `pending` item — never the number of
distinct underlying objects. An object appearing in more than one channel
of the same result MUST be counted once per channel it is removed from, so
it can increment `withheld` by more than one for a single result. Skip
notices, which name documents an underlying service could not read, MUST
be reported as a separate `skipped_documents` count and MUST NOT be added
to `withheld`.

`query` is the one tool whose retrieval removes a confidential concept before
any channel exists to hold it. `query`'s `withheld` MUST therefore also
include the number of distinct confidential concepts retrieval excluded from
this call's candidates, so a client can tell the answer was computed without
a withheld object. That count MUST NOT identify the concept, and MUST be zero
when confidential exposure lifts the filter.

#### Scenario: A withheld object appearing in two channels is counted twice

- GIVEN a confidential concept that is both a filtered relation and a
  filtered provenance ancestor of the same `get` result
- WHEN `get` returns
- THEN `withheld` is incremented once for the removed relation and once for
  the removed provenance ancestor, for a total of two

#### Scenario: A confidential concept excluded by retrieval is counted by query

- GIVEN `--expose-confidential` is off and a confidential concept matches the
  question
- WHEN `query` returns
- THEN `withheld` is at least one, and no field of the result names the
  concept

#### Scenario: Skip notices are reported separately from withheld

- GIVEN a result whose underlying service reported one or more skip
  notices for documents it could not read
- WHEN the tool returns
- THEN those documents are counted in `skipped_documents`, and `withheld`
  is not incremented for them

### Requirement: Confidential Exposure Is A Launch-Time-Only Opt-In

The server MUST accept `--expose-confidential`, read once at launch and
never per request. It MUST default to off. WHEN off, the disclosure
predicate MUST withhold every object resolving to confidential, exactly as
`sensitivity-aware-llm`'s fail-closed resolution defines. There MUST be no
per-request parameter, header, or tool argument that can override this
launch-time setting for a single call.

#### Scenario: The flag defaults to off

- GIVEN `openkos mcp` launched with no `--expose-confidential` flag
- WHEN any tool returns a result touching a confidential object
- THEN that object is withheld

#### Scenario: No per-request override exists

- GIVEN the server launched with `--expose-confidential` off
- WHEN a `tools/call` request is inspected for any parameter resembling a
  confidential override
- THEN no such parameter is accepted or has any effect

### Requirement: With The Opt-In On, `query`'s LLM Egress Still Requires Both Gates

WHEN `--expose-confidential` is off, `query` MUST call the underlying
retrieval service with `include_confidential=False` and
`local_exemption=False`. WHEN it is on, `local_exemption` MUST be resolved
exactly as the CLI resolves it (from the configured backend's verifiable
locality and the workspace's exemption setting), while `include_confidential`
MUST remain `False` always — a confidential object reaching the LLM still
requires both gates, and a remote chat backend MUST NEVER receive
confidential content through this path.

#### Scenario: The opt-in off sends both LLM-egress gates closed

- GIVEN `--expose-confidential` is off
- WHEN `query` calls the retrieval service
- THEN it passes `include_confidential=False` and `local_exemption=False`

#### Scenario: The opt-in on still requires the local-exemption gate

- GIVEN `--expose-confidential` is on and the configured backend is
  verifiably local with the workspace exemption enabled
- WHEN `query` calls the retrieval service
- THEN `local_exemption` resolves to the same value the CLI would resolve,
  and `include_confidential` remains `False`

#### Scenario: A remote backend never receives confidential content via query

- GIVEN `--expose-confidential` is on and the configured backend is not
  verifiably local
- WHEN `query` calls the retrieval service
- THEN `local_exemption` resolves to `False`, and `include_confidential`
  remains `False`, so no confidential content enters the LLM payload

### Requirement: `query`'s Answer Text Is Withheld Whenever Any Object That Entered The Prompt Is Not Disclosable

WHEN any of `query`'s citations is withheld by the disclosure predicate, OR
any object whose content was actually placed in the prompt is not
disclosable — whether or not the model went on to cite it — the answer
text itself MUST be withheld (`answer: ""`, `answer_withheld: true`), since
the answer's wording may have drawn on that object's content. A
citation-only check is insufficient: the citation list is narrowed, after
the model replies, to only the objects the model's own reply reports
drawing on, so an object the model does not cite is absent from that list
even though its content was placed in the prompt. This is reachable even
with the launch opt-in off, through a race between the prompt's read and
the disclosure snapshot, INCLUDING the sub-case where the object entering
the prompt is never named in the model's reply; the fail-closed choice for
this whole race is to withhold the answer along with the object, cited or
not. WHEN every object placed in the prompt is disclosable, the answer
text MUST be returned normally, with `answer_withheld: false`.

#### Scenario: A withheld citation withholds the whole answer text

- GIVEN a `query` result whose citations, before filtering, include a
  confidential concept
- WHEN `query` returns its result over MCP without the launch opt-in
- THEN `answer` is empty, `answer_withheld` is `true`, and `withheld`
  counts the removed citation

#### Scenario: An uncited prompt object raised mid-flight withholds the answer

- GIVEN a `query` result whose model reply cites nothing, but whose prompt
  included a concept that was public when its content was read and is
  confidential by the time the disclosure snapshot is taken
- WHEN `query` returns its result over MCP without the launch opt-in
- THEN `answer` is empty and `answer_withheld` is `true`, even though no
  citation names that concept

#### Scenario: No withheld citation discloses the answer normally

- GIVEN a `query` result whose every citation is disclosable and every
  object placed in the prompt is disclosable
- WHEN `query` returns its result over MCP
- THEN `answer_withheld` is `false`, and `answer` carries the model's
  response text

### Requirement: A Disclosable Object's Text Is Shown As Written

A non-confidential object's body or note text MUST be returned exactly as
written, even when that text names or describes a separate confidential
object by title or content. The disclosure predicate MUST NOT redact,
scrub, or rewrite prose; it gates only the structured channels named above
(ids, titles, edges, provenance ancestors, pending subjects, and title
lists).

#### Scenario: A non-confidential note mentioning a confidential one is shown whole

- GIVEN a non-confidential note whose body text mentions a separate
  confidential note by name
- WHEN `get` returns that non-confidential note
- THEN its body text is returned unmodified, including the mention

### Requirement: Every Result Carries Consistency Warnings

Every tool result MUST carry a `warnings` list. Every tool MUST include an
`in_flight_write` warning, reported as a count only (never an id), sourced
from `bundle_ledger.scan_torn_writes`, whenever a torn write is observed.
`query` alone MUST additionally include a `stale_index` warning naming the
stale derived store(s) from `state.derived.stale_derived_stores`. A
consistency check that cannot run MUST become a `not_run` entry rather than
raise. The staleness check itself MUST NEVER raise and MUST NEVER produce a
`not_run` entry: it degrades to reporting no stale stores rather than
failing, so `stale_index` can appear as a `warnings` entry but never as a
`not_run` entry.

#### Scenario: An in-flight write produces a count-only warning on any tool

- GIVEN a pending merge-ledger marker `bundle_ledger.scan_torn_writes`
  detects
- WHEN any of the four tools runs
- THEN the result's `warnings` includes an `in_flight_write` entry carrying
  a count, and no survivor id

#### Scenario: A stale derived store warns only on query

- GIVEN a stale derived store `state.derived.stale_derived_stores` reports
- WHEN `query` runs
- THEN `warnings` includes a `stale_index` entry naming that store, and the
  tool still answers

#### Scenario: get, navigate, and pending do not report stale_index

- GIVEN the same stale derived store condition
- WHEN `get`, `navigate`, or `pending` runs
- THEN their `warnings` list carries no `stale_index` entry

#### Scenario: A check that cannot run is reported, never raised

- GIVEN a consistency check whose underlying scan cannot complete
- WHEN a tool runs
- THEN the result carries a `not_run` entry for that check rather than an
  exception

### Requirement: Tool Errors Are Structured And Retryable-Tagged

A tool failure raised by an underlying service call MUST be returned as a
result with `isError: true` and `structuredContent.error = {code,
retryable, message}`, using exactly these codes: `ollama_unavailable`
(retryable), `model_not_found`, `embedding_dimension_mismatch`,
`fts_unavailable`, `ollama_error` (retryable), `concept_not_found`,
`read_failed` (retryable). Each code MUST preserve the same cause
distinction the CLI already makes for the equivalent failure. This is the
exception-derived code set; `invalid_arguments` is a distinct code, not
raised by a service call, and is governed by its own requirement above.

#### Scenario: An unreachable Ollama server is reported as retryable

- GIVEN the configured Ollama backend is unreachable
- WHEN a tool call needs it
- THEN the result carries `isError: true` and
  `structuredContent.error.code == "ollama_unavailable"` with
  `retryable: true`

#### Scenario: A missing concept is reported as not retryable

- GIVEN `get` or `navigate` is called with a `concept_id` that does not
  exist in the bundle
- WHEN the tool runs
- THEN the result carries `structuredContent.error.code ==
  "concept_not_found"` with `retryable: false`

### Requirement: Protocol-Level Errors Use Standard JSON-RPC Codes With Generic Internal Messages

The server MUST map protocol-level failures to standard JSON-RPC error
codes: `-32700` for a parse failure, `-32600` for an invalid request
(including a pre-`initialize` request and a batch), `-32601` for an unknown
method, `-32602` for an unknown tool name or a malformed request envelope,
and `-32603` for an internal error. An internal error's message MUST be
generic and MUST NOT include the underlying exception's text, since that
text can carry a confidential path or content; diagnostic detail goes to
stderr only.

`-32602` MUST be reserved for exactly: a `tools/call` naming a tool the
registry does not contain, and a malformed `CallToolRequest` envelope
(`params` not an object, `name` not a string, `arguments` present and not
an object, or a `progressToken` that is neither a string nor an integer). A
well-formed call to a known tool whose `arguments` merely fail that tool's
own `inputSchema` MUST NOT use `-32602` — that is a tool execution error,
covered by the argument-validation requirement below.

#### Scenario: An unparseable frame yields -32700

- GIVEN a frame on stdin that is not valid JSON
- WHEN the server reads it
- THEN it responds with JSON-RPC error `-32700`

#### Scenario: An unknown tool name yields -32602

- GIVEN a `tools/call` naming a tool the registry does not contain
- WHEN the server dispatches it
- THEN it responds with JSON-RPC error `-32602`

#### Scenario: A malformed request envelope yields -32602

- GIVEN a `tools/call` whose `params` is not an object, whose `name` is not
  a string, whose `arguments` is present and not an object, or whose
  `progressToken` is neither a string nor an integer
- WHEN the server dispatches it
- THEN it responds with JSON-RPC error `-32602`

#### Scenario: An internal error never echoes exception text

- GIVEN an unexpected internal exception whose message contains a
  confidential path
- WHEN the server maps it to `-32603`
- THEN the JSON-RPC error's message is a generic internal-error string, and
  the confidential path appears nowhere in the response

### Requirement: Invalid Tool Arguments Are A Tool Execution Error, Not A Protocol Error

WHEN a well-formed `tools/call` names a known tool but its `arguments`
fail that tool's own `inputSchema`, the server MUST respond with a normal
JSON-RPC result — not a JSON-RPC error — whose result carries `isError:
true` and `structuredContent.error.code == "invalid_arguments"`. This
follows protocol revision 2025-11-25's distinction between a *protocol
error* (the request itself is malformed) and a *tool execution error* (the
request is valid JSON-RPC, but the named tool could not complete), so a
calling model can see and correct an argument mistake the same way it sees
any other tool failure. The error's `message` MUST name the offending
property and MUST NOT echo the value that failed validation, since that
value is caller-supplied and MAY itself be sensitive.

#### Scenario: Invalid arguments produce a tool execution error, not -32602

- GIVEN a `tools/call` naming a known tool whose `arguments` fail that
  tool's `inputSchema` (a missing required property, a wrong type, or an
  unlisted property)
- WHEN the server dispatches it
- THEN the response is an ordinary JSON-RPC result, not a JSON-RPC error,
  and that result's `structuredContent.error.code` equals
  `"invalid_arguments"` with `isError: true`

#### Scenario: The invalid-arguments message never echoes the value

- GIVEN a `tools/call` whose `arguments` include a property that fails
  validation with a specific, sensitive-looking value
- WHEN the server produces the `invalid_arguments` error
- THEN `error.message` names the offending property and does not contain
  the value that failed validation

### Requirement: Partial Reads Are Data, Never An Error

Every result MUST carry a `not_run` list — present even when empty — never
omitted. A read that could only partially complete — a `NotRun` outcome
from the underlying service — MUST be returned as a successful result
carrying a `not_run` entry describing what could not be checked, and MUST
NOT be returned as a tool error or a protocol error.

Every `not_run` entry's `reason` MUST be a fixed string, never the
underlying exception's text and never a document path interpolated
directly, since either can carry confidential content. Every entry's
`label` MUST come from a fixed vocabulary: `in_flight_write`,
`stale_index`, `concept_read`, `relations`, `provenance_walk`,
`graph_build`. WHEN an underlying service produces one or more `NotRun`
outcomes labelled by individual document paths, the gate MUST aggregate
them into exactly one `not_run` entry whose `reason` is a fixed, count-only
message (for example, naming how many documents could not be read), never
the individual paths. WHEN an underlying service produces a `NotRun` whose
label is not in the fixed vocabulary, the gate MUST aggregate it the same
way rather than forwarding the unrecognized label.

#### Scenario: not_run is present, even empty, on every result

- GIVEN a tool call whose underlying services produce no `NotRun` outcome
  at all
- WHEN the tool returns
- THEN the result still carries a `not_run` field, and it is an empty list

#### Scenario: An unreadable ancestor is reported as not_run, not an error

- GIVEN `get`'s call to `list_provenance_sources` returns a `not_run` entry
  for an ancestor it could not resolve
- WHEN `get` returns
- THEN the result is a success carrying that `not_run` entry, not an error

#### Scenario: Document-labelled not_run entries are aggregated

- GIVEN an underlying service produces several `NotRun` outcomes, each
  labelled by a different document path it could not read
- WHEN the gate composes the result
- THEN they appear as exactly one `not_run` entry, labelled from the fixed
  vocabulary, whose `reason` is a fixed count-only message naming how many
  documents could not be read — never any individual path

#### Scenario: An unrecognized label is aggregated, not forwarded

- GIVEN an underlying service produces a `NotRun` outcome whose label is
  not one of the fixed vocabulary's entries
- WHEN the gate composes the result
- THEN that outcome is aggregated into a count-only entry rather than
  crossing the boundary with its original label

#### Scenario: stale_index never produces a not_run entry

- GIVEN the staleness check cannot determine which derived stores are
  stale
- WHEN `query` runs
- THEN the result carries no `not_run` entry for it, because the check
  degrades to reporting no stale stores rather than raising

### Requirement: Cancellation Abandons The Worker And Sends No Response

Receiving `notifications/cancelled` for an in-flight request MUST cancel
the awaiting task for that request. No response MUST be sent for the
cancelled request. The worker thread executing the tool's service call MUST
be abandoned rather than forcibly stopped, and that abandonment MUST be
logged to stderr. `initialize` MUST NOT be cancellable.

#### Scenario: A cancelled query sends no response

- GIVEN an in-flight `query` request
- WHEN the client sends `notifications/cancelled` naming that request's id
- THEN no response is ever sent for that request, and the abandonment is
  logged to stderr

#### Scenario: A cancelled request does not block later requests

- GIVEN a cancelled request whose worker thread is still abandoned and
  running
- WHEN the client sends a new request afterward
- THEN the new request is served normally, unblocked by the abandoned
  worker

#### Scenario: initialize cannot be cancelled

- GIVEN an in-flight `initialize` request
- WHEN the client sends `notifications/cancelled` naming it
- THEN the handshake proceeds to completion regardless

### Requirement: Stdin Lines And Tool Calls Are Bounded

The server MUST bound what one peer can make it hold. A stdin line longer
than 8 MiB (terminator included) MUST be dropped without being buffered
whole: the rest of that line is discarded, the dropped message is answered
with `-32700` and `id: null` exactly like any unparseable frame, and the
next line is read and served normally. The queue between the stdin reader
and the dispatcher MUST be bounded, and the reader MUST block, not drop
frames, when it is full.

Tool calls MUST be bounded in both number and time. At most a fixed number
of tool-call worker threads run at once; a call beyond that waits for a
free slot rather than starting a thread. Every `tools/call` MUST have a
deadline, the configured `chat_timeout` plus a fixed headroom (the packaged
default `chat_timeout` plus that headroom when the config cannot be read),
covering both the wait for a slot and the run. When it expires the server
MUST answer that request with JSON-RPC error `-32001` and a generic message,
and MUST NOT write any further response for that request id, including when
the abandoned worker later returns a result. A worker thread cannot be
stopped, so its slot MUST be released only when the thread actually
finishes, never when its request is answered, cancelled or abandoned.

#### Scenario: An over-long line is dropped and the next message is served

- GIVEN a stdin line longer than the maximum line length, followed by a
  valid request
- WHEN the server reads them
- THEN it answers the long line with `-32700` and `id: null`, and answers
  the valid request normally

#### Scenario: A tool call past its deadline gets one error response

- GIVEN a `tools/call` whose tool runs longer than the deadline
- WHEN the deadline expires
- THEN the server responds with error `-32001`, and when the worker later
  finishes, nothing further is written for that request id

#### Scenario: A call beyond the concurrency cap waits for a slot

- GIVEN as many tool calls running as the cap allows
- WHEN another `tools/call` arrives
- THEN no worker thread starts for it until a running worker finishes, and
  it is answered with `-32001` if its deadline expires first

### Requirement: Closing Stdin Abandons In-Flight Requests And Exits Cleanly

WHEN the client closes stdin (end of input), the server MUST stop reading
further frames, abandon every still-running in-flight request exactly as a
cancellation abandons its worker — sending no response for any of them —
log the count of abandoned requests to stderr, and exit with status `0`. A
`KeyboardInterrupt` MUST exit with status `130` instead.

#### Scenario: Closing stdin abandons in-flight requests and exits 0

- GIVEN one or more in-flight tool calls when the client closes stdin
- WHEN the server observes end of input
- THEN it sends no response for any of those in-flight requests, logs the
  abandonment to stderr, and exits with status `0`

#### Scenario: A KeyboardInterrupt exits distinctly

- GIVEN the server process receives a `KeyboardInterrupt`
- WHEN it handles the interrupt
- THEN it exits with status `130`

### Requirement: `openkos mcp` Is A Read-Only Command That Never Waits On The Workspace Lock

`openkos mcp` MUST be classified among the CLI's read-only commands, the
same set every lock-free verb belongs to. Serving MUST NOT block on, or
attempt to acquire, the workspace write lock at any point — not at
launch, and not for any tool call during its whole serving lifetime.

#### Scenario: mcp is classified as a read-only command

- GIVEN the CLI's set of read-only commands
- WHEN `openkos mcp` is inspected
- THEN it is a member of that set

#### Scenario: Serving never waits on the workspace lock

- GIVEN another process holds the workspace write lock for the entire
  duration
- WHEN `openkos mcp` launches and serves tool calls
- THEN it never blocks waiting for that lock, consistent with every tool
  holding no lock

### Requirement: Progress Notifications Report Query's Phases

WHEN a `tools/call` for `query` carries `_meta.progressToken`, the server
MUST emit `notifications/progress` for that request faithfully carrying
each phase and total `answer()`'s `progress` callback reports
(`query-answer`'s progress requirement defines the phase vocabulary and the
total), with a monotonically non-decreasing progress value and the phase
name as that notification's message. WHEN no `progressToken` is present,
or for any of the other three tools, no progress notification MUST be
emitted.

#### Scenario: A progressToken produces progress notifications

- GIVEN a `query` call carrying `_meta.progressToken`
- WHEN the request runs to completion
- THEN one or more `notifications/progress` frames are emitted for that
  token, with non-decreasing progress values and each frame's phase and
  total matching what `answer()`'s callback reported

#### Scenario: No progressToken means no progress notifications

- GIVEN a `query` call with no `_meta.progressToken`
- WHEN the request runs
- THEN no `notifications/progress` frame is emitted for it

#### Scenario: get, navigate, and pending never emit progress

- GIVEN a `tools/call` for `get`, `navigate`, or `pending`, even one
  carrying `_meta.progressToken`
- WHEN the request runs
- THEN no `notifications/progress` frame is emitted

### Requirement: The Enumeration Guard Proves No Confidential Leak

An enumeration guard MUST run every registered tool, including its error
paths, against a fixture holding a confidential canary object, and MUST
assert that the canary's id, title, and a body marker appear in no
serialized response, success or error. The guard MUST be proven against a
deliberately violating test-only tool written before any real tool, and
that test MUST demonstrate the guard catching the violation.

#### Scenario: The guard catches a deliberately violating tool

- GIVEN a test-only tool that returns the canary's id in its result
- WHEN the enumeration guard runs against it
- THEN the guard's assertion fails, proving the guard is capable of
  detecting a leak

#### Scenario: Every real tool passes the guard on every path

- GIVEN the four real tools, exercised through both success and error paths
- WHEN the enumeration guard runs
- THEN the canary's id, title, and body marker appear in no serialized
  response

### Requirement: Layering Keeps The Core Free Of The Adapter, And The Adapter Free Of The CLI

`openkos.mcp` MUST import only: `application` modules, `openkos.config`,
`openkos.read_outcome`, the LLM backend's base interface, the Ollama and
OpenAI-compatible clients' concrete classes and their exception types
(needed to construct either backend and to map its exceptions to tool-error
codes), and the FTS module's unavailability exception; it MUST NOT import
`openkos.cli`, and it MUST NOT import `openkos.graph` — `navigate` reaches
the graph exclusively through an `application` service, not directly.
`openkos.sensitivity` MUST be imported by exactly one module under `mcp/`
(`gate.py`); no other `mcp` module MUST reference the disclosure predicate
or its set-producing sibling. `application` MUST NOT import `openkos.mcp`.
The CLI MUST import `openkos.mcp` lazily, inside the `mcp` verb's own
function body, so `asyncio` never loads on the CLI's ordinary startup path.

#### Scenario: openkos.mcp never imports openkos.cli

- GIVEN a static import check of every module under `src/openkos/mcp/`
- WHEN its imports are inspected
- THEN `openkos.cli` is absent from all of them

#### Scenario: openkos.mcp never imports openkos.graph

- GIVEN a static import check of every module under `src/openkos/mcp/`
- WHEN its imports are inspected
- THEN `openkos.graph` is absent from all of them

#### Scenario: Only gate.py imports the disclosure predicate module

- GIVEN a static import check of every module under `src/openkos/mcp/`
  other than `gate.py`
- WHEN its imports are inspected
- THEN none of them imports `openkos.sensitivity`

#### Scenario: application never imports openkos.mcp

- GIVEN a static import check of every module under
  `src/openkos/application/`
- WHEN its imports are inspected
- THEN `openkos.mcp` is absent from all of them

#### Scenario: The CLI imports mcp lazily, only inside the verb

- GIVEN a static import check of `cli/main.py`'s module-level imports
- WHEN they are inspected
- THEN `openkos.mcp` is absent from them, and appears only inside the `mcp`
  verb's function body

#### Scenario: mcp/server.py may import both concrete client classes

- GIVEN `mcp/server.py`'s imports, needed to inject both backends' concrete
  classes as factories into the resolver
- WHEN a static import check runs
- THEN both `OllamaClient` and `OpenAICompatibleClient` (and their exception
  types) are permitted imports for `mcp/server.py`, and the ban on importing
  `openkos.cli`/`openkos.graph` still holds
### Requirement: Chat And Embed Client Construction Lives In `application/backends.py` Behind Injected Factories, With CLI Delegators

The definitions of chat-client construction and local-exemption resolution
MUST live in `application/backends.py`, taking the concrete client class as
an injected factory argument rather than importing one directly — the
application layer MUST bind no concrete backend of its own. This chat-client
construction function MUST additionally dispatch between injected factories
by `cfg.backend` (`ollama` or `openai-compatible`), and
`application/backends.py` MUST expose a parallel `embed_client()` function
following the same injected-factory, backend-dispatch shape for embedding
construction. `cli/main.py` MUST keep its existing `_chat_client` and
`_resolve_local_exemption` names, each reduced to a one-line delegator that
calls the `application/backends.py` definition. The CLI's call sites stay in
place as delegators — they are not repointed to `application` directly — so
every existing test seam that patches `_chat_client`/`_resolve_local_exemption`
by name keeps working, and the CLI's own observable behavior is unaffected.
Both the CLI and the MCP
adapter build their chat AND embed clients through these same,
non-CLI-importable definitions, injecting both concrete client classes as
factories so either can be selected by `cfg.backend`.

#### Scenario: The definition takes the client class as an injected factory

- GIVEN `application/backends.py`'s chat-client construction function
- WHEN its signature is inspected
- THEN it accepts the concrete client class(es) as injected factory
  arguments, and the module imports no concrete client class of its own

#### Scenario: The CLI keeps one-line delegators under their existing names

- GIVEN `cli/main.py`
- WHEN `_chat_client` and `_resolve_local_exemption` are inspected
- THEN each still exists under its existing name as a single-line call into
  `application/backends.py`'s definition

#### Scenario: The CLI's observable behavior, and its test seam, are unaffected

- GIVEN the CLI's existing chat-client and local-exemption behavior, and
  the existing tests that patch `_chat_client`/`_resolve_local_exemption` by
  name
- WHEN the CLI runs any command that constructs a backend
- THEN its observable behavior is unchanged, and the same patches still take
  effect

#### Scenario: Chat construction dispatches to the factory matching cfg.backend

- GIVEN `cfg.backend == "openai-compatible"` and both concrete client
  classes injected as factories
- WHEN the chat-client resolver is called by either the CLI or the MCP
  adapter
- THEN it constructs and returns an instance of the `openai-compatible`
  factory, not the `ollama` one

#### Scenario: The MCP adapter builds its embed client through embed_client()

- GIVEN `mcp/server.py`'s embed-construction call site
- WHEN it needs an embedding client
- THEN it calls `application/backends.py`'s `embed_client()` rather than
  constructing `OllamaClient` or `OpenAICompatibleClient` directly

### Requirement: `pending` Also Lists Open Queue Rows Through The Disclosure Gate

WHEN the pending-work queue exists, the `pending` tool's result MUST carry,
beside the existing `next_action` recommendation, the open queue rows --
kind, target ids, and the resolving command -- read without a lock and
without a model call. Every row MUST pass through the same disclosure gate
the existing recommendation passes through: a row with any subject that is
not fully disclosable MUST be withheld whole, and the result MUST NOT count
withheld rows. The tool MUST NOT change any row's status. WHEN the queue is
absent, the result MUST say it has not been computed, never that nothing is
pending.

#### Scenario: A row naming a confidential concept is withheld

- GIVEN an open identity row whose members include a concept not
  disclosable at the server's launch setting
- WHEN `pending()` returns
- THEN that row is absent and no count of withheld rows is present

#### Scenario: Listing does not claim a row

- GIVEN an open row
- WHEN `pending()` is called
- THEN the row's status is unchanged
