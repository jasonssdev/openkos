# Design: Two-Output Rule — file a query answer back as a concept

## Technical Approach

`query --save` files the just-printed cited answer as a new derived OKF concept,
reusing ingest's `build_concept` → Phase-A preview → confirm(+`--auto`) → Phase-B
write ordering. All new work is gated behind `if save:` placed AFTER the existing
answer/citations print and AFTER the `no_match_cause` early return, so the
read-only path is byte-identical when `--save` is absent. A small helper
`_stage_filed_answer` mirrors `_stage_derived_objects`: it validates inputs,
re-reads each cited concept's frontmatter to fold sensitivity, and returns one
`_FiledAnswerPlan`. See proposal (Engram `sdd/two-output-rule/proposal`).

## Architecture Decisions

### Decision: Parameterize `## Related` wording (byte-identical ingest)

**Choice**: Add one trailing keyword arg to `build_concept` with a default equal
to today's literal:

```python
def build_concept(*, type, title, description, body, provenance, sensitivity,
                  timestamp, related_note: str = "source this was extracted from") -> str:
    ...
    related = "\n".join(f"- [{ref}](/{ref}.md) — {related_note}" for ref in provenance)
```

Filed answers pass `related_note="concept cited to produce this answer"`.
**Alternatives**: a filed-answer-specific builder (duplicates validation);
overloading `description`/body (mixes concerns).
**Rationale**: ingest never passes the arg → output byte-identical; the imprecise
"extracted from" phrasing is fixed only for the concept→concept case.

### Decision: `_stage_filed_answer` helper (not inline)

**Choice**: A pure-ish helper returning a frozen `_FiledAnswerPlan(link_dir,
section, slug, title, description, path, content, sensitivity)`. It: refuses on
zero citations; derives `slug=_slugify(title)`; refuses on empty slug or on
`path.exists()` collision; folds sensitivity seeded at `cfg.default_sensitivity`
by `okf.combine_sensitivity` over each readable cited concept's frontmatter
(unreadable → skipped, floor holds — fail-safe); builds provenance =
`[c.concept_id for c in citations]`; calls `build_concept`. All refusals raise
`ValueError` with a specific message, caught once at the call site.
**Alternatives**: inline in `query` (untestable, bloats the command).
**Rationale**: mirrors the established ingest staging seam; unit-testable without
a workspace.

### Decision: Refuse `--save` when zero citations

**Choice**: If `result.citations` is empty, refuse with exit 1:
`nothing to file -- the answer cited no concepts; --save records provenance from
citations`. **Rationale**: `build_concept` requires non-empty provenance; a
sourceless "derived" concept is not a real derived node. NO_MATCH cannot reach
here (early return). A sentinel provenance would fabricate a false link.

### Decision: Slug collision handling (mirror ingest)

**Choice**: Detect `path.exists()` in Phase A and refuse (exit 1):
`a concept already exists at bundle/<link_dir>/<slug>.md; use --title to file
under a different name, or forget the existing one`. `write_exclusive` remains the
create-only backstop. **Rationale**: same create-only reconciliation ingest uses;
clear message beats a raw `FileExistsError`.

## Data Flow

    query (read path, unchanged) ──print answer+citations──▶ stdout
         │ if save and citations:
         ▼
    _stage_filed_answer ──re-read cited frontmatter──▶ combine_sensitivity
         │  build_concept(provenance=cited ids, related_note=...)
         ▼
    preview ─▶ confirm gate (--auto / cfg.review / TTY) ─▶ Phase B write:
         concepts/<slug>.md (write_exclusive) ─▶ index.md ─▶ log.md (write_atomic, catalog LAST)

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `src/openkos/model/okf.py` | Modify | Add `related_note` kwarg to `build_concept` (default preserves ingest) |
| `src/openkos/cli/main.py` | Modify | `--save/--title/--type/--description/--auto` flags; `_FiledAnswerPlan`; `_stage_filed_answer`; gated save block in `query` |
| `openspec/specs/query-command/spec.md` | Modify | Reverse the re-filing Non-Goal; add `--save` requirements |

## Interfaces / Contracts

- Flags: `--save` (bool, off), `--title` (str→question), `--description`
  (str→question), `--type` (str, default `"Concept"`; validated by `build_concept`
  against `CLASSIFIABLE_TYPES`, `ValueError` → clean exit 1), `--auto` (bool).
- Log line: `**Filed answer**: [{title}](/{link_dir}/{slug}.md) from query.`
- `--include-deprecated/--include-confidential` unchanged: the fold re-reads the
  actual cited concepts, which already passed those retrieval filters, so a
  confidential-cited answer under `--include-confidential` folds to confidential.

## Testing Strategy (Strict TDD — RED first, `uv run pytest`)

| Layer | RED test |
|-------|----------|
| Unit | `_stage_filed_answer`: provenance=cited ids; title/description defaults=question; `--title`/`--type`/`--description` overrides; zero-citations→ValueError; empty-slug→ValueError; collision→ValueError; sensitivity floor default; high-water-mark (confidential cited → confidential plan) |
| Unit | `build_concept` byte-identical when `related_note` omitted (golden); custom `related_note` renders in `## Related` |
| Integration (CLI) | Purity: `query` (no `--save`) stdout+stderr byte-identical to baseline; `--save` writes concept with correct body/provenance/title/sensitivity + index bullet + "Filed answer" log line; preview+confirm; `--auto` bypass; non-TTY without `--auto` refuses (exit 1); zero-citations refuses; collision refuses; reindex hint printed; ingest golden unchanged after signature change |

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, or executable-file
classification. Filesystem write only: user-supplied title is contained by
`_slugify` (`[^a-z0-9]+`→`-`) under `bundle/<link_dir>/`, and writes are
create-only (`write_exclusive`) with catalog written last — identical containment
and ordering guarantees to `ingest`.

## Migration / Rollout

No migration. Purely additive opt-in flag; revert = drop the `query` diff and the
`build_concept` kwarg. Reindex required after a save (Non-Goal to auto-reindex;
surface a hint in output).

## Size Forecast

build_concept kwarg ~3 LOC; `_FiledAnswerPlan` + `_stage_filed_answer` ~55 LOC;
query flags + gated save block ~55 LOC; tests ~200 LOC. One PR, ~115 authored
non-test LOC — well under the 400/800 budget.

## Open Questions

- None blocking. All scope decisions locked (Engram `sdd/two-output-rule/proposal`).
