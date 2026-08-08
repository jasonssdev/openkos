# Exploration: cross-type-duplicate-candidates

**Issue**: #437 — candidate generation cannot see a duplicate whose two sources
were classified into different OKF types. Split out of #427 (closed); its former
hard dependency #379 is closed (LLM call total measured bounded at 309 by
construction).
**Phase**: `sdd-explore`. No production code written; no behavior changed.
**Engram twin**: `sdd/cross-type-duplicate-candidates/explore` (observation #2559).

## Current State

`_keyed_docs_by_type` (src/openkos/resolution/candidates.py:208-246) is the
shared prelude for BOTH `find_candidates`/`find_candidates_report` and
`find_exact_title_groups`. It calls `_iter_eligible`, drops deprecated
concepts (unless `include_deprecated=True`), then buckets survivors into a
`dict[okf_type, list[(concept_id, normalized_key)]]` BEFORE any HIGH/ACRONYM/
LOW pairing runs. Every downstream pairing loop (`_high_candidate_groups`,
the ACRONYM/LOW `combinations` loop in `find_candidates_report`) only ever
sees one type's docs at a time. A `Concept` and an `Entity` with identical
normalized titles are bucketed into different lists and never compared —
confirmed by `test_cross_type_identical_normalized_title_produces_no_candidate`
(tests/unit/resolution/test_candidates.py:167-176), which asserts
`find_candidates(bundle_dir) == []` for exactly that fixture.

`CandidateGroup.okf_type` (candidates.py:69-75) is a single required `str`
field, docstring: "The exact OKF `type` shared by every member." It is set
identically at all 3 construction sites in candidates.py (HIGH:262, ACRONYM:345,
LOW:357), always from the single `okf_type` the enclosing per-type loop is
iterating.

## Actual blast radius (verified by grep; corrects the issue's "~66 callers" framing)

- **Field-access consumers** (would need logic changes if `okf_type` becomes
  ambiguous/plural): only 2 files.
  - `src/openkos/resolution/adjudication.py:354` — `_build_messages(candidate.okf_type, candidate.tier.value, members)`
    bakes `okf_type` into the user prompt as `"OKF TYPE: {okf_type}"`
    (adjudication.py:202). This is prompt content only, not adjudication
    logic — but a cross-type group has no single value to interpolate here.
  - `src/openkos/cli/main.py` — 4 read sites, ALL pure display/serialization:
    `_adjudication_payload` (main.py:1104, `--json` field),
    `_render_adjudicate_report` (main.py:1150, human `[TIER] type -- trigger`
    line), `_echo_n_gt2_skip` (main.py:1372, N>2 skip report), and one more at
    main.py:8241 (same report-line pattern).
  - `candidates.py` itself: 2 internal sort-key uses (`_cap_rank_key:141`, both
    `.sort()` calls at 366/472) — these use `okf_type` purely as a stable
    tie-break string, not as partition logic; a joined/tuple representation
    would still sort fine as long as it stringifies deterministically.
- **Construction-site consumers** (would need an `okf_type=` argument at every
  fixture): 76 `CandidateGroup(` call sites across 6 test files —
  `test_adjudicate.py` (55), `test_curate.py` (11), `test_duplicates.py` (7),
  `test_candidates.py`, `test_adjudication.py`,
  `test_confidential_local_exemption.py` (1 each). This is the real "blast
  radius" — a dataclass field-shape change forces every one of these keyword
  args to be revisited, even though most don't read `okf_type` back out.
- **Non-consumers, despite importing `CandidateGroup`**:
  `src/openkos/cli/next_action.py` (`exact_title_groups` property,
  next_action.py:207-215) only calls `len(groups)` — never touches
  `.okf_type`. `src/openkos/resolution/__init__.py` is a pure re-export.
  `src/openkos/cli/curate.py` only type-hints and isinstance-filters
  `CandidateGroup` (curate.py:336) — never reads `.okf_type`. These three
  files need ZERO changes for any of the options below.

## Cost/contract verification

- `_pairs_covered_by_high_groups` (candidates.py:271-287) is `O(m^2)` only in
  the size of one exact-title CLUSTER (pairs within an already-formed HIGH
  group), not `O(n^2)` in corpus size — confirmed by its own docstring and by
  only being called from `find_candidates_report`, never from
  `find_exact_title_groups`.
- `find_exact_title_groups`'s O(n) contract (bucket + one sort, no pairwise
  work) is pinned by two tests:
  `test_find_exact_title_groups_equals_the_high_slice_in_order`
  (test_candidates.py:636, exact list-order equality against the HIGH slice of
  `find_candidates`) and
  `test_find_exact_title_groups_never_calls_near_match_score`
  (test_candidates.py:688, spies `similarity.near_match_score` and asserts
  zero calls). A cross-type EXACT-TITLE-ONLY pass (bucket by normalized key
  ACROSS all types instead of per-type, still zero pairwise work) would
  preserve this O(n) bucket-then-sort shape and could be verified the same
  way. Relaxing the LOW tier cross-type, by contrast, would multiply the
  existing per-type O(n^2) `near_match_score` pass to O(N^2) over the WHOLE
  corpus (no more per-type ceiling on n) — a real cost-profile change that
  interacts with `_MAX_CANDIDATE_GROUPS` (50, candidates.py:88) only at the
  truncation boundary, not before it: the O(N^2) pairwise cost is paid in full
  BEFORE `_cap_rank_key` ever ranks/slices the result
  (find_candidates_report:332-365).
- `_cap_rank_key` (candidates.py:124-141) already treats `okf_type` as an
  opaque tie-break string (`(_TIER_ORDER[tier], -score, okf_type, member_ids)`)
  — it does not need `okf_type` to be a single scalar per se, only a stable
  sortable value, so a joined/canonical string (e.g. `"Concept+Entity"`) would
  work as a tie-break without further change, though it changes what the
  string MEANS to a human reading rank output.
- `_MAX_CANDIDATE_GROUPS=50` and adjudication's cap-driven LLM-call bound
  (#379: 309 total by construction) are currently computed from the FULL
  cross-type-partitioned group SET's size, not from a per-type count. Adding a
  new candidate CLASS (cross-type exact-title groups) that participates in the
  SAME cap/rank/adjudicate pipeline stays inside the existing bound by
  construction, as long as it is emitted into the same `groups` list before
  `_cap_rank_key` sorts and slices — it does not need a separate budget.

## Survivor-type question — how adjudication/merge work today

- `adjudicate_candidates` (adjudication.py:263-375) builds one prompt per
  group via `_build_messages(candidate.okf_type, candidate.tier.value, members)`
  — the verdict schema returned by the LLM is `{"verdict", "confidence",
  "rationale"}` only (`_SYSTEM_PROMPT`, adjudication.py:68-71; `_parse_reply`,
  adjudication.py:239-260). There is NO type field in the verdict today —
  adding one would require both prompt-schema and
  `_parse_reply`/`AdjudicatedCandidate` changes.
- `build_merged_document` (src/openkos/model/okf.py:1018-1069) resolves `type`
  implicitly: it copies `survivor_metadata` first
  (`merged = dict(survivor_metadata)`, line 1018), then for every OTHER key in
  `absorbed_metadata` not in `_SPECIAL_KEYS`, only fills it in
  `elif key not in merged` (line 1045) — since `type` is always already
  present on the survivor, the absorbed document's `type` is silently
  discarded. Comment at line 1047 confirms: "a scalar already present on the
  survivor wins -- no-op." This is today's de facto answer to the
  survivor-type question, but it is an accident of generic scalar-merge logic,
  not a deliberate decision — issue #437 is right that it's currently silent.
- `docs/knowledge-object-model.md:203-210` documents `type_alternative` as a
  compiler-emitted optional field recording a "close call" runner-up type.
  Verified: "`type` remains the answer: nothing reads `type_alternative` to
  route or file a document" — it is inert metadata today. Grep confirms it is
  referenced only in `main.py`, `okf.py`, `extraction/concept.py` (the
  compiler that WRITES it), never read by resolution/adjudication code. Using
  it as a signal for cross-type candidate generation or survivor-type
  resolution would be new consumption, not an extension of an existing read
  path.

## Approaches

### A. Representing a cross-type group

1. **Joined/canonical sentinel string** (e.g. `"Concept+Entity"`, types
   sorted) — Pros: zero dataclass shape change, all 76 test construction
   sites and the 2 display/prompt consumers keep compiling; `_cap_rank_key`'s
   tie-break keeps working unmodified. Cons: lossy for programmatic consumers
   (can't cleanly test "was this group Concept-only"); display strings become
   ad-hoc parsed if anyone later needs the individual types back; a silent
   convention (join order, separator) needs to be pinned and tested. Effort:
   Low.
2. **`okf_type: str | None`, `None` for cross-type + new
   `member_types: tuple[str, ...]` field** — Pros: explicit, type-checked;
   existing single-type call sites keep `okf_type` populated and behave
   identically; cross-type is a distinguishable, queryable case. Cons: every
   one of the 76 test construction sites and the
   `_render_adjudicate_report`/`_adjudication_payload`/`_echo_n_gt2_skip`/
   adjudication.py:354 prompt-builder call sites need an
   `if okf_type is None` branch or a `member_types`-based fallback; the
   `_cap_rank_key`/`.sort()` tie-break must switch from `okf_type` to
   something always-populated (e.g. `member_types`). Effort: Medium.
3. **New field `member_types: tuple[str, ...]` ALONGSIDE the existing scalar
   `okf_type` kept as first-member's type or a joined label for display
   only** — Pros: additive, backward compatible for every existing consumer
   that only reads `.okf_type` as a display string; new code path
   (survivor-type resolution) reads the structured `member_types` instead.
   Cons: two fields encode overlapping information — a future author can
   update one and forget the other; slightly redundant. Effort: Low-Medium.
4. **Separate class/tier for cross-type groups** (e.g. a
   `CrossTypeCandidateGroup` or a new `Tier.CROSS_TYPE`) — Pros: cleanest
   separation of concerns, forces every existing `isinstance`/pattern-match
   consumer to explicitly opt in to handling the new shape (fail-loud instead
   of silently mishandling). Cons: doubles the type surface in
   `resolution/__init__.py`'s public API; `curate.py`'s
   `isinstance(item, CandidateGroup)` filter (curate.py:336) and any other
   `isinstance` check must be extended; highest effort of the four, and the
   LOW/ACRONYM tiers still assume a `CandidateGroup` shape for cap ranking
   (`_cap_rank_key` takes `CandidateGroup`), so a new class needs its own
   rank-key path or a common protocol. Effort: High.

### B. Survivor-type resolution

1. **Make today's implicit behavior explicit** (survivor's type always wins,
   documented and tested) — Pros: zero behavior change, lowest risk, matches
   the existing `_SPECIAL_KEYS`/generic-scalar-merge pattern in
   `build_merged_document`; only requires a docstring/comment update plus a
   new pinning test for the cross-type case specifically. Cons: does not use
   any signal from adjudication or `type_alternative`; may produce a "wrong"
   type if the LLM's SAME verdict implies the absorbed doc's type was more
   accurate. Effort: Low.
2. **Adjudicator returns the resolved type in the SAME verdict** — requires
   extending `_SYSTEM_PROMPT`'s JSON shape to add a `"type"` field,
   `_parse_reply` to extract/validate it (with a fail-closed default when
   absent/invalid — mirrors `_map_verdict`/`_coerce_confidence`'s existing
   fail-closed pattern), `AdjudicatedCandidate` to carry it, and
   `build_merged_document`'s caller to pass it through as an override. Pros:
   uses the model's actual read of member content, matches the "LLM already
   decides SAME, may as well decide type too" intuition. Cons: schema change
   touches the prompt (adjudication.py:68-72), `_parse_reply`
   (adjudication.py:239-260), and the merge-invocation call site in main.py;
   increases prompt complexity and adds a new fail-closed-degrade case to
   test; the module docstring's "one call per group, mirrors extract_concept"
   contract is unaffected (still one call) but the VERDICT contract grows.
   Effort: Medium-High.
3. **Block cross-type merge, surface for user decision** (adjudicate reports
   SAME but `merge`/`adjudicate --apply` refuses to auto-merge a cross-type
   pair, printing both types and asking the operator to pick, or requiring an
   explicit `--survivor-type` flag) — Pros: no silent type loss ever, no
   prompt-schema change, fits the hard constraint that this must stay an
   ordinary 2-member LLM-gated merge (not a synthesis step) by keeping the
   type decision a human act rather than inventing new LLM authority. Cons:
   adds user friction to what issue #437 is trying to make visible (a
   cross-type duplicate would be FOUND but not automatically mergeable),
   partially undercutting the value; needs new CLI surface. Effort: Medium.
4. **Use `type_alternative` hints to auto-resolve** (if one member's `type`
   equals the other's `type_alternative`, that's evidence they were a close
   call for the SAME underlying type; prefer whichever type has stronger
   corroborating evidence) — Pros: reuses data the compiler already writes,
   directly addresses the issue's cited "close call" framing. Cons:
   `type_alternative` is documented as absent in the normal case ("no
   sentinel value...whenever classification was clear"), so this only helps a
   NARROW subset of cross-type duplicates (both classified as close calls)
   and does nothing for the general case (two clearly-but-differently
   classified docs that are still the same entity); adds a new READ path for
   a field the whole codebase currently treats as write-only/inert, which is
   itself a documented design tension ("nothing reads `type_alternative` to
   route or file a document") that this change would break for the first
   time. Effort: Medium (small in code, but requires deciding whether the
   documented "nothing reads type_alternative" invariant may be broken).

## ACRONYM tier and status/next surfaces

- ACRONYM (Tier.ACRONYM, candidates.py:54-57) runs inside the SAME per-type
  loop as LOW in `find_candidates_report` (candidates.py:332-351) — it is
  gated by the same `_keyed_docs_by_type` partitioning, so it is
  cross-type-blind for the identical structural reason as LOW. Its
  `acronym_expansion_match` is stdlib-cheap (token-set intersection, not
  `near_match_score`), so a cross-type ACRONYM pass would be closer to HIGH's
  O(n) cost profile than LOW's O(n^2) one — but it still requires pairwise
  `combinations` today (candidates.py:332), unlike HIGH's pure bucketing.
  Recommend treating ACRONYM as OUT of an initial cross-type slice (ship
  cross-type EXACT-TITLE only first, matching `find_exact_title_groups`'s
  existing "cheap tier only" precedent from #216) and revisiting ACRONYM
  cross-type as a fast-follow once the type-representation and survivor-type
  questions are settled for the simpler HIGH case.
- `next_action.py`'s `exact_title_groups` property only counts groups
  (`len(groups)`), so it needs NO change to surface a cross-type count —
  confirmed no `.okf_type` read in that file. `status`'s duplicate-count line
  (precedent: archived change `2026-07-27-status-surfaces-pending-duplicates`)
  similarly only needs the flat count, per that change's own recommendation
  to avoid tier-language leakage into `status`.

## Tests that pin current (type-partitioned) behavior and will need to change

- `tests/unit/resolution/test_candidates.py:167-176` —
  `test_cross_type_identical_normalized_title_produces_no_candidate` asserts
  `find_candidates(bundle_dir) == []` for a same-title Concept+Entity pair.
  This is the test the issue is explicitly about; it must be REPLACED (not
  just relaxed) with a test asserting the pair IS now found, once cross-type
  matching ships.
- `tests/unit/resolution/test_candidates.py:179-193` —
  `test_two_different_types_each_with_their_own_matching_pair` asserts
  `{g.okf_type for g in groups} == {"Concept", "Entity"}` with
  `len(groups) == 2` for two SEPARATE same-type matching pairs
  (Stoicism/STOICISM in Concept, Epictetus/EPICTETUS in Entity) — this
  test's fixture has no cross-type overlap, so it should stay valid even
  after cross-type matching ships, but it is worth re-verifying it doesn't
  accidentally also become a cross-type hit under whatever new bucketing
  logic lands.
- `tests/unit/resolution/test_candidates.py:636-657` —
  `test_find_exact_title_groups_equals_the_high_slice_in_order` and its
  sibling at 660-686 (`..._with_include_deprecated`) pin the EQUIVALENCE
  contract between `find_exact_title_groups` and `find_candidates`'s HIGH
  slice. If cross-type exact-title matching is added to ONE function first
  but not the other, this equivalence breaks — the two functions must be
  changed together, exactly as `_keyed_docs_by_type`'s shared-prelude
  docstring already promises ("so `find_candidates` and
  `find_exact_title_groups` cannot drift").
- `tests/unit/resolution/test_candidates.py:1075`
  (`test_high_slice_is_a_prefix_of_find_exact_title_groups`) pins the
  above-cap PREFIX relation, which depends on `_cap_rank_key`'s ordering —
  if cross-type groups are ranked, this test's fixture-derived ordering
  assumptions should be re-checked.
- `tests/unit/resolution/test_candidates.py:688` —
  `test_find_exact_title_groups_never_calls_near_match_score` — must
  continue to hold if cross-type is added ONLY to the exact-title/HIGH
  bucketing path (Approach: EXACT-TITLE-ONLY cross-type pass); it would
  break if cross-type is naively implemented by unioning all types before
  the LOW pairwise pass instead.

## Recommendation

Ship cross-type matching for the HIGH (exact-title) tier ONLY, first,
mirroring the #216 "cheap tier only" precedent `find_exact_title_groups`
already established. Concretely: replace `_keyed_docs_by_type`'s partition
step for the HIGH pass with a single cross-type bucket keyed by normalized
title (still O(n), no pairwise work), while ACRONYM/LOW keep today's per-type
partitioning to preserve their existing O(n^2)-per-type cost ceiling.
Represent a cross-type group with Approach A.3 (additive
`member_types: tuple[str, ...]` field, `okf_type` kept as a display-only
joined label or first-member's type) — this is the lowest-blast-radius
option: it does not force every one of the 76 test construction sites or the
4 display/prompt call sites to add branching logic, only the NEW cross-type
code path needs to read `member_types`. For the survivor-type question, ship
Approach B.1 first (make the existing survivor-wins behavior explicit and
tested for the cross-type case) — it requires no prompt-schema change and no
new LLM contract, keeping the "ordinary 2-member LLM-gated merge, not a
synthesis step" constraint from #427 trivially satisfied. Approach B.2
(adjudicator-returned type) is a defensible fast-follow once B.1's explicit
behavior is shipped and observed in practice, not a blocker for the first
slice. `type_alternative` (Approach B.4) should NOT be consumed in this first
slice — it only covers a narrow subset of cases and would be the first code
to break the documented "nothing reads type_alternative" invariant; revisit
only if B.1's survivor-wins default proves wrong often enough in practice to
justify it.

## Risks

- The "~66 callers" figure in the issue does not match a literal `.okf_type`
  grep (7 real field-access sites across 2 files); the actual large number
  (76) comes from `CandidateGroup(` CONSTRUCTION sites in tests, which is a
  different and arguably less risky kind of blast radius (mechanical
  keyword-arg additions, not behavioral branching) than the issue's framing
  implies. The propose/design phase should recompute the actual diff size
  from the chosen representation, not the issue's cited count.
- A cross-type LOW/ACRONYM pass (out of scope for the first slice per the
  recommendation) would multiply the O(n^2) `near_match_score` cost from
  per-type to whole-corpus and has NOT been measured against the #379
  309-call bound — do not assume it is safe without a fresh cost measurement
  if a later slice proposes it.
- `type_alternative` consumption (Approach B.4) breaks a documented
  cross-file invariant (`docs/knowledge-object-model.md:210`) if adopted;
  flagged as a design-review point even though not recommended for slice 1.
- The equivalence tests binding `find_exact_title_groups` and
  `find_candidates`'s HIGH slice (test_candidates.py:636, :660, :1075) must
  be updated together; a partial implementation that changes one function's
  cross-type bucketing without the other will fail these tests immediately,
  which is useful as a guardrail but should be anticipated in the tasks
  breakdown.

## Ready for Proposal

Yes. The structural obstacles are mapped precisely (blast radius is smaller
than the issue's framing suggested), the cost-profile question is answered
(HIGH-only cross-type stays O(n); LOW/ACRONYM cross-type would not), and both
open design questions (representation, survivor-type) have a recommended
low-risk default with named fallback options for propose/design to confirm or
override explicitly.
