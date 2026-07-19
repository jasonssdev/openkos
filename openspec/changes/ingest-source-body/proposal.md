# Proposal: `ingest-source-body` — make ingested sources actually queryable (MVP-1 value fix)

## Intent

`query` (MVP-1 chain #4) is complete but dead-ends: `ingest` is a null compiler —
the source's real text never enters the bundle. `okf.build_source_concept`
(okf.py:75) renders the body as boilerplate `description` only, so `query`
retrieves/cites empty stubs and the LLM answers "the bundle does not cover this."
This change embeds the raw source's actual text into the generated Source
concept's BODY, making ingested content visible to the existing FTS + retrieval +
citation stack — delivering MVP-1's headline value (ask questions about your notes
→ cited answers) WITHOUT LLM extraction/concept-splitting (explicitly MVP-2 per
roadmap.md:63). This is the change that makes MVP-1 genuinely user-testable.

## Scope

### In Scope
- **`okf.build_source_concept`** — new body-only param (e.g. `raw_content`) that
  embeds the source's verbatim text under a clearly-labeled section (design picks
  headings; `# Citations` stays after it). `description` stays SHORT single-line.
- **`cli/main.py::ingest`** — read the copied raw file as UTF-8, pass content to
  `build_source_concept`; on decode failure (binary/PDF), fall back to a short
  honest body noting text couldn't be embedded (no crash). Empty file renders
  distinctly. `description` reworded honestly (verbatim content, NOT extracted).
- **`lint.check_stale_stamps`** — skip the stale-stamp scan on `freshness:
  snapshot` docs (what `ingest` produces). Closes a false-positive THIS change
  would introduce; makes lint.py:156-158's already-documented claim true.
- **`docs/cli.md:56-72`** — describe body-embedded queryable content; still no
  extraction/splitting.
- Tests: verbatim-body assertion, updated honesty assertion, decode-failure/empty
  edge cases, ingest→fts→answer loop.

### Out of Scope (non-goals)
- NO LLM extraction, concept splitting, relationship/entity extraction (all MVP-2).
- NO content truncation (verbatim; MVP-1 notes are small).
- NO changes to `state/fts.py` or `retrieval/answer.py` (they index/feed/cite generically — verified).
- NO changes to `raw/` immutability; bundle stays self-contained/reconstructible.
- NO `bundle/index.py` change (`description` stays single-line, satisfies `_reject_newline`).

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `ingestion`: Source concept body now embeds verbatim source text (queryable); `description` reworded honestly; decode-failure + empty-file handling.
- `lint`: stale-stamp scan is skipped for `freshness: snapshot` docs.

## Approach

Maintainer-locked (exploration-verified): `ingest` reads the raw file's text and
passes it to `build_source_concept`, which renders it in the body under its own
heading, separate from `description` and before `# Citations`. Because FTS
(fts.py:183-189) indexes the body and `answer._assemble_context` (answer.py:69-77)
feeds the body verbatim to the LLM and cites it, embedded content becomes
retrievable and citable with ZERO canonical-module changes. `description` remains
single-line for `bundle/index.py`. `lint` skips snapshot docs to avoid a
false stale finding on user text containing a `(as of YYYY-MM-DD)`-shaped string.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/model/okf.py` | Modified | `build_source_concept` new body-only content param; docstring honesty contract shifts |
| `src/openkos/cli/main.py` | Modified | `ingest` reads source text, decode-failure/empty fallback, short honest `description` |
| `src/openkos/lint.py` | Modified | `check_stale_stamps` skips `freshness: snapshot` docs |
| `docs/cli.md` | Modified | `ingest` section: content embedded + queryable, still no extraction |
| `tests/unit/cli/test_ingest.py` | Modified | Verbatim body + updated honesty + edge-case assertions |
| `tests/unit/retrieval/test_answer.py` | Modified | ingest→fts→answer loop cites embedded content |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Undecodable/binary source crashes ingest | Med | Catch decode error, honest boilerplate-only fallback body |
| Context/index bloat from large verbatim bodies | Low (MVP-1) | Accepted non-goal; noted as future truncation risk |
| lint orphan-scan reads embedded `[x](y)` text | Low | Additive to `referenced` set only — cannot cause false orphan |
| Honesty drift (implying extraction) | Low | `description`/body must say verbatim/not-extracted; test asserts wording |

## Rollback Plan

Additive and local: revert the `build_source_concept` body param + its docstring,
revert the `ingest` wiring (text read + fallback + `description` rewording), revert
the `lint` snapshot skip, and revert `docs/cli.md` + test changes. No persisted
state, no migration, no config-schema change, no dependency change; `raw/` untouched.

## Dependencies

- Consumes archived `fts-state`, `query-answer`, `query-command` read-only (no signature changes).

## Success Criteria

- [ ] After `ingest`, the Source concept body contains the source's real text verbatim under a labeled section, `# Citations` preserved.
- [ ] `query` over an ingested source returns a cited answer referencing that Source (not `NO_MATCH`); LLM context contains the real text.
- [ ] `description` is single-line, honest (verbatim, not extracted) — no false "compiled" claim.
- [ ] Binary/undecodable source ingests without crash via honest fallback body; empty file renders distinctly.
- [ ] A snapshot Source whose text contains `(as of YYYY-MM-DD)` produces NO stale finding.
- [ ] `docs/cli.md` describes queryable embedded content, still no extraction/splitting.
- [ ] No changes to `state/fts.py`, `retrieval/answer.py`, `raw/`; `uv run pytest` green, ruff/mypy clean.

## AGENTS.md Non-Negotiables

Honored: local-first, offline (no new network), immutable `raw/`, provenance/honesty
(body says verbatim-not-extracted), reconstructible-from-canonical (content lives in
the self-contained bundle body, not an external index).
