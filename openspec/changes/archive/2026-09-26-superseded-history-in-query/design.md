# Design: superseded-history-in-query — show how a retrieved answer came to be

## Technical Approach

This follows the proposal (Refs #1014 piece b) and the binding decisions in
`exploration.md`. When the workspace opts in with `revision_history: true`,
`query` attaches each retrieved concept's earlier versions to it. A retrieved
concept is a *successor*, and its earlier versions are the concepts reached
through its outbound `supersedes` and `revises` edges. Each earlier version
becomes its own numbered and labelled **history block**, placed directly
after its successor's block.

The design keeps each concern at one existing seam:

- **The walk** is a pure function in a new `retrieval/history.py`. It reads
  files only through a reader that the caller injects.
- **Every file read**, for hits and predecessors alike, goes through one new
  guarded-read helper in `retrieval/answer.py`. It is extracted verbatim from
  today's hit loop (`answer.py:636-654`), so history cannot bypass a gate
  that a hit passes.
- **The budget** is computed as today over the hits. The history is then
  paid for from the successor's own share, plus any budget that today goes
  unspent. The pure arithmetic lives in `prompt_budget.nested_shares`. The
  frame cost is measured by construction in `answer.py`, which is how #882
  already does it.
- **Event dates** come from a new package-root leaf,
  `src/openkos/event_dates.py`, beside `lifecycle.py`. It owns `DateState`,
  and `resolution/decision_revision.py` re-exports it.
- **The surface** is small: a config key threaded like `sufficiency_check`,
  two citation markers, one stderr notice, and one optional
  `okf.build_concept` argument for the `--save` note.

When the key is off, which is the default, `answer()` never calls the walk.
Every hit is read exactly once, as it is today. `_bound_bodies` runs
unchanged, and the prompt is byte-identical.

The delta specs (`specs/query-answer`, `status-aware-retrieval`,
`query-command`) are written in parallel. Where this design sharpens their
wording, it says so under **Open Questions / spec alignment**.

## Architecture Decisions

### Decision 1: `event_dates.py` is a package-root leaf that owns `DateState`

**Choice**

A new module, `src/openkos/event_dates.py`. It imports only the standard
library, `openkos.model.okf` and `openkos.model.types`. That is the same
leaf discipline as `lifecycle.py` (`lifecycle.py:1-10`).

```python
DateState = Literal["dated", "missing", "multiple", "none-reached"]

@dataclass(frozen=True)
class ResolvedEventDate:
    state: DateState
    earliest: date | None  # set for "dated" (== latest) and "multiple"
    latest: date | None    # None for "missing" / "none-reached"

def resolve_event_date(
    bundle_dir: Path,
    metadata: Mapping[str, object],
    *,
    admit: Callable[[str, Mapping[str, object]], bool] | None = None,
) -> ResolvedEventDate: ...
```

`resolution/decision_revision.py:58` becomes an explicit alias,
`DateState = event_dates.DateState` (after `from openkos import event_dates`).
It must stay importable from `decision_revision`, because
`tests/unit/resolution/test_decision_revision.py:26` imports it from there
and mypy strict's no-implicit-reexport would reject a bare `from … import`.

`DecisionDate`, `DirectionReason` and `pair_direction` are untouched. The
harness imports only `DecisionDate`
(`evals/decision_revisions/revision_fixtures.py:36`,
`run_decision_revisions_eval.py:94-98`), so it is unaffected. Its docstring
mention of `decision_revision.DateState` (`revision_fixtures.py:116`) stays
true through the alias.

**The resolution rule: at most one hop past the member's own provenance**

1. Read the member's `provenance:` from `metadata`, which is **already
   read**, so this costs no read. A value that is not a list is treated as
   empty. Entries that are not strings are skipped. Entries are
   de-duplicated, and their order does not matter because the result is a
   set.
2. An entry prefixed `f"{TYPE_TO_LINK_DIR['Source']}/"` is a Source. This is
   the `sources/` prefix convention of `bundle/provenance.py:283`. For each
   Source, do **one guarded read** and apply `okf.read_event_date`.
3. Any other entry is an intermediate. Do one guarded read, take *its*
   `provenance:`, and apply step 2 to its `sources/` entries only. Entries at
   the second hop that are not Sources are ignored. The walk stops there.
4. **Guarded read.** An `OSError`, a `UnicodeDecodeError`, any parse
   failure, or `admit(id, meta) is False` makes a **Source** count as
   `missing`: it was reached, but its date cannot be used. The same failures
   make an **intermediate** contribute nothing, and it is not traversed.
5. **Aggregation**, checked in this order, which is the detector spec's
   "Decision Event Date Resolution" and the harness order
   (`revision_fixtures.py:125-128`):
   - No Source reached: `none-reached`.
   - Any reached Source is absent, malformed or refused: `missing`.
   - More than one distinct date: `multiple`, with `earliest`/`latest` set.
   - Otherwise: `dated`.

The resolver never raises.

`admit` is how the caller keeps the send-time gate. `answer.py` passes a
closure over `blocked` and `sensitivity.should_block`, so the date of a
confidential Source never enters the prompt. The leaf stays free of
`sensitivity`, and the default `None` admits everything, which is what the
detector's local-only use needs.

**Alternatives considered**

- `bundle/provenance.py`. Its contract is pure and does no I/O over a
  snapshot.
- `model/okf.py`. It owns the format, not provenance semantics.
- `retrieval/`. The detector's `resolution/` code could not import it.
- Whole-bundle `provenance_source_ancestors`. Its cost scales with the
  bundle.

**Rationale**

Reads are bounded by the chain, which is at most 3 members per hit, times
their provenance fan-out. There is one vocabulary for both features, and the
layering is clean.

### Decision 2: The walk is pure BFS with an injected reader; the guards live in one helper

**Choice**

`src/openkos/retrieval/history.py`:

```python
HistoryRole = Literal["superseded", "refined"]
MAX_DEPTH: Final = 3
MAX_BLOCKS: Final = 3

@dataclass(frozen=True)
class Predecessor:
    concept_id: str
    role: HistoryRole                # from the edge that reached it
    holder_id: str                   # the concept whose edge reached it
    metadata: dict[str, object]
    body: str

@dataclass(frozen=True)
class HistoryWalk:
    predecessors: tuple[Predecessor, ...]
    truncated: bool

def walk_history(
    successor_id: str,
    successor_metadata: Mapping[str, object],
    *,
    read: Callable[[str], tuple[dict[str, object], str] | None],
    skip: AbstractSet[str],
) -> HistoryWalk: ...
```

**The algorithm.** It is exact, because this is the order a test pins.

- `edges(meta)` is `okf.decode_relations(meta)`, filtered to `supersedes` and
  `revises` and sorted by `(0 if supersedes else 1, target)`. A `ValueError`
  from a malformed `relations:` yields `[]`, the same fail-safe as
  `lifecycle.py:73-76`.
- Setup: `visited = {successor_id}`. The queue (a `deque`) starts with the
  successor's edges at depth 1, with `holder = successor_id`.
- For each popped `(target, role, depth, holder)`:
  1. If `target in visited`, continue. Otherwise add it to `visited`. This
     is the cycle guard, and it also drops self-edges.
  2. If `target in skip`, continue, and do not expand it. It is either an
     ordinary hit (it gets its own walk) or it is already attached or
     rejected elsewhere.
  3. If `len(attached) == MAX_BLOCKS`, set `truncated` and stop. The flag is
     set only if some queued target (this one included) is not in `visited`
     or `skip`.
  4. Call `read(target)`. `None` means a gate refused it or it was
     unreadable. That node is a **dead end**, and its edges are never
     followed. This is why no label can ever name a node that was not
     admitted.
  5. Append the node as a `Predecessor`.
  6. If `depth == MAX_DEPTH`, set `truncated` when the node has any edge
     whose target is not in `visited` or `skip`, then continue. Otherwise
     enqueue its edges at `depth + 1` with `holder = target`.

**The order.** BFS level by level. Within a node, `supersedes` edges come
before `revises` edges, then ascending concept id. Nodes at the same level
come in their parents' order. The result is fully deterministic.

**`truncated`** means "the chain continues past what was shown", whether
because of the block cap or the depth limit. It is computed **without any
extra read**. It uses only edge targets taken from admitted frontmatter, so
it discloses nothing about a concept that was not admitted.

**`role`** comes from the edge that reached the node, and `holder_id` is that
edge's owner. The label says "P3 superseded by P2", which is true. It does
not say "superseded by S", which would be false for a depth-2 node.

**The guarded-read refactor.** `answer.py` gains:

```python
def _guarded_read(
    bundle_dir, concept_id, blocked, *, include_confidential, local_exemption
) -> tuple[dict[str, object], str] | None
```

It is the exact body of `answer.py:637-654`: the `blocked` check, the
`concept_path_for` read under `(OSError, UnicodeDecodeError)`,
`load_frontmatter` under a broad `except` (keeping the `noqa: S112`), and
`sensitivity.should_block`. The hit loop calls it. The walk's `read` is
`functools.partial` over the same helper. There is one function, so a hit
and a predecessor cannot diverge.

**Alternatives considered**

- Walking inside `answer.py`. That is harder to test and bloats the loop.
- Letting `history.py` do its own reads. That would put a second copy of the
  gates in a second place.
- Traversing through a gated node. Its successor's label would name a
  concept that was not admitted.

**Rationale**

The walk is testable with a dict-backed fake reader, and the security
property holds by construction.

### Decision 3: Attaching happens inside `_assemble_context`, right after the successor's re-read

**Choice**

`_assemble_context` (`answer.py:569`) gains three keyword-only parameters:

- `revision_history: bool = False`
- `deprecated: frozenset[str] = frozenset()`
- `history_truncated_out: list[str] | None = None`

The first two positional parameters and every existing keyword are
unchanged. The harness calls `_assemble_context(bundle, fused)`
(`evals/query_sufficiency/run_query_sufficiency_probe.py:297`,
`evals/query_entailment/run_query_entailment_probe.py:348,871`), and those
calls keep today's behavior.

**Inside the loop, for each `concept_id` in fused order**

1. Call `_guarded_read`. If it returns nothing, skip the concept, as today.
2. Build the hit's label, body and citation exactly as today
   (`answer.py:655-682`).
3. If `revision_history`, call `walk_history(concept_id, metadata,
   read=…, skip=fused_set | attached | rejected)`:
   - `fused_set` is `frozenset(concept_ids)`.
   - `attached` holds every predecessor already attached to an earlier
     successor, which gives the "first successor in fused order wins" rule.
   - `rejected` holds every id whose guarded read already failed, so it is
     never re-read.
   - The walk's reader wraps `_guarded_read` and records its failures into
     `rejected`.
4. For each returned predecessor:
   - resolve its date with `resolve_event_date(bundle_dir, pred.metadata,
     admit=…)`;
   - build its label (Decision 5);
   - append its block with a `holder` index that points at the hit block's
     position;
   - build its citation with `history=pred.role` and `confidential` computed
     exactly as for a hit (`answer.py:678-680`);
   - add its id to `attached`.
5. If `walk.truncated` and `history_truncated_out is not None`, append the
   **successor's** title.

**Dedupe against `fused_set`, not against the hits actually assembled.** A
fused hit that a gate skips (for being unreadable, blocked or confidential)
would fail the same gate as a predecessor, so the two sets agree. Using the
fused set removes any dependence on loop order. This generalizes the
proposal's rule, "a `revises` predecessor that is already a hit is not
repeated", to both relations. A `supersedes` target is never a hit unless
`--include-deprecated` is on, and in that case the walk does not run
(Decision 8).

**Where it runs in `answer()`**

`answer()` passes `revision_history=revision_history and not
include_deprecated`. The `deprecated` set it passes is the one it already
computes at `answer.py:1017-1019`. When `include_deprecated` is off, the set
is always computed, so this costs no extra walk.

**Rationale**

The successor's frontmatter is already in hand at the attach point, so
attaching history costs no bundle walk and no graph read.

### Decision 4: The budget is nested, and history is paid for by its successor and by today's unspent budget

This is **pure arithmetic** in `prompt_budget.py`. It is a config-free leaf,
and the arithmetic is shared with nothing else, but it belongs with
`fair_shares`.

```python
@dataclass(frozen=True)
class GroupShares:
    head: int                 # the hit's own share
    inner: tuple[int, ...]    # one per history block
    dropped: bool             # True: no history block of this group is sent

def nested_shares(
    head_sizes: Sequence[int],
    inner_sizes: Sequence[Sequence[int]],   # [] for a hit with no history
    inner_overheads: Sequence[int],         # frame chars the group's history adds
    *,
    budget: int,
) -> list[GroupShares]: ...
```

**The arithmetic, exactly**

1. `outer = fair_shares(head_sizes, budget=budget)`. These are today's shares
   over the hits, unchanged.
2. `slack = max(budget, 0) - sum(outer)`. This is the budget that today goes
   unspent, which is non-zero only when every hit fits.
3. For each group `i` with history:
   - `need_i = (head_sizes[i] - outer[i]) + inner_overheads[i] +
     sum(inner_sizes[i])`;
   - `extra = fair_shares([need_i …], budget=slack)`.
4. For each group `i` with history:
   - `pool_i = outer[i] + extra_i - inner_overheads[i]`;
   - if `pool_i <= 0`, the group is **dropped**;
   - otherwise `split = fair_shares([head_sizes[i], *inner_sizes[i]],
     budget=pool_i)`;
   - if `split[0] == 0` while `head_sizes[i] > 0` and `outer[i] > 0`, the
     group is **dropped**, because history never costs its successor the
     whole body;
   - otherwise `head = split[0]` and `inner = split[1:]`.
5. A **dropped** group gets `head = outer[i]` (its value today), and every
   inner share is `0`.
6. A group without history gets `head = outer[i]` and `inner = ()`.

**How the overhead is measured, by construction** (in
`answer._bound_with_history`)

- `overhead_hits = len(_user_content(hit_labels, q)) + max(len(_SYSTEM_PROMPT),
  len(_SUFFICIENCY_PROMPT))`. This is today's formula over the hit labels
  only, so `budget` is identical to today's.
- `inner_overheads[i]` is the **marginal** frame growth:
  `len(_user_content(L_i, q)) - len(_user_content(L_{i-1}, q))`, where
  `L_i` is the hit labels with the history labels of groups `≤ i` inserted
  in final order. The sum telescopes to the exact total growth.
- That growth covers the label text, the `[n] ` prefixes, the `\n\n` joins,
  and any digit growth when a later hit's number reaches `10`.

**Proof that an unrelated hit's bounded body is unchanged.** Steps 1 and 6
give every group without history `head = fair_shares(hit sizes,
budget)[i]`. That uses the same sizes and the same `budget`, because
`overhead_hits` is today's formula over the same labels. The bounded body is
`bounded_text(body, budget=head, windows=…)`, which is identical to today.

The block's **number** can shift, and it sits in the frame. Its **body**
cannot shift. The spec requirement is about the body.

**Proof that the prompt never exceeds today's planned window**

- The sum of the bodies is at most `Σ outer + Σ_kept (extra_i - e_i)`, which
  is at most `budget - Σ_kept e_i`.
- A dropped group sends no history label and spends at most `outer[i]`.
- The real frame depends only on the set of labels and the block count `N`.
  The numbering cost `Σ_{k≤N} len(f"[{k}] ")` has non-decreasing increments.
  So omitting a block, or dropping a group, can only make the real growth
  smaller than the marginal growth computed for the kept groups.
- Therefore `len(user_content) + max(system prompts)` is at most
  `budget + overhead_hits`, which is today's bound.

**Disclosure**

- An inner share that excerpts a block sets `excerpted=True` on its citation
  and adds it to `excerpted_titles`.
- An inner share of `0` on a non-empty body, a dropped group, or an omitted
  holder hit omits the history block. It is dropped from the prompt and from
  the citations, and it is added to `omitted_titles`. This is #882's rule
  unchanged (`answer.py:692-700`).
- History titles in both lists carry the suffix `" (earlier version)"`,
  because an earlier version usually shares its successor's title.

**Why `_bound_bodies` stays untouched**

`_bound_bodies`'s signature, its body, and its 2-tuple return are unchanged,
because `evals/query_attribution/run_query_attribution_probe.py:806,839`
calls it. A new `_bound_with_history(labels, bodies, holders, *, llm,
question) -> tuple[list[str], list[bool], list[bool]]` returns the bounded
bodies, the excerpted flags and the omitted flags.

`_assemble_context` calls `_bound_with_history` only when at least one
history block exists. Otherwise it calls `_bound_bodies`, exactly as today.
`llm=None` means no bound in both, which keeps the harness seam.

**Alternatives considered**

- **The proposal's literal split, with no slack**: `inner = fair_shares([head,
  *hist], share_i - e_i)`. Rejected. On the default window most hits fit, so
  `share_i == len(head)`, and every history block would then be excerpted or
  omitted while thousands of chars sat unspent. With the slack rule, the
  proposal's split is still what happens whenever the window is tight.
- **A flat pool**. Rejected by the proposal, because it shrinks unrelated
  hits.
- **The successor first, and history only from what is left**. That starves
  history under any tight window, and water-fill already protects a small
  successor.

### Decision 5: Labels and date display strings

The label of a history block is:

```
[concept_id: <P> — <P title><synthesis_note><history_note>]\n
```

`synthesis_note` is today's Insight note (`answer.py:663-668`), unchanged.
`history_note` is built by `history.label_note(role, holder_id, resolved,
current)`:

| Role | `history_note` |
| --- | --- |
| `superseded` | ` (earlier version, superseded by concept_id: <H>; <date phrase>; no longer current)` |
| `refined`, P not deprecated | ` (earlier version, refined by concept_id: <H>; <date phrase>; still current)` |
| `refined`, P in `deprecated` | ` (earlier version, refined by concept_id: <H>; <date phrase>; no longer current)` |

**The date phrase**, from `history.date_phrase(resolved)`:

| State | Phrase |
| --- | --- |
| `dated` | `event date 2026-07-14` |
| `multiple` | `event dates 2026-07-01 to 2026-07-14` (earliest to latest, ISO) |
| `missing` or `none-reached` | `event date unknown` |

**Why `concept_id: <H>` and not a bare id or a title**

- The model sees the exact token that heads H's own block, so it can tie the
  chain together.
- If the model copies it into prose, #193's scaffold stripper already
  removes it (`_SCAFFOLD_INLINE_RE`, bare form
  `concept_id[ \t]*:[ \t]*[^\s,;:!?)\]]+`, `answer.py:198-235`).
- A bare `concepts/x` path would slip past the stripper.
- A title is ambiguous across versions.

**Why currency comes from `deprecated`, not from the role**

- A `supersedes` target is always deprecated (`lifecycle.py:84`), so the
  role alone is enough for that row.
- A `revises` target can itself be superseded elsewhere. "Still current"
  would then be false.
- The set is already computed (Decision 3), so this costs nothing.

**The successor's own label is unchanged byte for byte.** The system prompt
is unchanged.

### Decision 6: `Citation.history`, attribution, and the CLI surface

- `Citation` gains `history: Literal["superseded", "refined"] | None = None`
  after `confidential` (`answer.py:351`). It follows the same default-`None`
  pattern, so every construction site stays valid.
- History blocks are appended to `labels`, `bodies` and `citations` in the
  same iteration, so the index alignment that `_split_attribution` relies on
  (`answer.py:1118-1131`) holds.
- The `USED:` subset rule and the `absent`/`unparsed` fallback apply
  unchanged.
- `AnswerResult` gains `history_truncated_titles: list[str] =
  field(default_factory=list)`. It holds the titles of the **successors**
  whose chain continues beyond what was shown, in fused order, and is `[]`
  when the walk is off. It is a list rather than a bool for the same reason
  `excerpted_titles` is one (`answer.py:438-442`). It is threaded onto every
  return that follows the assembly (`answer.py:1059`, `1091`, `1132`).
- `context_block_count` counts the history blocks, because it is
  `len(context_blocks)`. `fused_count` and the hit counts do not.
- **The CLI** (`cli/main.py:14524-14541`):
  - The marker `history = {"superseded": " [superseded]", "refined": "
    [refined]"}.get(citation.history, "")` is rendered first:
    `f"{history}{synthesis}{partial}{marker}"`.
  - One stderr notice after the omitted notice (`cli/main.py:14454-14468`)
    when `result.history_truncated_titles` is non-empty: `openkos query: the
    revision history of N document(s) (T1, T2) goes back further than the
    earlier versions shown; the answer did not see the rest.`

### Decision 7: `--save` marks history in `## Related`, with a byte-identical default

- `okf.build_concept` (`okf.py:699`) gains `related_notes: Mapping[str, str]
  | None = None`. A bullet reads `related_notes.get(ref, related_note)`
  (`okf.py:785`).
- A key that is not in `provenance` raises `ValueError`. That is a caller
  bug, and it fails closed like the builder's other checks.
- `None`, or an empty mapping, produces byte-identical output. Ingest never
  passes the argument.
- `stage_filed_answer` (`application/query.py:579-588`) passes
  `related_notes={c.concept_id: f"earlier version ({c.history}) cited as
  history for this answer" for c in citations if c.history}`, or `None` when
  that is empty.
- The rendered bullet is `- [concepts/p](/concepts/p.md) — earlier version
  (superseded) cited as history for this answer`.
- `provenance:` stays a flat list of ids. The high-water-mark loop
  (`application/query.py:546-571`) already folds over every citation,
  history included.
- There is no new frontmatter key, so merge, forget, purge and lint need no
  change.

### Decision 8: `--include-deprecated` suppresses the walk entirely

`answer()` computes `walk = revision_history and not include_deprecated`.
Under the flag, the predecessors are already ordinary hits. A history label
would contradict their hit status, and the flag's "zero added cost,
byte-identical" contract (`answer.py:950-951`) stays true.

### Decision 9: The config key `revision_history`

- `config.py` gains `DEFAULT_REVISION_HISTORY: Final = False`, with a
  docstring that says the behavior is **unmeasured** and that it is off
  until a harness measures it.
- `read_config` reads it with `raw.get("revision_history")` and validates it
  as a bool only, reusing the narrow `isinstance(x, bool)` guard and its
  message shape (`config.py:1470-1477`). An explicit null falls back to the
  default.
- The `Config` field is `revision_history: bool = DEFAULT_REVISION_HISTORY`,
  added **last and defaulted**. The six hand-built `config.Config(**fields)`
  test helpers (for example `tests/unit/cli/test_query_save.py:105-133`)
  therefore stay valid without churn.
- `run_query` passes `revision_history=cfg.revision_history` next to
  `sufficiency_check` (`application/query.py:159-162`).
- `answer()` gains the keyword-only `revision_history: bool = False`. The
  module stays config-free (the guard is `tests/unit/retrieval/test_answer.py:1457`).
- **The template.** A commented `# revision_history: false` block goes after
  `sufficiency_check` (`templates/openkos.yaml.template:54`). It states what
  the key does, that it is off by default, that it is **unmeasured**, and
  that it is not recommended yet.

**Leftover-key behavior.** `read_config` reads keys by name and never
rejects unknown top-level keys (`config.py:1254-1277`). A stale
`revision_history: true` left behind after a revert is therefore **ignored**,
and the rollback note needs no user action.

### Decision 10: An ADR is written

**ADR-0026**
(`docs/adr/0026-superseded-concepts-re-enter-answers-only-as-labelled-history.md`,
Proposed, 2026-09-26) records why. It is the first path by which a hidden
concept re-enters synthesis. Filed Insights will persist provenance that
points at superseded concepts. And the behavior is opt-in until it is
measured. Both conditions of the ADR gate hold: it is a trade-off against
status-aware hiding, and saved provenance in users' bundles is hard to
reverse.

## Data Flow

```
run_query ──cfg.revision_history──► answer(…, revision_history)
                                      │ walk = revision_history and not include_deprecated
                                      ▼
       FTS + dense → filter_hits(deprecated|confidential) → fuse → fused_ids
                                      ▼
                         _assemble_context(fused_ids, …, revision_history=walk, deprecated)
                                      │
   for each id ──► _guarded_read ──► hit block (label unchanged)
                     │  (blocked / unreadable / should_block)
                     └─ walk? ─► history.walk_history(meta, read=_guarded_read, skip)
                                   │ BFS ≤3 deep, ≤3 blocks, dead-end on refusal
                                   ▼
                               event_dates.resolve_event_date(pred.meta, admit=gate)
                                   ▼
                               history block (label + note), Citation(history=role)
                                      ▼
            no history? ─► _bound_bodies (today)   history? ─► _bound_with_history
                                                                  └► prompt_budget.nested_shares
                                      ▼
            omit / excerpt (#882) ─► _user_content (numbering) ─► sufficiency? ─► llm.chat
                                      ▼
            _split_attribution (USED:) ─► citations subset ─► AnswerResult(history_truncated_titles)
                                      ▼
            CLI: [superseded]/[refined] markers, truncation notice; --save: related_notes
```

The sequence for one successor S that supersedes P, where P revises Q:

```
answer.py          history.py          _guarded_read        event_dates.py
   │ read S ─────────────────────────────►│ ok(meta_S)
   │ walk(S, meta_S) ─►│                    │
   │                   │ read P ───────────►│ ok(meta_P)   (supersedes, depth 1)
   │                   │ read Q ───────────►│ ok(meta_Q)   (revises, depth 2, holder P)
   │◄── [P(superseded,holder S), Q(refined,holder P)], truncated=False
   │ resolve(meta_P, admit) ───────────────────────────────►│ reads P's sources/…
   │ resolve(meta_Q, admit) ───────────────────────────────►│ reads Q's sources/…
   │ blocks: [S] [P …superseded by concept_id: S…] [Q …refined by concept_id: P…]
```

## File Changes

| File | Action | Description |
| --- | --- | --- |
| `src/openkos/event_dates.py` | Create | `DateState`, `ResolvedEventDate`, `resolve_event_date` (the one-hop rule, `admit` gate) |
| `src/openkos/resolution/decision_revision.py` | Modify | `DateState = event_dates.DateState`, replacing the local `Literal` (line 58) |
| `src/openkos/retrieval/history.py` | Create | `walk_history`, `Predecessor`, `HistoryWalk`, `date_phrase`, `label_note`, constants |
| `src/openkos/prompt_budget.py` | Modify | `GroupShares`, `nested_shares` (pure) |
| `src/openkos/retrieval/answer.py` | Modify | `_guarded_read` extraction; attach in `_assemble_context`; `_bound_with_history`; `Citation.history`; `AnswerResult.history_truncated_titles`; the `answer(revision_history=)` kwarg; history-title suffix in the disclosures |
| `src/openkos/config.py` | Modify | `DEFAULT_REVISION_HISTORY`, the `Config.revision_history` field (last, defaulted), read and validation |
| `src/openkos/templates/openkos.yaml.template` | Modify | The commented key and the unmeasured, not-recommended notice |
| `src/openkos/application/query.py` | Modify | `run_query` threads the key; `stage_filed_answer` passes `related_notes` |
| `src/openkos/model/okf.py` | Modify | `build_concept(related_notes=None)` |
| `src/openkos/cli/main.py` | Modify | The `[superseded]`/`[refined]` markers and the truncation notice |
| `docs/cli.md` | Modify | A `query` note: the key, the markers, and that it is unmeasured and not recommended |
| `docs/adr/0026-…md`, `docs/adr/README.md` | Create/Modify | ADR-0026 and its index row (written in this phase) |
| `tests/unit/test_event_dates.py` | Create | Resolver states, gates, hop bound, leaf-import guard, alias identity |
| `tests/unit/retrieval/test_history.py` | Create | Walk order, depth, cap, cycle, skip, dead-end, truncation, label and phrase strings |
| `tests/unit/test_prompt_budget.py` (existing or new) | Modify | `nested_shares` properties |
| `tests/unit/retrieval/test_answer.py` | Modify | Attach, guards, dedupe, budget isolation, attribution, off-path read-count and golden |
| `tests/unit/retrieval/test_layering.py` | Create | `retrieval` never imports `resolution`; a positive assertion that it imports `openkos.event_dates` |
| `tests/unit/test_config.py`, `tests/unit/application/…`, `tests/unit/model/test_okf.py`, `tests/unit/cli/test_query*.py` | Modify | The key, threading, `related_notes`, markers and notice |

## Interfaces / Contracts

```python
# retrieval/answer.py
@dataclass(frozen=True)
class Citation:
    concept_id: str
    title: str
    excerpted: bool = False
    confidential: bool = False
    history: Literal["superseded", "refined"] | None = None

# AnswerResult (appended, defaulted)
history_truncated_titles: list[str] = field(default_factory=list)

def answer(question: str, *, …, sufficiency_check: bool = False,
           revision_history: bool = False) -> AnswerResult: ...

# model/okf.py
def build_concept(*, …, related_note: str = "source this was extracted from",
                  type_alternative: str | None = None,
                  related_notes: Mapping[str, str] | None = None) -> str: ...
```

## Testing Strategy

Strict TDD applies, with the runner `uv run pytest`. Every test below is
written RED first. **Mutation targets** name the line that the test must
catch when it is reverted or mutated, per the project's
"a test that passes first try" practice. Clear `__pycache__` between a
mutation and its revert.

| Slice | Test | Mutation target it must catch |
| --- | --- | --- |
| 1 | The resolver reaches `dated`, `missing` (absent, malformed, unreadable or refused Source), `multiple` (earliest/latest), and `none-reached` (empty provenance or intermediates only) | Swapping the `missing`/`multiple` check order; dropping `admit` |
| 1 | One hop: an intermediate's Sources count, but a Source two intermediates away does not | Recursing past hop 2 |
| 1 | A refused intermediate is not traversed | Traversing on refusal |
| 1 | The leaf's imports are stdlib and `openkos.model.*` only (AST); `decision_revision.DateState is event_dates.DateState` | A re-introduced local `Literal` |
| 2a | The walk's order with mixed edges and siblings (`supersedes` before `revises`, then id; BFS) | The sort key (`revises` first, or reversed id) |
| 2a | Depth: a 4-chain attaches 3, `truncated=True`; a 3-chain has `truncated=False` | `depth == MAX_DEPTH` becomes `>` |
| 2a | Cap: 5 direct predecessors attach 3 and set `truncated`; exactly 3 do not set it | The cap check placement, and a vacuous truncation flag |
| 2a | A mutual `revises` cycle terminates; a self-edge is ignored | Removing the `visited` add |
| 2a | `skip` members are neither attached nor expanded | Expanding a skipped node |
| 2a | A refused node is a dead end: its predecessor is never read (the fake reader records calls) | Enqueuing a refused node's edges |
| 2a | `nested_shares`: groups without history equal `fair_shares` (property over random inputs); the total fits the budget; a dropped group keeps its outer share; no history gives exactly `fair_shares` | Using `budget - Σoverhead` for the outer split; skipping the slack; the dropped-group fallback |
| 2a | `date_phrase`/`label_note` exact strings (all 3 phrases, 3 note rows) | Any wording drift |
| 2b | `_guarded_read` refactor: the existing `_assemble_context` tests stay green, unchanged | — (a regression net) |
| 2b | S supersedes P gives P its own block after S, the superseded label, `Citation.history`, and `fused_count` excludes P | Attaching before the successor; counting P |
| 2b | A confidential predecessor (fresh frontmatter) and a `blocked` predecessor are absent from blocks and citations; with `include_confidential`/`local_exemption` they are present | Calling `read_text` directly instead of `_guarded_read` |
| 2b | A confidential Source's date renders `event date unknown` | Dropping the `admit` closure |
| 2b | A `revises` predecessor that is also a hit appears once; one shared by S1 and S2 attaches under S1 | Skip without `fused_set`/`attached` |
| 2b | Budget isolation: 4 hits with a small window, history on hit 2 leaves hits 1, 3 and 4 with byte-identical bounded bodies versus a history-free bundle | The overhead measured over all labels (the flat pool) |
| 2b | Fit: `len(user_content) + max(system)` ≤ today's bound across a sweep of windows | The marginal-overhead computation |
| 2b | A zero-share history block is omitted and disclosed with ` (earlier version)`; an excerpted block is `excerpted` | The suffix; the omission rule for history |
| 2b | The `USED:` naming of a history block keeps its citation with `history` set; `absent` keeps all | — |
| 2b | **Off path.** With a `Path.read_text` spy counting bundle reads, `answer()` default and `revision_history=False` on a bundle with chains make exactly one read per fused hit, and the captured messages are equal to those of a chain-free twin bundle. With the key on, the count is higher, which proves the spy is not vacuous | Calling the walk unconditionally |
| 2b | `include_deprecated=True` with `revision_history=True` produces no history and the same messages as `revision_history=False` | Dropping the `not include_deprecated` term |
| 2b | `retrieval` does not import `resolution` (AST); `answer.py` does not import `config` (existing) | — |
| 3 | `read_config`: absent or null gives `False`; `true` is read; `1`, `"yes"` and a list are refused with the bool message | Truthy coercion |
| 3 | `run_query` passes `cfg.revision_history` (a spy on `answer`) | A hard-coded `False` |
| 3 | `build_concept(related_notes=None)` is byte-identical to a golden; a mapped ref renders the note; an unknown key raises | The default path changes bytes |
| 3 | `stage_filed_answer` with a history citation files it in `provenance:` and writes the marked bullet; with no history it is byte-identical | — |
| 3 | CLI: the `[superseded]`/`[refined]` markers and their order; the truncation notice appears only when titles are present | The marker mapping |
| 3 | The template carries the key commented out and its unmeasured notice (the existing template tests, if any, extended) | — |

## Threat Matrix

N/A. The change adds no routing, shell, subprocess, VCS/PR automation,
executable-file classification or process-integration boundary. The
sensitivity boundary it does touch is designed in Decisions 1–3, and the
slice 2b tests pin it.

## Migration / Rollout

No migration is needed. The key is absent from every existing
`openkos.yaml`, and absence means off.

**Rollback.** Revert the slice commits in reverse order. A stale
`revision_history:` line is ignored by `read_config` (Decision 9), so the
user does nothing. Insights filed in the interim keep a valid flat
`provenance:` and prose `## Related` bullets, which old code reads as
ordinary provenance.

**Slices** (auto-chain, stacked-to-main). The estimates are authored lines,
counting this repo's docstring density. Each slice is green on its own.

| Slice | Content | Estimate |
| --- | --- | --- |
| 1 | `event_dates.py`, the `DateState` alias, `test_event_dates.py` (ADR-0026 is already written in design; the slice commits it) | ~280 (≈100 source, ≈150 tests, ≈30 alias and docs) |
| 2a | `retrieval/history.py`, `prompt_budget.nested_shares`, their tests. No caller yet | ~380 (≈170 source, ≈210 tests) |
| 2b | `answer.py`: `_guarded_read`, attaching, `_bound_with_history`, `Citation.history`, `history_truncated_titles`, the kwarg (no caller enables it), the retrieval layering test, answer tests | ~400 (≈150 source, ≈250 tests) |
| 3 | The config key and template, `run_query`, CLI markers and notice, `build_concept` `related_notes`, `stage_filed_answer`, `docs/cli.md` | ~320 |

The total is about 1,380 lines. The proposal forecast 800–950. The
difference is the slack rule, the guarded-read extraction, and the docstring
density that this codebase keeps. **This design splits the proposal's
retrieval slice in two (2a and 2b)**, so each slice stays near the 400-line
budget.

## Open Questions / spec alignment

These are spec-wording items. None of them blocks the design; the spec
author should reconcile them.

- [ ] **The nested budget.** The spec says the successor's share is split
  across `[successor, *history]`. This design gives that split the
  successor's share plus a fair slice of today's **unspent** budget, and it
  drops history (disclosed) rather than let it take the successor's whole
  body (Decision 4). The unrelated-hits guarantee is unchanged. The spec
  scenario should say "the successor's share, plus unspent budget".
- [ ] **The label's successor.** The spec says the label names "the
  successor concept id". This design names the **edge holder**, which is
  the immediate later version. For a depth-1 predecessor that is the hit
  itself (Decision 2). The spec should say "the concept that supersedes or
  revises it".
- [ ] **Currency.** The spec pins `revises` to "still current". This design
  says "no longer current" when a refined predecessor is also deprecated
  elsewhere (Decision 5). A scenario should cover that case.
- [ ] **Truncation.** The spec ties the signal to the block cap only. This
  design also reports a chain cut at depth 3, in the field
  `history_truncated_titles` (successor titles). A disabled walk reports
  `[]`.
- [ ] **Dedupe.** The spec states it for `revises`. This design applies it to
  any predecessor already in the fused list. That is equivalent in practice,
  because a `supersedes` target is a hit only under `--include-deprecated`,
  and there the walk does not run.
