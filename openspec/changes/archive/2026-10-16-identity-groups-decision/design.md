# Design: Resolve Identity Groups Through The Decision Surface

Inputs: `proposal.md`, the maintainer's decision on #1430, ADR-0034, ADR-0053,
ADR-0059, ADR-0060, the living `curate-decisions`, `stable-python-api`,
`local-http-api` and `pending-work` specs, and the code named below. Paths are
under `src/openkos` unless they start with `tests/`, `docs/` or `openspec/`.

## Technical Approach

The decision surface already has everything a pairwise merge needs: a pure
preview (`curate_decisions.preview_merge`), a version whose content digest covers
every byte the plan rests on (`version_of`, `_merge_content_lines`), and a token
commit that re-previews, checks the row inside the commit section and writes
through `lifecycle.merge_core` (`commit_merge_decision`). A group decision is that
sequence generalised to N-1 steps over one survivor, with three additions: a
caller-supplied selection that the token must bind, a loop of steps inside one
commit section, and keep-distinct rulings for the members the person did not mark.

Everything new in `application/curate_decisions.py` sits beside its pairwise
counterpart and reuses its checks. `openkos.api` and `openkos.localapi` expose it
additively. No module moves, no core changes shape except one optional keyword on
`lifecycle.merge_core` and `queue_resolution.resolve_merged` (Decision 7).

### Findings from reading the code

These shaped the decisions below; each is cited where it matters.

1. **The surface classifier treats a widened non-`Literal` union as breaking**
   (`tests/unit/api/surface.py`, `_annotation_change`: only a `Literal` widening is
   additive). Adding a plan type to `DecisionPreview.plan`
   (`MergePlan | RelatePlan | TierPlan | KeepDistinctPlan`) or an outcome to
   `ApplyOutcome` would therefore force `API_VERSION` 2. A new enum member, a new
   `Literal` value, a new defaulted dataclass field, a new optional keyword, a new
   public name and a new exception attribute are all additive.
2. **A pairwise merge retires the group's row as `stale`.**
   `queue_resolution.resolve_merged` resolves the row over exactly the merged pair,
   then `_retire_naming` marks every open row naming the absorbed id `stale`
   (`pq.retire_open_naming`). The group's own row names every member, so the
   first step of a group would leave it `stale`, not `applied`.
3. **A keep-distinct ruling matches an exact member set.**
   `pending.is_group_kept_distinct` and the producers' `decline_in_force` compare
   the sorted member set's key, so a ruling over {A, C} does not suppress a later
   group over {A, C, D}, and the reverse.
4. **The scalar `type_alternative` holds one runner-up** (#1293,
   `okf.discarded_absorbed_type`): a survivor that already records one discards a
   later absorbed type, and the pairwise preview names it before consent.
5. **`PreviewRequest` uses `extra="forbid"`.** `_models._EchoRequest` documents
   that `forbid` reaches a nested dataclass and splits its OpenAPI component into
   `-Input` and `-Output` variants. A preview body that carries a nested
   selection must move onto `_EchoRequest`'s top-level rejection.
6. **The local API's error tables are pinned.** `tests/unit/localapi/test_errors.py`
   pins the `NotApplied` reasons and the full set of codes, and `outcome_problem`
   answers a reason missing from `_NOT_APPLIED` with 500 `internal_error`. A new
   `NotApplied` reason and its HTTP row must land in the same slice.
7. **Judging has no group-size or type filter.** `application/judging.py` and
   `identity_adjudication.py` key work by `group_key_for(member_ids)`; nothing
   limits a judged group to two members or one type (grep, 2026-10-16). S3 pins
   this with an end-to-end test rather than relying on the grep.

## Architecture Decisions

### Decision 1: Wire shape: a new `merge_group` action with a selection carried in the version

**Choice.** `Action.MERGE_GROUP = "merge_group"`. A new application dataclass,
re-exported by `openkos.api`:

```python
@dataclass(frozen=True)
class GroupSelection:
    survivor_id: str
    member_ids: tuple[str, ...]  # marked members, survivor included
```

`preview_decision(workspace, key, action, *, selection=None)` takes it.
`DecisionVersion` gains `selection: GroupSelection | None = None`, set on a
merge-group's version only, so `apply_decision(workspace, version, action)` reads
it from the version and gains no parameter. The selection is canonicalised before
it enters the version: `member_ids` deduplicated and sorted.

`merge_group` is offered only where `merge` is withheld for `more_than_two_members`
or `cross_type`, and `merge` stays withheld there with the same reason
(`_identity_actions`). A client that does not know `merge_group` sees exactly
what it sees today.

**Alternatives.**

| Option | Why not |
|---|---|
| Reuse `merge` with optional `survivor_id`/`member_ids` | `merge` would be offered or withheld depending on parameters the list cannot show, and the existing "merge withheld: `more_than_two_members`" contract that clients already render would become false. |
| A separate action per shape (`merge_subset`, `merge_as_type`) | Two actions for one operation: a cross-type group of three needs both a subset and a type. One selection covers both shapes. |
| Carry the type the person chose (`okf_type`) instead of a survivor | The maintainer's rule is "the member of the chosen type survives", but two members can share the chosen type, so the survivor still has to be named. The plan reports `survivor_type`, so the confirmation can state the kind. |
| Pass the selection to `apply_decision` instead of the version | Breaks "a caller that holds no state other than the token and the `decision_key` can complete a commit" (curate-decisions), and lets a caller preview one selection and apply another unless the digest is compared twice. In the version it is echoed for free and tamper-evident. |

**Rationale.** It is the only shape that is additive under the classifier, keeps
the version the single piece of state a stateless caller holds, and leaves every
existing client's view of the list unchanged.

### Decision 2: The plan reuses `MergePlan`; a new `MergeStep` lists the steps

**Choice.** `merge_group` plans a `MergePlan` with `kind="merge"`, as
`relate_reversed` plans a `RelatePlan` with `kind="relate"`. `MergePlan` gains,
after `kind`:

```python
steps: tuple[MergeStep, ...] = ()
kept_distinct_ids: tuple[str, ...] = ()
survivor_type: str | None = None
```

```python
@dataclass(frozen=True)
class MergeStep:
    survivor_id: str
    absorbed_id: str
    absorbed_type: str | None
    type_outcome: Literal["same_type", "recorded", "discarded", "unknown"]
    files_rewritten: int
```

For a pairwise `merge`, `steps` holds the one step and `survivor_type` is set, so
a client reads one shape for both actions. For `merge_group`: `survivor_id` is the
chosen survivor; `absorbed_id` is the first step's absorbed member;
`survivor_criterion` is `"chosen"`; `files_rewritten` is the number of distinct
documents, other than the marked members, that any step rewrites;
`cross_source_same_pair` and `cross_type_concern` are computed over the marked
members.

**Alternatives.** A `MergeGroupPlan` with `kind="merge_group"` reads better, but
it widens the `DecisionPreview.plan` union, which the classifier rightly calls
breaking (a generated client's exhaustive switch gains a case), so it would cost
`API_VERSION` 2 and a `/v2` prefix for one action. Rejected. Putting the steps in a
new `DecisionPreview.group` field instead leaves `plan` describing only the first
step, which is the half-truth `steps` avoids.

### Decision 3: One version for the whole decision; staleness checked once, before any write

**Choice.** A group decision has exactly one `DecisionVersion`: the group's
`decision_key`, the row's `payload_digest`, the selection, and one
`content_digest` over these lines, sorted:

- `select\tsurvivor\t<id>` and one `select\tmember\t<id>` per marked member;
- every step's `_merge_content_lines`, each step prepared independently against
  the current bundle (pinned survivor and absorbed member);
- `keep\t<id>\t<sha256>` per unmarked member (`-` when it cannot be read).

`DecisionVersion.token()` appends the selection's canonical lines only when
`selection` is set, so every existing token is byte-identical.

The commit re-previews from current bytes with `version.selection` and compares
`content_digest` before the section (`Stale("content_changed")`). Inside the
section the row half (`row_resolved`, `row_superseded`) and `row_inputs_changed`
are each checked once. The decision is never split into N-1 tokens, and no step
after the first re-checks a token: after the first write the row is resolved by
this apply (Decision 7), so a replay of the version is `Stale("row_resolved")`.

Each step's `_merge_content_lines` already covers the whole-bundle reference scan
(`read_dependencies` plus every visible document's name), so the first step's
lines alone pin every document a later step reads; including every step costs
N-1 scans at preview and makes the digest independent of that argument. Selection
validation runs before the digest: a well-formed but invalid selection is
`NotApplied("invalid_selection")`, never `Stale`.

### Decision 4: Execution: one commit section, steps in ascending absorbed order, later steps prepared inside the section

**Choice.** Inside one `ports.commit_section()`, in order:

1. `_stale_row` (returned `Stale`);
2. every group member, marked or not, present, else `NotApplied("member_missing")`
   through `observer.merge_member_missing`, nothing written;
3. the first step's target drift, catalog recompose and read drift, exactly as
   `commit_merge_decision` (raised `DriftDetected`, nothing written);
4. `_stale_inputs` (returned `Stale`);
5. step 1: `merge_core` with the group row (Decision 7);
6. each later step: `lifecycle.prepare_one_merge(..., ordered_pair=(survivor, absorbed_k))`
   against the bytes the earlier steps wrote, `recompose_merge_catalog`, then
   `merge_core`;
7. the rulings for unmarked members (Decision 8);
8. one auto-commit (Decision 5).

Absorbed members run in ascending Concept ID order. The order decides which
absorbed type a survivor with no runner-up records (Finding 4); the preview
simulates exactly this order, so the plan the person read is the plan written.

**The lock.** The whole decision holds the workspace lock once. A busy workspace
fails before step 1 (`WorkspaceBusyError`, HTTP 503 with `Retry-After`), so a group
is never half-written because the lock was taken between steps, and no other
writer, the daemon included, can interleave between steps. The lock is held for
N-1 whole-bundle scans and writes but never across a model call: the API never
reconciles (`_POLICY = DecisionPolicy(no_reconcile=True)`). ADR-0036 bounds the
lock to a short commit phase, not to one write; S2a measures the hold on a
300-concept fixture with a group of four and records it.

**Alternative: one section per step.** Shorter holds, but the daemon or another
apply could land between steps, so step k could drift after step k-1 committed,
and a busy workspace could stop the group halfway. Rejected: it turns a lock wait
into a partial merge.

```mermaid
sequenceDiagram
    participant C as Client
    participant A as openkos.api
    participant S as curate_decisions
    participant L as lifecycle / merge_core
    participant Q as queue_resolution
    participant G as git (autocommit port)
    C->>A: preview_decision(key, MERGE_GROUP, selection)
    A->>S: preview_decision(...)
    S->>S: validate selection (invalid_selection)
    S->>L: prepare_one_merge per absorbed (pinned pair), no write
    S-->>A: MergeGroupDecisionPreview + version(selection, digest)
    A-->>C: DecisionPreview(plan kind=merge, steps, kept_distinct_ids)
    Note over C: person reads the plan, any time later
    C->>A: apply_decision(version, MERGE_GROUP)
    A->>S: apply_decision(...)
    S->>S: re-preview with version.selection; digest equal? else Stale
    S->>S: enter commit_section (lock, bounded wait)
    S->>S: stale row? all members present? step-1 drift/catalog/read drift? stale inputs?
    S->>L: step 1 merge_core(group_row=key)
    L->>Q: resolve_merged(pair) + resolve group row applied, then retire naming absorbed
    loop each later absorbed member
        S->>L: prepare_one_merge (inside section) + recompose catalog
        S->>L: merge_core
    end
    S->>S: record keep-distinct rulings (fail-open)
    S->>G: one autocommit of all paths
    S-->>A: Applied(merged, kept_distinct, commit_sha)
    A-->>C: Applied
```

### Decision 5: One git commit per decision

**Choice.** `merge_core` is called per step without the auto-commit, and one
auto-commit holds the union of every step's `merge_commit_paths` and every ruling
path, with the message
`openkos: merge group into <survivor> (<absorbed>, ...)` followed, when rulings
were recorded, by `; keep distinct <survivor>/<id>, ...`. `Applied.commit_sha` is
that commit, and `committed_confidential` reports it as for any apply.

**Rationale.** One decision, one commit: `Applied` has one `commit_sha`, the
history reads as the person's one choice, and the confidential notice covers the
whole decision. Reversibility does not depend on git commits: each step's ledger
entry is what `unmerge` restores from, LIFO per survivor, and
`unmerge --to <first absorbed>` unwinds the group.

**Alternative: one commit per step,** as running the pairwise `openkos merge`
commands produces. It matches the CLI's history, but `Applied` would need a list
of shas and the confidential notice would be per step. Rejected.

### Decision 6: Partial failure is a raised `MergeGroupInterrupted` that keeps what landed

**Choice.** A new exception in `application/write_gate.py`, re-exported:

```python
class MergeGroupInterrupted(WriteFailed):
    completed: tuple[tuple[str, str], ...]  # (survivor, absorbed), in order
    commit_sha: str | None
    failed_absorbed_id: str
```

When a step after the first cannot be prepared (a member vanished outside the
lock, an `OSError`/`ValueError`) or written, the loop stops; the completed steps'
paths are auto-committed (best effort, as any commit), no ruling is recorded, and
`MergeGroupInterrupted` is raised with `operation="merge_group"`. A failure of the
first step's write stays the ordinary `WriteFailed`, because nothing completed.
The row is already resolved (Decision 7), so the remaining members come back
through the producer's next pass if detection still groups them.

**Why raised, not returned.** ADR-0053 returns the outcomes a walk continues past
(skipped, member missing, stale) and raises the ones that end it (drift, write
failure). A group cut short by a write failure is a write failure. As a subclass
of `WriteFailed`, every existing handler (`except WriteFailed`,
`except OpenKosError`) still catches it, and the subclass is additive.

**Over HTTP.** A row placed before `WriteFailed` (subclass-first) answers 500
`merge_group_interrupted`, not retryable: re-posting the version is
`Stale("row_resolved")`. The uniform body carries no steps, so the client lists
the decisions again; `GET /v1/decisions` and `GET /v1/status` show the state.

**Alternative: return `Applied` with an `interrupted` flag.** Reports a failure as
success over HTTP (200) and needs every client to check a flag. Rejected.
**Alternative: roll back the completed steps.** `unmerge` inside the failure path
is a second destructive sequence that can itself fail, and the completed steps are
already individually reversible. Rejected.

### Decision 7: The group's row resolves `applied` after the first write

**Choice.** `lifecycle.merge_core` gains an optional keyword
`group_row: queue_resolution.GroupRow | None = None`
(`GroupRow(decision_key, as_proposed)`), forwarded to `resolve_merged`, which
resolves that key's open row `applied` (`as_proposed` when every member was
marked, `modified` otherwise) **before** `_retire_naming` runs. Only step 1
passes it. The write-core registry is unchanged: `merge_core` still maps to
`resolve_merged`.

**Rationale.** The row moves only after a write it proposed has landed
(pending-work: "Only Human-Facing Write Paths Resolve A Row"), and the
retirement that follows finds it resolved and leaves it (Finding 2). Resolving
before step 1 would close the row for a write that then failed; resolving after
the last step finds it already `stale`.

### Decision 8: Unmarked members get pairwise rulings with the survivor, plus one over the remainder

**Choice.** After every step, for unmarked members U (ascending): one ruling over
{survivor, u} for each u in U, and, when |U| ≥ 2, one ruling over
{survivor} ∪ U. Each goes through `ports.record_identity_ruling` (the
`duplicates_service.apply_identity_decision` writer keep-distinct uses), which
also declines an open row over exactly those members. Failures are fail-open, as
`record_keep_distinct` is: the merges stand, `ports.on_warning` receives one
fixed-text warning per failed ruling, and `Applied.kept_distinct` lists the
member sets that were recorded.

**Why these sets.** Every one is a statement the person made: by marking the
survivor and not u, they said u is not that thing; and the remainder set is not
one entity, because the survivor is in it and differs from each u. Rulings match
exact member sets (Finding 3), so the pairwise rulings stop the pairs coming back
and the remainder ruling stops the group most likely to be re-detected once the
absorbed members are gone. No ruling is recorded between two unmarked members on
their own: the person said nothing about whether those are the same, and {u1, u2}
alone stays proposable.

**Alternatives.** Pairwise only: honest, but the re-detected {survivor, u1, u2}
group asks the person a question they already answered. Remainder only: a later
pair {survivor, u1} is asked again. No ruling at all: contradicts the maintainer's
"with a keep-distinct ruling where the existing model supports one".

### Decision 9: Cross-type stays explicit and warned

`merge` stays withheld with `cross_type`; the only route is `merge_group` with a
named survivor, which is the API's equivalent of `merge --include-cross-type`
(the deliberate opt-in) and is never derived by the engine. The plan carries
`cross_type_concern` over the marked members and each step's `type_outcome`, so
the person sees which type is recorded as `type_alternative` and which, if any,
is discarded (#1293) before consenting. The written outcome comes from the
unchanged merge write (`bundle.merge.plan_merge`), and a test pins that the
previewed outcome equals it. Bulk eligibility (`BULK_ELIGIBILITY["identity"]`
is `NEVER`; `item_consent` returns `ask` for identity) is unchanged, and neither
`accept-recommended` nor `--auto-merge` (ADR-0034's class excludes N>2 and
cross-type groups) reaches the new action.

### Decision 10: An unjudged group is judged through the existing surface

`merge_group` follows `merge`'s judgment gate: offered only for a persisted SAME
verdict, withheld with `pending_evaluation`, `judged_different` or
`judged_uncertain` otherwise. Withholding it (rather than omitting it) tells a
client that the group could be combined once checked, which is the
"Check these" wording the brief already has. The client quotes and runs identity
judging as today (ADR-0059); a SAME verdict written through to the open row
(curate-decisions: "A Persisted Verdict Written Through...") makes `merge_group`
offered with no new code (Finding 7). The surface still never calls a model.

### Decision 11: The CLI is not changed in this change

`curate`'s Identity stage keeps `_echo_n_gt2_skip` and `_cross_type_skip_line`,
byte for byte. The capability is already at parity: the commands those lines
print (`openkos merge <survivor> <absorbed>` per step, and
`openkos merge --include-cross-type`) are exactly the pairwise merges a group
decision runs, each reversible with `unmerge`, and `duplicates --keep-distinct`
records the rulings. What the CLI lacks is the one-screen ergonomics. Adding it
to `curate` needs a multi-select prompt design the maintainer's decision does not
cover, rewrites the command's goldens (curate-decisions: "Behavior Of The Command
Is Unchanged"), and would roughly double this change. The cost is not small, so
it is recommended as a follow-up issue once the app's wording has met a reader,
and the CLI walk can then reuse `commit_merge_group_decision` unchanged.

### Decision 12: Local API

- `PreviewRequest` moves onto `_EchoRequest` (Finding 5) and gains
  `selection: api.GroupSelection | None = None`. `ApplyRequest` is unchanged; its
  echoed `version` now carries `selection`.
- `_NOT_APPLIED["invalid_selection"] = (422, "invalid_selection", False, "the selection does not name a survivor and the members to combine with it")`.
- `_ROWS` gains `(api.MergeGroupInterrupted, 500, "merge_group_interrupted", False, "the group was combined only in part; the completed merges are kept")`
  immediately before the `WriteFailed` row.
- The `preview` and `apply` routes' `responses=` gain no new status (422 and 500
  are router-wide; 409, 413, 503 already listed).
- `tests/unit/localapi/openapi.json` is regenerated in every slice that changes a
  re-exported type (S1, S2a, S3, S4).

### Decision 13: ADR gate

The decision is an interface (a public action, a token field and a plan shape
inside the frozen `API_VERSION` 1) and a trade-off (one decision, one lock, one
commit, partial failure kept), and it is hard to reverse: removing any of the new
public names is a breaking change that costs `API_VERSION` 2. Both conditions
hold, so it is recorded as **ADR-0063**, Proposed, written with this plan; the
archive slice flips it to Accepted.

## File Changes

| File | Change | Slice |
|---|---|---|
| `application/curate_decisions.py` | S1: `Action.MERGE_GROUP`, `NotApplied` reason `invalid_selection`, `GroupSelection`, `DecisionVersion.selection`, `validate_selection`, `MergeGroupDecisionPreview`, `preview_merge_group`, `version_of` branch. S2a: `Applied.merged`/`kept_distinct`, `commit_merge_group_decision` (steps). S2b: rulings, interruption. S3: list/withhold, dispatch in `preview_decision`/`apply_decision` | S1, S2a, S2b, S3 |
| `application/lifecycle.py` | `merge_core(..., group_row=None)` | S2a |
| `application/queue_resolution.py` | `GroupRow`; `resolve_merged(..., group_row=None)` | S2a |
| `application/merge_service.py` | `merge_group_commit_message` beside `merge_commit_message` | S2a |
| `application/write_gate.py` | `MergeGroupInterrupted` | S2b |
| `api/_decisions.py` | `MergeStep`, `MergePlan` fields, `_plan_of` group branch, `preview_decision(selection=)` | S3 |
| `api/__init__.py` | export `GroupSelection` (S1, because `DecisionVersion` names it and the leak guard forbids an unexported annotation); `MergeStep`, `MergeGroupInterrupted` (S3) | S1, S3 |
| `localapi/_errors.py` | `invalid_selection` row (S1, beside the reason, Finding 6); `merge_group_interrupted` row (S4) | S1, S4 |
| `localapi/_models.py`, `_routes.py` | `PreviewRequest.selection` on `_EchoRequest` | S4 |
| `tests/unit/api/api_surface.json`, `tests/unit/localapi/openapi.json` | regenerated whenever a re-exported type changes | S1, S2a, S3, S4 |
| `docs/decision-wording.md`, `CHANGELOG.md` | wording, entry | S5 |
| `docs/adr/0063-...md`, `docs/adr/README.md` | Proposed now; Accepted at archive | plan, S5 |

## Interfaces / Contracts

```python
# application/curate_decisions.py
def validate_selection(group_members: Sequence[str], selection: GroupSelection | None
                       ) -> GroupSelection | NotApplied: ...
def preview_merge_group(root, layout, group: CandidateGroup, *, verdict: Verdict | None,
                        selection: GroupSelection) -> MergeGroupDecisionPreview | NotApplied: ...
def commit_merge_group_decision(root, layout, preview: MergeGroupDecisionPreview, *,
                                ports: DecisionPorts, observer: DecisionObserver,
                                surface: str, expected: DecisionVersion) -> ApplyOutcome: ...

# openkos.api
def preview_decision(workspace, decision_key, action, *, selection=None
                     ) -> DecisionPreview | NotApplied | Stale: ...
```

`commit_merge_group_decision` takes a mandatory `expected`: there is no in-process
caller (Decision 11), so no baseline-only path is built.

## Testing Strategy

Strict TDD (RED, GREEN, REFACTOR) with `uv run pytest`. Fixtures build real
workspaces with concept files, a populated queue row and a SAME verdict, as
`tests/unit/application/test_curate_decisions.py` does. Each slice's RED tests are
listed in `tasks.md`; the load-bearing ones:

- **Selection binding:** an altered selection in the version is `Stale`; a
  reordered or duplicated selection yields the same token (S1).
- **Previewed type outcome equals the written one** for a three-type group (S1
  pins the preview; S2a pins it against the written frontmatter).
- **Byte-parity round trip:** commit a group, `unmerge` each step newest first,
  compare every file to the pre-group bytes (S2a).
- **Interleaving is impossible:** a second writer blocked on the lock for the
  whole group; a busy lock with wait 0 writes nothing (S2a).
- **Interrupted at step 2:** a patched `merge_core` failing on the second call;
  assert one commit with step 1's paths, no ruling, the exception's fields (S2b).
  Mutate the "keep what landed" commit away and confirm the test fails.
- **Row resolution:** the group row ends `applied` (as proposed / modified),
  never `stale` (S2a).
- **Additivity:** `test_api_surface.py` against the merge base passes with
  `API_VERSION` 1 (S1, S2a, S3).
- **HTTP:** populated round trip of a merge-group plan; `invalid_selection` 422;
  `merge_group_interrupted` 500; altered echoed selection 409; one
  `GroupSelection` component (S4).

## Threat Matrix

| Threat | Control |
|---|---|
| A client merges members the person did not mark | The selection is in the version and digest-bound; an altered one is `Stale`. |
| A crafted id escapes the bundle | Only ids that are members of the open row's group are accepted (`invalid_selection` otherwise); ids are never joined onto a path before that check, and the ruling writer canonicalises again. |
| Confidential content in an error body | `merge_group_interrupted` and `invalid_selection` use fixed messages; no id or `str(exc)` reaches the body. |
| A cross-type merge without consent | No derived survivor; `merge` stays withheld; the concern is in the plan; identity is never bulk. |

## Migration / Rollout

No data migration: queue schema, ledger format and ruling format are unchanged.
Existing tokens are byte-identical (`selection` is `None`). The slices land in
order S1 → S2a → S2b → S3 → S4 → S5. Until S3 nothing offers `merge_group`, so a
request for it is `not_offered` (422 `action_not_offered`) on every surface.
Between S3 and S4 the action is offered but an HTTP client cannot yet send a
selection (422 `invalid_selection`), and an interrupted group maps to
`write_failed`; no shipped client reaches either, because the desktop app does
not exist yet.

## Open Questions

None that block apply. Two follow-ups are recommended, not decided here: a
`curate` group walk (Decision 11), and an `openkos.api` unmerge operation so the
app can offer the "Split again" undo the wording brief already promises for every
combine, pairwise or group.
