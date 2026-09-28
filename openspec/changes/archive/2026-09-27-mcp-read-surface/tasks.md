# Tasks: mcp-read-surface — a read-only MCP server that never discloses what it must not

Closes #1009, #1010. Design: `design.md` (FINAL, including "Orchestrator
verification" and its ten-slice `Migration / Rollout` plan). Proposal:
`proposal.md` (including "Human confirmation (2026-09-26)", which answers
Product decision P1: a non-confidential note's text is shown as written,
even when it mentions a confidential one by name). Delta specs:
`specs/mcp/spec.md`, `specs/sensitivity-aware-llm/spec.md`,
`specs/next-action-pointer/spec.md`, `specs/query-answer/spec.md`,
`specs/query-application-service/spec.md`. ADR-0027
(`docs/adr/0027-hand-rolled-stdio-mcp-server.md`) and ADR-0028
(`docs/adr/0028-mcp-disclosure-is-its-own-boundary.md`) are already written,
status `Proposed`, with their `docs/adr/README.md` index rows already
present in the worktree — no task below writes their bodies; each is staged
into the slice design assigns it to (ADR-0028 → slice 1, ADR-0027 → slice
2). ADR-0020 and ADR-0021 become Accepted at archive, per
`openspec/config.yaml`'s archive-only ADR-acceptance rule — not addressed by
any task here.

Strict TDD is ON, runner `uv run pytest`. Every behavioral task pairs a
`[TEST]` task, observed RED with the reason it is RED today and the mutation
it must kill, with the `[IMPL]` task that turns it GREEN — in that order. A
task marked `[IMPL] N/A` means the behavior is expected to already be GREEN
by construction from an earlier task; if it is RED instead, that is a design
violation to fix in the earlier task, not new production code to add here.
A task marked `[CHECK]` records a regression baseline, not a RED/GREEN pair.
A task marked `[GOLDEN]` is written first and confirmed GREEN on `main`
before any change lands, per design's Testing Strategy preamble. Revert
every mutation with the inverse edit (never `git checkout --`), and purge
`__pycache__` before trusting a verdict, per this project's "a test that
passes first try" and "mutation-pycache-invalidates-verdicts" practice.
Coverage stays at or above 90% (`uv run pytest --cov`).

**Delivery.** `delivery_strategy: auto-chain`, `chain_strategy:
stacked-to-main`. Ten slices, each its own PR, each green alone: PR 1
targets `main`; PR 2 targets PR 1's branch; PR 3 targets PR 2's branch; and
so on through PR 10 targeting PR 9's branch. Once each PR merges in order,
GitHub retargets the next PR onto `main` automatically. Slices 3 and 9 sit
at the design's own budget estimate; if either grows past it during
implementation, apply the fallback split named in that slice's section
below rather than exceeding budget.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~3,390 (design.md "Migration / Rollout" per-slice estimates below; delta specs excluded) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (predicate + ADR-0028) → PR 2 (transport + ADR-0027) → PR 3 (server core + tools skeleton) → PR 4 (verb + gate skeleton + canary guard) → PR 5 (get) → PR 6 (navigate) → PR 7 (pending) → PR 8 (backends + progress prep) → PR 9 (query) → PR 10 (docs) |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

Per-slice estimate (design.md "Migration / Rollout"):

| Slice | Content | Authored lines (est.) |
| --- | --- | --- |
| 1 | Disclosure predicate and set sibling, their tests, ADR-0028 staged | ~270 |
| 2 | `mcp/__init__.py`, `mcp/transport.py`, transport tests incl. fd-hygiene subprocess test, ADR-0027 staged | ~360 |
| 3 | `mcp/server.py` core (lifecycle, dispatch, protocol errors, generic `-32603`, `run_in_worker`), `mcp/tools.py` skeleton, tests with test-only tools | ~400 |
| 4 | `openkos mcp` verb, `_READ_ONLY_COMMANDS`, `serve()`, `mcp/gate.py` skeleton, the canary fixture and guard with `leaky_probe`, layering tests, the subprocess tier | ~390 |
| 5 | `concept_read.read_concept`, `consistency.py`, `gate.disclose_get` and warnings in `finish`, the `get` tool, `concept_not_found`/`read_failed` mapping, guard row | ~400 |
| 6 | `concept_read.concept_neighbors`, `gate.disclose_navigate`, the `navigate` tool, guard row | ~280 |
| 7 | `next` golden first; `NextAction.subjects`, `declination_subjects`, `record_declination(subjects=)`, `LintFinding.related_ids`; `gate.disclose_pending`, the `pending` tool, guard row | ~380 |
| 8 | `application/backends.py` and the CLI delegators; `answer`/`run_query` `progress`; `AnswerResult` id lists | ~360 |
| 9 | The `query` tool, `gate.disclose_query`, the Ollama/FTS error rows, cancellation, progress notifications, `stale_index`, guard row, subprocess `ollama_unavailable` | ~400 |
| 10 | `docs/cli.md`, `docs/architecture.md`, `docs/roadmap.md` | ~150 |

Total ≈ 3,390, well over the ~400 single-PR budget. Each slice is useful and
green on its own, exactly as design's "Approach" states: the predicate lands
first as a pure leaf; transport and lifecycle land with an empty registry;
the verb and the enumeration guard (with its violating tool) land before
any real tool, so every tool that follows is admitted through the guard;
`get`/`navigate`/`pending` follow in that order; `query` needs the backend
move and the progress callback first; docs close the chain.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Disclosure predicate (`sensitivity.py`) and its allowed-set sibling | PR 1 → `main` | `uv run pytest tests/unit/test_sensitivity.py` | N/A — pure leaf, no caller yet | Revert `sensitivity.py`'s two new functions and their tests; ADR-0028 stays (staged as-is) |
| 2 | Stdio transport and framing (`mcp/transport.py`, `mcp/__init__.py`) | PR 2 → PR 1's branch | `uv run pytest tests/unit/mcp/test_transport.py tests/unit/mcp/test_stdio_subprocess.py -k transport` | `uv run python -m openkos.mcp.transport` smoke via the subprocess test's own harness (no CLI verb exists yet) | Revert `src/openkos/mcp/` entirely; nothing outside it references transport yet |
| 3 | Server lifecycle, dispatch, protocol errors, and the tool-registry skeleton (`mcp/server.py`, `mcp/tools.py`) | PR 3 → PR 2's branch | `uv run pytest tests/unit/mcp/test_server.py tests/unit/mcp/test_tools.py` | N/A — no CLI verb yet; exercised only through in-memory JSON-RPC frames against test-only tools | Revert `mcp/server.py` and `mcp/tools.py`'s skeleton; transport (PR 2) is unaffected |
| 4 | `openkos mcp` verb, `mcp/gate.py` skeleton, the canary fixture and enumeration guard | PR 4 → PR 3's branch | `uv run pytest tests/unit/cli/test_mcp_cmd.py tests/unit/mcp/test_layering.py tests/unit/mcp/test_enumeration_guard.py` | `uv run openkos mcp --workspace <fixture>` piped through the subprocess test's own `initialize`→`initialized`→`tools/list`→`ping`→close-stdin sequence | Revert the `mcp` verb from `cli/main.py`, `mcp/gate.py`, `tests/unit/mcp/canary.py`; the server core (PR 3) still serves an empty registry |
| 5 | The `get` tool end to end | PR 5 → PR 4's branch | `uv run pytest tests/unit/application/test_concept_read.py tests/unit/mcp/test_gate.py -k get` | `uv run openkos mcp` + a scripted `get` `tools/call` against a scratch workspace, confirming curated fields and a withheld confidential target | Revert `application/concept_read.py`'s `read_concept` half, `application/consistency.py`, `gate.disclose_get`, and the `get` registration; `query`/`navigate`/`pending` do not exist yet to be affected |
| 6 | The `navigate` tool end to end | PR 6 → PR 5's branch | `uv run pytest tests/unit/application/test_concept_read.py -k neighbors tests/unit/mcp/test_gate.py -k navigate` | `uv run openkos mcp` + a scripted `navigate` call confirming both edge directions | Revert `concept_read.concept_neighbors`, `gate.disclose_navigate`, and the `navigate` registration; `get` (PR 5) is unaffected |
| 7 | `pending` end to end, and the `openkos next` byte-identity golden | PR 7 → PR 6's branch | `uv run pytest tests/unit/cli/test_next_golden.py tests/unit/application/test_next_action.py tests/unit/mcp/test_gate.py -k pending` | `uv run openkos next` on a fixture bundle, confirming byte-identical stdout to `main`; `uv run openkos mcp` + a scripted `pending` call | Revert `NextAction.subjects`/`declination_subjects`, `LintFinding.related_ids`, `gate.disclose_pending`, and the `pending` registration; the golden pins the CLI is unaffected either way |
| 8 | Chat-client relocation (`application/backends.py`) and query preparation (`progress`, id lists) | PR 8 → PR 7's branch | `uv run pytest tests/unit/application/test_backends.py tests/unit/retrieval/test_answer.py tests/unit/application/test_query_service.py` | `uv run openkos query "<question>"` on a scratch workspace, confirming unchanged output (no MCP caller exists yet) | Revert `application/backends.py`, the `cli/main.py` delegators (reverting to the original inline bodies), and `answer()`/`run_query`'s `progress` kwarg and id lists; all are additive/behavior-preserving |
| 9 | The `query` tool, cancellation, and progress notifications | PR 9 → PR 8's branch | `uv run pytest tests/unit/mcp/test_gate.py -k query tests/unit/mcp/test_server.py -k "cancel or progress"` | `uv run openkos mcp` + a scripted `query` call with a `progressToken`, and a cancelled second call | Revert the `query` registration, `gate.disclose_query`, and `server.py`'s cancellation/progress wiring; `get`/`navigate`/`pending` (PRs 5–7) are unaffected |
| 10 | Docs (`docs/cli.md`, `docs/architecture.md`, `docs/roadmap.md`) | PR 10 → PR 9's branch | `uv run pytest tests/unit/cli/test_adr_index.py` (or wherever ADR-index/help-text checks live) | N/A — documentation only | Revert the three doc files; no behavior changes |

## Scenario → Task Coverage

Every scenario in the five delta specs mapped to at least one task below.
"Unaffected"/"confirmed by construction" rows name existing or structural
guarantees this change must not break, verified by the slice's own
`pytest`/AST gate rather than a dedicated new task.

| Spec | Scenario | Task(s) |
|---|---|---|
| mcp | initialize negotiates the supported revision | 3.1 |
| mcp | A different requested revision is answered with the supported one | 3.1 |
| mcp | A request before initialize is rejected | 3.1 |
| mcp | A batch request is rejected | 3.2 |
| mcp | ping receives a response | 3.1 |
| mcp | A subprocess run emits only JSON-RPC lines on stdout | 4.10 |
| mcp | An internal error still logs only to stderr | 3.3 |
| mcp | A valid workspace directory serves normally | 4.1, 4.2 |
| mcp | An invalid workspace directory refuses to serve | 4.1 |
| mcp | Each tool calls its underlying service unmodified | 5.12, 6.6, 7.10, 9.9 |
| mcp | A read tool never raises WorkspaceBusyError | confirmed by construction — 4.4 extended to assert no `mcp/` module imports the lock-acquisition helper |
| mcp | get's result names only the curated fields | 5.1 |
| mcp | A withheld id returns an empty object with a count, not content | 5.5 |
| mcp | An unreadable target under the opt-in is a success with a not_run entry | 5.3, 5.5 |
| mcp | The same unreadable target, opt-in off, is reported as withheld | 5.3, 5.5 |
| mcp | navigate returns typed and untyped neighbors | 6.1 |
| mcp | navigate includes inbound neighbors, not only outbound | 6.1 |
| mcp | A graph read that skipped edges reports a graph_build not_run entry | 6.2 |
| mcp | A withheld concept contributes only to the count | 5.5, 5.6, 6.2 |
| mcp | An edge survives only when both ends are disclosable | 6.2 |
| mcp | A title is scrubbed by its paired id, not by title text | 9.2 |
| mcp | A misaligned title/id pair drops every title in that list | 9.2 |
| mcp | A pending subject that is not fully disclosable withholds the whole item | 7.7 |
| mcp | Undeclared pending subjects withhold the action | 7.7 |
| mcp | A declared, subject-free tier is disclosed, not withheld | 7.7 |
| mcp | Misaligned declination subjects withhold every declination | 7.7 |
| mcp | The CLI is unaffected by the gate | 7.1 (golden), 8.7/8.9 (progress/id-list byte-identity) |
| mcp | A withheld object appearing in two channels is counted twice | 5.6 |
| mcp | Skip notices are reported separately from withheld | 5.7, 7.7, 9.2 |
| mcp | The flag defaults to off | 4.2 |
| mcp | No per-request override exists | 4.2, 3.4/3.5 (schema rejects any extra property) |
| mcp | The opt-in off sends both LLM-egress gates closed | 9.1 |
| mcp | The opt-in on still requires the local-exemption gate | 9.1 |
| mcp | A remote backend never receives confidential content via query | 9.1 |
| mcp | A withheld citation withholds the whole answer text | 9.2 |
| mcp | No withheld citation discloses the answer normally | 9.2 |
| mcp | A non-confidential note mentioning a confidential one is shown whole | confirmed by construction — 5.1/5.9 (the gate never touches body text, only structured channels) |
| mcp | An in-flight write produces a count-only warning on any tool | 5.4, 5.11 |
| mcp | A stale derived store warns only on query | 9.6 |
| mcp | get, navigate, and pending do not report stale_index | 9.6 |
| mcp | A check that cannot run is reported, never raised | 5.4, 5.7 |
| mcp | An unreachable Ollama server is reported as retryable | 9.3, 9.8 |
| mcp | A missing concept is reported as not retryable | 5.12, 9.3 |
| mcp | An unparseable frame yields -32700 | 3.2 |
| mcp | An unknown tool name yields -32602 | 3.2 |
| mcp | A malformed request envelope yields -32602 | 3.2 |
| mcp | An internal error never echoes exception text | 3.3 |
| mcp | Invalid arguments produce a tool execution error, not -32602 | 3.6 |
| mcp | The invalid-arguments message never echoes the value | 3.6 |
| mcp | not_run is present, even empty, on every result | 5.11 (default), extended by every tool's own gate test |
| mcp | An unreadable ancestor is reported as not_run, not an error | 5.7 |
| mcp | Document-labelled not_run entries are aggregated | 5.7 |
| mcp | An unrecognized label is aggregated, not forwarded | 5.7 |
| mcp | stale_index never produces a not_run entry | 5.4, 9.6 |
| mcp | A cancelled query sends no response | 9.4 |
| mcp | A cancelled request does not block later requests | 9.4 |
| mcp | initialize cannot be cancelled | 3.1 |
| mcp | Closing stdin abandons in-flight requests and exits 0 | 3.8, 4.10 |
| mcp | A KeyboardInterrupt exits distinctly | 3.8 |
| mcp | mcp is classified as a read-only command | 4.2 |
| mcp | Serving never waits on the workspace lock | confirmed by construction — 4.4 extended (see above) |
| mcp | A progressToken produces progress notifications | 9.5 |
| mcp | No progressToken means no progress notifications | 9.5 |
| mcp | get, navigate, and pending never emit progress | 9.5, confirmed by 5.12/6.6/7.10's registrations never setting `emits_progress=True` |
| mcp | The guard catches a deliberately violating tool | 4.5 |
| mcp | Every real tool passes the guard on every path | 4.5, extended by 5.8, 6.3, 7.8, 9.7 |
| mcp | openkos.mcp never imports openkos.cli | 4.4 |
| mcp | openkos.mcp never imports openkos.graph | 4.4 |
| mcp | Only gate.py imports the disclosure predicate module | 4.4 |
| mcp | application never imports openkos.mcp | 4.4 |
| mcp | The CLI imports mcp lazily, only inside the verb | 4.3, 4.4 |
| mcp | The definition takes the client class as an injected factory | 8.1 |
| mcp | The CLI keeps one-line delegators under their existing names | 8.3 |
| mcp | The CLI's observable behavior, and its test seam, are unaffected | 8.3, 8.4 |
| sensitivity-aware-llm | An explicit confidential value is withheld by default | 1.1 |
| sensitivity-aware-llm | A resolved-confidential value is disclosable once the opt-in is on | 1.1 |
| sensitivity-aware-llm | Private and public values are always disclosable | 1.1 |
| sensitivity-aware-llm | The predicate accepts no LLM-egress escape parameters | 1.2 |
| sensitivity-aware-llm | The bulk sibling applies the identical rank | 1.3 |
| sensitivity-aware-llm | An id with no walked document is withheld even under the opt-in | 1.4 |
| next-action-pointer | A tier-2 recommendation names its Source's concept id | 7.3 |
| next-action-pointer | A declination names its subject | 7.3 |
| next-action-pointer | A tier with no resolvable subject declares an explicit empty tuple | 7.3 |
| next-action-pointer | declination_subjects stays index-aligned with declinations | 7.3 |
| next-action-pointer | A below-source-sensitivity finding's subjects include its related Source | 7.3 |
| next-action-pointer | A multi-source-uncovered finding's subjects include every cited id | 7.3 |
| next-action-pointer | related_ids does not affect LintFinding equality | 7.4 |
| next-action-pointer | openkos next's stdout is byte-identical | 7.1 |
| query-answer | Omitting progress is byte-identical to today's contract | 8.7 |
| query-answer | A supplied callback observes the four phases in order | 8.7 |
| query-answer | Without the sufficiency check, checking is absent and total is 3 | 8.7 |
| query-answer | The empty-question short-circuit emits no progress | 8.7 |
| query-answer | progress is not read from configuration | 8.7 |
| query-answer | Each id list stays index-aligned with its title list | 8.9 |
| query-answer | An empty title list pairs with an empty id list | 8.9 |
| query-application-service | A non-CLI caller answers a question | confirmed by construction — pre-existing behavior, unaffected by 8.11 |
| query-application-service | No concrete backend is bound inside the service | confirmed by construction — pre-existing behavior, unaffected by 8.11 |
| query-application-service | Progress is threaded through unmodified | 8.8 |
| query-application-service | Omitting progress keeps composition byte-identical | 8.8 |

---

## Slice 1 (PR 1 → `main`): the disclosure predicate and ADR-0028

### `sensitivity.py` — the predicate and its allowed-set sibling (design Decision 1)

- [x] **1.1** [TEST] `tests/unit/test_sensitivity.py` — add
  `test_blocks_disclosure_matches_llm_rank`, parametrized over the corpus
  `None, "", "  ", "public", "private", "confidential", "Confidential",
  "secret", 1, [], {}, True`: with the opt-in off,
  `blocks_disclosure(v) == blocks_llm_send(v)` for every value; with the
  opt-in on, `blocks_disclosure(v, expose_confidential=True)` is always
  `False`. Covers sensitivity-aware-llm's "An explicit confidential value is
  withheld by default", "A resolved-confidential value is disclosable once
  the opt-in is on", and "Private and public values are always disclosable".
  **RED today**: `AttributeError` — `blocks_disclosure` does not exist.
  Kills delegating to a rank other than `okf._rank` (a blank value would
  then rank private instead of confidential), and dropping the flag
  short-circuit.
- [x] **1.2** [TEST] Same file — `test_blocks_disclosure_signature_has_no_llm_hatch`:
  the signature is exactly `(value, *, expose_confidential=False)` —
  keyword-only, defaulting `False`, with no `local_exemption` or
  `include_confidential` parameter. Covers "The predicate accepts no
  LLM-egress escape parameters". **RED today**: same reason as 1.1. Kills
  adding a `local_exemption` parameter, and defaulting `expose_confidential`
  to `True`.
- [x] **1.3** [TEST] Same file — `test_disclosable_concept_ids_ranks_and_flag`:
  a fixture `bundle_dir` with one doc at each rank — public, private,
  confidential, blank `sensitivity`, absent `sensitivity`, invalid-UTF-8
  (unreadable), and unparseable frontmatter — asserts
  `disclosable_concept_ids(bundle_dir)` returns exactly the public and
  private ids with the flag off, and every walked id (including the
  unreadable and unparseable ones) with `expose_confidential=True`. Covers
  "The bulk sibling applies the identical rank". **RED today**: `AttributeError`
  — `disclosable_concept_ids` does not exist. Kills implementing it as a
  block list instead of an allow list, and including the unreadable ids
  with the flag off.
- [x] **1.4** [TEST] Same file — `test_dangling_id_withheld_even_with_flag`:
  an id referenced by a relation or provenance entry for which no file
  exists on disk is absent from `disclosable_concept_ids`'s returned set,
  both with `expose_confidential=True` and `False`. This is the must-have
  dangling-id proof. Covers "An id with no walked document is withheld even
  under the opt-in". **RED today**: same reason as 1.3. Kills treating a
  not-found id as included once the flag is on.
- [x] **1.5** [TEST] Same file — `test_sensitivity_module_import_bound`: an
  AST scan of `src/openkos/sensitivity.py`'s import statements asserts every
  imported module is stdlib or `openkos.model.okf`. Write this alongside
  1.6's implementation and confirm it is GREEN immediately (the module
  already has this shape; the two new functions must not add a new import),
  then treat it as the standing regression guard against a future
  disallowed import (e.g. `resolution`, a `mcp` module). If it goes RED once
  1.6 lands, that is a design violation to fix in 1.6, not a reason to widen
  this test.
- [x] **1.6** [IMPL] `src/openkos/sensitivity.py` — add
  `blocks_disclosure(value, *, expose_confidential=False) -> bool` (delegates
  to `blocks_llm_send(value)` unless `expose_confidential`, in which case it
  returns `False`) and `disclosable_concept_ids(bundle_dir, *,
  expose_confidential=False) -> frozenset[str]` (walks `okf._iter_docs`
  once; a doc with `read_error`/`parse_error` is excluded unless the flag is
  on, in which case every walked id is included; any other doc is included
  when `not blocks_disclosure(meta.get("sensitivity"))`), per design
  Decision 1 exactly. Update the module docstring to name the disclosure
  boundary. Makes 1.1–1.4 GREEN; 1.5 must already be GREEN by construction.
- [x] **1.7** [CHECK] Run the full existing `tests/unit/test_sensitivity.py`
  suite (unpiped) — confirm `blocks_llm_send`, `sensitive_concept_ids`, and
  every other pre-existing test stay green, unaffected beyond 1.1–1.5.

### ADR-0028 (already written)

- [x] **1.8** Confirm `docs/adr/0028-mcp-disclosure-is-its-own-boundary.md`
  (status `Proposed` in both frontmatter and body) is present as written
  during the design phase, and confirm `docs/adr/README.md` carries its
  index row. No edit is made here. `docs/adr/README.md` also carries
  ADR-0027's row (staged in slice 2) — when committing this slice, stage
  only the hunk of `README.md` that adds ADR-0028's row (`git add -p` if
  both rows already sit in the same working-tree diff), leaving ADR-0027's
  row for slice 2's commit.

### Slice 1 verification

- [x] **1.9** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [x] **1.10** Run `uv run pytest` (unpiped) — must be green.
- [x] **1.11** Run `uv run python evals/run_self_tests.py` — must be green.
- [x] **1.12** Commit as one or more work-unit commits — scope `mcp`, e.g.
  `feat(mcp): add the disclosure predicate and its allowed-set sibling`;
  tests alongside their behavior; ADR-0028 and its `README.md` row staged
  as-is with this commit (see 1.8). Open PR 1 targeting `main`.

---

## Slice 2 (PR 2 → PR 1's branch): the stdio transport and ADR-0027

This slice depends on nothing from slice 1 (it never imports
`sensitivity`); it can proceed independently once PR 1 merges (stacked
ordering only, per the chosen chain strategy).

### `mcp/transport.py` — framing (design Decision 10)

- [x] **2.1** [TEST] `tests/unit/mcp/test_transport.py` (new) —
  `test_decode_line_all_cases`, parametrized over: a line ending in `\n`; a
  line ending in `\r\n`; a blank line (→ `None`); invalid-UTF-8 bytes (→
  `ParseError`); invalid JSON text (→ `ParseError`); a JSON body containing
  the literal token `NaN` (→ `ParseError`, since `parse_constant` raises).
  **RED today**: `ModuleNotFoundError` — `transport.py` does not exist.
  Kills stripping only `\n` (leaving a trailing `\r` on Windows-style
  input), and accepting `NaN` as a value.
- [x] **2.2** [TEST] Same file — `test_encode_message_framing`: a payload
  with a string value containing an embedded `\n` round-trips through
  `json.loads` to the same value; the encoded bytes end with exactly one
  `\n` and contain no other raw `\n` or any `\r`; `encode_message` raises on
  a payload containing `float("nan")` (`allow_nan=False`). **RED today**:
  same reason as 2.1. Kills an `ensure_ascii`/separator choice that lets a
  raw newline leak into the frame, and `allow_nan=True`.
- [x] **2.3** [TEST] Same file — `test_start_reader_posts_lines_then_sentinel`:
  over a real `os.pipe` with a running event loop, `start_reader` posts each
  written line onto the queue in order and then `None` on end of input;
  closing the loop before the reader thread finishes does not raise inside
  the thread (the closed-loop `RuntimeError` is swallowed). **RED today**:
  same reason as 2.1. Kills omitting the `None` sentinel on end of input.
- [x] **2.4** [TEST] Same file — `test_message_writer_send_writes_bytes`:
  `MessageWriter(stream).send({...})` writes `encode_message`'s exact bytes
  to the given binary stream. **RED today**: same reason as 2.1.
- [x] **2.5** [TEST] `tests/unit/mcp/test_stdio_subprocess.py` (new,
  `cross_platform_smoke`) — `test_claim_stdio_redirects_stray_writes`: a
  real subprocess calls `claim_stdio()`, then performs a bare `print()`, a
  `sys.stdout.write()`, and `os.write(1, b"...")` before writing one
  protocol frame through the claimed writer; the captured real stdout
  contains only that one frame, and the stray text lands on stderr instead.
  This is the must-have stray-print proof that stdout carries only
  protocol. **RED today**: `ModuleNotFoundError` — same reason as 2.1. Kills
  rebinding `sys.stdout` without also `os.dup2(2, 1)` (an fd-level write
  would still leak to the real stdout).
- [x] **2.6** [IMPL] `src/openkos/mcp/__init__.py` (new) — package docstring
  only; imports nothing.
- [x] **2.7** [IMPL] `src/openkos/mcp/transport.py` (new) — `StdioStreams`,
  `claim_stdio`, `start_reader`, `ParseError`, `decode_line`,
  `encode_message`, `MessageWriter`, exactly per design Decision 10. Makes
  2.1–2.5 GREEN.

### ADR-0027 (already written)

- [x] **2.8** Confirm `docs/adr/0027-hand-rolled-stdio-mcp-server.md`
  (status `Proposed`) is present as written during the design phase, and
  confirm `docs/adr/README.md` carries its index row (added alongside
  ADR-0028's, per 1.8). Stage only ADR-0027's hunk of `README.md` with this
  slice's commit.

### Slice 2 verification

- [x] **2.9** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [x] **2.10** Run `uv run pytest` (unpiped) — must be green, including
  `tests/unit/mcp/test_transport.py` and the `cross_platform_smoke`
  subprocess test.
- [x] **2.11** Run `uv run python evals/run_self_tests.py` — must be green.
- [x] **2.12** Commit as one or more work-unit commits — scope `mcp`, e.g.
  `feat(mcp): add the stdio transport and framing`; tests alongside their
  behavior; ADR-0027 and its `README.md` row staged as-is with this commit
  (see 2.8). Open PR 2 targeting PR 1's branch.

---

## Slice 3 (PR 3 → PR 2's branch): server lifecycle, dispatch, and the tool-registry skeleton

**Fallback split** (design's Migration / Rollout, if this slice grows past
~400 lines): split into 3a "lifecycle" (3.1–3.3, 3.7–3.8, 3.10 —
`initialize`/`ping`/protocol-error mapping, the in-flight map, end of
input) and 3b "tools/call plus validator" (3.4–3.6, 3.9 — the `tools.py`
skeleton, `validate_arguments`, the schema-keyword walk, the
`invalid_arguments` mapping), each its own PR in the same stacked order.

### `mcp/server.py` — lifecycle, dispatch, protocol errors (design Decisions 12–13)

- [x] **3.1** [TEST] `tests/unit/mcp/test_server.py` (new) —
  `test_lifecycle_table`: a request other than `initialize`/`ping` sent
  before `initialize` is rejected `-32600`; `ping` IS answered before
  `initialize` (Decision 12: `ping` answers `{}` in any state); a second
  `initialize` is rejected `-32600`; `initialize` naming `"2024-11-05"` or
  `"2099-01-01"` is answered naming `"2025-11-25"` in both cases;
  `notifications/initialized` is optional (`tools/list` works without it
  having been sent); `notifications/cancelled` naming an in-flight
  `initialize` request does not cancel the handshake. Covers mcp's
  "initialize negotiates the supported revision", "A different requested
  revision is answered with the supported one", "A request before
  initialize is rejected", "ping receives a response", and "initialize
  cannot be cancelled". **RED today**: `ModuleNotFoundError` — `server.py`
  does not exist. Kills accepting `tools/list` before `initialize`, and
  echoing the client's requested protocol version instead of the server's
  own.
- [x] **3.2** [TEST] Same file — `test_protocol_error_rows`, parametrized
  over Decision 12's error table: an unparseable/non-UTF-8 frame →
  `-32700`, `id: null`; a JSON array (batch) → one `-32600`, `id: null`; a
  non-object, `jsonrpc != "2.0"`, `method` not a string, or `id`
  null/boolean/float → `-32600` (with the id when it was a valid id type,
  else `null`); a response object with no `method` is silently ignored; an
  unknown request method → `-32601`; an unknown notification is silently
  ignored; a `tools/call` with `params` not an object, `name` not a string,
  `arguments` present and not an object, or a non-string/int
  `progressToken` → `-32602`; an unknown tool name → `-32602`. Covers "A
  batch request is rejected", "An unparseable frame yields -32700", "An
  unknown tool name yields -32602", and "A malformed request envelope
  yields -32602". **RED today**: same reason as 3.1. Kills treating a
  boolean id as a valid integer id, and answering a batch element-wise
  instead of with one `-32600`.
- [x] **3.3** [TEST] Same file — `test_internal_error_never_echoes_exception_text`:
  a test-only tool whose `run()` raises `RuntimeError("secret text naming a
  confidential path")` produces a `-32603` response whose message is the
  fixed generic string, with no trace of "secret text" anywhere in the
  response bytes; the traceback IS present in captured stderr. Covers "An
  internal error still logs only to stderr" and "An internal error never
  echoes exception text". **RED today**: same reason as 3.1. Kills using
  `str(exc)` as the `-32603` message.
- [x] **3.7** [TEST] Same file — `test_duplicate_inflight_id_rejected`: a
  request id already in flight (a test-only tool blocked on a
  `threading.Event`) receives a second request with the same id; the
  second is answered `-32600` "duplicate request id"; releasing the event
  lets the first request complete normally. **RED today**: same reason as
  3.1. Kills overwriting the first in-flight entry with the second, which
  would silently abandon the first request's eventual response.
- [x] **3.8** [TEST] Same file — `test_end_of_input_abandons_and_exits_zero`:
  an in-flight test-only tool call blocked on a `threading.Event` when the
  input stream reaches EOF; `serve()`'s dispatch loop stops reading, sends
  no response for the in-flight call, logs the abandonment count to
  stderr, and returns `0`; a `KeyboardInterrupt` raised during `serve()`
  returns `130` instead. Covers mcp's "Closing stdin abandons in-flight
  requests and exits 0" at the unit level (slice 4's subprocess tier
  reproduces this with a real process end to end) and "A KeyboardInterrupt
  exits distinctly". **RED today**: same reason as 3.1. Kills joining or
  waiting on the abandoned worker thread before returning (which would hang
  past end of input).
- [x] **3.10** [IMPL] `src/openkos/mcp/server.py` (new) — `PROTOCOL_VERSION`,
  `Server`, dispatch tables, the protocol-error mapping, the generic
  `-32603` fallback (fixed message, traceback to stderr only),
  `run_in_worker`, the in-flight map keyed by `RequestKey`, `serve()`
  including its end-of-input and `KeyboardInterrupt` handling. The
  tool-error table's exception rows land incrementally in slices 5 and 9 as
  those tools' exceptions become reachable. Makes 3.1–3.3, 3.7–3.8 GREEN.

### `mcp/tools.py` — the registry skeleton and validator (design Decision 2, 15)

- [x] **3.4** [TEST] `tests/unit/mcp/test_tools.py` (new) —
  `test_validate_arguments_all_keyword_shapes`, parametrized over
  `SUPPORTED_SCHEMA_KEYWORDS`: a missing required property; `additionalProperties:
  false` rejecting an extra property; `minLength` violated; `minimum`
  violated; an `integer`-typed property given `True` (rejected — Python's
  `bool` is an `int` subclass); a `type` union (`["string", "null"]`)
  accepting either; `items` validating array entries; and every rejection
  message names the offending property and never echoes the value that
  failed validation. Covers "Invalid arguments produce a tool execution
  error, not -32602" (message half) and "The invalid-arguments message
  never echoes the value". **RED today**: `ModuleNotFoundError` —
  `tools.py` does not exist. Kills `isinstance(x, int)` without excluding
  `bool`, and an error message that includes the failing value.
- [x] **3.5** [TEST] Same file — `test_every_schema_uses_only_supported_keywords`:
  a recursive walk of every registered tool's `inputSchema` and
  `outputSchema` (the empty `REGISTRY` at this slice, plus one test-only
  tool with a deliberately out-of-set keyword, e.g. `"pattern"`) asserts
  every keyword found is in `SUPPORTED_SCHEMA_KEYWORDS`, and fails for the
  deliberately-added one. **RED today**: same reason as 3.4. Kills removing
  a keyword from `SUPPORTED_SCHEMA_KEYWORDS` without removing its
  enforcement, which would silently stop validating that keyword.
- [x] **3.6** [TEST] Same file — `test_invalid_arguments_is_a_tool_error_not_dash32602`:
  a `tools/call` for a registered test-only tool whose `arguments` fail its
  `inputSchema` returns an ordinary JSON-RPC result (not a JSON-RPC error)
  with `isError: true` and `structuredContent.error.code ==
  "invalid_arguments"`. Covers "Invalid arguments produce a tool execution
  error, not -32602" (routing half). **RED today**: same reason as 3.4.
  Kills mapping schema-validation failures to `-32602` — contradicted by
  the reading confirmed in design's "Orchestrator verification" against
  the 2025-11-25 spec.
- [x] **3.9** [IMPL] `src/openkos/mcp/tools.py` (new) — `Tool`,
  `ToolContext`, the empty `REGISTRY`, `validate_arguments`,
  `SUPPORTED_SCHEMA_KEYWORDS`, `execute` (skeleton; `execute`'s full
  composition with `gate.disclose`/`consistency.read_consistency` per
  design Decision 2 lands once `gate.py` exists in slice 4 — for this
  slice, confirm `execute` still dispatches to a test-only tool's `run`
  and reports a schema-validation failure as `invalid_arguments` without a
  forward reference to `gate.py`). Makes 3.4–3.6 GREEN.

### Slice 3 verification

- [x] **3.11** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [x] **3.12** Run `uv run pytest` (unpiped) — must be green, including
  `tests/unit/mcp/test_server.py` and `tests/unit/mcp/test_tools.py`.
- [x] **3.13** Run `uv run python evals/run_self_tests.py` — must be green.
- [x] **3.14** Commit as one or more work-unit commits — scope `mcp`, e.g.
  `feat(mcp): add server lifecycle, dispatch, and the tool-registry
  skeleton`; tests alongside their behavior. Open PR 3 targeting PR 2's
  branch.

---

## Slice 4 (PR 4 → PR 3's branch): the `openkos mcp` verb, the gate skeleton, and the canary enumeration guard

### `openkos mcp` verb (design Decision 14)

- [x] **4.1** [TEST] `tests/unit/cli/test_mcp_cmd.py` (new) —
  `test_refuses_non_workspace_before_serving`: `openkos mcp --workspace
  <a non-workspace dir>` exits `1`, prints the refusal on stderr, and
  stdout is empty (no protocol frame is ever written); a workspace whose
  `openkos.yaml` fails `read_config` also exits `1` with nothing on
  stdout. Covers mcp's "An invalid workspace directory refuses to serve"
  and the "Workspace root selection" threat-matrix row. **RED today**: the
  `mcp` command does not exist. Kills validating the workspace only after
  `serve()` has already started emitting frames.
- [x] **4.2** [TEST] Same file — `test_flags_reach_serve_and_command_metadata`:
  a patched recorder on `openkos.mcp.server.serve` captures the resolved
  `root: Path` and `expose_confidential: bool` for `--workspace DIR
  --expose-confidential` and for no flags (default `False`); `mcp` is a
  member of `cli.main._READ_ONLY_COMMANDS` and appears in the `Explore`
  help panel; the command's help text passes the existing
  `test_no_command_help_publishes_internal_references` check. Covers "A
  valid workspace directory serves normally", "The flag defaults to off",
  and "mcp is classified as a read-only command". **RED today**: same
  reason as 4.1. Kills a default of `True` for `--expose-confidential`.
- [x] **4.3** [TEST] Same file — `test_mcp_module_not_imported_at_cli_startup`:
  in a fresh subprocess, `python -c "import openkos.cli.main"` leaves
  `openkos.mcp` absent from `sys.modules`. Covers "The CLI imports mcp
  lazily, only inside the verb" (import-timing half). **RED today**: passes
  trivially until the verb exists; write it alongside 4.7's lazy import
  and confirm it stays GREEN — if it goes RED once the verb lands, that
  means the import was hoisted to module level, which 4.7 must fix. Kills a
  module-level `import openkos.mcp` in `cli/main.py`.
- [x] **4.11** [TEST] Same file — `test_nonlocal_embed_host_advisory_at_startup`:
  with a configured embedding host that is not local, launching `openkos
  mcp` logs one stderr advisory before serving, mirroring the CLI's
  `_warn_if_nonlocal_embed_host`; a local host logs no such advisory.
  Covers the "Environment inheritance" threat-matrix row's advisory half.
  **RED today**: same reason as 4.1. Kills omitting the advisory, or
  emitting it on stdout.

### `mcp/gate.py` — the skeleton, and layering (design Decision 2)

- [x] **4.4** [TEST] `tests/unit/mcp/test_layering.py` (new) —
  `test_layering_invariants`: an AST walk asserting `asyncio` is imported
  only under `src/openkos/mcp/` (nowhere else in `src/openkos/`); no module
  under `src/openkos/mcp/` imports `openkos.cli`; no module under
  `src/openkos/application/` imports `openkos.mcp`; every module under
  `src/openkos/mcp/` imports only stdlib or `openkos.*`; among `mcp/`'s own
  modules, only `gate.py` references `sensitivity.blocks_disclosure` or
  `sensitivity.disclosable_concept_ids` (a call-site scan, not only an
  import scan); no module under `src/openkos/mcp/` imports the
  lock-acquisition helper (covers "A read tool never raises
  WorkspaceBusyError" and "Serving never waits on the workspace lock" by
  construction). Covers "openkos.mcp never imports openkos.cli",
  "openkos.mcp never imports openkos.graph", "Only gate.py imports the
  disclosure predicate module", "application never imports openkos.mcp",
  and the import-shape half of "The CLI imports mcp lazily, only inside the
  verb". **RED today**: `ModuleNotFoundError` — `gate.py` does not exist
  yet, and `cli.main`'s `mcp` verb does not exist to be scanned. Kills an
  `asyncio` import inside `application/`, and a direct predicate call from
  `tools.py` instead of routing through `gate.py`.
- [x] **4.7** [IMPL] `src/openkos/cli/main.py` — add the `mcp` command per
  design Decision 14: `--workspace` (default `Path(".")`),
  `--expose-confidential`, `require_workspace` + `read_config` validated
  before any stdio activity, the nonlocal-embed-host stderr advisory, a
  lazy `from openkos.mcp import server as mcp_server` inside the function
  body, `mcp` joins `_READ_ONLY_COMMANDS`, `"Explore"` panel. Makes 4.1–4.3
  and 4.11 GREEN.
- [x] **4.8** [IMPL] `src/openkos/mcp/gate.py` (new, skeleton) — `Snapshot`
  (frozen dataclass: `allowed`, `expose_confidential`, `discloses()`),
  `take_snapshot(bundle_dir, *, expose_confidential)`, `finish(payload,
  consistency)` without `warnings` yet (warnings land in slice 5 with
  `consistency.py`); this is the only `mcp` module that imports
  `openkos.sensitivity`. Makes 4.4's "only gate.py" assertion GREEN.

### The canary enumeration guard (design Decision 16, must-have)

- [x] **4.5** [TEST] `tests/unit/mcp/canary.py` (new, fixture helpers) +
  `tests/unit/mcp/test_enumeration_guard.py` (new) —
  `test_guard_matrix_covers_empty_registry_and_leaky_probe`: build the
  canary fixture workspace per design Decision 16 — confidential
  `concepts/zq-canary-7f3a.md` (title `Zq Canary Title 7f3a`, body
  containing `ZQ-CANARY-BODY-7F3A`, a `relations:` entry to
  `concepts/pub`); confidential `sources/zq-canary-src-7f3a.md`
  (`extraction_status: failed`); public `concepts/pub.md` (relating to and
  citing the canary and the confidential Source, its own prose never
  mentioning a canary); invalid-UTF-8 `concepts/zq-broken-7f3a.md`; a
  pending merge-ledger marker whose survivor is the canary; a findings row
  pairing `pub` with the canary; an FTS index built with `reindex`; a fake
  `LLMBackend` echoing the user message back. `set(tools.REGISTRY) ==
  set(GUARD_MATRIX)` holds trivially over the empty registry; adding a
  test-only `leaky_probe` tool (whose `disclose` returns the canary title)
  to a COPY of the registry, WITH a matching matrix row, makes
  `find_canary_leaks(run_matrix(...))` return a non-empty list (assertion
  3 — the guard demonstrably catches it, shown FAILING before this test is
  written GREEN); the same copy WITHOUT the matrix row fails assertion 1.
  Covers "The guard catches a deliberately violating tool". **RED today**:
  `ModuleNotFoundError` — `canary.py`, `test_enumeration_guard.py`, and
  `gate.py` do not exist. Kills a needle search (`find_canary_leaks`) that
  inspects only `structuredContent` and skips the `text` block and raw
  response bytes — the helper must scan every byte the server wrote, both
  raw and after `json.loads` of each `text` block.
- [x] **4.6** [TEST] Same file — `test_positive_controls_prove_the_fixture_is_live`:
  with `leaky_probe` registered and `expose_confidential=True` passed
  through `ToolContext`, the canary needle IS found — proving the
  fixture's needles are genuinely reachable, not a guard that would report
  clean on a broken fixture. The real per-tool positive controls (`get`,
  `navigate`, `pending`, `query` with the flag on) are added incrementally
  in slices 5, 6, 7, and 9 as each tool lands. **RED today**: same reason
  as 4.5.
- [x] **4.9** [IMPL] `tests/unit/mcp/canary.py` — the fixture-building
  helpers, needle list (every canary id, the slugs without their
  directory, the title, and the body marker, each also case-folded),
  `find_canary_leaks`, `GUARD_MATRIX` scaffolding, `run_matrix`, and the
  test-only `leaky_probe` tool. Makes 4.5–4.6 GREEN.

### The subprocess tier (design Decision 16 / Testing Strategy)

- [x] **4.10** [TEST] `tests/unit/mcp/test_stdio_subprocess.py` —
  `test_full_handshake_over_stdio` (`cross_platform_smoke`,
  `OLLAMA_HOST=http://127.0.0.1:9`): a real subprocess of `openkos mcp
  --workspace <fixture>`; send `initialize` (framed with `\r\n`) →
  `notifications/initialized` → `tools/list` → `ping` → close stdin; every
  captured stdout line parses as a well-formed JSON-RPC 2.0 message, no
  line contains `\r`, and the process exits `0`. Covers "A subprocess run
  emits only JSON-RPC lines on stdout" and "Closing stdin abandons
  in-flight requests and exits 0" at the real-process level. **RED today**:
  the `mcp` verb does not exist to spawn. Kills leaving stdout in text
  mode (which would translate `\n`), and not exiting on end of input.
- [x] **4.12** [IMPL] N/A — expected GREEN by construction from 4.7 (the
  verb), 3.10 (`serve()`'s end-of-input handling from slice 3), and 2.7
  (`claim_stdio`). If 4.10 is RED, that is a design violation in an earlier
  slice to fix there, not new production code here.

### Slice 4 verification

- [x] **4.13** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [x] **4.14** Run `uv run pytest` (unpiped) — must be green, including the
  `cross_platform_smoke` subprocess test.
- [x] **4.15** Run `uv run python evals/run_self_tests.py` — must be green.
- [x] **4.16** Commit as one or more work-unit commits — scope `cli` for
  the `mcp` verb (e.g. `feat(cli): add the openkos mcp verb`), scope `mcp`
  for `gate.py`'s skeleton and the canary enumeration guard (e.g.
  `feat(mcp): add the disclosure gate skeleton and the canary enumeration
  guard`); tests alongside their behavior. Open PR 4 targeting PR 3's
  branch.

---

## Slice 5 (PR 5 → PR 4's branch): the `get` tool

### `application/concept_read.py` — `read_concept` (design Decision 4)

- [x] **5.1** [TEST] `tests/unit/application/test_concept_read.py` (new) —
  `test_read_concept_curated_fields_only`: a concept with an extra
  frontmatter key (e.g. `secret_ids`) produces a `ConceptRecord` whose
  fields are exactly the curated set — that extra key is absent from the
  record entirely (confirm via `dataclasses.fields`, not just unused
  access); the record's `body` is the text as written, including a mention
  of a separate confidential concept by name (the gate never touches
  prose — see mcp's "A non-confidential note mentioning a confidential one
  is shown whole"). Covers "get's result names only the curated fields".
  **RED today**: `ModuleNotFoundError` — `concept_read.py` does not exist.
  Kills passing the raw frontmatter dict through instead of the curated
  dataclass.
- [x] **5.2** [TEST] Same file — `test_read_concept_path_traversal_refusals`,
  parametrized over `../../etc/passwd`, an absolute path, a reserved name
  (`index`), and a symlinked segment escaping the bundle — each raises
  `ConceptNotFound`, and no read is attempted (assert via a read spy).
  Covers the "Caller-supplied concept ids" threat-matrix row. **RED
  today**: same reason as 5.1. Kills a canonicalization that resolves a
  symlink escape before refusing it, or accepting `..` after a partial
  canonicalization pass.
- [x] **5.3** [TEST] Same file — `test_read_concept_unreadable_and_malformed`:
  invalid-UTF-8 bytes produce `UnreadableConcept` (not an exception);
  malformed `relations:` (a `ValueError` from `okf.decode_relations`)
  produces `relations=()` plus a `NotRun("relations", ...)` entry, with the
  rest of the record intact. Covers "An unreadable target under the opt-in
  is a success with a not_run entry" and "The same unreadable target,
  opt-in off, is reported as withheld" (record-shape half; the gate
  behavior half is 5.5). **RED today**: same reason as 5.1. Kills raising
  instead of returning `UnreadableConcept`, and dropping the whole record
  instead of only `relations` when `relations:` is malformed.

### `application/consistency.py` (design Decision 11)

- [x] **5.4** [TEST] `tests/unit/application/test_consistency.py` (new) —
  `test_read_consistency_in_flight_and_stale`: a pending merge-ledger
  marker gives `in_flight_writes == 1`; a marker whose scan raises gives
  `in_flight_writes is None` and a `NotRun("in_flight_write", str(exc))`;
  `stale_reads=("fts",)` with a stale FTS store lists `"fts"` in
  `stale_stores`; `stale_reads=()` (the default) never touches staleness at
  all; the staleness check itself never raises and never produces a
  `NotRun` (it degrades to `()`). Covers "An in-flight write produces a
  count-only warning on any tool" (source half), "A check that cannot run
  is reported, never raised" (source half), and "stale_index never
  produces a not_run entry" (source half). **RED today**:
  `ModuleNotFoundError` — `consistency.py` does not exist. Kills letting
  `scan_torn_writes`'s exception propagate instead of catching it into a
  `NotRun`.

### `mcp/gate.py` — `disclose_get` and `finish`'s warnings/aggregation (design Decisions 3, 4)

- [x] **5.5** [TEST] `tests/unit/mcp/test_gate.py` (new) —
  `test_disclose_get_table`, parametrized over design Decision 4's table: a
  target not in the snapshot's allowed set (regardless of its own read) →
  `concept: null, withheld: 1`; a target IN the allowed set whose own
  freshly-read sensitivity says confidential → `concept: null, withheld:
  1` (the conjunction's reverse direction); a disclosable `ConceptRecord`
  → `concept` with `relations`/`provenance` filtered through the snapshot
  and `source_ancestors = ancestors ∩ allowed`. Covers "A withheld id
  returns an empty object with a count, not content", "An unreadable
  target under the opt-in is a success with a not_run entry" (gate half),
  "The same unreadable target, opt-in off, is reported as withheld" (gate
  half), and "A withheld concept contributes only to the count". **RED
  today**: `AttributeError` — `gate.disclose_get` does not exist. Kills
  checking only the snapshot (missing the record's own re-check), or only
  the record (missing the snapshot's later-raised-object catch).
- [x] **5.6** [TEST] Same file — `test_disclose_get_withheld_counts_entries`:
  a `get` result with 2 filtered relations, 1 filtered provenance id, and
  1 filtered ancestor sets `withheld == 4`, even when the same underlying
  id appears in more than one of those channels. Covers "A withheld object
  appearing in two channels is counted twice". **RED today**: same reason
  as 5.5. Kills counting distinct underlying objects instead of removed
  entries.
- [x] **5.7** [TEST] Same file — `test_document_labelled_not_run_aggregated`:
  several `NotRun` outcomes each labelled by a different document path are
  aggregated by `gate.finish` into exactly one `not_run` entry with a
  fixed, count-only reason and an allowlisted label; a `NotRun` with a
  label outside the fixed vocabulary is aggregated the same way, never
  forwarded with its original label. Covers "An unreadable ancestor is
  reported as not_run, not an error", "Document-labelled not_run entries
  are aggregated", "An unrecognized label is aggregated, not forwarded",
  and "A check that cannot run is reported, never raised" (gate half).
  **RED today**: same reason as 5.5 (slice 4's `finish` skeleton has no
  aggregation yet). Kills forwarding `NotRun.reason` verbatim, which could
  carry a document path.

### The `get` tool and its guard row

- [x] **5.8** [TEST] `tests/unit/mcp/test_enumeration_guard.py` — extend
  `GUARD_MATRIX` with a `get` row: a disclosable id (`pub`), each canary
  id, a missing id, invalid arguments, and an injected `OSError`/
  `RuntimeError` carrying the canary's body marker in its message; add the
  positive control (`expose_confidential=True` on the canary, asserting
  its title DOES appear). Covers "Every real tool passes the guard on
  every path" (get's contribution). **RED today**: `get` is not registered
  yet — `set(tools.REGISTRY) == set(GUARD_MATRIX)` fails once the row is
  added without the tool. Kills leaving `get`'s error paths out of the
  matrix.
- [x] **5.9** [IMPL] `src/openkos/application/concept_read.py` (new) —
  `ConceptNotFound`, `ConceptRecord`, `UnreadableConcept`, `read_concept`
  per design Decision 4: resolve via
  `lifecycle.resolve_concept_path`; one `read_text` + `okf.load_frontmatter`;
  `status` via `lifecycle.deprecated_concept_ids`; malformed `relations:`
  → `relations=()` plus a `NotRun`. Makes 5.1–5.3 GREEN.
- [x] **5.10** [IMPL] `src/openkos/application/consistency.py` (new) —
  `Consistency`, `read_consistency` per design Decision 11:
  `in_flight_writes` via `bundle_ledger.scan_torn_writes` caught into a
  `NotRun`; `stale_stores` via `application_status.stale_index_names` when
  `stale_reads` is non-empty. Makes 5.4 GREEN.
- [x] **5.11** [IMPL] `src/openkos/mcp/gate.py` — add `disclose_get`; extend
  `finish` to render `warnings` (`{in_flight_write, count}` /
  `{stale_index, stores}`) and aggregate `not_run` entries per the fixed
  label vocabulary and count-only reasons. Makes 5.5–5.7 GREEN.
- [x] **5.12** [IMPL] `src/openkos/mcp/tools.py` — register the `get`
  tool: `run()` calls `concept_read.read_concept` +
  `list_service.list_provenance_sources`; `disclose()` = `gate.disclose_get`;
  the `inputSchema` from design Decision 15; wire `ConceptNotFound` →
  `concept_not_found` (not retryable) and any `OSError` →
  `read_failed` (retryable) into `server.py`'s tool-error table (the
  Ollama-related rows land in slice 9). Covers "Each tool calls its
  underlying service unmodified" and "A missing concept is reported as not
  retryable" (get's contribution). Makes 5.8 GREEN alongside 5.11.

### Slice 5 verification

- [x] **5.13** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [x] **5.14** Run `uv run pytest` (unpiped) — must be green.
- [x] **5.15** Run `uv run python evals/run_self_tests.py` — must be
  green.
- [x] **5.16** Commit as one or more work-unit commits — scope `mcp`, e.g.
  `feat(mcp): add the get tool and its disclosure gate`; tests alongside
  their behavior. Open PR 5 targeting PR 4's branch.

---

## Slice 6 (PR 6 → PR 5's branch): the `navigate` tool

### `application/concept_read.py` — `concept_neighbors` (design Decision 5)

- [x] **6.1** [TEST] `tests/unit/application/test_concept_read.py` —
  `test_concept_neighbors_both_directions`: a concept with an outbound
  typed relation, an outbound untyped link, and an INBOUND edge from
  another concept — `concept_neighbors` returns all three, each with the
  correct `direction` (`"out"`/`"in"`) and `relation_type` (`None` for
  untyped, the type string for typed, `"derived_from"` for a
  provenance-derived edge); a missing concept id raises `ConceptNotFound`.
  Covers "navigate returns typed and untyped neighbors" and "navigate
  includes inbound neighbors, not only outbound". **RED today**:
  `AttributeError` — `concept_neighbors` does not exist. Kills using
  `SqliteGraphStore.neighbors` (out-only) instead of filtering
  `store.edges()` by both `source_id` and `target_id`.

### `mcp/gate.py` — `disclose_navigate` (design Decision 5)

- [x] **6.2** [TEST] `tests/unit/mcp/test_gate.py` —
  `test_disclose_navigate_table`: a confidential neighbor reachable via an
  outbound edge is removed and counted; the same via an inbound edge is
  also removed and counted; a target `concept_id` itself not disclosable
  returns `concept_id: null, withheld: 1` (no neighbors listed);
  `store.skipped` non-empty produces a `not_run` entry labelled
  `graph_build` with a count-only reason, and the tool still returns
  whatever neighbors it did read. Covers "A graph read that skipped edges
  reports a graph_build not_run entry", "A withheld concept contributes
  only to the count" (navigate's contribution), and "An edge survives only
  when both ends are disclosable". **RED today**: `AttributeError` —
  `disclose_navigate` does not exist. Kills filtering only outbound edges
  (missing the inbound-edge removal case).

### The `navigate` tool and its guard row

- [x] **6.3** [TEST] `tests/unit/mcp/test_enumeration_guard.py` — extend
  `GUARD_MATRIX` with a `navigate` row (same shape as 5.8: `pub`, each
  canary id, missing id, invalid arguments, injected failures) and its
  positive control. **RED today**: `navigate` is not registered — the
  registry/matrix set equality fails. Kills leaving `navigate`'s error
  paths out of the matrix.
- [x] **6.4** [IMPL] `src/openkos/application/concept_read.py` — add
  `Neighbor`, `Neighborhood`, `concept_neighbors` per design Decision 5:
  resolve the id, run `build_graph(layout.bundle_dir)` with no candidates,
  filter `store.edges()` to `source_id == id` (out) and `target_id == id`
  (in), sorted `(direction, concept_id, relation_type or "")`. Makes 6.1
  GREEN.
- [x] **6.5** [IMPL] `src/openkos/mcp/gate.py` — add `disclose_navigate` per
  design Decision 5. Makes 6.2 GREEN.
- [x] **6.6** [IMPL] `src/openkos/mcp/tools.py` — register the `navigate`
  tool (`run()` = `concept_read.concept_neighbors`, `disclose()` =
  `gate.disclose_navigate`, `inputSchema` per Decision 15). Covers "Each
  tool calls its underlying service unmodified" (navigate's contribution).
  Makes 6.3 GREEN.

### Slice 6 verification

- [x] **6.7** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [x] **6.8** Run `uv run pytest` (unpiped) — must be green.
- [x] **6.9** Run `uv run python evals/run_self_tests.py` — must be green.
- [x] **6.10** Commit as one or more work-unit commits — scope `mcp`, e.g.
  `feat(mcp): add the navigate tool and its disclosure gate`; tests
  alongside their behavior. Open PR 6 targeting PR 5's branch.

---

## Slice 7 (PR 7 → PR 6's branch): the `pending` tool, and the `openkos next` byte-identity golden

### `openkos next` characterization golden (must-have — written and green on `main` first)

- [x] **7.1** [GOLDEN] `tests/unit/cli/test_next_golden.py` (new) —
  `test_next_stdout_golden`: capture `openkos next`'s exact stdout for
  bootstrap (both branches), missing vector index, missing FTS, stale
  indexes, unextracted with a declination and a skip notice,
  multi-source-uncovered with an unspellable-id declination, duplicate
  groups, non-NFC, and an open contradiction. Written and confirmed GREEN
  on `main` BEFORE 7.2–7.6 land — it is the regression net for this
  slice's `subjects` work, not a RED-first test. Covers "openkos next's
  stdout is byte-identical" and mcp's "The CLI is unaffected by the gate"
  (next's contribution). Any subsequent mutation of a reason string or
  rendering order must fail it.

### `application/next_action.py` and `lint.py` — structured subjects (design Decision 6)

- [x] **7.2** [TEST] `tests/unit/application/test_next_action.py` (or its
  existing home) — `test_every_next_action_and_declination_call_declares_subjects`:
  an AST scan of `application/next_action.py` asserts every `NextAction(`
  call site passes `subjects=` explicitly (never relying on the `None`
  default), and every `record_declination(` call passes `subjects=`
  explicitly. Write this right after 7.6 adds the fields but before every
  call site is updated, so it is genuinely RED. **RED today**: `subjects`
  does not exist yet — `AttributeError`/a call site the scan flags. Kills a
  new tier landing later without declaring `subjects=`.
- [x] **7.3** [TEST] Same file — `test_per_tier_subjects_match_design_table`:
  one assertion per tier from Decision 6's table — bootstrap,
  missing-vector-index, missing-FTS, stale-indexes, duplicate-groups, and
  non-NFC give `subjects=()`; unextracted and unjudged (action and both
  declinations) give `(finding.concept_id,)`; below-source-sensitivity
  gives `(finding.concept_id, *finding.related_ids)`;
  multi-source-uncovered (action and declination) gives
  `(finding.concept_id, *finding.related_ids)`; open contradictions gives
  `finding.pair_ids`; `declination_subjects` stays index-aligned with
  `declinations`. Covers next-action-pointer's "A tier-2 recommendation
  names its Source's concept id", "A declination names its subject", "A
  tier with no resolvable subject declares an explicit empty tuple",
  "declination_subjects stays index-aligned with declinations", "A
  below-source-sensitivity finding's subjects include its related Source",
  and "A multi-source-uncovered finding's subjects include every cited
  id". **RED today**: same reason as 7.2. Kills omitting `related_ids`
  from the below-source-sensitivity or multi-source-uncovered tiers'
  `subjects`.
- [x] **7.4** [TEST] `tests/unit/test_lint.py` —
  `test_related_ids_populated_and_excluded_from_equality`:
  `LintFinding.related_ids` is `()` by default; populated with
  `(source_id,)` for `below-source-sensitivity` and with the cited ids for
  `multi-source-uncovered`; two findings equal in every field except
  `related_ids` compare equal. Covers "related_ids does not affect
  LintFinding equality". **RED today**: `AttributeError` — `related_ids`
  does not exist. Kills `compare=True` on the new field, which would break
  every existing `LintFinding` equality assertion across the lint suite.
- [x] **7.5** [IMPL] `src/openkos/lint.py` — add `LintFinding.related_ids:
  tuple[str, ...] = field(default=(), compare=False)`; populate it for
  `below-source-sensitivity` with `(source_id,)` and for
  `multi-source-uncovered` with the cited ids. No lint rendering changes.
  Makes 7.4 GREEN.
- [x] **7.6** [IMPL] `src/openkos/application/next_action.py` — add
  `NextAction.subjects: tuple[str, ...] | None = None`,
  `NextResult.declination_subjects: tuple[tuple[str, ...] | None, ...] =
  ()`, make `record_declination(subjects=)` a required keyword; update
  every `NextAction(...)` and `record_declination(...)` call site per
  Decision 6's table. Makes 7.2–7.3 GREEN; confirm 7.1's golden is still
  GREEN (`render_lines` is untouched, so stdout stays byte-identical).

### `mcp/gate.py` — `disclose_pending`, and the `pending` tool

- [x] **7.7** [TEST] `tests/unit/mcp/test_gate.py` —
  `test_disclose_pending_table`: an action whose `subjects` is `None` is
  withheld regardless of the finding's actual harmlessness; an action
  whose `subjects` is `()` is disclosed normally; an action whose
  `subjects` includes one non-disclosable id is withheld in full;
  declinations where `len(declination_subjects) != len(declinations)`
  withhold every declination; `skip_notices` become `skipped_documents`,
  never `withheld`. Covers "A pending subject that is not fully
  disclosable withholds the whole item", "Undeclared pending subjects
  withhold the action", "A declared, subject-free tier is disclosed, not
  withheld", "Misaligned declination subjects withhold every declination",
  and "Skip notices are reported separately from withheld" (pending's
  contribution). **RED today**: `AttributeError` — `gate.disclose_pending`
  does not exist. Kills treating `None` the same as `()`, and zipping
  misaligned `declination_subjects`/`declinations` instead of withholding
  all on mismatch.
- [x] **7.8** [TEST] `tests/unit/mcp/test_enumeration_guard.py` — extend
  `GUARD_MATRIX` with a `pending` row, including a positive control with
  `expose_confidential=True` asserting the canary Source's declination
  DOES surface (proving the fixture's unextracted-tier canary is
  genuinely reachable). **RED today**: `pending` is not registered — the
  registry/matrix set equality fails. Kills leaving `pending`'s
  declinations out of the matrix's needle search.
- [x] **7.9** [IMPL] `src/openkos/mcp/gate.py` — add `disclose_pending` per
  design Decision 6's gate rules. Makes 7.7 GREEN.
- [x] **7.10** [IMPL] `src/openkos/mcp/tools.py` — register the `pending`
  tool (`run()` = `next_action.next_action(...)`, `disclose()` =
  `gate.disclose_pending`, empty `inputSchema` per Decision 15). Covers
  "Each tool calls its underlying service unmodified" (pending's
  contribution). Makes 7.8 GREEN.

### Slice 7 verification

- [x] **7.11** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [x] **7.12** Run `uv run pytest` (unpiped) — must be green, including
  7.1's golden staying byte-identical.
- [x] **7.13** Run `uv run python evals/run_self_tests.py` — must be
  green.
- [x] **7.14** Commit as one or more work-unit commits — scope `cli` for
  the `next_action`/`lint` subjects change (matching this branch's
  precedent for `application/next_action.py` moves, e.g. `feat(cli): add
  structured subjects to next_action recommendations`), scope `mcp` for
  `disclose_pending` and the `pending` tool (e.g. `feat(mcp): add the
  pending tool and its disclosure gate`); the golden lands with whichever
  commit it protects. Committed as `764d1f2`/`a6c9493` on
  `feat/1009-mcp-pending` (this run's launch instructions forbid creating
  branches, pushing, or opening a PR — the orchestrator handles delivery).
  Opening PR 7 targeting PR 6's branch is left for the orchestrator.

---

## Slice 8 (PR 8 → PR 7's branch): chat-client relocation, and query preparation

### `application/backends.py` and the CLI delegators (design Decision 7)

- [x] **8.1** [TEST] `tests/unit/application/test_backends.py` (new) —
  `test_chat_client_passes_six_kwargs`: `chat_client(cfg,
  factory=recording_factory, task=...)` calls the factory with exactly
  `model` (via `resolve_task_model`), `timeout`, `max_generation_tokens`,
  `context_window`, `temperature`, `seed` — no more, no fewer. Covers "The
  definition takes the client class as an injected factory". **RED
  today**: `ModuleNotFoundError` — `backends.py` does not exist. Kills
  dropping `timeout=` (or any of the six).
- [x] **8.2** [TEST] Same file — `test_resolve_local_exemption_truth_table`:
  the 4 combinations of `client.locality.is_local` and
  `cfg.confidential_local_exemption`, asserting `and` (not `or`)
  semantics. **RED today**: same reason as 8.1. Kills using `or` instead
  of `and`.
- [x] **8.3** [TEST] `tests/unit/cli/test_backends_delegation.py` (new, or
  extend an existing CLI test file) —
  `test_delegators_are_single_line_and_singly_defined`: an AST check that
  `cli.main._chat_client` and `_resolve_local_exemption` are each a single
  `return` statement calling `application_backends.*`; a source-wide
  AST/grep confirms each function's real body (`chat_client`,
  `resolve_local_exemption`) has exactly one definition under `src/`.
  Covers "The CLI keeps one-line delegators under their existing names"
  and "The CLI's observable behavior, and its test seam, are unaffected"
  (structural half). **RED today**: same reason as 8.1 — the delegator
  does not exist to inspect yet (today's bodies are the full
  implementations). Kills leaving a second copy of either body after the
  move.
- [x] **8.4** [TEST] Same file — `test_ollama_client_monkeypatch_still_intercepts`:
  reuse (do not rewrite) the existing network-guard fixture that patches
  `openkos.cli.main.OllamaClient`, and confirm a call path that
  constructs a chat client through the new `application/backends.py`
  delegator still gets the patched class. Prove it with a mutation: patch
  `application.backends.chat_client`'s factory resolution to import
  `OllamaClient` directly from `openkos.llm.ollama` instead of receiving
  it as an injected argument; confirm this mutation makes the existing
  `tests/unit/conftest.py:476` network guard's patch silently inert (a
  real network call, or the guard's own assertion, fails); revert the
  mutation. Covers "The CLI's observable behavior, and its test seam, are
  unaffected" (the ~200-monkeypatch must-have). **RED today**: same reason
  as 8.1, until `_chat_client` becomes a delegator that still reads
  `OllamaClient` from `cli.main`'s own module globals at call time.
- [x] **8.5** [IMPL] `src/openkos/application/backends.py` (new) —
  `_Locality`, `HasLocality`, `ClientT`, `chat_client(cfg, *, factory,
  task=None)`, `resolve_local_exemption(client, cfg)` per design Decision
  7, with the two docstrings moved from `cli/main.py`. Makes 8.1–8.2
  GREEN.
- [x] **8.6** [IMPL] `src/openkos/cli/main.py` — reduce `_chat_client` and
  `_resolve_local_exemption` to one-line delegators that pass
  `OllamaClient` (read from `cli.main`'s own module globals, unchanged) as
  the factory into `application_backends.chat_client`/
  `resolve_local_exemption`. `cli/curate.py`'s own client construction is
  left untouched (design's stated exception). Makes 8.3–8.4 GREEN; confirm
  `tests/unit/cli/test_chat_timeout_wiring.py` and the application
  layering guard (`test_layering.py:107-130`) stay green unchanged.

### `answer()`/`run_query` progress, and `AnswerResult` id lists (design Decision 8)

- [x] **8.7** [TEST] `tests/unit/retrieval/test_answer.py` —
  `test_progress_byte_identity_and_phase_order`: over one fixture,
  `answer(progress=None)` and `answer(progress=recorder)` return equal
  `AnswerResult`s and send byte-identical `messages` to the fake LLM; the
  recorder observed `"retrieving"`, `"assembling"`, `"synthesizing"` in
  order with `completed` strictly increasing and `total == 3` when the
  sufficiency check is disabled, and additionally `"checking"` with
  `total == 4` when it is enabled; the empty-question short-circuit
  invokes the callback zero times; a static AST check confirms
  `retrieval/answer.py` imports no `openkos.config`. Covers query-answer's
  "Omitting progress is byte-identical to today's contract", "A supplied
  callback observes the four phases in order", "Without the sufficiency
  check, checking is absent and total is 3", "The empty-question
  short-circuit emits no progress", and "progress is not read from
  configuration". **RED today**: `TypeError` — `answer()` accepts no
  `progress` keyword yet. Kills emitting a progress call even when
  `progress is None`, and reporting `total == 3` when the sufficiency
  check is enabled (or vice versa).
- [x] **8.8** [TEST] `tests/unit/application/test_query_service.py` —
  `test_run_query_threads_progress`: a spy on `answer()` records the
  `progress` kwarg it receives; `run_query(..., progress=cb)` passes that
  exact `cb` through unmodified; omitting `progress` keeps the composed
  call byte-identical to before this parameter existed. Covers
  query-application-service's "Progress is threaded through unmodified"
  and "Omitting progress keeps composition byte-identical". **RED
  today**: `TypeError` — `run_query` accepts no `progress` keyword yet.
  Kills dropping the kwarg between `run_query` and `answer`.
- [x] **8.9** [TEST] `tests/unit/retrieval/test_answer.py` —
  `test_id_lists_align_with_title_lists`: on the existing `#882`
  excerpt/omission fixtures and a history-truncation fixture (from the
  merged `superseded-history-in-query` change),
  `excerpted_ids[n]`/`omitted_ids[n]`/`history_truncated_ids[n]` name the
  same concept as position `n` of their paired title list, for both a
  populated case and the empty-list case. Covers query-answer's "Each id
  list stays index-aligned with its title list" and "An empty title list
  pairs with an empty id list". **RED today**: `AttributeError` —
  `AnswerResult` has no id-list fields yet. Kills appending an id outside
  the same statement as its title, which would desynchronize the two
  lists under a future edit.
- [x] **8.10** [IMPL] `src/openkos/retrieval/answer.py` — add
  `AnswerPhase`, `ProgressCallback`, the `progress` keyword-only parameter
  to `answer()` (after `revision_history`), the four emission call sites
  per design Decision 8's table, and `excerpted_ids`/`omitted_ids`/
  `history_truncated_ids` fields plus the id-emitting statements alongside
  their existing title-emitting statements. Makes 8.7 and 8.9 GREEN.
- [x] **8.11** [IMPL] `src/openkos/application/query.py` — add the
  `progress` keyword to `run_query`, threaded to `answer()` next to
  `revision_history`. Makes 8.8 GREEN.

### Slice 8 verification

- [x] **8.12** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [x] **8.13** Run `uv run pytest` (unpiped) — must be green, including
  the full suite that relies on the `~200`
  `openkos.cli.main.OllamaClient`-patch network guard staying intact.
- [x] **8.14** Run `uv run python evals/run_self_tests.py` — must be
  green (confirms the harness call sites at `_assemble_context`'s
  boundary, e.g. `evals/query_sufficiency/run_query_sufficiency_probe.py:297`,
  are unaffected by the additive `progress`/id-out parameters).
- [x] **8.15** Commit as one or more work-unit commits — scope `cli` for
  the backends move (matching this branch's precedent for a CLI-owned
  construction relocated into `application/`, e.g. `refactor(cli): move
  chat-client construction into application/backends.py`), scope
  `retrieval` for the progress callback and id lists (e.g. `feat(retrieval):
  add an optional progress callback and id-aligned title lists`). Open
  PR 8 targeting PR 7's branch.

---

## Slice 9 (PR 9 → PR 8's branch): the `query` tool, cancellation, and progress notifications

**Fallback split** (design's Migration / Rollout, if this slice grows past
~400 lines): split into 9a "query tool and disclosure" (9.1–9.3, 9.6–9.10,
9.12) and 9b "cancellation and progress" (9.4–9.5, 9.11), the latter tested
with a test-only blocking tool if that keeps 9b independent of 9a's tool
registration.

### The `query` tool and its egress conjunction (design Decision 9)

- [x] **9.1** [TEST] `tests/unit/mcp/test_gate.py` (or `test_tools.py`) —
  `test_query_llm_egress_conjunction`: with a recording fake LLM and a
  local backend, the canary's body reaches the prompt ONLY when
  `expose_confidential=True`; it never reaches the prompt with the flag
  off, and never with the flag on but a non-local (remote) backend. Covers
  "The opt-in off sends both LLM-egress gates closed", "The opt-in on
  still requires the local-exemption gate", and "A remote backend never
  receives confidential content via query". **RED today**: `query` is not
  registered — `AttributeError`/`KeyError`. Kills passing
  `include_confidential=ctx.expose_confidential` directly, bypassing the
  `local_exemption` conjunction Decision 9 requires.
- [x] **9.2** [TEST] `tests/unit/mcp/test_gate.py` —
  `test_disclose_query_table`: citations are filtered per the snapshot;
  ONE withheld citation empties the answer text and sets
  `answer_withheld: true`; a misaligned title/id pair drops every title in
  it and counts it; `skip_notices` become `skipped_documents`. Covers "A
  title is scrubbed by its paired id, not by title text", "A misaligned
  title/id pair drops every title in that list", "A withheld citation
  withholds the whole answer text", "No withheld citation discloses the
  answer normally", and "Skip notices are reported separately from
  withheld" (query's contribution). **RED today**: `AttributeError` —
  `gate.disclose_query` does not exist. Kills keeping the answer text when
  any citation is withheld.
- [x] **9.3** [TEST] `tests/unit/mcp/test_server.py` —
  `test_tool_error_table_ordered_and_fixed_message`: each of
  `OllamaUnavailable`, `OllamaModelNotFound`,
  `OllamaEmbeddingDimensionMismatch`, `FtsUnavailable`, `OllamaError`,
  `ConceptNotFound`, and any other `OSError`/`WorkspaceReadError` maps to
  its code and `retryable` flag from Decision 12's table with a fixed
  message (never `str(exc)`); a pairwise check confirms the table is
  ordered subclass-before-superclass (e.g. `OllamaModelNotFound` before
  `OllamaError`). Covers "An unreachable Ollama server is reported as
  retryable" and "A missing concept is reported as not retryable" (full
  table). **RED today**: the `query` tool and its Ollama exception mapping
  do not exist yet. Kills moving `OllamaError` above one of its subclasses
  in the lookup order, which would mis-map the subclass to the
  superclass's code.
- [x] **9.6** [TEST] `tests/unit/mcp/test_gate.py` —
  `test_stale_index_only_on_query`: `query`'s result carries a
  `stale_index` warning when a derived store is stale; `get`/`navigate`/
  `pending`'s results never do, even under the identical stale-store
  condition; `in_flight_write` appears on every one of the four tools.
  Covers "A stale derived store warns only on query" and "get, navigate,
  and pending do not report stale_index". **RED today**: the `query` tool
  does not declare `stale_reads=("fts",)` yet. Kills declaring
  `stale_reads` on a tool other than `query`.
- [x] **9.7** [TEST] `tests/unit/mcp/test_enumeration_guard.py` — extend
  `GUARD_MATRIX` with a `query` row: a disclosable question, each canary
  id surfaced as if it were a citation target (via the fake retrieval
  fixture), a missing id, invalid arguments, and each `Ollama*`/
  `FtsUnavailable` error injected with the canary in its message; add the
  positive control (flag on, local backend, canary content reaches the
  fake LLM's echoed answer). Covers "Every real tool passes the guard on
  every path" (query's contribution, completing the guard over all four
  tools). **RED today**: `query` is not registered — the registry/matrix
  set equality fails. Kills leaving any `Ollama*` injection out of the
  matrix.
- [x] **9.8** [TEST] `tests/unit/mcp/test_stdio_subprocess.py` —
  `test_query_ollama_unavailable_over_stdio` (`cross_platform_smoke`,
  `OLLAMA_HOST=http://127.0.0.1:9`): a real subprocess's `query` call
  against the poisoned host returns a tool result with `isError: true`,
  `structuredContent.error.code == "ollama_unavailable"`,
  `retryable: true`. **RED today**: `query` is not registered. Kills a
  mapping that reports `retryable: false` for `OllamaUnavailable`, or
  fails to catch the connection error at all.
- [x] **9.9** [IMPL] `src/openkos/mcp/tools.py` — register the `query`
  tool: `run()` builds `cfg`/`llm`/`embedder` via
  `ctx.make_llm`/`ctx.make_embedder`, resolves `local_exemption` only when
  `ctx.expose_confidential`, calls `run_query` with
  `include_confidential=False` always, `progress` threaded from the
  request's `progressToken`; the `inputSchema` (`question` required
  `minLength` 1, `limit` optional `minimum` 1, defaulting to 5) per
  Decision 15; wire the full `Ollama*`/`FtsUnavailable` exception table
  into `server.py`'s tool-error mapping, ordered subclass-first. Covers
  "Each tool calls its underlying service unmodified" (query's
  contribution). Makes 9.1, 9.3, 9.6, 9.8 GREEN.
- [x] **9.10** [IMPL] `src/openkos/mcp/gate.py` — add `disclose_query` per
  design Decision 9. Makes 9.2 GREEN.
- [x] **9.12** [IMPL] `tests/unit/mcp/canary.py` — the `query` guard-matrix
  row and its fake-LLM-based positive control. Makes 9.7 GREEN.

### Cancellation and progress notifications (design Decision 13)

- [x] **9.4** [TEST] `tests/unit/mcp/test_server.py` —
  `test_cancellation_abandons_worker_and_unblocks_later`: an in-flight
  `query` call (blocked via a fake LLM held on a `threading.Event`) is
  cancelled via `notifications/cancelled`; no response is ever sent for
  it; a subsequent `ping` and `tools/call` are answered normally while the
  cancelled worker is still running; releasing the `Event` afterward
  produces no late write. Covers "A cancelled query sends no response"
  and "A cancelled request does not block later requests". **RED today**:
  cancellation is not wired to a real tool worker yet (slice 3's skeleton
  has the in-flight map but no cancel-then-still-serve proof against a
  real blocking call). Kills writing a response after `CancelledError` is
  caught.
- [x] **9.5** [TEST] Same file —
  `test_progress_notifications_monotonic_and_gated_by_token`: a `query`
  call with `_meta.progressToken` receives `notifications/progress` with
  strictly increasing progress values, all written before the final
  response; a `query` call with no token receives none; `get`/`navigate`/
  `pending` never emit progress even when a `progressToken` is present; a
  progress event that arrives after the response is written (simulated
  via a late callback invocation) is dropped by the monotonic/finished
  guard. Covers "A progressToken produces progress notifications", "No
  progressToken means no progress notifications", and "get, navigate, and
  pending never emit progress". **RED today**: the `query` tool does not
  exist yet to carry a `progressToken`. Kills sending a progress
  notification with no token present, and omitting the "already finished"
  guard.
- [x] **9.11** [IMPL] `src/openkos/mcp/server.py` — wire
  `notifications/cancelled` to `task.cancel()` for the in-flight map, the
  FIFO-ordered progress-then-result write via `call_soon_threadsafe`, and
  the monotonic/finished progress guard, per Decision 13. Makes 9.4–9.5
  GREEN.

### Slice 9 verification

- [x] **9.13** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [x] **9.14** Run `uv run pytest` (unpiped) — must be green, including
  the `cross_platform_smoke` subprocess test.
- [x] **9.15** Run `uv run python evals/run_self_tests.py` — must be
  green.
- [x] **9.16** Commit as one or more work-unit commits — scope `mcp`, e.g.
  `feat(mcp): add the query tool, cancellation, and progress
  notifications`; tests alongside their behavior. Open PR 9 targeting
  PR 8's branch.

---

## Slice 10 (PR 10 → PR 9's branch): docs

- [x] **10.1** [IMPL] `docs/cli.md` — add the `mcp` entry: flags, a client
  configuration example (a stdio launch command), the flag-off
  completeness note (MCP answers are less complete than `openkos query`
  on a local backend when the flag is off), and the per-object prose rule
  (a disclosable object's text is shown as written, per Product decision
  P1).
- [x] **10.2** [IMPL] `docs/architecture.md` — add the `mcp/` package to
  the shipped tree/description.
- [x] **10.3** [IMPL] `docs/roadmap.md` — update MVP 3 status to reflect
  the `mcp` adapter's landing.
- [x] **10.4** [CHECK] Run `uv run pytest tests/unit/cli/test_adr_index.py`
  (or wherever the ADR-index/help-text checks live) and the full
  doc-adjacent test set — confirm green, unaffected beyond the new doc
  content.

### Slice 10 verification

- [x] **10.5** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [x] **10.6** Run `uv run pytest` (unpiped) — must be green.
- [x] **10.7** Run `uv run python evals/run_self_tests.py` — must be
  green.
- [x] **10.8** Commit as one or more work-unit commits — scope `docs`,
  e.g. `docs: document the openkos mcp verb and MVP 3 status`. Open PR 10
  targeting PR 9's branch.

Once PR 10 merges, the change is ready for `openspec` archive, which flips
ADR-0020, ADR-0021, ADR-0027, and ADR-0028 to `Accepted` in both frontmatter
and body, and updates their `docs/adr/README.md` rows — not addressed by
any task in this file, per `openspec/config.yaml`'s archive-only
ADR-acceptance rule.

## Orchestrator note (2026-09-26)

Tasks 1.8 and 2.8 rely on `git add -p`, which is interactive and unsupported in this environment. **Both ADR-0027 and ADR-0028 (Proposed) and both of their README rows ship together in slice 1.** Slice 2 stages no ADR file.
