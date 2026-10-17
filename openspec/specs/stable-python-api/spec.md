# Stable Python API Specification

## Purpose

The one declared, versioned, in-process Python surface of OpenKOS (ADR-0039).
`openkos.api` is a facade over the synchronous `application/` services; no
`application/` module is public in place, and none moves. The v1 operation
set is the review queue, guided setup and asking: workspace status, the pending report, listing,
previewing and applying open decisions, judging quote and run, the
model-free queue refresh, reading the pages a decision shows, the merge
history and its unmerge, the list of combined pages, the undo of a
keep-separate ruling, a typed relation or a type's tier, guided setup's
model-free readiness read and configured-model pull, and asking a question.
Ingest and doctor are not part of v1.

The surface is owner-local, as the CLI is: its caller is the workspace owner.
It applies no sensitivity disclosure gate except to the answer `ask` returns,
which follows the query disclosure rule of the MCP `query` tool and `query
--json` (ADR-0068); any other disclosing adapter applies the local API's gate
itself (ADR-0058).

Compatibility is promised by `API_VERSION`, not by the package version, which
is 0.x and promises nothing about this surface.

## Requirements

### Requirement: The Public Package Declares Exactly Its Public Names

`openkos.api` MUST be an importable package that defines `__all__`. Every name
in `__all__` MUST be importable from `openkos.api`, and every public name the
package exposes (a name not starting with an underscore that is defined or
re-exported by it) MUST appear in `__all__`. Modules under `openkos.application`
and other internal packages MUST NOT be declared public by this capability; a
name is public only if `openkos.api` exports it.

#### Scenario: Every declared name resolves

- GIVEN `openkos.api` is imported
- WHEN each entry of `__all__` is looked up on the module
- THEN every lookup succeeds

#### Scenario: A public name missing from `__all__` is detected

- GIVEN `openkos.api` exposes a non-underscore name that is not in `__all__`
- WHEN the `__all__` completeness test runs
- THEN the test fails and names that symbol

#### Scenario: An internal module is not made public by the facade

- GIVEN an `application/` module that `openkos.api` does not export
- WHEN `__all__` is read
- THEN it contains no name that exists only to expose that module

### Requirement: `API_VERSION` Is An Integer Independent Of The Package Version

`openkos.api` MUST export `API_VERSION`, an `int`, equal to `1` for the first
published surface. `API_VERSION` MUST be independent of the package version in
`pyproject.toml`: a package release MUST NOT change it, and a change to it
MUST NOT require a package version change.

#### Scenario: The version is an integer starting at 1

- GIVEN `openkos.api` is imported
- WHEN `API_VERSION` is read
- THEN it is an `int` equal to `1`

#### Scenario: The package version does not drive it

- GIVEN the package version changes and no public name, signature, field or
  exception base changes
- WHEN `API_VERSION` is read
- THEN it is unchanged

### Requirement: Changes Within A Major Are Additive; A Breaking Change Bumps The Version

Within one value of `API_VERSION`, a change to the public surface MUST be
additive: adding a new public name, a new optional parameter with a default, a
new field on a result dataclass that is constructed only by the API, or a new
exception subclass is permitted. Removing or renaming a public name, changing
or removing a parameter, making an optional parameter required, changing or
removing a public dataclass field, or changing an exception's base classes in a
way that stops an existing `except` clause from catching it, is a breaking
change and MUST be accompanied by an increase of `API_VERSION` in the same
change.

#### Scenario: An additive change keeps the version

- GIVEN a new optional keyword parameter with a default is added to a public
  function
- WHEN the snapshot is regenerated
- THEN the diff is additive and `API_VERSION` may stay unchanged

The bump is enforced against the base branch. A test MUST read the snapshot
recorded at the merge base of `HEAD` and `origin/main`, classify the diff
between it and the live snapshot, and fail when the diff is breaking and
`API_VERSION` is not higher than the one recorded there. Comparing with `HEAD`
alone cannot enforce the bump in CI, because a pull request checkout already
contains its own regenerated snapshot. While a regenerated snapshot is
uncommitted, the same test MUST also compare it with the snapshot recorded at
`git HEAD`. When no base ref is reachable the test MUST skip, unless
`OPENKOS_REQUIRE_API_BASE_REF=1`, which CI sets and which turns a missing base
into a failure. CI checks out the full history so the merge base exists.

#### Scenario: A breaking change without a bump fails against the base branch

- GIVEN a public function loses a parameter, the regenerated snapshot is
  committed, and `API_VERSION` is unchanged from the merge base with `origin/main`
- WHEN the snapshot test runs
- THEN it fails until `API_VERSION` is increased

#### Scenario: A breaking change with a bump passes

- GIVEN the same breaking change and an `API_VERSION` higher than the merge
  base's
- WHEN the snapshot test runs
- THEN the bump test passes

#### Scenario: An additive change needs no bump

- GIVEN a new public name and an unchanged `API_VERSION`
- WHEN the snapshot test runs
- THEN the bump test passes

#### Scenario: A breaking change without a bump fails before it is committed

- GIVEN a public function loses a parameter, the snapshot is regenerated but not
  yet committed, and `API_VERSION` is unchanged
- WHEN the snapshot test runs against the snapshot recorded at `git HEAD`
- THEN the test fails until `API_VERSION` is increased

#### Scenario: A missing base is a failure in CI and a skip elsewhere

- GIVEN no `origin/main` ref is reachable
- WHEN the bump test runs without `OPENKOS_REQUIRE_API_BASE_REF=1`
- THEN it is skipped
- AND with `OPENKOS_REQUIRE_API_BASE_REF=1` it fails

### Requirement: A Golden Snapshot Pins The Public Surface

A committed golden snapshot MUST record, for every name in `__all__`: its kind,
its signature (parameter names, kinds, annotations and defaults), the fields of
every public dataclass (name, annotation, default presence), and the base
classes of every public exception. A test MUST compare the live surface against
the snapshot and MUST fail on any difference. The snapshot MUST also record the
`API_VERSION` it was generated under. A difference that is breaking under the
compatibility policy MUST additionally require a higher `API_VERSION` than the
recorded one for the test to pass after regeneration; a purely additive
difference MUST pass once the snapshot is regenerated and MUST NOT require a
bump. The bump is checked against the snapshot at the merge base with
`origin/main`, and against `git HEAD` for an uncommitted regeneration (see
"Changes Within A Major Are Additive; A Breaking Change Bumps The Version").

#### Scenario: An untouched surface matches the snapshot

- GIVEN no public name, signature, field or exception base has changed
- WHEN the snapshot test runs
- THEN it passes

#### Scenario: Removing a public field fails

- GIVEN a public dataclass field is removed
- WHEN the snapshot test runs
- THEN it fails, naming the dataclass and field

#### Scenario: Changing an exception base fails

- GIVEN a public exception no longer inherits from the base recorded in the
  snapshot
- WHEN the snapshot test runs
- THEN it fails, naming the exception

#### Scenario: A breaking diff regenerated without a bump fails

- GIVEN a breaking diff and a regenerated snapshot recording the same
  `API_VERSION` as the merge base with `origin/main`, committed or not
- WHEN the snapshot test runs
- THEN it fails

### Requirement: An Import Guard Bars Interactive And Disclosing Layers From The API

A test MUST inspect the imports of every module under `openkos.api` against an
allowlist and MUST fail, naming the module and the import, on any import
outside it. Every module under `openkos.api` MAY import only the standard
library, `openkos.api.*`, `openkos.errors`, `openkos.config`, `openkos.lock`,
`openkos.application.*` and `openkos.llm.base`; the API's backend and commit
wiring module additionally MAY import the concrete backend client modules and
the print-free git commit core. In particular the test MUST fail, by name, if
any module imports `openkos.cli`, `openkos.mcp` (including `openkos.mcp.gate`),
`typer`, `rich`, `asyncio`, `openkos.state` or `sqlite3`. A second check MUST
fail if any public signature or public dataclass field annotation mentions
`sqlite3`, a type defined under `openkos.state`, or any `openkos` type that
`openkos.api` does not export. A third check MUST fail if any module under
`src/openkos/` outside `openkos.api` and outside `openkos.localapi` imports
`openkos.api`; `openkos.localapi`, the local HTTP adapter, is the one package
permitted to import it, and it MUST import no other `openkos` module (see the
`local-http-api` capability). The existing layering tests MUST continue to bar
`openkos.mcp`, and every top-level package other than `application`, `cli` and
`api` (so including `openkos.localapi`), from importing the owner-local
`application/` services, and MUST NOT be weakened for any importer.

#### Scenario: An API module importing the CLI is rejected

- GIVEN a module under `openkos.api` imports `openkos.cli`
- WHEN the import guard runs
- THEN it fails and names the module and the import

#### Scenario: An API module importing the disclosure gate is rejected

- GIVEN a module under `openkos.api` imports `openkos.mcp.gate`
- WHEN the import guard runs
- THEN it fails

#### Scenario: A leaked internal type is rejected

- GIVEN a public function whose return annotation is a `state.*` type
- WHEN the signature-type check runs
- THEN it fails and names the function

#### Scenario: An import outside the allowlist is rejected

- GIVEN a module under `openkos.api` imports a module not on the allowlist
- WHEN the import guard runs
- THEN it fails and names the module and the import

#### Scenario: Nothing outside the API and its local adapter imports it

- GIVEN a module outside `openkos.api` and outside `openkos.localapi` imports
  `openkos.api`
- WHEN the guard runs
- THEN it fails and names the module

#### Scenario: The local HTTP adapter may import the API

- GIVEN a module under `openkos.localapi` imports `openkos.api`
- WHEN the guard runs
- THEN it passes

#### Scenario: The local HTTP adapter cannot reach owner-local services

- GIVEN a module under `openkos.localapi` imports an owner-local
  `openkos.application` service
- WHEN the existing layering test runs
- THEN it fails

#### Scenario: The MCP adapter still cannot reach owner-local services

- GIVEN a module under `openkos.mcp` imports an owner-local `application/`
  service
- WHEN the existing layering test runs
- THEN it fails

### Requirement: A Workspace Handle Resolves The Workspace Once

`openkos.api` MUST export a `Workspace` type. `Workspace.open(path)` MUST
validate the workspace and read `openkos.yaml` once, and return an immutable
handle. The `path` MUST be the workspace root itself, the rule every CLI verb
applies (the workspace-root requirement of `config.require_workspace`); the
API MUST NOT search upward from a nested directory. The path MUST be taken as
given (made absolute, not symlink-resolved), so the refusal of a symlinked
workspace path still applies. The handle MUST NOT cache the configuration for
the services: each operation lets the service re-read `openkos.yaml`, so a
change of model, backend or locality made while the handle is held is observed.
Constructing `Workspace(root=path)` directly MUST be equivalent to
`Workspace.open(path)`. Every v1 operation MUST accept the handle as its
workspace argument, so a caller never has to choose between a root path and a
layout.

#### Scenario: Open a workspace by its root

- GIVEN an initialised workspace
- WHEN `Workspace.open` is called with its root
- THEN a handle is returned exposing the root

#### Scenario: A nested directory is not a workspace root

- GIVEN an initialised workspace
- WHEN `Workspace.open` is called with a directory inside it
- THEN the typed not-found exception is raised, as the CLI refuses to run from
  a subdirectory, and no upward search is made

#### Scenario: A configuration change is observed through a held handle

- GIVEN an open handle and a later edit of the backend model in `openkos.yaml`
- WHEN an operation that reads the configuration runs on the same handle
- THEN it observes the new configuration

#### Scenario: The handle is immutable

- GIVEN an open handle
- WHEN a caller assigns to one of its attributes
- THEN the assignment raises

### Requirement: A Missing Workspace Is A Typed Not-Found Failure

When `Workspace.open` is given a path that is not the root of an OpenKOS
workspace (absent, a nested directory, or not a workspace), it MUST raise a
public not-found exception, a subclass of `OpenKosError`, that carries the path
it was given and a reason. When the path is a workspace but cannot be read
(permission denied, a symlinked path segment, or an unreadable or invalid
`openkos.yaml`) it MUST raise a distinct public unreadable exception, also a
subclass of `OpenKosError`, carrying the path and a reason. It MUST NOT raise a
bare `FileNotFoundError`, `KeyError` or a CLI exit, and MUST NOT create or
modify anything on disk.

#### Scenario: A directory that is not a workspace root

- GIVEN a directory that is not a workspace root
- WHEN `Workspace.open` is called with it
- THEN the typed not-found exception is raised, it is an `OpenKosError`, and it
  names the path
- AND nothing is written under that directory

#### Scenario: An unreadable configuration is not "not found"

- GIVEN a workspace root whose `openkos.yaml` cannot be read or parsed
- WHEN `Workspace.open` is called with it
- THEN the typed unreadable exception is raised, it is an `OpenKosError`, and
  it names the path

### Requirement: `OpenKosError` Is The Single Catchable Root

`openkos.api` MUST export `OpenKosError`. The typed refusals reachable through
the v1 operations MUST inherit from it, including the write-refusal family
(`WriteRefused` and its subclasses), `JudgingRefused`, the pending-queue
unavailable error, the queue-refresh refusal, the workspace busy and lock
unavailable errors, the two workspace-open errors, and the neutral `Backend*`
errors from `llm/base.py`; each of these MUST be exported. The suggestion
refusal reachable through a judging run, `ListUsageError` and `ConceptNotFound`
MUST also inherit from the root but need not be exported in v1. Stale outcomes
are not exceptions: a stale decision (`Stale`), a stale judging quote
(`QuoteStale`) and a refused run (`RunRefused`) are returned values and MUST NOT
be raised or placed under the root. Adding the root MUST be catch-compatible:
every one of these exceptions MUST remain an instance of every class it was an
instance of before the root was added (its previous bases are kept, in the same
relative order), so an existing `except WriteRefused` (or the equivalent for
each type) keeps catching it and the CLI's exit codes and messages are
unchanged. Exceptions outside this list (for example a programming error such as
a `ValueError` for an unknown kind) MUST NOT be forced under the root.

#### Scenario: One except clause catches a refusal

- GIVEN a v1 operation that raises `JudgingRefused`
- WHEN the caller wraps the call in `except OpenKosError`
- THEN the refusal is caught

#### Scenario: Existing catches keep working

- GIVEN code that catches `WriteRefused` today
- WHEN a write-refusal subclass is raised
- THEN it is still caught by `except WriteRefused`
- AND it is also an instance of `OpenKosError`

#### Scenario: A stale outcome is a value, not an exception

- GIVEN a stale decision or a stale judging quote
- WHEN the corresponding apply or run operation is called
- THEN the stale outcome is returned and no `OpenKosError` is raised

#### Scenario: The hierarchy is in the snapshot

- GIVEN a public exception's base list changes
- WHEN the snapshot test runs
- THEN it fails

### Requirement: The Surface Is Owner-Local And Gates Only An Answer

The Python API MUST NOT apply the sensitivity disclosure gate to any result
other than the `Answer` that `ask` returns, and its documentation (the package
docstring) MUST state that it is an owner-local, in-process surface whose
caller is the owner, that `ask` applies the query disclosure rule to its answer
(ADR-0068), and that any other disclosing adapter MUST apply the local API's
gate itself (ADR-0058). Results other than an answer MAY therefore carry stored
content. The API MUST NOT send content to any model backend other than the
configured one, and only through the judging run and `ask`. The only other
process it invokes is the local git auto-commit for a decision it applies,
which never pushes. The docstring MUST also state that cancellation is
cooperative (a stop waits for the chunk in flight), that v1 apply does not
reconcile merged bodies and does not refresh the derived stores, and that
`API_VERSION`, not the package version, is the compatibility promise.

#### Scenario: The docstring states the boundary

- GIVEN `openkos.api` is imported
- WHEN its `__doc__` is read
- THEN it states that the surface is owner-local and that only `ask` applies a
  disclosure rule, to its answer

#### Scenario: The facade does not depend on the gate

- GIVEN the import guard
- WHEN it inspects `openkos.api`
- THEN no module imports `openkos.mcp.gate`

### Requirement: Workspace Status And The Pending Report Are Readable

The API MUST expose a read operation for workspace status and one for the
pending-work report. Each MUST take a `Workspace`, MUST make no model call,
MUST take no workspace lock, MUST build no model client and MUST write nothing.
Each MUST return the same information the corresponding CLI verb (`status`,
`pending`) reads, as API-owned result types that satisfy the signature-type
check; internal types (lint findings, bundle surveys, queue rows, job records)
MUST be mapped to counts, strings and API-owned values, never exposed. When the
pending queue has not been computed, the pending report MUST say so as a
distinct state and MUST NOT present it as an empty queue. When the queue cannot
be read, the pending report MUST raise the public queue-unavailable error.

#### Scenario: Status of an initialised workspace

- GIVEN an initialised workspace
- WHEN the status operation is called
- THEN it returns a result and the workspace is unchanged

#### Scenario: An absent queue is not an empty queue

- GIVEN a workspace whose queue table has not been computed
- WHEN the pending report operation is called
- THEN the result marks the queue as not computed, distinct from a report with
  zero open rows

#### Scenario: Reading is lock-free and model-free

- GIVEN a workspace lock held by another process
- WHEN status and the pending report are read
- THEN both return, and no model client is built

### Requirement: Open Decisions Can Be Listed, Previewed And Applied

The API MUST expose operations to list open decisions, to preview one decision,
and to apply one decision, over the same services the `curate` decision
surface uses. Listing and preview MUST write nothing, take no lock, build no
model client and make no model call. Applying MUST act on one named decision per
call, taking the version token a preview returned, and MUST NOT apply any other
item. A decision that is no longer open, a key that no queue row ever carried, or a
decision whose inputs changed since the preview, MUST NOT be applied: the apply and preview operations MUST return the
shipped `Stale` or `NotApplied` outcome as a value (they do not raise it), so a
caller cannot forget to handle it. A key that no queue row ever carried MUST be
reported with the `Stale` reason `unknown_key`, distinct from `row_resolved`,
which means a row carrying the key was settled or retired. A successful apply returns `Applied` and
MUST record the resolution the same way the corresponding human-facing path
does (a row resolves as `applied`) and, as the CLI does, attempt a local git
auto-commit of the paths it wrote; when no commit can be made (not a
repository, identity unset, failure) the apply MUST still succeed with no commit
identity and MUST report the skipped commit through the caller's optional
warning callback. A commit MUST contain only the paths the decision wrote
(a pre-staged unrelated file or an unrelated dirty edit of `openkos.yaml` is
never swept in). `Applied` MUST say, as returned data, whether the commit it
records holds a concept explicitly marked confidential, so a caller can show the
notice the CLI prints; it is false when no commit landed. Applying takes the workspace lock only inside its commit
section, waiting at most the caller's `wait_seconds` (default zero) and raising
the existing workspace-busy error beyond it.

In v1, preview and apply MUST NOT reconcile merged concept bodies with a model
and MUST NOT refresh the derived indexes; `status` reports the indexes as stale
and a later maintenance pass or reindex refreshes them. This is a documented
limitation, not a guarantee of equivalence with `curate`'s merge output.

The decision payload is an unversioned stored mapping and MUST NOT be exposed.
Listing MUST return API-owned values whose kind-specific details (identity,
relation, tier) are typed fields built from the stored payload by reader
functions that live beside the payload's writer; the adjudicator's confidence
MUST NOT be exposed. A payload that cannot be read into its typed detail MUST
yield a listed decision with no detail rather than hiding it. Previews MUST be
API-owned per-action plans holding only identifiers, types and flags.

#### Scenario: Listing shows open decisions

- GIVEN a queue with two open decisions
- WHEN the list operation is called
- THEN both are returned and nothing is written

#### Scenario: Preview does not change anything

- GIVEN an open decision
- WHEN it is previewed
- THEN the bundle and the queue are byte-identical afterwards

#### Scenario: Apply acts on one decision

- GIVEN two open decisions
- WHEN one is applied
- THEN that one is `applied` and the other is still open

#### Scenario: A stale decision is returned as a value

- GIVEN a previewed decision whose member was edited afterwards
- WHEN it is applied with the preview's version
- THEN `Stale` is returned, nothing is raised, and the bundle is unchanged

#### Scenario: A malformed payload is still listed

- GIVEN an open row whose stored payload cannot be read into a typed detail
- WHEN the list operation is called
- THEN the decision is listed with no detail

#### Scenario: A commit that cannot be made does not fail the apply

- GIVEN a workspace that is not a git repository
- WHEN a decision is applied
- THEN the result is `Applied` with no commit identity and the warning callback
  is called once

#### Scenario: Apply does not sweep unrelated changes into the commit

- GIVEN an unrelated staged file in the repository
- WHEN a decision is applied
- THEN the commit contains only the decision's paths and the staged file stays
  staged and uncommitted

#### Scenario: A held lock surfaces as busy

- GIVEN the workspace lock held by another process and `wait_seconds` of zero
- WHEN a decision is applied
- THEN the workspace-busy error is raised and nothing is written

#### Scenario: A key that never existed is distinguishable from a resolved decision

- GIVEN a decision key that no queue row ever carried, and a decision that was
  already applied
- WHEN each is previewed or applied
- THEN the first is `Stale` with reason `unknown_key` and the second is `Stale`
  with reason `row_resolved`, and nothing is written

#### Scenario: A commit holding a confidential concept says so

- GIVEN a decision whose commit holds a concept marked `sensitivity: confidential`
- WHEN the decision is applied
- THEN `Applied` reports a confidential commit, and a commit holding no such
  concept, or no commit at all, reports none

### Requirement: Judging Is Quoted, Then Run Against That Quote

The API MUST expose a judging quote operation and a judging run operation over
the judging application service. Quoting MUST make no model call and MUST
return a quote bound to a token; it constructs a client in order to read the
backend's locality and the task model, but MUST NOT call it. Running MUST
require that token. A run with no token MUST return `RunRefused`, and a run
whose binding moved since the quote (inputs, model, backend or locality) MUST
return `QuoteStale`, as the judging service does: both are returned values, not
raised exceptions, and neither makes a model call. The run's result type is the
union of the judging outcome, `QuoteStale` and `RunRefused`. Running MUST call
the model for at most the items the quote covered. The run MUST accept the
existing cooperative stop token and observer ports. A stop MUST take effect
between chunks only; the API MUST NOT claim to preempt a model call in flight,
and its documentation MUST say so. A run that cannot persist a chunk because
the workspace stays busy MUST report that through its outcome status rather
than raising.

#### Scenario: A quote costs no model call

- GIVEN a backend that fails the test if its chat is called
- WHEN a quote is requested
- THEN a quote with a token is returned

#### Scenario: A stale quote is returned, not raised

- GIVEN a quote and the configured model changed afterwards
- WHEN the run is invoked with the quote's token
- THEN `QuoteStale` is returned, nothing is raised, and no model call is made

#### Scenario: A run without a token is refused as a value

- GIVEN no quote token
- WHEN the run is invoked
- THEN `RunRefused` is returned and no model call is made

#### Scenario: A stop is honoured between chunks

- GIVEN a run in progress and a stop requested during a chunk
- WHEN that chunk finishes
- THEN the run ends without starting the next chunk and reports that it was
  stopped

### Requirement: Judging Resolves Its Default Backend Inside The API Without The CLI

When the caller supplies no chat client, the judging quote and run MUST build
one from the workspace configuration as the service freshly reads it
(`openkos.yaml` `backend` and model) through the resolvers in
`application/backends.py`, with no import of `openkos.cli`. A caller MAY
override the client with one factory (task name to client) that replaces the
default. The default MUST select the same backend and model that the
configuration names, and the same constructor arguments the CLI's client
construction uses. The quote builds a client but makes no model call. The
status and pending reads, list, preview, and refresh MUST build no client.

#### Scenario: The default follows the configuration

- GIVEN a workspace configured for a given backend and model
- WHEN a run is invoked with no client supplied
- THEN the client built is for that backend and model

#### Scenario: An override replaces the default

- GIVEN a caller-supplied chat client
- WHEN a run is invoked
- THEN the supplied client is used and no default client is built

#### Scenario: A backend failure surfaces as a neutral error

- GIVEN a backend that is unreachable
- WHEN a run is invoked
- THEN the backend's error is raised unchanged: it is an instance of the public
  neutral `BackendUnavailable` (or `BackendModelNotFound`) exported by
  `openkos.api`, and therefore of the neutral `BackendError` base and of
  `OpenKosError`
- AND the vendor-specific leaf class (for example the Ollama one) is not
  exported, so a caller catches the neutral base rather than the leaf

#### Scenario: An unreachable backend is not a stale quote

- GIVEN a backend that is unreachable at run time
- WHEN a run is invoked with a valid quote token
- THEN the neutral backend error is raised and no `QuoteStale` is returned

### Requirement: Refreshing The Decision Queue Is Exposed As A Model-Free Operation

The API MUST expose `refresh_decision_queue`, taking a `Workspace`, and
returning a result that reports, per kind (`identity`, `relation_type`,
`volatility`), what the pass did: rows inserted, amended, unchanged, replaced,
suppressed and retired, rows retired because they name a concept no longer in
the bundle (`retired_missing`), and whether the pass was complete for that kind;
the volatility entry additionally counts the types skipped because the two
confidentiality regimes disagreed, and the result carries the identity
truncation notice when the identity report was truncated. Its behavior is
specified under the `pending-work` capability ("The Decision Queue Can Be
Refreshed Without A Model"); this capability requires only that the API exposes
it, that it builds no backend client, that it takes the lock only inside commit
sections (waiting at most the caller's `wait_seconds`, default zero, and raising
the existing workspace-busy error beyond it), and that it returns the per-kind
result. Only the identity entry can report a retirement or a complete pass; the
other two always report `complete` false. Only the identity and relation_type
entries can report `retired_missing`; the volatility entry reports zero. The
field is additive within `API_VERSION` 1. A workspace that cannot be read raises
the public queue-refresh refusal and writes nothing.

#### Scenario: Refresh needs no backend

- GIVEN a workspace whose configured backend is unreachable
- WHEN `refresh_decision_queue` is called
- THEN it completes and no backend client is built

#### Scenario: The result is reported per kind

- GIVEN a refresh over a workspace with persisted verdicts of all three kinds
- WHEN the result is read
- THEN it carries one entry per kind with its inserted, amended and retired
  counts and its completeness

#### Scenario: Only identity can report a complete pass

- GIVEN any refresh
- WHEN the result is read
- THEN the relation_type and volatility entries report `complete` false and
  zero retired

#### Scenario: Rows naming a missing page are counted apart

- GIVEN an open identity row and an open relation-type row, each naming a
  concept whose file is gone
- WHEN `refresh_decision_queue` is called
- THEN the identity and relation_type entries each report `retired_missing` 1

### Requirement: The API Is Importable And Usable Without The CLI

A caller MUST be able to open a workspace, read status and the pending report,
list, preview and apply decisions, quote and run judging, and refresh the queue
using only names exported by `openkos.api`, in a process where `openkos.cli`
has not been imported. Importing `openkos.api` MUST NOT import `openkos.cli`
transitively.

#### Scenario: A fresh interpreter never loads the CLI

- GIVEN a fresh interpreter
- WHEN `openkos.api` is imported
- THEN `openkos.cli` is not in `sys.modules`

#### Scenario: The CLI is unchanged

- GIVEN the existing CLI golden outputs
- WHEN they are run after the API exists
- THEN stdout, stderr and exit codes are byte-identical

### Requirement: Group Decisions Extend v1 Additively

The API MUST expose the curate surface's merge-group action within
`API_VERSION` 1, and every change it makes to the public surface MUST be
additive under the compatibility policy, so the golden snapshot is regenerated
and `API_VERSION` is not raised:

- `Action` gains the member `MERGE_GROUP` (`"merge_group"`);
- a public `GroupSelection` dataclass holds `survivor_id` and `member_ids`;
- `DecisionVersion` gains a defaulted `selection: GroupSelection | None`, which
  is `None` on every version except a merge-group's;
- `preview_decision` gains an optional keyword `selection`, defaulting to
  `None`; `apply_decision` takes no new parameter, because the selection travels
  in the version;
- a public `MergeStep` dataclass describes one pairwise step: the survivor, the
  absorbed member, the absorbed member's OKF type, what happens to that type
  (`same_type`, `recorded`, `discarded` or `unknown`) and the number of other
  documents the step rewrites;
- `MergePlan` gains defaulted `steps`, `kept_distinct_ids` and `survivor_type`;
  a merge plan lists its one step, and a merge-group plan lists every step, the
  members it keeps distinct and the survivor's type;
- `Applied` gains defaulted `merged` (the (survivor, absorbed) pairs written) and
  `kept_distinct` (the member sets a ruling was recorded for);
- `NotApplied.reason` gains the value `invalid_selection`;
- a public `MergeGroupInterrupted` exception, a subclass of `WriteFailed`, so
  `except WriteFailed` and `except OpenKosError` keep catching it, carries the
  completed pairs, the commit identity and the failed absorbed member.

A merge-group preview MUST return a `MergePlan` whose `kind` is `merge`, as a
`relate_reversed` preview returns a plan of kind `relate`; the plan type union
of `DecisionPreview` and the `ApplyOutcome` alias MUST NOT be widened. The
merge-group's survivor, absorbed members and kept-distinct members MUST be
reported by Concept ID only; no stored payload and no adjudicator confidence is
exposed.

#### Scenario: The surface diff is additive

- GIVEN the snapshot recorded at the merge base with `origin/main`
- WHEN the snapshot is regenerated after the change
- THEN every difference is classified additive and `API_VERSION` is still 1

#### Scenario: A merge-group preview round-trips through apply

- GIVEN an open identity decision judged SAME over three members
- WHEN `preview_decision` is called with `MERGE_GROUP` and a selection, and
  `apply_decision` with the returned version and `MERGE_GROUP`
- THEN the preview's plan has `kind` `merge` and two steps, and the result is
  `Applied` whose `merged` lists both pairs

#### Scenario: An interrupted group is caught as a write failure

- GIVEN a merge-group whose second step fails
- WHEN it is applied inside `except WriteFailed`
- THEN the handler runs with a `MergeGroupInterrupted` naming the completed pair

#### Scenario: A pairwise merge plan lists its one step

- GIVEN an open identity decision judged SAME over two members of one type
- WHEN it is previewed with `MERGE`
- THEN the plan's `steps` holds one step whose survivor and absorbed member
  equal the plan's `survivor_id` and `absorbed_id`

### Requirement: Merge History Is Readable

`openkos.api` MUST export `merge_history(workspace, concept_id)`, returning a
tuple of `MergeRecord`, newest merge first, one per entry of the concept's
committed merge ledger. A `MergeRecord` MUST carry `survivor_id`, `absorbed_id`,
`absorbed_title` (the `title` of the absorbed document as the ledger recorded it,
or `None` when it cannot be read) and `merged_at` (the ledger's timestamp). The
read MUST NOT take the workspace lock, build a model client or write anything. An
id that is not a valid Concept ID, or whose concept has no ledger, MUST return an
empty tuple rather than raise.

#### Scenario: A group's merges are listed newest first

- GIVEN a survivor that absorbed `b` and then `c` through a group decision
- WHEN `merge_history` is called for the survivor
- THEN it returns two records, `c` first, each naming the survivor, the absorbed
  id, its recorded title and a timestamp, and the workspace is unchanged

#### Scenario: A concept never merged lists nothing

- GIVEN a concept with no merge ledger, and an id with a `..` segment
- WHEN `merge_history` is called for each
- THEN each returns an empty tuple

### Requirement: Unmerge Is Previewed, Then Applied Against Its Version

`openkos.api` MUST export `preview_unmerge(workspace, survivor_id, absorbed_id,
*, discard_survivor_edits=False)` and `apply_unmerge(workspace, version, *,
wait_seconds=0.0, on_warning=None)`.

The preview MUST plan the unwind of the survivor's merge ledger from the newest
entry down to and including the entry that absorbed `absorbed_id`, and return an
`UnmergePreview` whose `steps` list one `UnmergeStep` per single-step unmerge in
execution order, and whose `version` is an `UnmergeVersion` the apply commits
against. When `absorbed_id` names the newest entry the plan has exactly one step.
Each `UnmergeStep` MUST carry the survivor, the absorbed id, its recorded title,
`merged_at`, the number of other documents the step rewrites, whether the
survivor-edit check is unverifiable for that entry, and whether the entry restores
whole-file catalog snapshots. The preview MUST NOT take the lock, build a model
client or write anything.

The preview MUST return `NotUnmergeable` (never raise) with reason
`survivor_missing` when the survivor's document does not exist (with
`absorbed_into` naming the concept that absorbed it, when a ledger records one),
`not_merged` when the survivor's ledger records no merge of `absorbed_id`, and
`survivor_edited` when restoring the first step would discard an edit made to the
survivor after that step's merge and `discard_survivor_edits` is false. With
`discard_survivor_edits` true, the plan proceeds and `survivor_edits_discarded`
reports whether an edit is discarded. Any other refusal of the plan MUST raise
`Refused`.

The version MUST carry the survivor, the absorbed id, `discard_survivor_edits`
and a content digest covering the ledger entries being reversed and the current
bytes of every file the steps restore, rewrite or recreate. The apply MUST hold
the workspace lock (waiting at most `wait_seconds`, 0 to 3600) while it plans the
same unwind again from current bytes and, when the digest still matches, runs
every step, each as the CLI's single-step unmerge committed on its own. It MUST
return `Unmerged` naming the restored ids in execution order and each step's
commit identity (`None` for a commit that could not be made, reported once
through `on_warning`), `UnmergeStale("content_changed")` when the digest differs,
or the same `NotUnmergeable` a preview would return; neither of those writes
anything. A first step that fails MUST raise its own refusal (`Refused` or
`DriftDetected`) with nothing written; a later step that fails MUST raise
`UnmergeInterrupted`, a subclass of `WriteFailed`, carrying the completed
restored ids, their commits and the failed absorbed id, and the completed steps
MUST be kept. The apply MUST NOT reconcile with a model or refresh the derived
indexes.

#### Scenario: A pairwise merge is split again

- GIVEN two concepts merged through a decision
- WHEN the merge is previewed with `preview_unmerge` and applied with the
  returned version
- THEN the result is `Unmerged` with one restored id and one commit, and every
  file of the bundle except `log.md` equals its bytes before the merge

#### Scenario: A group is split again in one plan

- GIVEN a group decision that merged `b` and `c` into `a`
- WHEN `preview_unmerge(a, b)` is called
- THEN its steps are `c` then `b`, and applying its version restores both with
  one commit per step

#### Scenario: A changed bundle makes the version stale

- GIVEN a preview, after which a document the plan restores or rewrites changes
- WHEN the version is applied
- THEN the result is `UnmergeStale("content_changed")` and nothing is written

#### Scenario: An edited survivor is refused unless the caller opts in

- GIVEN a merge whose survivor was edited afterwards
- WHEN it is previewed without and then with `discard_survivor_edits`
- THEN the first result is `NotUnmergeable("survivor_edited")` and the second is
  a preview reporting `survivor_edits_discarded`

#### Scenario: A flipped echoed flag is stale

- GIVEN a preview without `discard_survivor_edits`
- WHEN its version is applied with the flag set to true
- THEN the result is `UnmergeStale` and nothing is written

#### Scenario: A busy workspace writes nothing

- GIVEN the workspace lock is held by another process
- WHEN a version is applied with `wait_seconds` 0
- THEN `WorkspaceBusyError` is raised and nothing is written

### Requirement: Unmerge Extends v1 Additively

The unmerge operations MUST be added within `API_VERSION` 1, and every change to
the public surface MUST be classified additive by the surface classifier, so the
golden snapshot is regenerated and `API_VERSION` is not raised: new names
(`MergeRecord`, `UnmergeStep`, `UnmergeVersion`, `UnmergePreview`,
`NotUnmergeable`, `UnmergeStale`, `Unmerged`, `UnmergeInterrupted`,
`merge_history`, `preview_unmerge`, `apply_unmerge`) and the value `"unmerge"`
added to `WriteFailed.operation`. No existing union, alias or signature changes.

#### Scenario: The unmerge surface diff is additive

- GIVEN the snapshot recorded at the merge base with `origin/main`
- WHEN the snapshot is regenerated after the change
- THEN every difference is classified additive and `API_VERSION` is still 1

#### Scenario: An interrupted chain is caught as a write failure

- GIVEN an unwind whose second step fails
- WHEN it is applied inside `except WriteFailed`
- THEN the handler runs with an `UnmergeInterrupted` naming the restored id of
  the first step

### Requirement: A Page Is Readable As A Curated Field Set

`openkos.api` MUST export `read_page(workspace, concept_id)`, returning a
`Page` or `None`. A `Page` MUST carry exactly `concept_id` (the canonical
Concept ID), `title` (the frontmatter title with whitespace collapsed),
`okf_type` (the frontmatter `type` when it is a string, else `None`),
`excerpt`, `sensitivity` and `sources`, and no other stored field: it is a
curated field set, never a frontmatter passthrough.

- `excerpt` MUST be the opening of the body as plain text: a leading level-one
  heading equal to the title is dropped; a markdown link keeps its label and
  loses its target; citation markers such as `[1]`, emphasis marks and heading
  marks are dropped; runs of whitespace become one space; and text longer than
  280 characters is cut at the last word boundary before that length and ends
  in `…`.
- `sensitivity` MUST be one of `public`, `private`, `confidential`, normalised
  with the engine's fail-closed rank: a missing or blank value is `private` and
  an unrecognised one is `confidential`.
- `sources` MUST be a tuple of `PageSource(source_id, file_name, added_at)`,
  one per provenance entry, in provenance order, whose document is a readable
  `Source` with a `raw/` resource: `file_name` is that resource's base name and
  `added_at` the Source's `generated.at` as recorded, or `None` when absent.
  Entries that are not such a Source are omitted.

The read MUST return `None` for an id that is not a valid Concept ID, a page
that does not exist, and a page whose file cannot be read or whose frontmatter
cannot be parsed. It MUST NOT take the workspace lock, build a model client or
write anything, and it applies no disclosure gate (the surface is owner-local).

#### Scenario: A page is read with its sources

- GIVEN a Person page whose provenance names one Source of `raw/notes.md`,
  added at `2026-10-12T09:00:00Z`
- WHEN `read_page` is called for it
- THEN it returns its title, `okf_type` `Person`, its plain opening and one
  source with `file_name` `notes.md` and that `added_at`
- AND the workspace is unchanged

#### Scenario: The excerpt is plain and bounded

- GIVEN a page whose body opens with its title as a heading, a link
  `[Stoicism](/concepts/stoicism.md)`, a citation marker `[1]` and 400
  characters of text
- WHEN `read_page` is called
- THEN the excerpt starts after the heading, contains `Stoicism` but not the
  link target or the marker, is at most 281 characters and ends in `…`

#### Scenario: An unrecognised sensitivity fails closed

- GIVEN a page whose `sensitivity` is `secret`, and one with none
- WHEN `read_page` is called for each
- THEN the first reads `confidential` and the second `private`

#### Scenario: An invalid, missing or unreadable page is None

- GIVEN an id with a `..` segment, an id with no file, and a page whose
  frontmatter does not parse
- WHEN `read_page` is called for each
- THEN each returns `None` and nothing is written

### Requirement: The Page Read Extends v1 Additively

The page read MUST be added within `API_VERSION` 1, and every change to the
public surface MUST be classified additive by the surface classifier, so the
golden snapshot is regenerated and `API_VERSION` is not raised: new names
(`Page`, `PageSource`, `read_page`). No existing union, alias or signature
changes.

#### Scenario: The page-read surface diff is additive

- GIVEN the snapshot recorded at the merge base with `origin/main`
- WHEN the snapshot is regenerated after the change
- THEN every difference is classified additive and `API_VERSION` is still 1

### Requirement: Every Combined Page Is Listable

`openkos.api` MUST export `merged_concepts(workspace)`, returning a tuple of
`MergedConcept`, one per page whose committed merge ledger records at least one
merge and whose concept file exists. A `MergedConcept` MUST carry `concept_id`
and `merges`, the tuple `merge_history` returns for that page (newest first).
The pages MUST be ordered by their newest merge, newest first, then by Concept
ID. A ledger whose page no longer exists MUST be left out, and a ledger that
cannot be read MUST be skipped rather than fail the list. The read MUST NOT take
the workspace lock, build a model client or write anything.

#### Scenario: Two combined pages are listed newest combine first

- GIVEN page `a` absorbed `b`, and later page `c` absorbed `d`
- WHEN `merged_concepts` is called
- THEN it returns `c` then `a`, each with the records `merge_history` returns
  for it, and the workspace is unchanged

#### Scenario: A page absorbed after its own combine is left out

- GIVEN page `m` absorbed `l`, and then page `t` absorbed `m`
- WHEN `merged_concepts` is called
- THEN it lists `t` only

#### Scenario: A workspace with no combine lists nothing

- GIVEN a workspace where nothing was ever merged
- WHEN `merged_concepts` is called
- THEN it returns an empty tuple

### Requirement: A Kept-Separate Ruling Is Listed And Reopened Against Its Version

`openkos.api` MUST export `kept_distinct(workspace)`, returning a tuple of
`KeptDistinct` (`member_ids`, sorted, and `decided_at`), one per identity ruling
whose state is declined, newest first; it MUST NOT take the lock, build a client
or write.

`openkos.api` MUST export `preview_reopen(workspace, member_ids)` and
`apply_reopen(workspace, version, *, wait_seconds=0.0, on_warning=None)`. The
preview MUST return a `ReopenPreview` carrying the sorted members and a
`ReopenVersion`, whose `content_digest` covers the member set and the bytes of
the sidecar holding the ruling, or `NotUndoable("not_kept_distinct")` when no
declined ruling over exactly that member set exists (an id that is not a valid
Concept ID, or fewer than two distinct members, included). The preview MUST NOT
take the lock, build a client or write.

The apply MUST take the workspace lock once, waiting at most `wait_seconds`
(`WorkspaceBusyError` beyond it, nothing written), re-plan from current bytes,
return `NotUndoable` when the re-plan refuses and `UndoStale` when the digest
differs, and otherwise rewrite the ruling with state `open`, commit only the
sidecar it wrote, and return `Reopened` (`member_ids`, `commit_sha`, `None`
when the commit could not be made, reported once through `on_warning`). It MUST
NOT resolve or propose a queue row.

#### Scenario: A keep-separate decision is reopened

- GIVEN a pair kept separate through `apply_decision`
- WHEN `preview_reopen` then `apply_reopen` are called with the pair
- THEN `kept_distinct` no longer lists it, the ruling's state is `open`, and the
  commit holds only the sidecar

#### Scenario: A pair never kept separate is refused

- GIVEN two pages with no ruling
- WHEN `preview_reopen` is called with them
- THEN it returns `NotUndoable("not_kept_distinct")` and nothing is written

#### Scenario: A ruling changed after the preview is stale

- GIVEN a reopen preview, and the same ruling reopened and kept separate again
  by the CLI afterwards
- WHEN `apply_reopen` is called with the preview's version
- THEN it returns `UndoStale` and nothing is written

### Requirement: A Typed Relation Is Removed Against Its Version

`openkos.api` MUST export `preview_unrelate(workspace, source_id,
relation_type, target_id)` and `apply_unrelate(workspace, version, *,
wait_seconds=0.0, on_warning=None)`. The preview MUST plan the removal of the
`{target, type}` entry from the source page's relations exactly as `unrelate`
does, and return an `UnrelatePreview` carrying the edge, whether the target's
deprecated-status export is withdrawn (`withdraws_deprecation`) and an
`UnrelateVersion`, whose `content_digest` covers the edge, the source page's
bytes, and the target page's bytes when the export is withdrawn.
It MUST return `NotUndoable("not_related")` when the source holds no such
relation, either page no longer exists, an id is not a valid Concept ID, the two
ids are the same page, or the type is blank (it is read as `unrelate`
reads it, surrounding spaces stripped). It MUST NOT take the
lock, build a client or write.

The apply MUST take the lock once as `apply_reopen` does, re-plan, return
`NotUndoable` or `UndoStale` as it does, and otherwise write the source page,
the target's withdrawal when planned and the `log.md` line, commit only those
paths, and return `Unrelated` (the edge, `commit_sha`,
`committed_confidential`). It MUST NOT resolve or propose a queue row.

#### Scenario: A connection made through a decision is removed

- GIVEN a relation written by applying a relation-type decision
- WHEN `preview_unrelate` then `apply_unrelate` are called with its edge
- THEN the source page no longer holds the relation, `log.md` gains one
  line, and the commit holds the source page and `log.md`

#### Scenario: A relation that is not there is refused

- GIVEN a page with no `references` relation to another
- WHEN `preview_unrelate` is called for that edge
- THEN it returns `NotUndoable("not_related")` and nothing is written

#### Scenario: A page edited after the preview is stale

- GIVEN an unrelate preview, and the source page edited afterwards
- WHEN `apply_unrelate` is called with the preview's version
- THEN it returns `UndoStale` and nothing is written

### Requirement: A Type's Tier Is Set Or Restored Against Its Version

`openkos.api` MUST export `VolatilityTier` (`"static"`, `"slow"`,
`"volatile"`), `preview_tier_change(workspace, type_name, tier)` and
`apply_tier_change(workspace, version, *, wait_seconds=0.0, on_warning=None)`.
With a tier the change sets the type's `type_tiers` entry as `set-volatility`
does; with `None` it removes the entry so the engine's default applies, leaving
every other line of `openkos.yaml` byte-identical. The preview MUST return a
`TierChangePreview` carrying the type, the requested tier, `configured_tier`
(the entry now, `None` when there is none) and a `TierChangeVersion`, whose
`content_digest` covers the type, the requested tier and the bytes of
`openkos.yaml`; `NotUndoable("unknown_type")` for a type the engine does not
know; `NotUndoable("tier_unchanged")` when the entry already is the requested
one (absent, for `None`). A tier outside `VolatilityTier` MUST raise
`ValueError`. The preview MUST NOT take the lock, build a client or write.

The apply MUST take the lock once, re-plan, return `NotUndoable` or `UndoStale`
as the other undos do, refuse with `Refused` when `openkos.yaml` carries an
uncommitted edit to the same entry (as a set-tier decision does), and otherwise
write `openkos.yaml`, commit only that change, and return `TierChanged` (the
type, the tier, `commit_sha`). It MUST NOT resolve or propose a queue row.

A set-tier decision's `TierPlan` MUST carry `configured_tier`, the type's entry
before the change (`None` when it had none), so a client can restore it.

#### Scenario: A tier set through a decision is restored by removing the entry

- GIVEN a type with no `type_tiers` entry, set to `volatile` through a decision
  whose preview reported `configured_tier` `None`
- WHEN `preview_tier_change(workspace, type, None)` then `apply_tier_change` are
  called
- THEN `openkos.yaml` has no entry for the type, so the engine's default
  applies again, and every other line is kept; a `type_tiers:` header the
  decision added stays

#### Scenario: A tier set over an earlier entry is restored byte for byte

- GIVEN a type whose entry is `slow`, set to `volatile` through a decision
  whose preview reported `configured_tier` `slow`
- WHEN `preview_tier_change(workspace, type, "slow")` then `apply_tier_change`
  are called
- THEN `openkos.yaml` is byte-identical to before the decision

#### Scenario: A tier that already holds is refused

- GIVEN a type whose entry is `slow`
- WHEN `preview_tier_change` is called with `slow`
- THEN it returns `NotUndoable("tier_unchanged")` and nothing is written

#### Scenario: A configuration changed after the preview is stale

- GIVEN a tier-change preview, and `openkos.yaml` edited afterwards
- WHEN `apply_tier_change` is called with the preview's version
- THEN it returns `UndoStale` and nothing is written

### Requirement: Undo And The Combined-Pages Read Extend v1 Additively

Every name this change adds (`merged_concepts`, `MergedConcept`,
`kept_distinct`, `KeptDistinct`, `preview_reopen`, `apply_reopen`,
`ReopenPreview`, `ReopenVersion`, `Reopened`, `preview_unrelate`,
`apply_unrelate`, `UnrelatePreview`, `UnrelateVersion`, `Unrelated`,
`preview_tier_change`, `apply_tier_change`, `TierChangePreview`,
`TierChangeVersion`, `TierChanged`, `VolatilityTier`, `NotUndoable`,
`UndoStale`) and the field `TierPlan.configured_tier` MUST be classified
additive by the surface classifier, and `API_VERSION` MUST stay 1.

#### Scenario: The undo surface diff is additive

- GIVEN the regenerated `api_surface.json`
- WHEN the classifier compares it with the snapshot on `main`
- THEN every change is additive and `API_VERSION` is 1

### Requirement: Setup Readiness Is A Model-Free Read

`openkos.api.setup_readiness(workspace)` MUST return a `SetupReadiness` with:

- `runtime`: the configured `backend` (`ollama` or `openai-compatible`), the
  runtime's `host` as the userinfo-redacted display host, `is_local`,
  `reachable`, `version` (the runtime's version string on `ollama` when it
  answers, otherwise null) and `download_url` (the official Ollama download page
  on `ollama`, otherwise null).
- `models`: one entry per distinct configured tag, in the order chat model,
  embedding model, then task models by task name, where the task models are the
  per-task models that differ from the chat model, enumerated by the same
  function `doctor` uses. Each entry carries `tag`, `roles` (`chat`,
  `embedding` or the task names), `required` (true for the chat and embedding
  models only), `installed` (null when the runtime did not answer) and
  `pullable` (true on `ollama` only).
- `memory`: `total_bytes` (null where it cannot be read), `floor_bytes`
  (32 GiB, ADR-0047) and `meets_floor` (null when `total_bytes` is null).
- `ready`: true exactly when the runtime answered and every required model is
  installed.

It MUST NOT call a model, write anything or take the workspace lock. On
`ollama` it MUST probe `GET /api/version` and, only when that answers, list the
installed models; on `openai-compatible` reachability MUST be the model listing.
An unreachable runtime MUST be reported as `reachable: false`, never raised. A
configuration that cannot be read MUST raise `WorkspaceUnreadable`.

#### Scenario: A ready machine

- GIVEN a workspace on `ollama` whose runtime answers version `0.32.9` and lists
  the chat and embedding models
- WHEN `setup_readiness` is called
- THEN `runtime.reachable` is true, `runtime.version` is `0.32.9` and `ready` is
  true

#### Scenario: No runtime is running

- GIVEN a workspace on `ollama` whose host refuses connections
- WHEN `setup_readiness` is called
- THEN `runtime.reachable` is false, every model's `installed` is null, `ready`
  is false, `download_url` names the Ollama download, and nothing was raised

#### Scenario: A missing embedding model blocks readiness

- GIVEN a reachable runtime that lists the chat model only
- WHEN `setup_readiness` is called
- THEN the embedding model's entry is `installed: false`, `required: true`, and
  `ready` is false

#### Scenario: A missing judge model does not block readiness

- GIVEN a reachable runtime with the chat and embedding models but not the judge
  model
- WHEN `setup_readiness` is called
- THEN the judge model is listed with roles `adjudication` and `contradiction`,
  `required: false`, `installed: false`, and `ready` is true

#### Scenario: Memory below the floor is reported, not enforced

- GIVEN a machine reporting 16 GiB of memory
- WHEN `setup_readiness` is called
- THEN `memory.meets_floor` is false and the call still succeeds

### Requirement: A Configured Model Is Pulled With Progress And A Cooperative Stop

`openkos.api.pull_model(workspace, model, *, stop=None, observer=None)` MUST
pull `model` through the configured runtime and return a `PullOutcome` with
`model`, `status` (`completed`, `already_installed` or `stopped`),
`completed_bytes` and `total_bytes`, or a returned `PullRefused` with `model`
and `reason`:

- `not_configured` when `model` is not one of the tags `setup_readiness` lists,
  in which case nothing is sent to the runtime;
- `backend_cannot_pull` when the backend is not `ollama`.

A model the runtime already lists MUST return `already_installed` without a
pull. Otherwise the service MUST check `stop` once before the stream starts and
after every progress line, and on a stop MUST close the stream and return
`stopped`. The observer's `progress` MUST receive `PullProgress` events with
`model`, `phase` (`preparing`, `downloading`, `verifying` or `finishing`),
`completed_bytes` and `total_bytes` summed over the layers reported so far; the
runtime's own status text MUST NOT be forwarded. An unreachable runtime MUST
raise `BackendUnavailable`; a runtime that reports the model does not exist
MUST raise `BackendModelNotFound`; any other error line MUST raise
`BackendError`. The pull MUST NOT write under the workspace or take its lock.

#### Scenario: A pull reports progress and completes

- GIVEN a configured, uninstalled model and a runtime that streams two layers
  then `success`
- WHEN `pull_model` is called with an observer
- THEN the observer saw non-decreasing `completed_bytes` and the outcome is
  `completed`

#### Scenario: A stop between lines ends the pull

- GIVEN a pull in progress
- WHEN the stop token is set
- THEN the stream is closed after the current line and the outcome is `stopped`

#### Scenario: An unconfigured model is refused before any request

- GIVEN a model tag the workspace does not configure
- WHEN `pull_model` is called
- THEN it returns `PullRefused(reason="not_configured")` and the runtime
  received no request

#### Scenario: An installed model is not pulled again

- GIVEN the runtime already lists the model
- WHEN `pull_model` is called
- THEN it returns `already_installed` and no pull request was made

### Requirement: Setup Extends v1 Additively

`setup_readiness`, `pull_model`, `SetupReadiness`, `RuntimeReadiness`,
`ModelReadiness`, `MemoryReadiness`, `PullProgress`, `PullObserver`,
`PullOutcome` and `PullRefused` MUST be added to `__all__` and to the golden
snapshot without raising `API_VERSION`, and the surface classifier MUST classify
the change as additive.

#### Scenario: The classifier accepts the additions

- GIVEN the surface on the base branch
- WHEN the setup names are added
- THEN the classifier reports an additive change and `API_VERSION` is still 1

### Requirement: A Question Is Asked Under The Query Disclosure Rule

`ask(workspace, question, *, limit=8, include_deprecated=False,
include_confidential=False, progress=None)` MUST answer `question` through the
query application service the CLI's `query` uses, with the chat and embedding
clients built from the configuration read afresh by the API's own composition
root, and the local exemption resolved by the shared resolver on the built chat
client. It MUST write nothing, take no lock and create no derived store.

It MUST return an `Answer` whose `outcome` is `answered`, `no_match` (every
no-match cause except the sufficiency refusal), `no_answer_in_context` (the
sufficiency refusal) or `withheld`, built by the same rule `query --json` and
the MCP `query` tool apply, through `application.query_report`, with the
disclosable set taken after the query ran:

- Without `include_confidential`, no confidential concept is named, titled or
  quoted anywhere in the `Answer`; it is counted in `withheld` only.
- An answer whose prompt held a concept outside the disclosable set, or one of
  whose citations was withheld, has `outcome` `withheld` and `answer` `None`.
- `answer` is the text only when `outcome` is `answered`.

Without `include_confidential`, a confidential concept MUST reach the chat
model only when the shared resolver grants the local exemption (a verifiably
local host and the workspace's exemption on). With it, confidential concepts
enter retrieval and MAY be named, each citation marking `confidential`.
`include_deprecated` includes deprecated and superseded concepts as on the CLI.

`limit` below 1, or a `bool`, MUST raise `ValueError` before any client is
built. An unreadable or invalid configuration MUST raise `WorkspaceUnreadable`.
The neutral backend errors (`BackendUnavailable`, `BackendModelNotFound`,
`BackendEmbeddingDimensionMismatch`, `BackendGenerationCapped`, `BackendError`)
MUST propagate unchanged. `progress`, when given, MUST be called with an
`AskProgress(phase, completed, total)` for each answer phase in order.

The `Answer` MUST also report the no-match cause, the documents the context
budget left out (`omitted`, filtered like citations), the attribution status,
whether the sufficiency check ran and whether it could not run, whether the
lexical or dense retriever was unavailable, and the stale derived stores the
query reads.

#### Scenario: A grounded answer cites its concepts

- GIVEN a workspace whose index matches a public concept and a model that
  answers citing it
- WHEN `ask` is called
- THEN `outcome` is `answered`, `answer` is the text and the citation names the
  concept, and the workspace bytes are unchanged

#### Scenario: A remote model never receives a confidential concept without consent

- GIVEN a confidential and a public concept matching the question and a chat
  backend that is not local
- WHEN `ask` is called without `include_confidential`
- THEN the confidential body is absent from every prompt sent and the
  confidential concept is counted in `withheld`, never named

#### Scenario: A local model's answer over a confidential concept is withheld

- GIVEN the same concepts and a local chat backend with the exemption on
- WHEN `ask` is called without `include_confidential`
- THEN the confidential body reaches the prompt, `outcome` is `withheld`,
  `answer` is `None`, and no field names the confidential concept

#### Scenario: Consent names a confidential citation

- GIVEN the same concepts
- WHEN `ask` is called with `include_confidential=True`
- THEN the answer is returned and the confidential concept's citation has
  `confidential` true

#### Scenario: An empty question is a no-match, not an error

- GIVEN any workspace
- WHEN `ask` is called with a blank question
- THEN `outcome` is `no_match` with cause `empty_query` and no model is called

#### Scenario: A sufficiency refusal is its own outcome

- GIVEN the sufficiency check is on and the model replies that the context
  does not answer
- WHEN `ask` is called
- THEN `outcome` is `no_answer_in_context` and `answer` is `None`

#### Scenario: An invalid limit is refused before any client

- GIVEN a factory that fails the test when a client is built
- WHEN `ask` is called with `limit=0`
- THEN `ValueError` is raised

#### Scenario: An unreachable backend is the neutral error

- GIVEN a chat backend that cannot be reached
- WHEN `ask` is called
- THEN `BackendUnavailable` is raised

#### Scenario: Progress reports the phases in order

- GIVEN the sufficiency check is on
- WHEN `ask` is called with a progress callback
- THEN it hears `retrieving`, `assembling`, `checking` and `synthesizing` with
  a total of 4

### Requirement: Ask Extends v1 Additively

`ask`, `Answer`, `AnswerCitation`, `OmittedConcept`, `AskProgress` and
`BackendEmbeddingDimensionMismatch` MUST be exported, and the committed surface
snapshot MUST be regenerated so the classifier reports only additive changes;
`API_VERSION` MUST stay 1.

#### Scenario: The snapshot change is additive

- GIVEN the snapshot from `main` before this change
- WHEN the classifier compares it with the live surface
- THEN every change it prints is additive and `API_VERSION` is 1
