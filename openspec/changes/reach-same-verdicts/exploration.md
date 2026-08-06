# Exploration: reach-same-verdicts

**Issue**: #427 — entity resolution is the accumulation path: make `SAME`
reachable, and correct the docs that promise a synthesis step.
**Decision (2026-08-06, recorded on the issue, FINAL — not re-litigated here)**:
lean on entity resolution; no cross-source synthesis step is being built.
Options 1 (build synthesis) and 3 (retire the thesis) are rejected. The thesis
stands — accumulation is what justifies compiling over retrieving — but it is
delivered through entity resolution rather than a separate pass.
**Phase**: `sdd-explore`. No production code written; no behavior changed.

One load-bearing claim from the first pass was false and is corrected below with
`file:line` evidence. Everything else held up and is carried forward.

## Current state

### The union mechanism exists and is sound

`build_merged_document` (`src/openkos/model/okf.py:977-1051`) unions every
list-valued frontmatter key. `provenance` is **not** in `_SPECIAL_KEYS`
(`okf.py:1029-1035`, verified verbatim: `sensitivity`, `freshness`, `timestamp`,
`MERGED_FROM_KEY`, `RELATIONS_KEY`), so it falls to the list branch at
`okf.py:1040-1043` and is unioned via `_union_dedup` — deduped and
order-preserving.

The CLI wiring reaches it end to end. Both `adjudicate --apply` / `--apply-same`
and `curate`'s Identity stage call the same chain: `find_candidates` →
`adjudicate_candidates` → on a `Verdict.SAME` for a 2-member group →
`_prepare_one_merge` → `_commit_one_merge` → `build_merged_document`. There is
exactly one path from a `SAME` verdict to merged provenance, not two that can
drift. An N>2 HIGH group is never auto-merged (`_echo_n_gt2_skip`); the pairwise
`merge` commands are printed for a human instead. That is out of scope here and
unaffected.

**Conclusion on #379 criterion 1**: the union path is not merely plausible, it
is exercised by existing merge-level tests. What is missing is an end-to-end
fixture proving `find_candidates` → `adjudicate_candidates` (`SAME`) → merge →
unioned `provenance` in one flow.

### Candidate generation is not the failure it looked like

`find_candidates` (`resolution/candidates.py:374-399`) is a deterministic,
stdlib-only generator over `_keyed_docs_by_type` (`candidates.py:208-246`) with
three tiers:

- **HIGH** — exact shared `normalize_key(title)` within a type partition.
- **ACRONYM** (#397) — one title's token is the initials of a word run in the
  other (`similarity.py:115-155`).
- **LOW** — token-subset near-match via `difflib.SequenceMatcher`
  (`similarity.py:35-79`), threshold `0.75`.

`_MAX_CANDIDATE_GROUPS = 50` (`candidates.py:88`, from #382) is **not** the
binding constraint at the measured scale: the 8-source run produced 2 candidate
groups, nowhere near the cap. It is a safety rail for a pathological corpus, not
the reachability bottleneck.

**Both measured pairs were nominated correctly and rejected correctly.**

- "MCP Workflows" / "Model Context Protocol" was nominated by the **ACRONYM**
  tier — `acronym_expansion_match`'s docstring (`similarity.py:138-145`) cites
  this exact pair as one of only two it has fired on in a real 19-document
  bundle.
- "Skill Creation Process" / "Skill Creator" was nominated by the **LOW** tier —
  `SequenceMatcher("creator", "creation").ratio() ≈ 0.8` clears the `0.75`
  threshold.

Both were adjudicated `DIFFERENT`, correctly: the adjudicator's system prompt
explicitly instructs it to reject part-whole and aspect relationships as
duplicates (`adjudication.py:52-58`). Candidate generation worked. The
adjudicator was right. **The corpus contained no true duplicate.** That is the
honest answer for same-type, title-related pairs, and it means #427's Half A is
mostly a proof obligation, not a repair.

### The one real reachability gap: strict type partitioning

`_keyed_docs_by_type` partitions eligible documents by **exact** declared
`okf_type` before any tier runs — its own docstring says "partitions what
survives by exact `okf_type`" (`candidates.py:208-246`). This is deliberate and
pinned by a test:
`test_cross_type_identical_normalized_title_produces_no_candidate`
(`tests/unit/resolution/test_candidates.py:167-176`) asserts a `Concept` and an
`Entity` with **identical** normalized titles produce zero candidates.

`docs/knowledge-object-model.md:197-210` documents that OKF type classification
is sometimes "a close call" — its own example is a seminar defensibly `Event` or
`Project` — and the compiler emits a `type_alternative` hint precisely because
classification is not deterministic. Two ingest runs over two sources describing
the same real-world entity, classified into two different types by two separate
extraction passes, is therefore a plausible true-duplicate class that
`find_candidates` cannot see by construction: never nominated, so never
adjudicated.

This is a candidate-**generation** gap. It is also the only one found: HIGH
covers exact titles, ACRONYM covers initialism expansion, LOW covers token-level
near matches. A purely semantic match with no shared tokens is out of reach for
all three, but an embedding-proximity tier was already weighed and rejected on
cost grounds in `similarity.py:138-145` ("the proximity alternative … cost 18
adjudication calls to deliver [one] true positive"). Not reopened here.

## Correction — the Half B fix (prior claim was FALSE)

**Prior claim**: the good-life-demo fixtures' two-entry `provenance:` lists are
unreachable by any code path, so the minimal correction is to trim them to the
single source each document is causally derived from.

**Why it is false**, on two independent grounds.

**1. Trimming would contradict the fixture's own body.**
`examples/good-life-demo/bundle/concepts/stoicism.md` closes with a Citations
block naming both sources, and its prose carries `[1]` and `[2]` markers keyed
to them:

```
[1] [Reading notes — Enchiridion, 2026-07-05](/sources/notes-on-the-enchiridion-2026-07-05.md)
[2] [Call with Maria Salazar — 2026-07-14](/sources/call-with-maria-2026-07-14.md)
```

Both sources genuinely contributed. Dropping one from `provenance:` would leave
the document citing a source its own provenance denies — trading one
inconsistency for a worse one.

**2. The two-source list IS reachable — by exactly the mechanism this issue
endorses.** Two sources each yielding a `Concept` titled "Stoicism" produce one
shared `normalize_key(title)` inside the `Concept` partition, which is the
**HIGH** tier's trigger. A `SAME` verdict on that group merges them, and the
merge unions `provenance` to both entries (`okf.py:1040-1043`). The fixture is
not aspirational; it is a picture of entity resolution having fired.

**What is actually wrong** is the framing around the fixture, not the fixture:

- `examples/README.md:5` — "It shows what the MVP 1 `ingest` should produce."
  `ingest` alone cannot produce it: every ingest call site writes a
  single-element provenance literal (`cli/main.py:1955`, `:2795`). The bundle
  shows what `ingest` **plus entity resolution** produces.
- `docs/knowledge-object-model.md:91-129` — introduces the same content as "taken
  verbatim from `examples/good-life-demo/`" with no indication that the
  two-source `provenance:` is the product of a merge rather than a single
  compile. The quotation is accurate; the causal framing is absent.
- `docs/knowledge-object-model.md:311` — "As new sources arrive, the engine
  rewrites existing objects — revising claims, reconciling contradictions,
  strengthening synthesis." This overclaims passive, automatic behavior. Nothing
  rewrites an object as sources merely "arrive"; the only mechanism that
  enlarges an object's sources is a `SAME` merge, reached through `curate`'s
  Identity stage or `adjudicate --apply`.

So Half B touches **three prose sites and zero fixture files**. That is both
smaller and more honest than the trim, and it leaves the fixtures as the
worked example the end-to-end test in Half A reproduces.

`docs/cli.md:77` was checked and needs no change — it already states correctly
that there is no cross-document synthesis step in this slice.

## Options evaluated (Half A)

| Option | Description | Effort | Risk |
| --- | --- | --- | --- |
| (a) Cross-type candidate tier | A bounded tier that crosses `okf_type` on exact normalized title | ~150–300 | `CandidateGroup.okf_type` is a single scalar field; a cross-type group breaks that invariant, and `find_exact_title_groups` / `_pairs_covered_by_high_groups` both assume strict type scoping |
| (b) End-to-end union proof | One fixture + test proving `find_candidates` → `SAME` → merge → unioned provenance, per #379 criterion 1 | ~50–90 | None — proves existing behavior, changes none |
| (c) Embedding-proximity tier | Semantic similarity beyond title tokens | — | Rejected: already weighed and dismissed on cost in `similarity.py:138-145`; the decision does not ask for it |

**Recommendation: (b) plus the corrected Half B, as one slice.** (b) is the
direct answer to #379 criterion 1, which says outright: "If no natural `SAME`
pair arises in the corpus, construct one." It changes no behavior, so it lands
cheaply and immediately unblocks the P0 gate.

**(a) is deferred to its own issue, not dropped.** It is the only genuine
reachability gap found, so #427's "make `SAME` reachable" is not fully
discharged without it. But it mutates a partitioning invariant that two other
call sites depend on and it breaks `CandidateGroup`'s single-`okf_type`
contract — a qualitatively different change that deserves its own proposal
rather than being folded into a docs-and-proof slice.

## Affected areas

- New end-to-end test: two same-type `Concept`s with an exact shared title from
  distinct sources, stub `LLMBackend` returning `SAME`, asserting the merged
  survivor's `provenance` cites both sources.
- `examples/README.md:5`
- `docs/knowledge-object-model.md:91-129`, `:311`
- No production source file changes. No fixture changes.

## Tests

- Behavior-first, stub `LLMBackend` per the module's config-free-leaf convention
  — never a real Ollama call in unit tests.
- The new test is the deliverable, not a side effect: it is #379 criterion 1's
  regression guard.
- Half B is prose only: structural readback, no test changes.

## Changed-lines forecast

| Slice | Estimate |
| --- | --- |
| Half A (b) — end-to-end union proof | ~50–90 |
| Half B — three prose corrections | ~15–25 |
| Combined | ~65–115 |

Comfortably one slice, one PR.

## Risks

- The end-to-end test must assert the **union**, not merely that a merge
  occurred; a test that only checks the survivor exists would pass while the
  criterion it guards silently regressed.
- Half B must not overcorrect into claiming the fixture is unreachable — it is
  reachable, and saying otherwise would re-introduce the false claim this
  exploration corrects.
- Deferring option (a) leaves a named gap. It must be filed as an issue before
  #427 closes, or the reachability work silently disappears.

## Ready for proposal

Yes. Scope is (b) + corrected Half B as one slice, with the cross-type tier
filed as a follow-up issue.
