# Proposal: The Structure Stage Is Reviewed On Request (#1268)

## Intent

The pilot of the #1268 pre-registered measurement found the Structure stage
presenting exactly its 50-prompt cap in every arm (of about 500 candidate
edges): 2.27 decisions per source on its own, so the arc's bar of at most one
decision per source is unreachable whatever identity does. Its suggestions come
from edge typing, measured at 0.36 type accuracy on the packaged model (#1269).
The owner decided (2026-10-04) that the stage becomes opt-in: relation
suggestions are still computed and kept as `relation_type` pending rows, `curate`
no longer presents them by default, and they stay visible, with how many wait and
which command reviews them.

## Scope

### In Scope

- `curate` skips Structure unless the run asks for it: `--structure`, or
  `--accept structure` (which implies it). `review: false` and
  `--accept metadata` do not ask for it.
- A skipped Structure is not probed (no graph walk), not computed (no model
  call, no client use) and not presented. Its summary line states how many
  relation suggestions wait and names `openkos curate --structure`.
- `pending` states the same count and command under its heading; a relation row's
  resolving command becomes `openkos curate --structure`.
- The daemon's what-changed digest ends with the same line when suggestions wait.
- Messages that told a person to run `openkos curate` to type edges now name
  `openkos curate --structure` (`status`'s untyped-edge line, the zero-typed-edges
  advice of `contradictions` and of the candidate zero state).
- `docs/cli.md`; living-spec deltas for `curate-command`, `pending-work` and
  `job-runtime`.

### Out of Scope

- Auto-applying a measured class of relations (deferred until a corrected Label
  round on #1269 selects an edge-typing model worth trusting, under its own
  pre-registration).
- Changing what the unattended engine computes, the 50-edge cap, the Structure
  prompts, or the pre-registered protocol and bars.
- A new daemon line on passes that made no commit (the digest is silent then, by
  its own requirement); `pending` and `status` carry the count there.

## Where suggestions are computed

Unchanged, and none of it is `curate`'s default path:

- The daemon's maintenance pass runs the relations advisor
  (`cli/daemon.py::_relations_stage`) and enqueues one `relation_type` row per
  typed edge, bounded by the `unattended:` budget.
- `suggest-relations` persists its suggestions and the queue serves them.
- `curate --structure` serves fresh open rows first and types only the rest.

So a workspace that never runs the daemon or `suggest-relations` has no
suggestions waiting; the summary then says none wait and that
`openkos curate --structure` computes and reviews them. No default path spends
model calls on suggestions nobody will see.

## What typed relations feed (the tradeoff)

Verified in the code, not assumed:

- **Contradictions** derives its candidate pairs from typed edges only (plus
  merged pairs): `resolution/contradiction.py`, "derives candidate pairs from
  TYPED edges only". An edge never typed is never checked for a contradiction.
  This is the real cost of the default.
- **`show` and the MCP neighbor listing** label a neighbor with its relation type
  (`application/concept_read.py`, `mcp/gate.py`); an untyped link is listed with
  no label.
- **`status`** keeps counting untyped edges as pending work.
- **Retrieval** does not read Structure's types: its history walk reads only the
  lifecycle relations `supersedes` and `revises`, which the suggester never
  proposes (`SUGGESTABLE_RELATION_TYPES` excludes them). Graph analysis carries
  the type as an edge attribute and does not branch on it.

What is gained: a plain `curate` session drops from decisions-per-source of about
4.3 toward the bar without hiding anything, because the suggestions stay in the
queue and are counted on every surface a person looks at. What is risked: a
person who never opts in leaves edges untyped, and contradiction coverage over
those edges stays empty until they do. The suggester is wrong about the type
roughly two times in three on the packaged model, so a default walk over them
was mostly asking people to reject noise.

## Design decisions

1. **Flag: `--structure`.** `--accept` already names stages but means "answer yes
   for me", and `review: false` feeds it; overloading it as the opt-in would make
   a standing config knob turn a stage on. A one-word flag per opt-in stage,
   declared on the stage descriptor (`Stage.opt_in_flag`), keeps the sequencer
   generic.
2. **`--accept structure` implies the opt-in.** Naming a stage to accept in bulk
   is a request to run it; making it a silent no-op would be a trap. `review:
   false` and `--accept metadata` do not.
3. **Skipped means unprobed.** The probe walks the graph, so skipping only the
   run would still cost a walk. The count comes from the queue alone.
4. **One count, one definition.** Open `relation_type` rows minus supersessions,
   defined once in `application/pending_queue_report.py` and used by the curate
   summary, `pending` and the digest. It counts open rows, not freshness-checked
   ones, so it can include a row an edit made stale until the producer's next
   complete pass retires it; `curate --structure` re-derives freshness.
5. **Summary status is `empty`.** The six-way outcome vocabulary is fixed by the
   living spec; the notice carries the real text.

## Risks

- Pre-registered harness: it drives `curate` on a pseudo-terminal and attributes
  prompts to stages by the TTY-only `Structure: checking...` line. A plain
  `curate` no longer prints that line or any Structure prompt, which is the
  intended effect on B1. The harness is not edited here; the owner's binding run
  measures the commit that implements this.
- Existing tests that exercised Structure through a plain `curate` now pass
  `--structure`.
