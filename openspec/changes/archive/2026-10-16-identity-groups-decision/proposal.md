# Proposal: Resolve Identity Groups Of More Than Two Members Or Of Different Types Through The Decision Surface

Tracking issue: #1430 (refs #1408). Governing decisions: ADR-0053 (the two-phase
decision surface), ADR-0059 (judging never merges), ADR-0060 (the versioned
Python API), ADR-0034 (the only unattended merge class). New decision record:
ADR-0063 (Proposed, written with this plan). Paths are under `src/openkos` unless
they start with `tests/`, `docs/` or `openspec/`.

## Intent

An identity decision whose group has more than two members
(`more_than_two_members`) or whose members declare different OKF types
(`cross_type`) cannot be merged through the decision surface. `merge` is withheld
for both, and only the CLI resolves them: `adjudicate`'s skip line prints the
pairwise `openkos merge` commands, and `merge --include-cross-type` is the opt-in
for a cross-type pair. The desktop app reaches the engine only through
`openkos.api` and the local HTTP API, so a person whose review list holds such a
group cannot settle it. The MVP 6 exit criteria (`evals/desktop_app/PREREGISTRATION.md`)
require a person who never used a terminal to settle a pending decision, and the
decision-wording brief (`docs/decision-wording.md`) shows these groups as "These
can't be combined here yet".

## Binding scope decisions

Settled by the maintainer on #1430 (2026-10-16). Spec and design do not reopen
them.

1. **Built in MVP 6**, before the review screen.
2. **A group of more than two members:** the person marks which members are the
   same and which one stays (the survivor). The engine merges them pairwise into
   that survivor, and each step is reversible with `unmerge`.
3. **A cross-type group:** the person chooses what the thing is; the member of the
   chosen type survives. Never automatic. The cross-type warning and consent are
   preserved, and the survivor records the absorbed type in `type_alternative`,
   as `merge --include-cross-type` already does.
4. **Identity merges stay one decision at a time** and are never batched or
   applied automatically (ADR-0053, ADR-0034).

## Scope

### In scope

- **A new decision action, `merge_group`,** offered on a judged-SAME identity
  item exactly where `merge` is withheld for `more_than_two_members` or
  `cross_type`. `merge` stays withheld there, with the same reason, so a client
  that ignores the new action behaves as today.
- **A group selection,** `GroupSelection(survivor_id, member_ids)`: the marked
  members, survivor included. It is passed to preview and carried inside the
  returned `DecisionVersion`, so the version stays the only state a stateless
  caller holds, and an altered selection is a stale version.
- **The plan reuses `MergePlan`** with additive fields (`steps`,
  `kept_distinct_ids`, `survivor_type`). `merge_group` plans a `merge`, the way
  `relate_reversed` plans a `relate`, so the `kind` discriminator (#1426) and the
  plan union are unchanged.
- **The commit:** one token check, one commit section, the marked members merged
  pairwise into the survivor in ascending Concept ID order, one git commit for
  the decision. Each step writes its own ledger entry, so each is reversible with
  `unmerge` (LIFO per survivor).
- **Unmarked members** are left as they are. A keep-distinct ruling is recorded
  for the survivor with each unmarked member, and, when two or more stay
  unmarked, for the survivor with all of them.
- **A cross-type group** is merged only into the survivor the selection names.
  The plan carries the cross-type concern and, per step, whether the absorbed
  type is recorded in `type_alternative` or discarded because the survivor
  already records one (#1293).
- **Partial failure** is a raised `MergeGroupInterrupted` (a `WriteFailed`)
  carrying the completed steps and the commit that holds them.
- **Python API (`openkos.api`),** additive within `API_VERSION` 1: the enum
  member, the `selection` keyword on `preview_decision`, `GroupSelection`,
  `MergeStep`, the new `MergePlan` and `Applied` fields, the new `NotApplied`
  reason `invalid_selection` and the new exception.
- **Local HTTP API:** `selection` in the preview request body, two new error rows
  (`invalid_selection` 422, `merge_group_interrupted` 500), the regenerated
  OpenAPI snapshot.
- **Docs:** `docs/decision-wording.md` loses "These can't be combined here yet"
  and gains wording, marked as hypotheses, for marking members and choosing the
  kind; the CHANGELOG gains its entry. `docs/cli.md` does not change, because no
  verb changes.
- **ADR-0063,** Proposed, flipped to Accepted at archive.

### Out of scope

- **The `curate` Identity stage.** It keeps printing the pairwise `openkos merge`
  commands for an N>2 group, and the `merge --include-cross-type` command for a
  cross-type pair, byte for byte (curate-decisions: "Behavior Of The Command Is
  Unchanged"). The CLI already resolves both shapes through `merge`, so the
  capability is at parity; an interactive group walk in `curate` needs its own
  prompt design and would rewrite the command's goldens. It is a follow-up, not
  part of this change. See design Decision 9.
- **Undo from the app.** `unmerge` has no `openkos.api` operation yet, for a
  pairwise merge or a group. That gap predates this change and affects every
  combine the app offers; it is not closed here.
- **Choosing the survivor of an ordinary two-member, same-type pair.** `merge`
  keeps the engine's survivor rule (#776, #1228). `merge_group` is not offered
  there.
- **Judging.** A group that is pending evaluation is judged through the existing
  judging surface (ADR-0059). Nothing about judging changes.
- **Reconciling merged bodies and refreshing derived indexes.** The API does
  neither for any apply (stable-python-api v1 limitation), and a group apply is
  no different.

## Approach

`application/curate_decisions.py` gains a group preview and a group commit beside
the pairwise ones, in the same mould: a pure preview, a version whose content
digest covers the selection and every byte the steps rest on, and a commit that
re-previews from current bytes, then checks the row and the first step's drift
inside one commit section, before any write. Steps after the first are prepared
inside the section, against the bytes the earlier steps wrote, so no other writer
can interleave between them. The first step's merge resolves the group's queue row
`applied`; later steps retire nothing new for it. `openkos.api` and `openkos.localapi`
expose it additively. Design records the decisions and their rationale.

## Affected specs

| Spec | Change |
|---|---|
| `curate-decisions` | MODIFIED: Each Kind Carries Its Own Action Set; Only Judged-SAME Identity Items Are Mergeable; Apply Is A Two-Phase Preview And Commit Against A Version Token. ADDED: five group requirements. |
| `stable-python-api` | ADDED: Group Decisions Extend v1 Additively. |
| `local-http-api` | MODIFIED: Responses Are The Python API's Result Types, Populated And Round-Tripping; Outcomes And Errors Map To Status Codes With A Uniform Body. ADDED: The Group Selection Travels In The Preview Body And The Echoed Version. |
| `pending-work` | MODIFIED: Only Human-Facing Write Paths Resolve A Row. |

## Principles check

- **Human curates, engine maintains.** Every merge in a group is chosen by the
  person: the members and the survivor are both explicit, the engine never picks
  a survivor for a group or a cross-type pair, and a group needs a persisted SAME
  verdict before it is offered, exactly as a pair does.
- **Reversible.** Each step writes its own `merged_from` ledger entry; `unmerge`
  (and `unmerge --to`) restores byte parity per step.
- **Reconstructible.** Rulings live in `bundle/.state/decisions/**`, merges in the
  bundle and its ledger sidecars. The queue row's resolution is a derived-cache
  label.
- **Sensitivity.** Each step recomputes the survivor's sensitivity as a
  high-water mark, as every merge does; `committed_confidential` reports a
  confidential concept in the commit. No content leaves the device: the API never
  reconciles with a model.
- **Local-first, OKF.** No new dependency, format or file kind.

## Risks

| Risk | Mitigation |
|---|---|
| The lock is held for N-1 merges instead of one. | No model call runs inside it (the API never reconciles); each step is one whole-bundle scan and a few writes. The wait to acquire stays bounded (5 s on HTTP). Measured in S2a against a 300-concept fixture and recorded in the verify notes. |
| A failure after the first step leaves a partly combined group. | Completed steps are committed and reported in `MergeGroupInterrupted`; each is reversible with `unmerge`; the row is already resolved and the producer's next pass proposes whatever is still true. |
| A widened public union would break `API_VERSION` 1. | No union is widened: `merge_group` plans a `MergePlan`, outcomes stay `Applied`, `NotApplied`, `Stale`. Enum members, `Literal` values, defaulted fields, an optional keyword and an exception subclass are additive under the surface classifier (`tests/unit/api/surface.py`). |
| A client previews one selection and applies another. | The selection is inside the version and covered by its content digest; an altered selection is `Stale("content_changed")`. |
| Ruling writes fail after the merges landed. | Fail-open, as keep-distinct already is: the merges stand, the warning callback is told, and `Applied.kept_distinct` lists only the rulings that were recorded. |

## Rollback plan

Every slice is one squash-merged PR and reverts on its own, newest first. Until
S3, nothing offers `merge_group`, so the behavior of S1-S2b is unreachable from
any client. S3 is the one slice that offers the action; withdrawing it without
removing names is a one-line change in the listing (`_identity_actions`), after
which every group goes back to "merge withheld". Merges already made through it
remain ordinary merges with ledger entries, undone with `unmerge`, and rulings
already recorded are listed by `duplicates --kept-distinct` and reopened with
`duplicates --reopen`. S1, S2a, S3 and S4 change the public surface of
`openkos.api` or the HTTP contract (directly or through a re-exported type), so a
revert of one of them after merge removes public names or fields,
which is a breaking surface change: the preferred rollback is that one-line
withdrawal, and a full revert also raises `API_VERSION`.
