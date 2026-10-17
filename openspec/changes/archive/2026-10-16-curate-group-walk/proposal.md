# Proposal: Walk An Identity Group In `curate`

Tracking issue: #1438. Governing decisions: ADR-0053 (the two-phase decision
surface; identity is never automatic or bulk), ADR-0063 (an identity group is one
decision that merges the marked members pairwise into a chosen survivor). Follows
the archived `identity-groups-decision` change, whose design (Decision 11) left the
CLI walk out on purpose. Paths are under `src/openkos` unless they start with
`tests/`, `docs/` or `openspec/`.

## Intent

`openkos curate`'s Identity stage still skips a judged-SAME group of more than two
members, and a cross-type pair, with a line that prints the pairwise
`openkos merge` commands (and `merge --include-cross-type`). The app settles the
same groups through `merge_group`. A terminal user should be able to settle them in
the walk too, through the same application path, so the CLI and the app share one
group decision.

## Scope

### In scope

- The Identity walk asks, for each such group, which members are the same, what a
  cross-type thing is (when the marked members are of more than one type), and
  which member keeps its name, then previews the group decision and commits it
  through `curate_decisions.commit_merge_group_decision` against the version
  `preview_decision` issued, after one `y`.
- `d` keeps the whole group distinct through the existing keep-distinct path;
  `s` and Enter leave it for later, writing nothing.
- A group whose open row does not offer `merge_group` (no row, or a judgment the
  row does not carry) keeps today's skip lines, so the manual route stays named.
- `docs/cli.md` (the Identity paragraph states the walk's shape) and the CHANGELOG.

### Out of scope

- `adjudicate --apply`, `merge` and `pending`: their skip lines and suggested
  commands are unchanged.
- Any change to `application/` or `openkos.api`: the surface is reused unchanged.
- Bulk or automatic group merges: the automatic pass and accept-recommended stay
  pair-only.

## Approach

A new walk step in `cli/curate.py` replaces the two skip branches. It reads the
group's open item from `list_open_decisions`, prompts with the existing validating
answer loop, builds a `GroupSelection`, calls `preview_decision(..., MERGE_GROUP,
selection=...)`, renders the returned plan, and commits with
`commit_merge_group_decision(..., expected=version)`. Exit codes follow the pair
walk: drift exits 3, a write failure (including `MergeGroupInterrupted`, which keeps
what landed) exits 1.

## Risks

- The walk's stdout changes for these groups, which the curate-decisions
  requirement "Behavior Of The Command Is Unchanged" forbids for routing moves;
  that requirement is amended to exempt this specified behavior change.
- A multi-step prompt is new in the CLI; every answer is validated and re-asked,
  and every non-final answer can still skip.
