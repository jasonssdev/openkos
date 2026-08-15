# Exploration: named-person-capture — always-identify-people ruling (#712)

> **Provenance.** Produced by the `sdd-explore` phase and materialized here by
> the orchestrator: that phase agent has no file-write capability, so the
> hybrid artifact store could only be satisfied through Engram
> (`sdd/named-person-capture/explore`) at authoring time. Content is the
> phase's, unedited except for the correction noted under
> [The owner ruling](#the-owner-ruling).
>
> Three load-bearing claims were spot-checked by the orchestrator before this
> phase was accepted: the verbatim prompt pin at
> `tests/unit/extraction/test_concept.py:1488`, the
> `Stub Rejection at Judge Re-Admission` requirement in
> `openspec/specs/extraction-union-judge/spec.md`, and
> `_MAX_JUDGE_CANDIDATES = 24`. All three hold.

## Current State

**The participant pipeline, gate by gate** (`src/openkos/extraction/concept.py`):

1. `_is_meeting_shaped(source_title, source_text)` (line 383) — title regex OR
   `_transcript_shaped_text` content-shape detector (#673). Feeds three
   consumers: the no-title prompt branch (`_build_messages`),
   `_add_participant_capture`'s gate, and the judge re-admission conjunct. One
   predicate, three call sites (design D3's rule).
2. General extraction (`_SYSTEM_PROMPT`, line 34) runs twice (union path) or
   once per chunk. Its anti-enumeration paragraph (lines 127-139) explicitly
   tells the model NOT to emit "five Person stubs" for meeting participants —
   this is where a merely-named person is suppressed FIRST, before any gate
   below runs. `tests/unit/extraction/test_concept.py:1488` pins this exact
   sentence.
3. `_add_participant_capture` (line 2059) → `_capture_further_participants`
   (line 2030) — the #668 scoped follow-up call, gated unconditionally on
   `meeting_shaped`. Its prompt (`_PARTICIPANT_CAPTURE_SYSTEM_PROMPT`, line
   1956) explicitly demands an anchor ("Only report a participant you can
   anchor with a role, affiliation, or relation beyond their bare name... A
   name alone... is NOT a valid answer"). **This is the second suppression
   point**, upstream of any gate — a merely-named person is told not to be
   reported at all by this prompt's own instructions, independent of
   `_has_participant_anchor`.
4. Candidates from both passes are merged and handed to `judge.select`. The
   judge decides on its own criteria (unrelated to the anchor gate).
5. Judge re-admission conjunct (`extract_concept_union`, lines 2764-2775): a
   candidate whose title the judge did NOT select is re-admitted only if
   `c.type in _JUDGE_READMIT_TYPES` (`{Procedure, Person, Organization}`) AND
   (`c.type == "Procedure"` OR (`meeting_shaped and _has_participant_anchor(c)`)).
   **This is the third and final suppression point** — the Stub Rejection
   requirement (spec.md lines 221-242).
6. `_has_participant_anchor` (line 699) reads `f"{result.description} {result.body}"`
   — the model's OWN paraphrase, never `source_text` — against
   `_PARTICIPANT_ANCHOR_RE` (line 664), an English+Spanish
   role/affiliation/relation lexicon.

**Where the merely-named person is actually lost** — evidence from
`evals/participant_anchor/report.md` (#706, merged `c93874b`, do NOT
re-measure):

- The anchor gate (step 5/6) discarded **zero** candidates across all 9 runs, 3
  arms. It is a latent trapdoor, not the active suppressor measured here.
- Suppression happens at steps 2 and 3 — the general extraction prompt's
  anti-enumeration paragraph and the participant-capture prompt's own explicit
  "a name alone is NOT a valid answer" instruction. Both are PROMPT TEXT, not
  deterministic code.
- The `es-bare` control (source states nothing about anyone) still produced 9/9
  admitted `Person` candidates — but only because the model wrote the word
  `Participante` into its own description (prompt-vocabulary leakage), which
  then satisfied the gate. This is NOT reliable identification of a
  "merely-named, never-speaking" person; it is a model artifact that happened
  to pass. A person who is named but the model does NOT choose to describe with
  anchor vocabulary is dropped before the gate ever sees a candidate.
- 12/27 candidates with a REAL stated role (in source-supported language
  outside the lexicon) scored ANCHORLESS and survived only because the judge
  happened to select them directly (step 4) — the gate never re-admitted a
  single one on any run.

**Conclusion for #712's remedy**: the owner ruling requires unconditional
Person admission for a merely-named individual. The single line at fault for
the STATED goal ("must always be identified") is not primarily
`_has_participant_anchor` (it never fired in these 9 runs) — it is (a) the
general prompt's anti-enumeration instruction and (b) the participant-capture
prompt's own anchor demand, both of which actively instruct the model not to
report a bare name. The deterministic gate (`_has_participant_anchor` + the
`meeting_shaped and _has_participant_anchor(c)` conjunct) is the enforcement
backstop for the OLD stub rule and is exactly what the spec's "Stub Rejection
at Judge Re-Admission" requirement (spec.md:221-242) names for
removal/reversal.

## HARD CONSTRAINT — verbatim-pinned anti-enumeration paragraph

`_SYSTEM_PROMPT` (`concept.py:127-139`) is a SEPARATE prompt from
`_PARTICIPANT_CAPTURE_SYSTEM_PROMPT`. It is measured (#380, referenced in the
module docstring; `evals/model_spike/` is the harness) and pinned verbatim by
`tests/unit/extraction/test_concept.py:1488`:
`assert "extract the Event and the Decisions, not five Person stubs" in system_content`.

This paragraph MUST NOT be edited by this change — confirmed correct: the #668
design explicitly built the SEPARATE `_PARTICIPANT_CAPTURE_SYSTEM_PROMPT` (line
1956) specifically BECAUSE the general prompt suppresses people, and reused
that separation ("Deliberate, measure-first (#613/#622): no `_SYSTEM_PROMPT`
edit ships in phase 1" — design.md of
`2026-08-14-first-class-participants`). The correct lever for #712's remedy is
`_PARTICIPANT_CAPTURE_SYSTEM_PROMPT` (rewrite its "must anchor" instruction)
plus the deterministic `_has_participant_anchor` conjunct at judge re-admission
(remove or loosen it) — NOT `_SYSTEM_PROMPT`. This is confirmed by evidence,
not merely asserted: the module docstring itself states the anti-enumeration
paragraph is a design-D3-adjacent, verbatim-pinned constant, distinct from the
capture prompt built specifically to work around it.

## The owner ruling

Issue #712 comment, 2026-08-15:

> people must always be identified: someone who spoke once, spoke minimally, or
> never speaks and is only named, must still become a `Person`.

Plus a second ruling given in the same session: **participants get their own
budget lane** — `_UNION_BACKSTOP` stays the ceiling for subjects, and people no
longer compete for those slots.

> **Correction applied during gatekeeping.** The exploration originally read
> the #712 comment as carrying "four stated implementation requirements" from
> the owner. Two of those four are owner rulings (always identify; separate
> lane). The other two — preserve the anti-enumeration paragraph, and treat
> identity as binding sooner — are orchestrator implementation notes recorded
> in the same comment, not owner decisions. The distinction matters at design
> time: an orchestrator note can be overturned by evidence; an owner ruling
> cannot, without asking.

Lifecycle treatment for merely-named persons is explicitly left undecided by
the ruling itself.

## The budget lane — current single-lane mechanics

- `_UNION_BACKSTOP = 20` (line 2474) — applied EXACTLY ONCE, LAST, to `kept`
  (post-judge, post-re-admission) at line 2809:
  `retained = kept[:_UNION_BACKSTOP]`. This is a SINGLE shared slice across
  every type — Person/Organization compete with Decision/Event/Concept for the
  same 20 slots, positionally (first-N survive).
- `_MAX_OBJECTS_PER_SOURCE = 6` (line 1593) is the analogous cap on the OTHER,
  single-run `extract_concept` path (non-union). Both caps feed
  `ExtractionReport.produced`/`.retained`/`.discarded_titles` identically —
  `produced` is pre-cap count, `retained` is post-cap count,
  `discarded_titles` names the casualties, consumed ONLY by
  `_extraction_cap_notice` in `cli/main.py:3072` (disclosure contract, #404).
- `_MAX_JUDGE_CANDIDATES = 24` (line 2466) is an EARLIER ceiling, applied
  BEFORE the judge call, bounding judge-prompt size — always ≥
  `_UNION_BACKSTOP`, reported separately as `report.pre_judge_dropped`.
- Everything reading `retained`/`produced`/`discarded_titles`:
  `ExtractionReport` dataclass (line 2112+), `_extraction_cap_notice`
  (`cli/main.py:3072`, called at `cli/main.py:3409`), and the
  stub-flooding-guard fields (`participant_judge_selected_titles`,
  `participant_readmitted_titles`, `participant_anchorless_discarded_titles`)
  which are ADDITIONAL, SEPARATE bookkeeping already distinguishing
  participant-typed outcomes from the general cap notice.

**Two-lane budget design constraint for the proposal phase**: a second,
SEPARATE constant (mirroring `_JUDGE_READMIT_TYPES`'s "own set, own site"
precedent, D1 of the #668 design) is needed for participants, sliced
independently before or alongside the `_UNION_BACKSTOP` slice on SUBJECTS only.
The disclosure contract (`ExtractionReport.produced`/`.retained`/`.discarded_titles`,
and `_extraction_cap_notice`'s exact wording) must not silently start
conflating a participant-lane truncation with a subject-lane one — this needs
its own report field and its own CLI notice, symmetric with how
`participant_anchorless_discarded_titles` already got its own notice function
(`cli/main.py:3055`) distinct from `_extraction_cap_notice`. That precedent is
directly reusable.

## Name grounding — nothing currently checks names against source_text

`_strip_ungrounded_expansions` (line 1103) only strips PARENTHETICAL ACRONYM
EXPANSIONS in titles (`MCP (Machine Control Protocol)` shape) — it never
touches a person's name itself, and it is scoped to titles only, never
description/body. With the anchor-gate's role requirement removed (per the
ruling), grounding friction on Person NAMES disappears entirely unless a new
deterministic check is added.

A deterministic name-presence check against `source_text` (mirroring
`_strip_ungrounded_expansions`'s "strip, casefold, collapse whitespace, then
substring" comparison style — the module's one deliberately-dumb comparison
idiom, reused four times already: `_restates_source_title`,
`_contains_source_topic`, `_restates_source_acronym`,
`_strip_ungrounded_expansions`) would need to answer:

- **Accents**: `Germán` vs `German` — a naive casefold+substring check would
  NOT match across an accent difference; needs an explicit decision (strip
  diacritics before comparing, or accept the false negative — precedent:
  `_strip_ungrounded_expansions`'s own docstring explicitly ACCEPTS false
  negatives from inflection/hyphenation as a declared trade).
- **Initials/abbreviated names**: a source using "G. Vega" while the model
  expands to "Germán Vega" (or vice versa) — substring containment fails in
  both directions.
- **AMI-style single-letter speaker labels** (`A:`, `B:`, `C:`, `D:`) —
  `_transcript_shaped_text`'s own label regex (lines 297-310) explicitly allows
  one/two-letter labels for this exact corpus shape. A name-grounding check
  that requires the FULL proposed name to appear verbatim in `source_text`
  would reject every AMI participant outright, since the source only ever
  writes `B:`, never a real name — this is a genuine, named risk for the
  proposal phase, not a hypothetical.
- **Surname-only mentions**: source says "Sepúlveda mentioned..." and the model
  proposes title "Jason Sepúlveda" (full name from elsewhere in the transcript,
  or invented) — containment either way is ambiguous.

No existing code answers these; this is new deterministic-filter design work
for the proposal/design phase, not something to decide here.

## Identity — the #668 D8 seam (still undesigned, confirmed)

`openspec/changes/archive/2026-08-14-first-class-participants/design.md`,
section "D8: Identity seam (deferred, named only)" (lines 66-68, verbatim):
*"Person-name identity would plug in at `resolution/similarity.py` as a
**companion** predicate, surfaced through `suggest-relations --apply`
(#560/#483). Not designed here."* This remains true today — no companion
predicate exists in `resolution/similarity.py`.

What IS reusable, found in `src/openkos/resolution/similarity.py`:

- `tokenize`/`near_match_score`/`is_near_match` (lines 23-113) — token-set-based
  near-match over TITLES/KEYS, `MIN_TOKEN_LENGTH = 3`, generic and type-blind.
  Would need extension or a person-specific variant for "German" vs "German
  Patricio Vega Meza" (one is a strict token subset of the other —
  `near_match_score`'s existing containment-style logic is architecturally
  close, but it is currently used for concept-title dedup, not person-name
  matching, and has NOT been measured against name-matching false positives,
  e.g. two DIFFERENT people who happen to share a first name).
- `_initialisms`/`acronym_expansion_match` (lines 119-264) — acronym/expansion
  matching (`MCP` ↔ `Model Context Protocol`), `MIN_ACRONYM_LENGTH = 3`. Not
  obviously applicable to person names, but the pattern of "one title's
  initials matching another's word run" is architecturally the SAME shape as
  "an email address's local-part or a nickname matching a full name's
  initials" — worth flagging as a possible structural precedent, not a
  ready-made solution.
- `normalize_names_cmd` (`cli/main.py:7113`, the `normalize-names` verb, #474)
  exists as a CLI command — its body was not traced in this pass; flag as a
  file to read closely in the proposal/design phase, since its name suggests it
  already does SOME canonical-name normalization that a person-identity design
  should not duplicate.

None of the above constitutes a design. Per the task's explicit instruction, no
identity-matching design is proposed here — this section reports what exists,
nothing more.

## Lifecycle of a merely-named person — OPEN QUESTION, laid out not decided

Traced the concrete cost today (`cli/main.py:3509` and surrounding `ingest`
loop): EVERY `ExtractionResult`, of ANY type including `Person`/`Organization`,
goes through the identical path — `okf.build_concept(...)` → written as its own
`.md` file under `link_dir_path` → `type_birth_sensitivity` stamps sensitivity
(ADR-0015's `{Person: 1}` offset applies here) → later `openkos reindex` embeds
it into the vector store and seeds graph edges (`sqlite_graph.reindex_graph`,
`state/reindex.py`). There is NO lighter-weight object shape in the current
codebase — every concept, whatever its type or provenance richness, is a full
first-class file with full lifecycle (own embedding, own relations, own
sensitivity, own retrieval surface).

Two lifecycle options for a merely-named, never-speaking person, to weigh in
the proposal phase:

1. **Same lifecycle as a speaker** (own file, own embedding, own relations,
   ADR-0015 sensitivity) — zero new code paths, reuses `build_concept`
   unchanged; cost is a possibly large number of thin, low-information `Person`
   files (a meeting with 15 people named in an attendee list, none speaking,
   all becoming full concepts) — direct tension with the retained
   anti-enumeration prompt guidance, and with reviewer/retrieval noise (a
   `query` answer citing 15 near-empty Person stubs).
2. **A lighter shape** — e.g. a name recorded as an attribute/reference on the
   Event/meeting object rather than its own file, or a minimal-metadata Person
   variant — would require NEW code: a new build path, and a new file/no-file
   distinction that downstream systems (retrieval, sensitivity, merge,
   forget-cascade) would all need to understand. This is exactly the type-blind
   assumption `_scrub_entry_snapshots`/`_reconcile_merged_survivor` (#668
   design D7) currently rely on ("type-blind by construction" is repeated as a
   deliberate simplifying invariant across multiple existing subsystems —
   introducing a second Person shape breaks that invariant everywhere it is
   asserted).

## Blast radius

- **`openspec/specs/extraction-union-judge/spec.md`**, requirement heading
  **"Stub Rejection at Judge Re-Admission"** (lines 221-242) — directly
  reversed by the owner ruling. Its two scenarios ("Name-only candidate is not
  re-admitted", "Candidate with a meeting-role anchor is re-admitted") both
  assert the OLD behavior and must be rewritten or removed. The adjacent
  requirement **"Judge Re-Admission Set Extended to Person/Organization
  (Additive Only)"** (lines 180-219) is likely NOT itself reversed (its scope
  is the type set, not the anchor gate) — but its scenario "Judge-dropped
  Person on a meeting-shaped source is re-admitted" (line 191) says "carries a
  valid participant anchor", which needs re-wording once the anchor conjunct
  changes.
- **`tests/unit/extraction/test_concept.py`**:
  `test_participant_readmitted_reported_separately_from_judge_selected` (line
  2887), the anchorless-discard test (line 2925), and the stub-rejection pair
  described in design.md's testing strategy — all assert the OLD anchor-gate
  behavior and will need rewriting. Line 1488's `"...not five Person stubs"`
  pin must NOT be touched (see HARD CONSTRAINT).
- **ADR-0015** (`docs/adr/0015-per-type-default-sensitivity.md`) — NOT
  contradicted; its `{Person: 1}` offset is now MORE load-bearing (every
  merely-named person also gets it), consistent with its own stated motivation.
  No changes needed, but its "Consequences" section (a `Person` born
  `confidential` on a `private` workspace silently leaves non-local retrieval)
  becomes a bigger practical concern at higher Person volume.
- **`docs/`**: `docs/knowledge-object-model.md` references ADR-0015; not traced
  deeper — flag for the proposal phase to grep for any "Person needs an anchor"
  prose that would now be stale.
- **`evals/participant_anchor/`** (README.md, report.md) — these are HISTORICAL
  measurement artifacts and must NOT be edited to match the new behavior; they
  document what was measured under the OLD rule and remain historically
  accurate. Any NEW measurement belongs in a new eval directory or a new dated
  section, never a silent rewrite of #706's report.

## Gaps — no new measurement was run, and none should be

- No re-measurement of `_PARTICIPANT_CAPTURE_SYSTEM_PROMPT` wording changes has
  been done (correctly, per task constraints) — the proposal/design phase will
  need a NEW eval (not `evals/participant_anchor/`, which is closed) to
  validate any prompt rewrite that removes the "must anchor" instruction,
  before it ships, following the #613/#622 measure-first precedent this
  codebase applies everywhere.
- No measurement exists yet of how many merely-named-only Person objects a real
  meeting transcript would produce once the anchor gate is loosened/removed —
  this is exactly the volume question the two-lane budget needs before its
  capacity number can be chosen; flagged as a gap for the proposal phase, not
  filled here.
- Name-grounding false-negative/false-positive rates (accents, initials, AMI
  single-letter labels) are unmeasured — any grounding check the proposal
  designs will need its own eval before shipping, per the same measure-first
  precedent.

## Recommendation

The correct lever is `_PARTICIPANT_CAPTURE_SYSTEM_PROMPT` (rewrite the "name
alone is NOT a valid answer" instruction to permit bare-name reporting) plus
removing or loosening the `_has_participant_anchor` conjunct in the judge
re-admission step — NOT `_SYSTEM_PROMPT`, which stays untouched per the HARD
CONSTRAINT and per the ruling's own first requirement. This should proceed to
`sdd-propose`, which needs to resolve: (1) the two-lane budget constant and its
disclosure fields, (2) whether/how to add name-grounding, (3) explicitly punt
the identity seam (D8) and the lifecycle-shape open question to future work or
a follow-up design, consistent with how #668 itself deferred D8.

## Ready for Proposal

Yes. The mechanics are fully mapped, the hard constraint is confirmed and
located precisely, the blast radius is named with exact file/line references,
and the two genuinely open questions (identity, lifecycle shape) are laid out
with concrete costs for the proposal phase to weigh — not decided here.
