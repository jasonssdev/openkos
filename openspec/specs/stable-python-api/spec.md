# Stable Python API Specification

## Purpose

The one declared, versioned, in-process Python surface of OpenKOS (ADR-0039).
`openkos.api` is a facade over the synchronous `application/` services; no
`application/` module is public in place, and none moves. The v1 operation
set is the review queue only: workspace status, the pending report, listing,
previewing and applying open decisions, judging quote and run, the
model-free queue refresh, and reading the pages a decision shows. Ingest, query, doctor and guided setup are not part
of v1.

The surface is owner-local, as the CLI is: its caller is the workspace owner.
It applies no sensitivity disclosure gate; that gate stays the job of the
local API (ADR-0058).

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

### Requirement: The Surface Is Owner-Local And Applies No Disclosure Gate

The Python API MUST NOT apply the sensitivity disclosure gate to any result,
and its documentation (the package docstring) MUST state that it is an
owner-local, in-process surface whose caller is the owner, and that a
disclosing adapter MUST apply the local API's gate itself (ADR-0058). Results
MAY therefore carry stored content. The API MUST NOT send content to any model
backend other than the configured one, and only through the judging run. The
only other process it invokes is the local git auto-commit for a decision it
applies, which never pushes. The docstring MUST also state that cancellation is
cooperative (a stop waits for the chunk in flight), that v1 apply does not
reconcile merged bodies and does not refresh the derived stores, and that
`API_VERSION`, not the package version, is the compatibility promise.

#### Scenario: The docstring states the boundary

- GIVEN `openkos.api` is imported
- WHEN its `__doc__` is read
- THEN it states that the surface is owner-local and applies no disclosure gate

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
suppressed and retired, and whether the pass was complete for that kind; the
volatility entry additionally counts the types skipped because the two
confidentiality regimes disagreed, and the result carries the identity
truncation notice when the identity report was truncated. Its behavior is
specified under the `pending-work` capability ("The Decision Queue Can Be
Refreshed Without A Model"); this capability requires only that the API exposes
it, that it builds no backend client, that it takes the lock only inside commit
sections (waiting at most the caller's `wait_seconds`, default zero, and raising
the existing workspace-busy error beyond it), and that it returns the per-kind
result. Only the identity entry can report a retirement or a complete pass; the
other two always report `complete` false. A workspace that cannot be read raises
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
