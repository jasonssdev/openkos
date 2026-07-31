# Archive Report: source-title-from-heading

**Change**: `source-title-from-heading`
**Archived to**: `openspec/changes/archive/2026-07-31-source-title-from-heading/`
**Date**: 2026-07-31
**Mode**: hybrid (OpenSpec + Engram)

## What Shipped

The `source-title-from-heading` change implements content-based title derivation for Source ingestion (GitHub issue #248). Sources now derive their title from decoded raw content using a three-tiered precedence: (1) first ATX H1 outside fenced code; (2) else first title-plausible line; (3) else filename-derived slug, unchanged. Candidates are normalized and validated; forbidden characters and excessive length cause fallback to the slug.

**Key decisions:**
- **No backfill**: Existing Sources keep slug titles. The exact symptom #248 opens with (`openkos list` showing `01-introduction`) remains for pre-existing documents until a follow-up ships.
- **Broader than headings**: The settled rule accepts title-plausible plain first lines, not just `# ` headings. Both shipped examples (`examples/good-life-demo/raw/*.txt`) benefit from this broader rule even though neither contains a `#`.
- **Idempotence**: Re-ingesting byte-identical raw content produces byte-identical Source documents, guaranteed by pure-function design.

## Delivered Across Four PRs

| PR | Commit | Title | Key Changes |
|---|---|---|---|
| #292 | 9d84eb1 | `feat(ingest): add a pure title-derivation helper for Source concepts (#248)` | New `src/openkos/source_title.py` + 52 unit tests at 100% branch coverage. Wired nothing |
| #293 | 7f29cdd | `feat(ingest): derive a Source's title from its content (#248) (#293)` | Wired derivation into `openkos ingest`; 12 integration tests; closed GitHub issue #248 |
| #294 | f6e62ae | `fix(ingest): close the three findings the #248 review left open (#294)` | Added the blank-content guard and made its previously-vacuous test actually assert non-invocation; EXTENDED the forbidden-character class past ASCII to cover invisible and bidi code points; made the re-ingest preview NAME a title change (it does not prevent the retitle — re-ingest is a regeneration by design) |
| #295 | e0abe7f | `fix(ingest): reject the two invisible ranges the title guard still missed (#295)` | Unicode Tag block (`U+E0000`-`U+E007F`) + Arabic letter mark (`U+061C`) |

**Main moved from `afe92bf` (base) to `e0abe7f` (current).**

## Final Measured State (at `main` @ `e0abe7f`)

- **Test suite**: 2831 passed in 93.14s (strict TDD)
- **Coverage**: `src/openkos/source_title.py` at **100% branch coverage** (70 statements, 36 branches, 0 missed)
- **Unit tests**: 82 parametrized tests for title derivation
- **Integration tests**: 12 tests covering all spec scenarios
- **Code quality**: `uv run ruff check .`, `uv run ruff format --check .`, and `uv run mypy .` all clean
- **No ADR created**: Rollback is single-revert with no backfill; hard-to-reverse condition not met

## Verification — PASS WITH WARNINGS RESOLVED

**Verdict**: PASS with warnings resolved (GitHub issue #296 filed).

All 19 Given/When/Then scenarios in the delta spec have passing tests. All 7 task phases (1-7) are genuinely complete, verified against code, not checkboxes:
- Phase 1: Pure helper (52 unit tests when it shipped in #292; 82 after the review follow-ups in #294 and #295, always at 100% branch coverage)
- Phase 2: Call-site wiring (derivation at `main.py:1741-1743`, strictly between UTF-8 decode and `_build_source_document` call)
- Phase 3: LLM prompt consumer verification (asserts final derived title reaches `extraction/concept.py:189`)
- Phase 4: Integration tests for the 11 new scenarios (10 title-derivation plus 1 idempotence); the 8 pre-existing scenarios were carried forward unchanged and keep their existing tests
- Phase 5: Zero fixture edits required (verified by full-suite run)
- Phase 6: Final gate all clean (pytest, coverage, ruff, mypy)
- Phase 7: Five review findings closed (public API tests, fence-blindness pin, `#`-prefix test, dead `_FENCE_MARKERS` splat, spec rule 2 reworded)

**Design coherence**: Module placement, public signature, single-pass walk, copy-not-import fence masking, and call-site placement all shipped as designed. The design's stale line-number reference (`:1695` vs actual `:1689`) was caught and corrected during apply; this archive report reflects the corrected version.

**Non-goals confirmed held**: No backfill, no lint check, no escaping at render sites, source's own YAML `title:` not read, no setext heading support.

**Prior warning RESOLVED**: The Variation Selectors Supplement (`U+E0100`-`U+E01EF`) deferral was recorded only in PR #295's description. **GitHub issue #296 was filed for it** to make the decision discoverable in the repo's issue tracker, independent of GitHub PR retention. This resolves the documentation-durability gap.

## Specs Merged

**Delta spec**: `openspec/changes/source-title-from-heading/specs/ingestion/spec.md` (delta against main spec)

**Main spec action**: Modified `openspec/specs/ingestion/spec.md`
- Requirement "Ingest Raw Copy and Source Concept Generation" now includes title-derivation rules (precedence, normalization, validation, forbidden characters, frontmatter handling, fallback)
- New requirement "Idempotent Title Derivation" added (re-ingest of identical bytes yields identical Source document)
- All 19 scenarios integrated: the 8 pre-existing ones carried forward unchanged, plus 11 new (10 title-derivation scenarios on the modified requirement, and 1 on the new `Idempotent Title Derivation` requirement)

Main spec now reflects the shipped behavior. Delta spec archived as-is for reference.

## What Is Still Owed

Stated plainly, not softened:

1. **No backfill of existing bundles.** Every Source ingested before this change keeps its slug title. `openkos list` still shows `01-introduction` for pre-existing documents. The exact symptom issue #248 opens with survives by design. A backfill is a full-document regeneration (frontmatter `title:` AND body `# ` H1 at `okf.py:217`) plus `index.md` bullet-label rewrite, plus an unanswered question about whether historical `log.md` link labels are retroactively rewritten. That is a design problem of its own weight.

2. **No lint check** for "Source title still equals its slug". Candidate follow-up.

3. **GitHub issue #296** — Variation Selectors Supplement (`U+E0100`-`U+E01EF`) deferral. Flagged for a separate decision, not folded in silently.

4. **Broader rule than issue #248's literal wording.** The change is named `source-title-from-heading` to track #248, but the settled rule is broader: it accepts any title-plausible plain first line, not just headings. This was a deliberate maintainer decision, driven by the fact that neither file in `examples/good-life-demo/raw/` contains a single `#` heading, so a strict H1 rule would have delivered nothing for this project's own flagship corpus. The decision and its reason are recorded here, in the proposal, and in the change's own naming caveat.

## Artifact Coverage

All SDD artifacts present in archive:
- ✓ `explore.md` — exploration findings (5 open questions, 3 approaches, risks)
- ✓ `proposal.md` — intent, scope, decisions, capabilities, approach, success criteria
- ✓ `design.md` — technical approach, architecture decisions, data flow, interfaces, testing strategy, threat matrix (archived with line-number corrections applied)
- ✓ `tasks.md` — 7 phases, review workload forecast, all 7 phases checked
- ✓ `verify-report.md` — command evidence, completeness matrix, spec compliance, design coherence, non-goals, verdict
- ✓ `specs/ingestion/spec.md` — delta spec with MODIFIED and ADDED requirements, all 19 scenarios

**Engram traceability**: All observations recorded with their IDs for future reference:
- proposal: #2224
- spec: #2225
- design: #2226
- tasks: #2227
- verify-report: #2233
- archive-report: (saved after archive completion)

## SDD Cycle Closed

The `source-title-from-heading` change is fully planned (proposal/spec/design/tasks), fully implemented and delivered across 4 chained PRs, fully verified, and fully archived. Main specs now reflect the shipped behavior. Ready for the next change.
