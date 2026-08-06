# Exploration: surface-merged-body-contradictions

**Issue**: #409 — a merge stacks contradictory bodies and hides the disagreement
from contradiction detection.
**Status**: complete (revised after a gate-failure correction pass).
**Phase**: `sdd-explore`. No production code was written; no behavior changed.

Two load-bearing claims in the first pass were false. Both are corrected below
with file:line evidence. Everything else held up and is carried forward.

## Current state

`build_merged_document` (`src/openkos/model/okf.py:1021-1022`) blindly appends
the absorbed body under a `## Merged content ({absorbed_id})` heading:

```python
separator = f"\n\n## Merged content ({absorbed_id})\n\n"
merged_body = survivor_body.rstrip("\n") + separator + absorbed_body
```

Nothing compares the two bodies. `prepare_merge` (`cli/main.py:5503-5658`)
recomputes an OUTBOUND-relations report (`dropped_self_loops` /
`deduped_collisions`, from `okf.merge_relations`) for the preview, but never
touches the bodies.

`_candidate_pairs` (`resolution/contradiction.py:159-206`) derives judgeable
pairs only from `store.edges()` typed edges (excluding `derived_from`), deduped
by `frozenset({source_id, target_id})`. It is strictly pair-of-nodes: after a
merge there is one node, so the disagreement is invisible to it.

The absorbed body is **not** lost. `MergeLedgerEntry` (`okf.py:454-499`) stores
`absorbed_snapshot` (the full absorbed document text) and `survivor_before` (the
full survivor text immediately before that merge), appended LIFO by `plan_merge`
(`bundle/merge.py:129-165`). `decode_merged_from` returns the full flat history
from the survivor's own current frontmatter — no recursion needed. Both
contradictory bodies are therefore recoverable deterministically from the
survivor alone: no LLM at merge time, no second node, no re-ingest.

## Correction 1 — sensitivity gating (prior claim was FALSE)

**Prior claim**: the survivor's current on-disk sensitivity is `>=` every
historical ledger value and the absorbed document's original sensitivity, by
`combine_sensitivity` monotonicity, so gating on the survivor's current
frontmatter alone is sufficient.

**Why it is false.** The induction holds only across merge events. It breaks
across `set-sensitivity`, which can LOWER a concept's sensitivity —
`src/openkos/cli/main.py:4519-4548`:

```python
direction = okf.sensitivity_direction(current, level)
confirm_enabled = not auto and cfg.review
prompt_will_run = confirm_enabled and sys.stdin.isatty()
if direction == "lower" and not prompt_will_run and not allow_downgrade:
    ...  # refuse
```

Lowering is permitted whenever the confirm prompt actually runs (interactive
TTY), or unconditionally with `--allow-downgrade`. This is deliberate, shipped
ADR-0008 behavior, not a bug.

Concretely: a survivor absorbs a `confidential` document and recomputes to
`confidential` at merge time; a human later runs `set-sensitivity <survivor>
public` and confirms. The survivor's current frontmatter reads `public`, but
`entry.absorbed_snapshot` still embeds the original confidential body. Gating
solely on the current value would ship confidential text to the LLM — exactly
the fail-closed posture this design must not weaken.

**`sensitive_concept_ids` cannot cover this.** Confirmed by direct reading
(`sensitivity.py:144-186`): it walks `okf._iter_docs(bundle_dir)` once and reads
only `(scan.metadata or {}).get("sensitivity")` per file — the current on-disk
value. It never inspects `merged_from` / `MergeLedgerEntry` fields
(`sensitivity.py:176-184`). It is structurally incapable of seeing
ledger-embedded historical values.

**Corrected design.** A new ledger-aware gate is required, and belongs in
`sensitivity.py` as a sibling to `blocks_llm_send` / `should_block` — the
module's own docstring already argues against scattering this authority.

It does **not** need to reparse `entry.absorbed_snapshot`'s frontmatter.
`plan_merge` (`bundle/merge.py:151`) sets `entry.sensitivity_after` to
`str(merged_metadata.get("sensitivity"))`, and `build_merged_document` computed
that as `combine_sensitivity(survivor.sensitivity, absorbed.sensitivity)`
(`okf.py:1006-1008`) — a max over both sides at merge time. So
`entry.sensitivity_after` already dominates the absorbed document's original
sensitivity by construction.

The gate that is necessary and sufficient, **per ledger entry**, ranks
(fail-closed, via the same `okf._rank` semantics `should_block` already uses for
missing/blank values) the max of three values:

1. the survivor's current on-disk `sensitivity` — covers anything established by
   merges or raises that happened after this entry;
2. `entry.sensitivity_before` — the survivor's own level immediately before this
   specific merge, frozen in the ledger and never mutated by a later downgrade;
3. `entry.sensitivity_after` — which already dominates the absorbed document's
   own original sensitivity, per the derivation above.

It must be evaluated per entry, not once per survivor: a later downgrade can
have lowered the current value below what a specific historical entry
established. The entry's own frozen values act as an independent floor, giving a
fail-closed answer after any number of legitimate later downgrades.

It composes with the existing `include_confidential` / `local_exemption` escape
hatches identically to `sensitive_concept_ids`'s own contract
(`sensitivity.py:170-175`): either hatch short-circuits to "never blocked"
immediately, before any ledger entry is inspected. No new escape-hatch semantics
are invented.

Suggested shape:

```python
def merged_content_blocked(
    current_sensitivity: object,
    entry: okf.MergeLedgerEntry,
    *,
    threshold: str = "confidential",
    include_confidential: bool = False,
    local_exemption: bool = False,
) -> bool: ...
```

called once per candidate entry from the new intra-document candidate builder,
mirroring how `_load_doc` already calls the shared `should_block` as its own
walk-independent re-check (`contradiction.py:287-292`).

This extends the same fail-closed authority (ADR-0003, ADR-0008) to a code path
the first pass overlooked; it does not weaken it.

## Correction 2 — `pair_ids` representation (prior claim was FALSE)

**Prior claim**: `(id, id)` `pair_ids` is impossible today via the typed-edge
path, so it is a free discriminator for an intra-document candidate.

**Why it is false**, verified at every link:

1. `_pair_key` (`resolution/contradiction.py:150-156`) is
   `first, second = sorted((source_id, target_id))` — no self-pair guard, so
   `_pair_key(x, x) == (x, x)`.
2. `okf.merge_relations` leaves a **pre-existing** survivor-side self-loop
   untouched; it only drops a self-loop the merge itself would newly create by
   retargeting `absorbed_id -> survivor_id`.
3. `bundle/relations.py:33-34` states it outright: a `file -> file` self-loop
   already on a third-party file is reachable, because "the codec does not reject
   self-loops; only the `relate` CLI does."
4. Typed-edge projection (`graph/sqlite_graph.py:450-461`) applies exactly one
   filter:

   ```python
   for relation in relations:
       if relation.target in node_ids:
           typed_edges.add((source_id, relation.target, relation.type))
   ```

   A self-loop's target is the document's own id — always a known node — so
   `(x, x, type)` is inserted and reaches `store.edges()`.
5. `_candidate_pairs` keeps any edge with `relation_type is not None` and
   `!= "derived_from"`. A typed self-loop passes both filters.

So `(x, x)` is producible today from an ordinary hand-authored or
merge-preserved typed self-loop, independent of this change. Overloading the
tuple shape would make a self-loop-derived candidate structurally
indistinguishable from a merged-content candidate in `ContradictionVerdict` and
in every renderer.

### New finding — reported, not fixed

A typed self-loop relation, if present in a bundle today, already reaches
`find_contradictions` and is judged by calling `_load_doc(bundle_dir, x)` twice
for the same id (`contradiction.py:521-532`), producing a prompt that asks the
LLM whether a document contradicts itself (`contradiction.py:95-113, 297-318`).

This is pre-existing latent behavior, not introduced by this change. It is
unclear whether it is intended (a degenerate case that always resolves
`CONSISTENT` / `UNCERTAIN`) or worth a fail-closed guard excluding
`source_id == target_id` in `_candidate_pairs`, mirroring the self-loop drop
`merge_relations` already performs for the outbound case.

**Filed as #411.** Out of scope to fix here — but `sdd-propose` and
`sdd-design` must not assume `(id, id)` is otherwise impossible, because it
demonstrably is not. #411 carries a runnable reproduction that needs no LLM:
a hand-authored self-loop in `relations:` yields
`candidate pairs: [('concepts/stoicism', 'concepts/stoicism')]`.

If #411 lands first and excludes self-pairs in `_candidate_pairs`, the
ambiguity this correction guards against disappears — but the discriminator
requirement below does **not**: a merged-content candidate still needs an
explicit field, because it is not derived from an edge at all.

### Options evaluated

**(a) A dedicated non-None discriminator field.** Keep
`merged_absorbed_id: str | None = None` on `ContradictionVerdict`, and make its
non-`None`-ness — never the shape of `pair_ids` — the only signal a caller may
rely on. `pair_ids` may still be `(concept_id, concept_id)` for readability, but
every consumer must branch on `merged_absorbed_id is not None`.

This is not free, as the first pass claimed. It becomes the single load-bearing
piece of logic preventing two genuinely different candidate kinds from being
conflated, so it needs a dedicated test asserting a self-loop-derived verdict and
a merged-content verdict for the same concept id in one run are rendered
distinctly.

**(b) A separate `IntraDocumentVerdict` dataclass** with a parallel
candidate/judging/rendering path. Confirmed on rereading `find_contradictions`
(`contradiction.py:417-547`): this would duplicate `_MAX_PAIRS` accounting, the
`total_pair_count > len(verdicts)` cap-truncation signal, and the
`deprecated` / `confidential` exclusion wiring across two independently
maintained paths — the drift risk this codebase's docstrings repeatedly warn
against (`_echo_n_gt2_skip`: "ONE helper so the two walks can never drift apart
again"). That cost is structural — whole functions duplicated, versus one field
plus one test — so the balance still favors (a).

**(c) A sentinel-encoded id** (e.g. `pair_ids = (id, f"{id}#{absorbed_id}")`) —
rejected. It fabricates a non-existent id that could collide with a real bundle
path and leaks formatting assumptions into every consumer.

**Recommendation: option (a)**, with an explicit docstring warning on
`ContradictionVerdict` against ever falling back to `pair_ids` equality.

## Characterization carried forward (unchanged, still correct)

**`_load_doc` cannot be reused as-is.** It takes `(bundle_dir, concept_id)` and
reads a path. The two bodies for an intra-document candidate are
`entry.survivor_before`'s body and `entry.absorbed_snapshot`'s body — in-memory
ledger strings. A new sibling helper is required, with the same
degrade-on-parse-failure contract, parsing via `okf.load_frontmatter` instead of
reading a path, and calling the new `merged_content_blocked` gate from
Correction 1 instead of `should_block`.

**`_MAX_PAIRS`.** Merge intra-document candidates into the same ordered list
before the single `_MAX_PAIRS` slice, preserving one `total_pair_count` and one
cap-truncation signal rather than a second parallel cap.

**Nested merges are linear, not quadratic.** `merged_from` is a flat LIFO list;
`plan_merge` always appends `[*existing_entries, entry]`
(`bundle/merge.py:160-162`), so a survivor's current ledger already lists every
historical entry — no recursion. Exactly `len(merged_from)` candidates per
survivor: for each entry, pair `entry.survivor_before`'s body against
`entry.absorbed_snapshot`'s body. Not the fully-stacked current body against
every fragment, and not all-pairs-of-fragments.

**Unmerge parity is unaffected.** No new `MergeLedgerEntry` field, no schema
bump; `plan_unmerge` restores `tail.survivor_before` / `tail.absorbed_snapshot`
verbatim exactly as today. A future schema v4 caching "already compared" state is
a real follow-up question, explicitly out of scope.

**Report-half placement.** `okf.build_merged_document` stays pure and returns
`(metadata, body)` only. The body-stacking report is recomputed in
`prepare_merge` / `_prepare_one_merge`, surfaced via new `PreparedMerge` fields,
and echoed by the `merge` command block (`cli/main.py:6307`) and
`_format_merge_preview_line` (`:1146`) — following the established
`dropped_self_loops` / `deduped_collisions` pattern exactly. Whether
`_adjudication_payload`'s `--json` shape also gains the signal is an open
compatibility call for `sdd-propose`.

## Affected areas

- `src/openkos/model/okf.py:1021-1022` (`build_merged_document`), `:358-368`
  (`combine_sensitivity`), `:454-499` (`MergeLedgerEntry`)
- `src/openkos/bundle/merge.py` (`plan_merge` / `plan_unmerge`)
- `src/openkos/cli/main.py:5447-5658` (`PreparedMerge` / `prepare_merge`),
  `:6307` (merge echo), `:1146` (`_format_merge_preview_line`),
  `:1037-1059` (`_adjudication_payload`), `:8431-8643` (`contradictions`)
- `src/openkos/resolution/contradiction.py` — `ContradictionVerdict`
  (`:130-147`), `_pair_key` / `_candidate_pairs` (`:150-206`), `_pairs_and_types`
  (`:403-414`), `_load_doc` (`:240-294`), `_build_messages` (`:297-318`),
  `find_contradictions` (`:417-547`), `_MAX_PAIRS` (`:71-78`)
- `src/openkos/sensitivity.py` — new ledger-aware gate;
  `sensitive_concept_ids` (`:144-186`) itself unchanged, just insufficient alone
- `docs/cli.md:315-333` (merge), `:190-213` (adjudicate / contradictions)
- `docs/adr/README.md` — an ADR is plausible if the `ContradictionVerdict`
  extension and the new gate are judged significant; not pre-decided here

## Tests

- `tests/unit/model/test_okf.py` (36 hits) — likely unchanged if
  `build_merged_document` stays byte-identical.
- `tests/unit/cli/test_merge_core.py` (9 tests) — new `PreparedMerge` report
  fields and the `merge` echo line.
- `tests/unit/resolution/test_contradiction.py` (66 tests) — largest surface:
  intra-document candidate construction from `merged_from`; the
  `merged_absorbed_id`-as-discriminator contract; `_MAX_PAIRS` with a mixed
  candidate list; nested-merge linearity; **a candidate whose
  `sensitivity_before` / `sensitivity_after` is confidential stays blocked even
  after the survivor's current value was lowered via `set-sensitivity
  --allow-downgrade`** (Correction 1); **a self-loop-derived `(x, x)` verdict and
  a merged-content verdict for the same id in one run are never conflated**
  (Correction 2).
- Sensitivity tests — new coverage for the ledger-aware gate.
- `tests/unit/cli/test_contradictions.py`, `tests/unit/cli/test_adjudicate.py` —
  rendering assertions.
- Behavior-first grounding: the `concepts/apatheia` / `apatheia-2` collision in
  `examples/good-life-demo/raw/`, with a stub `LLMBackend` per the module's
  config-free-leaf convention — never a real Ollama call in unit tests.

## Docs

`docs/cli.md`'s merge, adjudicate, and contradictions sections each need a
paragraph. `docs/knowledge-object-model.md` has no existing `merged_from` or
"Merged content" section (grep-confirmed), so any coverage there is new content
rather than an edit.

## Changed-lines forecast (budget: 1200)

| Slice | Estimate |
| --- | --- |
| Report half — `PreparedMerge` fields, recompute, 3 echo sites, docs, tests | ~150–250 |
| Detection half — discriminator, candidate builder, ledger-body helper, ledger-aware sensitivity gate, CLI rendering, docs, tests | ~400–600 |
| Combined | ~550–850 |

Inside budget as one slice, but the two halves have clean, independent
boundaries: the report half needs nothing from the detection half, and the
detection half's ledger logic is independent of what the merge CLI prints. A
two-PR chain (report half first — smaller, lower risk, immediately useful) is
recommended and is more clearly justified now that the detection half grew.

## Risks

- The self-loop / merged-content collision is pre-existing latent behavior this
  design must not paper over. File it as its own issue rather than folding a fix
  into this change.
- The ledger-aware sensitivity gate is a second authority alongside
  `sensitive_concept_ids` / `should_block`. `sensitivity.py`'s module docstring
  already argues against duplicated gates, so this must be documented there as a
  deliberate addition covering a gap those two cannot see — not an accidental
  third copy.
- `merged_absorbed_id is not None` is easy to get right today and easy to erode
  later. It needs an explicit docstring warning against `pair_ids` equality.
- `_adjudication_payload`'s `--json` shape is a documented machine contract;
  whether it gains a report-half signal is an open call for `sdd-propose`.

## Ready for proposal

Yes — with both corrections as load-bearing requirements, not options.
`sdd-propose` and `sdd-design` must specify the ledger-aware sensitivity gate
(Correction 1) and must use `merged_absorbed_id`, never pair shape, as the sole
discriminator (Correction 2). They should also decide whether to file the
self-loop finding as a separate issue before or alongside this change.

Open decisions for `sdd-propose`:

1. Ship as one slice or a two-PR chain (chain recommended).
2. Whether `_adjudication_payload`'s `--json` gains a report-half signal.
3. Whether an ADR is warranted.
