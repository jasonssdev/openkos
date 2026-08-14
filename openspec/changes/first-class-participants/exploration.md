# Exploration: First-class Person/Organization participants (issue #668)

## Current State

`Person`/`Organization` are already FULLY registered as first-class citizens in the type registry (`src/openkos/model/types.py:38-50`): `ObjectType("Person", "people", "People", True, "slow")` and `ObjectType("Organization", "organizations", "Organizations", True, "slow")`, with `llm_classifiable=True`, a bundle link_dir, a catalog section, and a default volatility tier. `okf.build_concept` is type-agnostic and accepts them unconditionally (`tests/unit/model/test_okf.py::test_build_concept_accepts_person_type`/`..._organization_type` pass today). The builder, catalog, relations, sensitivity, merge, and forget machinery are all type-blind by construction — nothing there special-cases or excludes Person/Organization.

The gap is entirely upstream, in production:

1. **Extraction prompt** (`src/openkos/extraction/concept.py::_SYSTEM_PROMPT`) contains an explicit anti-enumeration example: "extract the Event and the Decisions, not five Person stubs" — the "no-stub rule" issue #668 references.
2. **Judge** (`src/openkos/extraction/judge.py`) is a deliberately type-blind leaf module (design D2: never imports `concept.py`, never sees `ObjectType`). It decides genuineness from title/description/type strings alone, with no per-type floor or exemption logic of its own.
3. **Re-admission precedent (D5) already exists**, but lives in `concept.py::extract_concept_union`, not in judge.py: at line ~2234, `_TWIN_EXEMPT_TYPE` ("Procedure") is deterministically re-admitted after the judge drops it — `c.type == _TWIN_EXEMPT_TYPE`. This is a single-constant equality check, trivially generalizable to a frozenset.
4. **Measured empirical defect** (`evals/decision_extraction/report.md`, filed as #454/#456/#457): across 12 runs on AMI meeting transcripts with independently annotated ground truth (17 Person mentions in `TS3005a`, 4 Organization; 3/1 in `TS3005b`), extraction emitted **zero** Person, Organization, or Place objects — including under the union+judge pipeline. The absence persists identically pre- and post-judge, meaning the dominant defect is at GENERATION (the general pass essentially never proposes a raw Person/Organization candidate on this corpus), not merely at judge suppression. Issue #643's finding (1 of 3 HEAD runs produced 2 raw candidates, judge dropped both) is a *second*, smaller-magnitude defect on top of the first.
5. **A near-identical measurement harness already exists**: `evals/decision_extraction/scripts/run_type_coverage.py`, which drives the real `extract_concept`/`extract_concept_union` seam against AMI's `PERSON`/`ORGANIZATION`/`LOCATION` named-entity ground truth and reports "explained" vs "unexplained" absence per type, across `--runs`. This is materially the measure-first participant-recall probe design question #5 asks for — it needs extension/scoring for Person/Organization specifically, not a new harness built from scratch.
6. **Identity resolution**: `src/openkos/resolution/similarity.py` provides `near_match`/`is_near_match` (token-subset containment with `MIN_TOKEN_LENGTH=3`) and `acronym_expansion_match` (#397) for general concept-title dedup. Both are deliberately generic, exact-token, non-semantic matchers built and measured for topic/title identity (e.g. `MCP` ↔ `Model Context Protocol`), not person-name identity. Applying token containment naively to person names is a known false-merge shape: `"Jason"` (single token) would satisfy containment against `"Jason S. Smith"` the same way `{agent}` manufactured false positives against `ai-agent` (`_MIN_TOPIC_TOKENS`/`_MIN_ACRONYM_LENGTH` in `concept.py` exist specifically to close that class of hole). No person-aware identity module exists today.
7. **normalize-names (#474) is NOT a person-name-variant tool.** It is Unicode NFC normalization of on-disk file/directory *names* (`normalize_names_cmd`, `cli/main.py:6723`) — unrelated to resolving "Jason" vs "Jason S." as the same participant. The issue's own "where to look" pointer is misleading here; a distinct mechanism is needed for participant name-variant merge.
8. **Per-item consent precedent** exists and is well-established: `suggest-relations --apply` (issue #560, `cli/main.py:11216+`) is an interactive per-item walk with a "revisitable-decline" contract (#483) — decisions are not silently auto-applied. This is the UX pattern person-merge should extend, rather than any silent/automatic merge.
9. **Sensitivity model**: `default_sensitivity` is a single workspace-level config value (`config.py::DEFAULT_SENSITIVITY = "private"`), applied uniformly at ingest as a floor gate. There is no per-TYPE default sensitivity override anywhere in the codebase (grep for `default_sensitivity`/`DEFAULT_SENSITIVITY` across `src/` confirms this). "Person objects default to higher sensitivity than the workspace floor" would be a genuinely new mechanism, not a config toggle.
10. **Forget's structural scrub** (`_scrub_entry_snapshots`, issue #602, `cli/main.py:638+`) operates generically on ledger entries by ID, with no type branching found — it should compose with Person/Organization objects for free, but this was not exhaustively traced end-to-end (see risks).
11. **Reconciliation** (#645/#667) was not deeply traced in this pass — flagged as a risk/unknown below.

## Affected Areas

- `src/openkos/extraction/concept.py` — `_SYSTEM_PROMPT` (no-stub wording), `_TWIN_EXEMPT_TYPE`/re-admission logic in `extract_concept_union` (~line 2234), possibly a new scoped/triggered sub-prompt mirroring `_MEETING_SHAPED_TITLE_RE`.
- `src/openkos/extraction/judge.py` — deliberately type-blind (design D2); likely stays unchanged if re-admission continues to live in `concept.py`, per existing precedent.
- `src/openkos/resolution/similarity.py` — existing identity seam; needs a person-aware companion or guarded extension, not a naive reuse of `near_match`.
- `src/openkos/model/types.py` — already correct; no change expected.
- `src/openkos/model/okf.py`, `src/openkos/model/relations.py` — already type-agnostic; likely no change, but `member_of`/`ASYMMETRIC_RELATION_TYPES` (#624) is where a Person→Organization relation would be asserted.
- `src/openkos/sensitivity.py`, `src/openkos/config.py` — would need new per-type default-sensitivity concept if design question #3 is answered "yes".
- `src/openkos/cli/main.py` — `_scrub_entry_snapshots` (forget), `_refresh_derived_after_write`, `suggest-relations --apply` (consent pattern to extend for merge), `normalize_names_cmd` (confirmed NOT the identity tool despite the issue's pointer).
- `evals/decision_extraction/scripts/run_type_coverage.py` and `report.md` — existing measurement harness and its findings; extension point for the required measure-first probe.

## Approaches (capture mechanism, design question #1)

1. **Dedicated extraction pass** — a new, separate LLM call specifically hunting participants, analogous in shape to `_reask_for_further_subjects`/`_add_reask_subjects` (#584).
   - Pros: isolates risk from the already-tuned general prompt (which has a long history of prompt-only regressions: #380, #561, #563, #380/D4-5b anti-twin priming); can be scoped by trigger (meeting/transcript-shaped sources) the same way `_MEETING_SHAPED_TITLE_RE` already gates other behavior.
   - Cons: adds a model round-trip per source (cost/latency, same tradeoff class as chunking, #454); new prompt surface requiring its own measurement and tuning cycle; does not, by itself, fix judge suppression of any general-pass candidates.

2. **Un-suppress the general pass** (soften "not five Person stubs") + generalize judge re-admission from `_TWIN_EXEMPT_TYPE` to a frozenset including Person/Organization.
   - Pros: the re-admission half is a near-zero-risk, deterministic change (one constant becomes a frozenset, same code path already proven for Procedure); directly fixes #643's measured defect (judge drops the few raw candidates that do appear).
   - Cons: report.md's measurement shows the dominant defect is GENERATION, not selection — 0 raw Person/Organization candidates across 12 runs even before the judge sees them. Softening the no-stub wording alone repeats the prompt-only pattern that has regressed unrelated behavior before, and doing nothing to generation means judge re-admission alone will not move the needle on this corpus.

3. **Hybrid** — ship the judge re-admission generalization first (low-risk, reuses proven D5 precedent, fixes #643's suppression defect immediately), measure with the extended `run_type_coverage.py` harness, and only add a scoped/triggered dedicated pass (approach 1) if zero-generation persists — which the existing measurement makes likely.
   - Pros: sequences the cheap, safe, already-precedented fix first; each increment is independently measurable and revertible via the existing harness; avoids committing to costly prompt/pass work before knowing whether it's needed.
   - Cons: two-phase delivery; still leaves the "no-stub rule" wording question open pending the phase-1 measurement.

| Approach | Pros | Cons | Effort |
|---|---|---|---|
| 1. Dedicated pass | Isolates risk; reuses trigger-gating pattern | Extra cost/latency; new prompt to tune; doesn't fix judge suppression alone | Medium-High |
| 2. Un-suppress general pass + judge re-admission | Re-admission is near-zero-risk and precedented | Prompt-only softening has a history of regressions; generation is the dominant defect per measurement | Medium |
| 3. Hybrid, sequenced | Cheapest safe fix first, measured before further investment | Two-phase delivery | Medium (phase 1 Low) |

**Recommendation**: Approach 3 (hybrid, sequenced). Phase 1 — generalize `_TWIN_EXEMPT_TYPE` to a frozenset covering Person/Organization in `concept.py::extract_concept_union`, ship the extended `run_type_coverage.py` probe as the measure-first gate (design question #5), and measure before touching the prompt. Phase 2 — only if the AMI corpus still shows near-zero raw Person/Organization generation after phase 1 (which report.md's 0-of-12 finding makes likely), invest in a scoped/triggered capture pass gated on transcript/meeting-shaped sources, keeping the general prompt's "fewer, richer objects" restraint intact for non-transcript documents.

## Design Questions — Findings

1. **Capture mechanism**: see Approaches above. Recommendation: hybrid, sequenced, measure-first.
2. **Identity resolution**: no person-aware matcher exists; `resolution/similarity.py`'s generic token-containment matchers are the wrong tool if reused naively (false-merge risk demonstrated by the project's own `_MIN_TOPIC_TOKENS` history). Recommendation direction: a dedicated person-identity predicate, deliberately conservative (fewer false merges over higher recall, mirroring the project's `_drop_source_title_twins` floor philosophy), surfaced through the `suggest-relations --apply` per-item consent pattern (#560/#483) rather than silent auto-merge.
3. **Privacy defaults**: no per-type sensitivity mechanism exists; `default_sensitivity` is workspace-global only. A "Person defaults higher" rule is new infrastructure, and its interaction with forget's structural scrub (#602) and reconciliation (#645/#667) needs its own design pass — not resolvable by config alone.
4. **Backfill**: not deeply investigated this pass; `_refresh_derived_after_write` and `normalize-names`/`backfill-sensitivity` are the closest existing "retroactive pass over existing bundle" precedents and are candidate models for a backfill verb, but no existing verb re-runs extraction over already-ingested sources — this would be new.
5. **Measurement**: `evals/decision_extraction/scripts/run_type_coverage.py` + `report.md` already IS the measure-first instrument for this exact question, using AMI's independent PERSON/ORGANIZATION annotations, and already shows the baseline defect (0 of 12 runs). Recommendation: extend/reuse this harness rather than building `evals/participant_recall/` from scratch.

## Risks

- Prompt-only changes to `_SYSTEM_PROMPT` have a documented history of regressing unrelated extraction behavior (anti-twin D4/5b priming, #380/#561/#563 language-leak false starts) — any phase-2 prompt work must be measured in isolation via the existing harnesses before merging.
- False-merge risk in identity resolution is real and previously paid for in this codebase (`_MIN_TOPIC_TOKENS`/acronym floor history) — naive reuse of `resolution/similarity.py` for person names would likely reproduce that class of defect.
- Sensitivity-default-by-type is new infrastructure with no existing seam; underestimating this could blow the review-line budget if bundled into the same change as capture-mechanism work.
- Reconciliation (#645/#667) and forget's structural scrub (#602) were not exhaustively traced against a hypothetical Person/Organization object in this pass — recommend a follow-up read before design/tasks phases if privacy defaults are in scope.
- `normalize-names` is a false lead for identity resolution (Unicode NFC only) — following the issue's pointer literally would waste implementation time; flagged explicitly so sdd-propose doesn't inherit the confusion.

## Ready for Proposal

Yes, with one open question for the user before sdd-propose: should sensitivity-default-by-type (design question #3) be scoped INTO this change, or split into a follow-up? It is genuinely new infrastructure (no existing per-type sensitivity seam), while capture mechanism + judge re-admission + measurement (questions #1/#5) can ship as a much smaller, lower-risk phase 1 using existing precedents.
