# Design: Curate Decisions As Data

## Technical Approach

A new narrow synchronous service, `src/openkos/application/curate_decisions.py`,
owns the *apply half* of a curation decision: preview one item, commit one chosen
action against the preview, and (from S4) list every open decision and apply one by
`decision_key` plus a version token. It follows the `merge_service` precedent
(`MergePorts`, `MergeObserver`, typed refusals on `write_gate`) and ADR-0018: no
`engine.py`, no async, no `openkos.cli` / `typer` / `rich` / `openkos.vcs` imports.

The CLI `curate` walks in `src/openkos/cli/curate.py` become the first client. They
keep everything that is *not* the per-item write: the stage engine (`Stage`,
`StageProbe`, `run_curate`, `gate`), the probes, judging and its cost gate, the
prompts (`_confirm_identity`, `_confirm_item`, `_confirm_choice`,
`_confirm_direction`, `_confirm_recommended`), the preview rendering, the counting,
`ctx.partial_progress`, and the mapping of typed refusals to exit codes. Only the
inlined write sequencing moves:

- Identity: `_identity_write_one` (curate.py:1387) and the keep-distinct block
  (curate.py:1772-1792).
- Structure: the relate block of `_structure_run` (curate.py:2194-2270).
- Metadata: the tier block of `_metadata_run` (curate.py:2518-2567).

The CLI keeps its in-process `prepared` baselines across the prompt exactly as today,
so its drift window is still guarded by the drift check against those baselines. A
stateless caller (the desktop app, later) cannot hold them, so the surface also
offers a token path: the preview carries a `DecisionVersion`, and a commit that
passes one is re-validated against the queue row inside the ADR-0036 commit section
and against the plan's bytes before any model call. A mismatch returns a typed
`Stale` outcome and writes nothing.

This maps to the proposal's approach 2 (vertical slices, identity first, generic list
last) and the `curate-decisions` / `curate-command` capabilities the spec phase is
writing.

### Corrections to the proposal, from reading the code

These are design-level adjustments. None of them reopens a binding scope decision.

1. **`--auto-merge` does not move onto the new apply.** It writes through
   `application/auto_merge.py::apply_auto_merges` (auto_merge.py:429), which holds
   one commit section for the whole pass and makes **one commit per run**
   (ADR-0049). Routing it through a per-item commit would change the commit count.
   It is already an application service, so it stays as is. Only accept-recommended
   (`_offer_accept_recommended`, curate.py:1274), which today calls
   `_identity_write_one`, moves onto `commit_merge_decision`.
2. **`_record_identity_decline_from_walk` is not deleted.** `adjudicate --apply`
   calls it too (main.py:2721), *without* a commit section or a member-presence
   check, while curate wraps it in both (curate.py:1787-1791). The helper stays as
   the single adapter for both walks, and its body delegates to the service's
   `record_keep_distinct`. Curate calls `commit_keep_distinct` (section, presence,
   record). Because a body that `adjudicate --apply` runs changes, S0 adds an
   `adjudicate --apply` `d` golden.
3. **S2 does not deduplicate against `apply_relation_suggestions`.** That walk
   (suggest_relations_service.py:792) prepares *inside* the commit section, skips an
   `already_present` relation without writing, has no endpoint-presence check, prints
   no commit disclosure and refreshes derived state once at the end. Curate prepares
   *before* the prompt, checks presence twice, has no `already_present` branch and
   discloses every commit. Merging the two would change one verb's output. Both
   already share the lifecycle cores (`prepare_relate`, `recompose_relate_log`,
   `relate_core`), and that sharing stays. The duplication is recorded as a
   follow-up, not removed.
4. **The commit-phase guard order is curate's, not `merge_concepts`'.** Curate runs
   drift, then catalog recompose, then read-drift (curate.py:1435-1453).
   `merge_concepts` runs drift, then read-drift, then recompose
   (merge_service.py:381-402). When two guards would both fire, the order decides
   which message prints, so the service preserves curate's order.

## Architecture Decisions

### Decision 1: One module, `application/curate_decisions.py`

**Choice**: One new module named `curate_decisions.py`, beside `curate_queue.py`
(reads and enqueues), `queue_resolution.py` (row resolution) and
`pending_queue_report.py` (the LLM-free report).

**Alternatives considered**:
- `application/decisions.py`, rejected because it collides in meaning with
  `bundle/decisions.py` (the keep-distinct and contradiction decline sidecars).
- One module per kind (`identity_decisions.py` and so on), rejected because the
  protocol (version, outcomes, ports, consent table) is shared and would need a
  fourth module anyway.
- Extending `curate_queue.py`, rejected because that module is read and enqueue
  only, and is imported by the daemon's producers. Keeping write sequencing out of
  it preserves that boundary.

**Rationale**: The name says what it holds (screaming architecture) and sits next to
its read-side sibling. If it outgrows one file after S4, it can split per kind
behind the same public names.

### Decision 2: Two-phase preview/commit, with the CLI keeping in-process baselines

**Choice**: Each actionable kind has a pure `preview_*` that returns a frozen preview
carrying the prepared plan, and a `commit_*` that takes that preview. The CLI calls
`commit_*(preview)` with no `expected` version, so the drift guard compares against
the baselines read before the prompt, exactly as today. A stateless caller goes
through `apply_decision(...)`, which re-previews, compares the result with the
caller's `DecisionVersion`, and then calls the same `commit_*`. Inputs the re-preview
needs beyond the token come from `DecisionPolicy` (Decision 7).

**Alternatives considered**:
- **Re-prepare at apply time only.** Rejected because a preview-to-write change
  would then be silently absorbed into a new plan the human never saw. This is the
  failure the exploration names.
- **Hold the workspace lock from preview to commit.** Rejected by ADR-0036: the lock
  covers commit phases only, never human time.
- **Make the CLI use the token path too.** Rejected because the CLI would gain a
  re-preview (a whole-bundle scan per merge), and a row superseded by a concurrent
  daemon pass would start returning `Stale` where curate merges today. That breaks
  byte identity.

**Rationale**: There is one write sequence per kind, with two ways in. The CLI's
human-time window stays guarded by its in-process baselines (`DriftDetected`, exit
3). The stateless caller's human-time window is guarded by the token (`Stale`).

### Decision 3: The version token

**Choice**:

```python
@dataclass(frozen=True)
class DecisionVersion:
    decision_key: str          # pq.PendingItem.decision_key, "<kind>:<body>"
    payload_digest: str | None # the open row's payload_digest; None when no row backs the item
    content_digest: str        # sha256 over the plan's baselines, see below

    def token(self) -> str:    # opaque hex for transport; parsing is deferred to the API stage
        ...
```

- **`payload_digest`** is `pq.payload_digest_for`, which is already the sha256 over
  the payload *and* the ordered input digests (pending_queue.py:247-251). It proves
  that the row, its proposal and its recorded inputs are unchanged.
- **`content_digest`** is the sha256 over sorted `"<role>\t<relative path>\t<sha256
  of bytes>"` lines, built from the plan's own baselines:
  - merge: `merge_drift_targets(layout, prepared, include_catalog=False)` with the
    absorbed file as role `delete`, `prepared.read_dependencies.present`, and the
    names in `read_dependencies.documents`;
  - relate: `prepared.source_bytes` and `prepared.target_bytes`;
  - tier: `prepared.config_bytes`.

  It proves the plan the human saw is the plan that would be written. It is needed
  beside `payload_digest` because the row's input digests are finding digests of
  the members (`current_finding_digest`), not the bytes of every referencing
  document a merge rewrites.
- A `None` `payload_digest` is CLI-only (no queue row backs the item). On the token
  path it is rejected as `Stale("row_resolved")`.
- `decision_key` is the handle. `PendingItem.id` is never used, because
  `findings.db` is a derived cache and its autoincrement id is not stable.
- `version_of(preview)` computes the token lazily. The CLI never calls it, so the
  walk pays no hashing cost.

**Where it is re-validated** (token path only):

1. Before any model call and before the section: re-preview from current bytes. If
   `content_digest` differs, return `Stale(reason="content_changed")`.
2. Inside `ports.commit_section()`, first: re-read the open row by `decision_key`.
   - No open row: `Stale("row_resolved")`.
   - A different `payload_digest`: `Stale("row_superseded")`.
   - Inputs that no longer pass the list's freshness predicate (`curate_queue.row_is_fresh`,
     the public predicate promoted from `_fresh` in S4 with unchanged semantics, for
     identity and relation rows; effective tier equal to `current_default` for
     volatility): `Stale("row_inputs_changed")`.
3. Then the ordinary guards run against the re-preview's baselines. Anything that
   moved between step 1 and the section is a race, and raises `DriftDetected` (exit
   3, retryable), as for every other verb.

The row read happens inside the section because every queue writer
(`upsert_proposal`, `retire_unseen`, `resolve_open_by_key`) enters the same section
(pending_queue.py:297-309), so the check and the write cannot interleave with a
producer.

**Alternatives considered**:
- `payload_digest` alone, rejected because it does not cover a referencing document
  edited after preview.
- Content alone, rejected because it misses a row resolved by another surface (for
  example `duplicates --keep-distinct`) while the app waited.
- An opaque string only, rejected because the caller and the tests need to know
  *which* half moved. The structured type keeps the reason, and `token()` exists for
  transport.

### Decision 4: Outcomes are returned, run-ending refusals are raised

Every outcome is a typed result, returned or raised as a typed exception; none is a
`typer.Exit` and none is printed.

**Choice**:

```python
@dataclass(frozen=True)
class Applied:
    decision_key: str | None
    action: Action
    commit_sha: str | None          # None when the auto-commit degraded (#800)

@dataclass(frozen=True)
class NotApplied:
    action: Action
    reason: Literal["skipped", "member_missing", "record_failed", "not_mergeable",
                    "not_offered"]
    detail: str | None = None

@dataclass(frozen=True)
class Stale:
    decision_key: str
    reason: Literal["content_changed", "row_resolved", "row_superseded", "row_inputs_changed"]

ApplyOutcome = Applied | NotApplied | Stale
```

A merge on an item that is not mergeable (null or non-SAME adjudication) is the
returned `NotApplied("not_mergeable")`, never a raised `Refused`: it is not an error.
An action outside the item's `actions` is `NotApplied("not_offered")`.

| Case | Returned or raised | CLI exit |
|---|---|---|
| Applied | returned `Applied` | 0 |
| Skip, vanished member, keep-distinct record failure, `not_mergeable`, `not_offered` | returned `NotApplied(reason)` | 0 (walk continues; a record failure is fail-open) |
| Token mismatch | returned `Stale(reason)` | never on the CLI path |
| Target drift, catalog recompose failure, read drift | raised `DriftDetected` (message names the check) | 3, run ends |
| Merge, relate or tier write failure | raised `WriteFailed` | 1 |
| Config-commit refusal | raised `Refused` | 1 |
| Workspace busy | raised `WorkspaceBusyError` | 3 |

`DriftDetected` is deliberately not split by cause: its message already says which
check failed, and `write_gate.DriftDetected` is shared by many verbs.

Raised (all subclasses of `write_gate.WriteRefused`, so a service's own
`except (OSError, ValueError)` can never swallow them):

- **`DriftDetected`** (existing). It carries the message composed by
  `drift.describe_drift`, `commit_phase.describe_read_drift` or
  `CatalogRecomposeError`, with the caller-supplied `surface` label. The adapter
  maps it to exit 3, and the run ends.
- **`WriteFailed(Refused)`** (new, in `write_gate.py`). It carries structured fields
  (`operation: Literal["merge", "relate", "set_tier"]`, `subjects: tuple[str, ...]`,
  `error: str`), its cause is chained, and `message` is a neutral rendering. The
  CLI formats its own exact line from the fields. The adapter maps it to exit 1.
- **`Refused`** (existing). Used for the config-commit refusal, whose text the
  adapter port composes. Exit 1.
- **`WorkspaceBusyError`** from entering the section propagates unchanged (the
  guard maps it to exit 3, as today).

**Alternatives considered**:
- Raising `Stale`, rejected because a stale item must not end a walk (it is a
  "re-preview this one", like a vanished member), and a returned union is matched
  exhaustively by mypy, whereas an exception can be caught too broadly.
- Embedding `openkos curate: Identity: failed while merging ...` in the refusal (the
  `merge_service.Refused` precedent), rejected because the surface must be
  UI-neutral. The stage label is the adapter's.
- Restructuring drift messages, rejected because `describe_drift` is shared by more
  than ten verbs. Its text is already composed in `application/` from a `verb`
  parameter, so the surface passes `surface` through and stays honest about it.

### Decision 5: Ports and observer

**Choice**:

```python
@dataclass(frozen=True)
class DecisionPorts:
    autocommit: Callable[[Path, Sequence[str], str], str | None]
    reconcile: Callable[[Path, PreparedMerge], PreparedMerge]
    plan_config_commit: Callable[[Path, str, str, bytes], ConfigCommitPlan | ConfigCommitRefusal]
    autocommit_config_edit: Callable[[Path, str, ConfigCommitPlan | None], str | None]
    commit_section: CommitSection = commit_phase.unlocked_section
    on_warning: Callable[[str], None] = _ignore

class DecisionObserver:              # every method a no-op
    def merge_member_missing(self, survivor: str, absorbed: str) -> None: ...
    def relate_endpoint_missing(self, source: str, target: str) -> None: ...
    def keep_distinct_unrecorded(self, member_ids: tuple[str, ...], error: str) -> None: ...
```

- **Autocommit** stays adapter-side. The CLI passes `lambda *a:
  cli_main._autocommit(*a)`. The lambda binds late, so the
  `monkeypatch.setattr("openkos.cli.main._autocommit", ...)` sites (for example
  test_curate_auto_merge.py:932) keep working. `test_shared_write_helpers_are_never_forked`
  holds, because `_autocommit` keeps its one definition in `cli/main.py`.
- **Commit section.** The CLI passes `lambda: cli_main._commit_section_for(ctx.root)()`.
  This is a `CommitSection` that reads the ContextVar (main.py:440) on *every*
  entry, as the walk does today, so the patch of `_commit_section_for` at
  test_curate_auto_merge.py:1297 keeps working. The default is `unlocked_section`,
  as in `MergePorts`.
- **Reconciliation** is a port, because it makes a model call. The CLI binds
  `cli_main._apply_reconciliation(root, prepared, no_reconcile=ctx.no_reconcile,
  reconcile=ctx.reconcile, verb="curate")`, which prints its own notices
  (main.py:2451-2464) at the same point as today. The preview exposes
  `reconcile_planned` from the one predicate,
  `application_lifecycle.reconcile_planned` (lifecycle.py:3199). The surface never
  calls a model itself.
- **Config commit** is a port, because `_plan_config_commit` reads git
  (main.py:1551-1556, `openkos.vcs`). Today it also echoes and raises `typer.Exit(1)`
  (main.py:1572-1580), so S3 splits it into a decision function that returns the
  plan or the refusal text, plus the existing wrapper, which keeps `set-volatility`'s
  behavior. `ConfigCommitPlan` is opaque to the service.
- **`on_warning`** is passed through to
  `duplicates_service.apply_identity_decision(on_warning=...)`. The CLI wires
  `cli_main._echo_warning`, as `_apply_identity_decision` does today
  (main.py:13218-13220).
- **The observer** carries exactly the three lines that print *inside* the commit
  section today, and fires them at the same point:
  - curate.py:1425-1431, the merge member-missing line;
  - curate.py:2232-2235, the in-section relate drop;
  - main.py:13200-13205, the keep-distinct record failure.

  Lines that are the last thing before the walk's next step are carried on the
  outcome and printed by the adapter where they print today:
  - `committed as <sha>`, after the section exits (curate.py:1471, 2268, 2565);
  - the pre-prepare relate drop (curate.py:2199-2204), which fires outside the
    section.

  A keep-distinct whose member vanished stays silent, as it is today
  (curate.py:1788). Keep-distinct writes the ruling sidecar and commits through
  `ports.autocommit`, as `_record_identity_decline_from_walk` does today.

**Rule, stated for reviewers**: an observer event fires where a line prints *before
further work in the same sequence*. A result that ends the sequence is returned.
Either way the byte stream and its order are unchanged, and the S0 goldens are the
proof.

**Alternatives considered**:
- Reusing `MergePorts`, rejected because it lacks the config ports and carries
  `has_reset_point`, `clock` and `after_commit`, which curate does not use per item.
  Curate's derived refresh is run-scoped in its command, not per merge.
- Passing `cli_main._autocommit` directly, rejected because it binds early and
  breaks the monkeypatch sites.

### Decision 6: Consent policy as data (S4)

**Choice**: In `curate_decisions.py`:

```python
class BulkEligibility(StrEnum):
    NEVER = "never"
    SYMMETRIC_ONLY = "symmetric_only"
    ALWAYS = "always"

BULK_ELIGIBILITY: Final[Mapping[DecisionKind, BulkEligibility]] = MappingProxyType({
    "identity": BulkEligibility.NEVER,
    "relation_type": BulkEligibility.SYMMETRIC_ONLY,   # asymmetric types ask per item (#624); related_to is bulk (#1222)
    "volatility": BulkEligibility.ALWAYS,
    "contradiction": BulkEligibility.NEVER,
    "revision": BulkEligibility.NEVER,
    "watch_refusal": BulkEligibility.NEVER,
})

def spend_consent(*, auto: bool, interactive: Callable[[], bool]) -> Literal["granted", "ask", "refused"]: ...
def write_walk_consent(*, writes: bool, interactive: Callable[[], bool], accepted: bool,
                       unattended_identity: bool) -> Literal["walk", "refused"]: ...
def item_consent(kind: DecisionKind, *, accepted: bool, asymmetric: bool,
                 interactive: Callable[[], bool]) -> Literal["bulk", "ask", "withheld"]: ...
```

- `gate()` (curate.py:806) prints its disclosures unchanged, then branches on
  `spend_consent`. `run_curate`'s write refusal (curate.py:3098-3124) branches on
  `write_walk_consent`. `_confirm_item` (curate.py:745-749) branches on
  `item_consent`.
- `interactive` is a thunk, so `sys.stdin.isatty()` is evaluated only on the
  branches that read it today. For example, `gate` does not read it under `--auto`
  (curate.py:841-843). The functions stay pure: the thunk is the only I/O, and the
  adapter supplies it.
- `item_consent("identity", ...)` returns `"ask"` for every input. Identity still
  never routes through `_confirm_item`. The table is the second, data-level barrier.
- `Stage.auto_acceptable` stays a field, because tests construct `Stage`. `_STAGES`
  derives it from `BULK_ELIGIBILITY[...] is not NEVER` for the three write stages,
  and a pinned test asserts the equality for every stage.
- **Accept-recommended and `--auto-merge` stay outside the table.** They are
  separately specified identity consents (#1298, ADR-0049): one y/N over fresh
  in-class SAME groups on a TTY, and the opt-in measured class under `--auto`. They
  are not generic bulk. `--accept identity` and `review: false` still cannot reach
  identity (`parse_accepted_stages`, curate.py:595-602; `resolve_accepted_stages`,
  curate.py:626).

**Alternatives considered**:
- Deleting `Stage.auto_acceptable`, rejected because it means needless churn in
  tests that construct `Stage`.
- Folding accept-recommended into the table as `SAME_CLASS_ONLY`, rejected because it
  would make the one-answer identity consent look like generic bulk, which is
  exactly the confusion the "never bulk" rule exists to prevent.
- A name such as `application/consent.py`, rejected because that module already
  exists (confirmation request types), and the policy is curate-specific.

### Decision 7: The list (S4) and per-kind action sets

**Choice**: `list_open_decisions(layout) -> tuple[DecisionItem, ...]`. It is
LLM-free, takes no lock and writes nothing. It reads every kind in `pq.KINDS` through
`curate_queue.read_open_rows`, and marks freshness with the same predicates the
typed readers use. It does not hide stale rows.

```python
DecisionKind = Literal["identity", "relation_type", "volatility",
                       "contradiction", "revision", "watch_refusal"]

class Action(StrEnum):
    MERGE = "merge"
    KEEP_DISTINCT = "keep_distinct"
    SKIP = "skip"
    RELATE = "relate"
    RELATE_REVERSED = "relate_reversed"
    SET_TIER = "set_tier"

@dataclass(frozen=True)
class WithheldAction:
    action: Action
    reason: Literal["pending_evaluation", "judged_different", "cross_type",
                    "more_than_two_members", "no_valid_suggestion"]

@dataclass(frozen=True)
class DecisionItem:
    decision_key: str
    kind: DecisionKind
    targets: tuple[str, ...]
    fresh: bool
    evaluation: Literal["judged_same", "judged_different",
                        "pending_evaluation", "not_applicable"]
    actions: tuple[Action, ...]
    withheld: tuple[WithheldAction, ...]
    resolves_via: str               # pending_queue_report.row_resolving_command(row, inbox, bundle_dir)
    payload: Mapping[str, object]   # owner-local: may derive from confidential concepts
    payload_digest: str
```

| Kind | Actions | Withheld | Notes |
|---|---|---|---|
| `identity`, judged SAME, 2 members, same type | merge, keep_distinct, skip | none | The only mergeable identity item (binding decision 1) |
| `identity`, `adjudication: null` | keep_distinct, skip | merge: `pending_evaluation` | Judging is out of scope (binding decision 3) |
| `identity`, judged DIFFERENT and still open | keep_distinct, skip | merge: `judged_different` | Normally resolved `declined` by `_persist_adjudications` |
| `identity`, cross-type pair | keep_distinct, skip | merge: `cross_type` | `resolves_via` names `merge --include-cross-type` (pending_queue_report.py:159-171) |
| `identity`, more than 2 members | keep_distinct, skip | merge: `more_than_two_members` | Curate prints pairwise commands |
| `relation_type` with a valid type | relate, skip, plus relate_reversed for an asymmetric type | none | A `supersedes` row has no actions; it resolves via `openkos relate` (pending_queue_report.py:180-186) |
| `volatility` with a valid tier | set_tier, skip | none | |
| `contradiction`, `revision`, `watch_refusal` | none | none | `resolves_via` from `row_resolving_command` (binding decision 2) |

- `SKIP` writes nothing and returns `NotApplied("skipped")`, so the spec rule "a
  presented but unwritten row returns to `pending`" holds for every client.
- There is **no `decline` action** for relation and volatility: today `n` writes
  nothing (flagged as out of scope). The CLI's `n` versus `s` split is only the
  `declined:` tally, and it stays in the adapter.
- The list does not present adjudicator confidence as a trust signal (ADR-0034
  outcome). The payload carries it, as stored, and nothing ranks by it.
- `list_open_decisions` orders deterministically: `pq.KINDS` order, then the queue's
  own order within a kind. Nothing ranks by confidence.
- `DecisionPolicy` is the one object for the inputs a re-preview needs that the token
  does not carry:

  ```python
  @dataclass(frozen=True)
  class DecisionPolicy:
      no_reconcile: bool = False      # default; the caller MAY override
      reconcile: bool = False         # default; the caller MAY override
      now: datetime | None = None     # None: read once per call from the clock
  ```

  Where each input comes from on the stateless path: the `decision_key` is in the
  token; the member ids, relation endpoints and type, and proposed tier come from the
  open row's payload; the ordered merge pair is derived from those member ids by
  `ordered_merge_pair`; `no_reconcile`, `reconcile` and `now` come from
  `DecisionPolicy` defaults. A caller holding only the token and the `decision_key`
  therefore commits with `DecisionPolicy()`. None of the three policy fields enters
  the token, so a different policy never causes `Stale`. The CLI does not use
  `DecisionPolicy`: it passes the same values as explicit arguments to
  `preview_merge` and `preview_relate`.
- `apply_decision(root, layout, version, action, *, policy=DecisionPolicy(), ports,
  observer, surface)` re-lists by `decision_key` (from `version`), returns
  `NotApplied("not_mergeable")` for a merge on an unjudged item and
  `NotApplied("not_offered")` for any other action not in the item's `actions`,
  re-previews, then follows the token path (Decision 3).

**Alternatives considered**:
- Hiding stale rows, rejected because `pending` lists them, and a client that hid
  them would disagree with it.
- Listing only actionable kinds, rejected by binding decision 2.

### Decision 8: Sensitivity boundary

**Choice**: The surface is owner-local. Its docstring says so, and an AST test (S4)
asserts that no module under `src/openkos/mcp/` or `src/openkos/api/` imports
`openkos.application.curate_decisions`. The guard tolerates a missing
`src/openkos/api/` (it does not exist yet). `mcp/` keeps its reduced `pending`
projection.

**Rationale**: `payload` (`rationale`, `conflicting_claims`) can derive from
confidential concepts when a producer ran under `local_exemption`. A future adapter
must bring its own disclosure gate.

## Data Flow

### CLI identity merge, after S1b

```
cli/curate.py walk            application/curate_decisions.py           ports (cli/main.py)
------------------            -------------------------------           -------------------
ordered_merge_pair, cross-type skip line           (unchanged, adapter)
preview_merge(root, layout, group, ordered_pair) ──▶ prepare_one_merge
   ◀── MergeDecisionPreview | NotApplied(not_mergeable)   [raises WriteFailed → exit 1]
render preview lines (_format_merge_preview_line, survivor, notes)   (unchanged)
_confirm_identity(...) == "yes"
commit_merge_decision(preview, ports, observer, surface="curate")
                               ├─ ports.reconcile ─────────────────────▶ _apply_reconciliation (prints notices)
                               ├─ with ports.commit_section(): ────────▶ _commit_section_for(root)()  [lock]
                               │    members present? no ─ observer.merge_member_missing ─▶ stderr line
                               │                          return NotApplied(member_missing)
                               │    describe_drift(...)          → raise DriftDetected
                               │    recompose_merge_catalog      → CatalogRecomposeError → DriftDetected
                               │    describe_read_drift(...)     → raise DriftDetected
                               │    merge_service.commit_merge(autocommit=ports.autocommit) ─▶ _autocommit
                               │         OSError/ValueError      → raise WriteFailed
                               └─ (section exits)                                              [unlock]
   ◀── Applied(commit_sha)
_echo_commit_disclosure(sha, prefix="  ") when sha is not None
except DriftDetected: echo(message, err) ; Exit(3)  — run ends (design D6 of curate)
except WriteFailed:   echo("openkos curate: Identity: failed while merging A into B -- err.", err) ; Exit(1)
```

### Stateless apply by token, from S4

```
client                       curate_decisions                               queue / bundle
------                       ----------------                               --------------
list_open_decisions(layout) ───────────────────────────────────────────▶ read_open_rows × 6 kinds
   ◀── DecisionItem(decision_key, payload_digest, actions, fresh, ...)
preview_decision(key, action) ── re-read row, prepare ──▶ Preview(version=DecisionVersion)
      ... human time, no lock ...
apply_decision(version, action)
   ├─ re-list item by key; action ∈ actions? else Refused
   ├─ re-preview; content_digest == version.content_digest? else return Stale(content_changed)
   ├─ (identity) ports.reconcile
   ├─ with ports.commit_section():
   │     open row by key? payload_digest equal? inputs fresh?  else return Stale(row_*)
   │     the same guards and write as the CLI path              → DriftDetected / WriteFailed
   └─ return Applied(commit_sha)
```

Queue resolution is not called by the surface. The write cores already resolve the
row: `merge_core` → `resolve_merged`, `relate_core` → `resolve_related`,
`set_volatility_core` → `resolve_volatility_set`, and `apply_identity_decision` →
`resolve_declined_identity` (`WRITE_CORE_RESOLUTIONS`, queue_resolution.py:53-66).
The new module defines no `*_core` function, so the registry needs no entry.

## File Changes

| File | Action | Slice | Description |
|---|---|---|---|
| `tests/unit/cli/test_curate_characterization.py` | Create | S0a/S0b | Full-stream goldens (stdout, stderr, exit code, commits made, rows resolved) |
| `tests/unit/cli/fixtures/curate_characterization_goldens.json` | Create | S0a/S0b | Recorded on the current tree before anything moves |
| `tests/unit/cli/test_curate_consent_matrix.py` | Create | S0b | stage × TTY × `--auto` × `--accept` × `--auto-merge` → (gate, write-walk, per-item) |
| `tests/unit/cli/golden_support.py` | Modify | S0a | Add a sha normaliser for `committed as <sha>` lines; `Goldens.check` is unchanged |
| `src/openkos/application/write_gate.py` | Modify | S1a | Add `WriteFailed(Refused)` with structured fields |
| `src/openkos/application/curate_decisions.py` | Create | S1a→S4 | Version, outcomes, ports, observer (S1a); keep-distinct (S1a); merge (S1b); relate (S2); tier (S3); list, apply, consent (S4) |
| `src/openkos/application/curate_queue.py` | Modify | S1b/S4 | A read-only `open_row_by_key(layout, decision_key)` for the in-section token check; `_fresh` is promoted to the public `row_is_fresh` (same semantics, the old name kept as an alias for existing callers) and shared with the list |
| `src/openkos/cli/curate.py` | Modify | S1a→S4 | The walks call the surface; add `_decision_ports(ctx)`, `_CurateDecisionObserver` and refusal-to-exit mapping; `_identity_write_one` is removed in S1b; consent functions wired in S4 |
| `src/openkos/cli/main.py` | Modify | S1a/S3 | `_record_identity_decline_from_walk` delegates to `record_keep_distinct` (S1a); `_plan_config_commit` splits into a decision function and the existing exit wrapper (S3) |
| `tests/unit/application/test_curate_decisions.py` | Create | S1a→S4 | Unit tests per function, RED first |
| `tests/unit/application/test_layering.py` | Modify | S1a | Scans the new module automatically (`_application_modules()`); S1a adds an AST assertion that `application/curate_decisions.py` never calls `isatty`. No existing check is weakened |
| `tests/unit/mcp/` or `tests/unit/application/` boundary test | Create | S4 | No `mcp/` or `api/` import of `curate_decisions` |
| `openspec/changes/curate-decisions-as-data/design.md` | Create | design | This document |
| `docs/adr/0053-curation-decisions-are-a-two-phase-application-surface.md`, `docs/adr/README.md` | Create / Modify | design | ADR-0053, status Proposed |
| `docs/architecture.md` | Possibly modify | S5 (archive) | Only if the `application/` listing names services; no counts |
| `src/openkos/application/auto_merge.py`, `src/openkos/cli/daemon.py` | Unchanged | — | See correction 1; `persist_findings` is not moved |

## Interfaces / Contracts

```python
# src/openkos/application/curate_decisions.py (public names; signatures abridged)

@dataclass(frozen=True)
class MergeDecisionPreview:
    prepared: PreparedMerge
    member_ids: tuple[str, ...]
    decision_key: str               # pq.stored_decision_key("identity", pq.identity_key(member_ids))
    reconcile_planned: bool         # lifecycle.reconcile_planned(prepared, no_reconcile=..., reconcile=...)
    cross_source_same_pair: bool
    cross_type_concern: str | None
    survivor_criterion: str

@dataclass(frozen=True)
class RelateDecisionPreview:
    prepared: PreparedRelate
    source_id: str
    target_id: str                  # already reversed when the action is RELATE_REVERSED
    relation_type: str
    decision_key: str               # pq.relation_type_key(...) is unordered, so reversal keeps the key

@dataclass(frozen=True)
class TierDecisionPreview:
    prepared: PreparedSetVolatility
    type_name: str
    tier: str
    decision_key: str

def version_of(preview: MergeDecisionPreview | RelateDecisionPreview | TierDecisionPreview,
               *, layout: WorkspaceLayout, payload_digest: str | None) -> DecisionVersion: ...

def preview_merge(root, layout, group, *, ordered_pair, no_reconcile, reconcile) -> MergeDecisionPreview | NotApplied: ...
def commit_merge_decision(root, layout, preview, *, ports, observer, surface,
                          expected: DecisionVersion | None = None) -> ApplyOutcome: ...

def record_keep_distinct(root, layout, member_ids, *, ports) -> Applied | NotApplied: ...   # no section (adjudicate)
def commit_keep_distinct(root, layout, member_ids, *, ports, observer) -> Applied | NotApplied:  # section + presence
    ...

def preview_relate(root, layout, suggestion, *, reverse: bool, now: datetime) -> RelateDecisionPreview | NotApplied: ...
def commit_relate_decision(root, layout, preview, *, ports, observer, surface,
                           expected: DecisionVersion | None = None) -> ApplyOutcome: ...

def preview_set_tier(layout, type_name, tier) -> TierDecisionPreview: ...
def commit_tier_decision(root, layout, preview, *, ports, surface,
                         expected: DecisionVersion | None = None) -> ApplyOutcome: ...

def list_open_decisions(layout) -> tuple[DecisionItem, ...]: ...                 # S4
def preview_decision(root, layout, decision_key, action, *, policy=DecisionPolicy()) -> ...: ...  # S4
def apply_decision(root, layout, version, action, *, policy=DecisionPolicy(), ports, observer, surface) -> ApplyOutcome: ...  # S4
```

Contract notes:

- Every lifecycle call goes through the module attribute
  (`application_lifecycle.relate_core(...)`, `merge_service.commit_merge(...)`),
  never a `from ... import`. The existing patches of
  `openkos.application.lifecycle.merge_core` and `relate_core`
  (test_adjudicate.py:2738, test_curate_lock.py:416-421) depend on that.
- **Where each kind prepares.** For a merge, the CLI calls `preview_merge` *before*
  its prompt, because the preview lines render from `prepared` (curate.py:1712-1765).
  For a relate or a tier, the CLI calls `preview_relate` and `preview_set_tier`
  *after* its prompt. That is where `prepare_relate` and `prepare_set_volatility`
  run today (curate.py:2197-2215, 2518-2521), because the prompt renders from the
  suggestion alone. The call order, and so the output order, is unchanged.
  `preview_relate`'s pre-prepare presence check returns
  `NotApplied("member_missing")`, and the adapter prints `_echo_dropped_edge` as it
  does today.
- `now` for relate is passed in once per stage run (curate.py:2108), so the stage
  keeps one timestamp.
- The relate commit paths and messages are byte-identical to curate.py:2256-2264.
  The tier message is `f"openkos: set-volatility {type} -> {tier}"`. The merge
  message comes from `merge_service.merge_commit_message`. The keep-distinct message
  is `f"openkos: keep distinct {'/'.join(member_ids)}"` (main.py:13199).
- **Exit mapping lives only in `cli/curate.py`:**
  - `DriftDetected` or `WorkspaceBusyError`: exit 3, and the run ends.
  - `WriteFailed` or `Refused`: exit 1, after printing the curate-formatted line.
  - `NotApplied("member_missing")`: the walk counts a skip.
  - `NotApplied("not_mergeable")` and `NotApplied("not_offered")`: the walk counts a
    skip; they are returned, not raised.
  - `NotApplied("record_failed")`: fail-open, and the walk continues.
  - `Stale`: never produced on the CLI path.

  `ctx.partial_progress` stays in the stage engine.

## Testing Strategy

Strict TDD applies from S1a, with runner `uv run pytest`. S0 is characterization: it
records the current tree, so it is green by design and has no RED phase. Every
golden is still proven able to fail before it is trusted: mutate the line it pins
and see it go red, then revert byte-exactly and purge `__pycache__`.

| Layer | What to test | Approach |
|---|---|---|
| Characterization (S0a) | Identity y/s/d; accept-recommended; `--auto-merge`; member vanished; drift exit 3 (identity); write failure exit 1 (prepare and write); keep-distinct record failure (fail-open); non-TTY refusal; partial availability (backend down mid-batch with `partial_progress`); `adjudicate --apply` `d` | `CliRunner`, full stdout, stderr and exit code, plus commits made and queue rows resolved, through `golden_support.Goldens` with `normalise` and a sha normaliser. Pin the environment: git identity through `GIT_CONFIG_COUNT`, `isatty` patched, no ambient `OLLAMA_HOST`. Reuse the existing curate test fixtures and monkeypatch targets |
| Characterization (S0b) | Structure y/n/s/a/r (with `--accept structure` for `a`/`r`); in-section endpoint drop; Structure drift exit 3 and write exit 1; Metadata y/n/a; Metadata drift exit 3; dirty `openkos.yaml` config-commit refusal exit 1 | Same harness |
| Consent matrix (S0b) | stage × TTY × `--auto` × `--accept` (none / structure / metadata) × `--auto-merge` × `review` | Parametrized; asserts the gate result, the write-walk refusal notice and whether a per-item prompt was reached. Unchanged across S1a–S5; S4 adds a pure-function twin |
| Unit (S1a–S4) | Each `preview_*` and `commit_*`: the guard order; `NotApplied` and `Stale` variants; `WriteFailed` fields and cause; ports called in order (reconcile before the section; autocommit inside it); observer events inside the section; no model call | `tests/unit/application/test_curate_decisions.py` with recording fakes for ports, `unlocked_section`, and a section fake that records entry and exit |
| Unit (S4) | The `BULK_ELIGIBILITY` pin (identity NEVER); `item_consent("identity", …)` is `"ask"` for all inputs; `Stage.auto_acceptable` equals the table; list across six kinds including stale, `adjudication: null`, cross-type, N>2, `supersedes`; token round trip; `Stale` for each reason; a token commit writes nothing on `Stale` | Pure tests plus a temp workspace with a seeded `findings.db` through `pq.upsert_proposal` |
| Guards | Layering (no `cli`, `typer`, `rich` or `vcs` imports; an AST assertion, added in S1a, that `curate_decisions.py` never calls `isatty`; shared helpers keep one definition); the registry guard; AST guards on `cli/curate.py` (`test_chat_timeout_wiring.py`, `test_backend_construction_guard.py`, `test_neutral_catch_sites.py`); the new `mcp/` and `api/` import guard | Existing tests, run unchanged; the new guard is RED-first by adding a temporary offending import |
| Monkeypatch survival | About 117 `openkos.cli.curate.*` targets: they stay valid because compute code does not move | Per slice, mutate one patched line and confirm a test reddens (an unread monkeypatch fails silently) |
| E2E | None new; the existing `tests/unit/e2e/` runs unchanged | — |

Gates per slice: `uv run pytest --cov` (branch coverage of at least 90%),
`uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy .`, and
`uv run python evals/run_self_tests.py`, each run directly and never piped through
`tail`.

## Slices (confirmed, with file scope)

About 400 authored lines per PR is advisory only. Tests and docs are never cut to
fit it.

| Slice | Scope | Files | Estimate |
|---|---|---|---|
| S0a | Identity-side goldens and the `adjudicate --apply` `d` golden | `test_curate_characterization.py`, goldens JSON, `golden_support.py` | ~350 |
| S0b | Structure and Metadata goldens, plus the consent matrix | same files, plus `test_curate_consent_matrix.py` | ~350 |
| S1a | `WriteFailed`; module skeleton (version, outcomes, ports, observer); `record_keep_distinct` / `commit_keep_distinct`; the curate `d` and skip paths rewired; `_record_identity_decline_from_walk` delegates | `write_gate.py`, `curate_decisions.py`, `curate.py`, `main.py`, `test_curate_decisions.py` | ~350 |
| S1b | `preview_merge` / `commit_merge_decision`; `_identity_write_one` removed; accept-recommended rewired; `--auto-merge` untouched | `curate_decisions.py`, `curate.py`, `curate_queue.py` (`open_row_by_key`), tests | ~450 |
| S2 | `preview_relate` / `commit_relate_decision`, including reversed; no dedupe with `apply_relation_suggestions` | `curate_decisions.py`, `curate.py`, tests | ~300 |
| S3 | `preview_set_tier` / `commit_tier_decision`; `_plan_config_commit` split | `curate_decisions.py`, `curate.py`, `main.py`, tests | ~300 |
| S4 | `list_open_decisions`, `preview_decision`, `apply_decision`, the consent table and functions wired into `gate`, `run_curate` and `_confirm_item`; the boundary guard | `curate_decisions.py`, `curate.py`, `curate_queue.py`, tests | ~450 |
| S5 | Archive: delta specs merged and name-matched; ADR-0053 Accepted; docs only where the shape changes | `openspec/specs/...`, `docs/adr/...`, maybe `docs/architecture.md` | ~150 |

Every slice leaves `curate` working end to end, and S0's goldens run unchanged at the
end of every slice. ADR-0053 and this design land with the first code PR (S1a)
unless the chain strategy puts them in S0a.

## Threat Matrix

N/A. The change adds no routing, shell command or subprocess, and no new VCS or PR
automation, executable-file classification or process-integration boundary. The
existing `_autocommit` and `_autocommit_config_edit` are invoked through ports with
byte-identical paths and messages (pinned by the S0 goldens), and `openkos.vcs`
stays adapter-side (layering guard).

## Migration / Rollout

No migration is required. There is no change to the queue schema, the on-disk
format, the CLI wording, exit codes or the consent rules. Each slice is
independently revertible. The S0 goldens remain as the guard for any retry.

## Open Questions

- [ ] **Chain strategy** (`stacked-to-main` or `feature-branch-chain`): decided at
  the tasks phase, not here.
- [ ] **Serialized token format**: `DecisionVersion.token()` exists for transport,
  but parsing a token back is deferred to the MVP 6 API stage (ADR-0039). Nothing in
  this change needs it, because the in-process caller passes the dataclass.
- [ ] **Follow-ups to record at archive** (not blocking):
  - "Judging as an application service" (the required next MVP 6 stage);
  - recording Structure and Metadata declines;
  - the relate-sequence duplication between `curate` and
    `suggest-relations --apply`;
  - a non-printing commit adapter for a future API caller.
