# Lifecycle Application Service Specification

## Purpose

The concept-lifecycle bounded context's application service composes the
orchestration around five already-mutating verbs — `merge`, `unmerge`,
`forget`, `purge`, and `adjudicate --apply`/`--apply-same` — into callables
usable by any adapter without importing from `openkos.cli`. Each verb's
Phase A (validate/preview), confirmation gate, and Phase B (write) shape is
staged as typed data; workspace layout, configuration, and any needed
`LLMBackend` arrive as parameters, so the layer binds no concrete backend
and performs no interactive I/O. It is the third and final artifact in the
`application/` layer (ADR-0018), following the shipped `application/query.py`
and `application/ingest.py`, and it is where the headless-consent protocol's
typed data contract lands.

The module also carries `relate`'s and `set_volatility_cmd`'s pure
`prepare_*`/`*_core` pairs (issue #959, following the shape `prepare_merge`/
`merge_core` set), each staging its confirm-gate wording as a
`BooleanConfirmation` the same way `PreparedMerge` does. Their presence here
does not widen "five" above: neither `relate` nor `set_volatility_cmd`
itself is composed into a service-owned workflow the way `merge`'s
reconciliation pass or `adjudicate --apply-same`'s batch preview are — each
adapter still drives its own confirm gate, preview echo, and `_autocommit`
sequencing directly against the relocated pair, just as it did before the
move.

## Non-Goals

Interactive confirmation, TTY detection, `sys.stdin.isatty()`; stdout/stderr
rendering; process exit-code selection; the shared write mechanics
(`_reject_drifted_targets`, `_autocommit`, `_refresh_derived_after_write`,
`_echo_commit_disclosure`), which the service calls through rather than owns;
LLM/`git-filter-repo` backend construction; `relate`'s and
`set_volatility_cmd`'s own CLI-level orchestration (their confirm-gate
driving, preview echoing, and `_autocommit` sequencing stay in
`cli/main.py`/`cli/curate.py` — only their pure `prepare_*`/`*_core` pairs
moved here, issue #959, not composition into a bigger verb the way the five
in Purpose are); `reconcile`; `curate`; the `api`/`mcp` adapters themselves;
the headless-consent protocol's wire/transport shape (how a non-TTY caller
supplies a pre-recorded answer); any change to on-disk formats, ledger
semantics, or observable CLI wording.

## Requirements

### Requirement: Non-CLI Callable Lifecycle Composition

The service MUST expose synchronous callables composing Phase A/confirm/Phase
B for `merge`, `unmerge`, `forget`, `purge`, and `adjudicate --apply`/
`--apply-same`, importable and callable by code that imports nothing from
`openkos.cli`. Each callable MUST receive workspace layout, configuration,
and any needed `LLMBackend` as parameters rather than constructing them.
`src/openkos/application/lifecycle.py` MUST reference none of `typer`,
`rich`, `openkos.cli`, and MUST NOT call `sys.stdin.isatty()`.

#### Scenario: A non-CLI caller prepares a merge

- GIVEN a module that imports nothing from `openkos.cli`
- WHEN it imports and calls the lifecycle application service's merge
  composition with a workspace layout and two concept-ids
- THEN it receives a result and no import of `openkos.cli` is triggered

#### Scenario: The module is free of CLI and interactive-I/O references

- GIVEN `src/openkos/application/lifecycle.py`
- WHEN it is scanned by `tests/unit/application/test_layering.py`
- THEN it references none of `typer`, `rich`, `openkos.cli`, and calls
  `sys.stdin.isatty()` nowhere in its source

### Requirement: ConfirmationRequest Is A Tagged Union Of Boolean And Typed-Challenge Variants

`ConfirmationRequest` MUST be a tagged union with at least one boolean
variant (prompt text, bypass flag, non-TTY refusal wording) and one
typed-challenge variant carrying the `expected` string a response MUST equal.
A typed-challenge response MUST be compared for exact equality against
`expected`; a merely-truthy response MUST NOT satisfy it.

The typed-challenge variant MUST cover BOTH typed gates in the codebase, which
differ in comparison policy: `purge`'s confirmation phrase compares the raw
response (`main.py:7331`), while `adjudicate --apply-same`'s eligible count
compares `response.strip()` (`main.py:3057`). That policy MUST be carried as a
field on the request, not re-derived at each call site.

The boolean variant's bypass flag MUST be nullable. `purge` has NO boolean
variant and MUST NOT be given one: its gate is a typed phrase with no `--auto`
bypass, because the operation is irreversible (`main.py:7316`). Representing
`purge` as a boolean confirmation would invent a bypass the CLI does not offer.

#### Scenario: A boolean confirmation is granted

- GIVEN a `merge` preview requiring a boolean confirmation
- WHEN the adapter supplies `granted=True`
- THEN the service proceeds to Phase B

#### Scenario: A typed-count response must match exactly

- GIVEN an `adjudicate --apply-same` batch whose `expected` is `"5"`
- WHEN the adapter supplies a response of `"4"` or a non-numeric truthy value
- THEN the request reports no match, distinguishing this from a granted
  boolean confirmation

#### Scenario: The two typed gates do not share a comparison policy

- GIVEN a `purge` request built from the same text as an `adjudicate
  --apply-same` request, each carrying its own comparison policy
- WHEN a response with surrounding whitespace is supplied to both
- THEN the `adjudicate --apply-same` request matches and the `purge` request
  does not, reproducing `main.py:3057` and `main.py:7331` exactly

#### Scenario: Purge is never representable as a bypassable boolean gate

- GIVEN the `purge` confirmation request
- WHEN the adapter inspects its variant
- THEN it is the typed-challenge variant, its supplying flag is
  `--confirm-phrase`, and no `--auto` bypass is representable on it

### Requirement: Hard Refusal Gates Are Not Representable As Confirmations

`forget`'s surviving-references gate (bypassed only by `--force`) and
`purge`'s per-target refusals MUST NOT be members of the
`ConfirmationRequest` union or satisfiable by any confirmation response.

#### Scenario: A surviving-references refusal cannot be granted

- GIVEN a `forget` target with surviving inbound references and no `--force`
- WHEN the adapter inspects the staged result
- THEN the refusal is a distinct, non-confirmation outcome that no
  `ConfirmationRequest` response can satisfy

### Requirement: Unmerge Has A Full Public Prepare/Core Pair Matching Merge

The service MUST expose a public `prepare_unmerge`/`unmerge_core` pair with
the same Phase A/Phase B shape as `prepare_merge`/`merge_core`, covering both
the preview and the write for `unmerge`.

#### Scenario: Unmerge preview is computed without writing

- GIVEN a previously merged pair of concept-ids
- WHEN the unmerge preparation callable runs
- THEN it returns a preview and performs no write until a separate write
  callable is invoked

### Requirement: Purge's Disclosure Renders From Returned Templates Byte-For-Byte

The service MUST return the "IRREVERSIBLE history rewrite" disclosure as
string template data; the adapter MUST render it unchanged, with no
rephrasing and no new wording.

#### Scenario: The disclosure text is unchanged after extraction

- GIVEN any scenario `tests/unit/cli/test_purge.py` covered before this
  change
- WHEN the same `purge` invocation runs after the extraction
- THEN the IRREVERSIBLE history-rewrite disclosure text is byte-identical

### Requirement: Shared Write Mechanics Stay Adapter-Side, Each With One Definition

The service MUST NOT hold a second definition of `_reject_drifted_targets`,
`_autocommit`, `_refresh_derived_after_write`, or `_echo_commit_disclosure`;
the adapter constructs any backend and calls the shared write helpers
unchanged.

#### Scenario: Committing a staged plan uses the existing shared helpers

- GIVEN a staged plan produced by the lifecycle service — for any of the
  five verbs it composes, or for either pure pair it merely holds (issue
  #959)
- WHEN a caller commits it
- THEN the same shared write helpers used by every other write-capable
  command run, with no duplicate implementation inside the service

### Requirement: Adapter Owns Interaction, Presentation, And Exit Codes

The service MUST NOT perform interactive confirmation, TTY detection,
stdout/stderr rendering, or process exit-code selection for ANY code it
holds — the five verbs it composes and `relate`'s and `set_volatility_cmd`'s
relocated pure pairs alike (issue #959); those stay with the calling
adapter.

This is the invariant the relocation exists for, so it is scoped to what
the module HOLDS rather than to what it composes: an `api`/`mcp` adapter
must be able to drive any pair in here without importing `openkos.cli`,
`typer`, or `rich`.

#### Scenario: The CLI still owns the non-TTY refusal

- GIVEN a mutating verb invoked without `--auto` on a non-TTY stdin with
  `cfg.review` true
- WHEN the command runs
- THEN the TTY detection and the refusal's exit code are decided in the CLI
  adapter, not inside the service

### Requirement: The Extraction Preserves Observable CLI Behavior

For every input covered by the existing CLI/unit test suites for `merge`,
`unmerge`, `forget`, `purge`, `adjudicate --apply`/`--apply-same`, and —
since issue #959 relocated their pairs — `relate` and `set-volatility`, each
command MUST produce the same exit code, stdout, and stderr — including the
non-TTY refusal path — after the extraction as before it.

#### Scenario: A previously-passing CLI scenario is unchanged

- GIVEN any scenario the existing test suites covered before this change,
  across every verb named above
- WHEN the same CLI invocation runs after the extraction
- THEN its exit code, stdout, and stderr are unchanged
