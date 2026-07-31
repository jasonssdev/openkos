# Proposal: Derive a Source's title from its content

> Naming caveat: the change is named `source-title-from-heading` to track issue #248, but the
> settled rule is broader than headings. It also accepts a title-plausible first line.

## Intent

`ingest` derives a Source's title from the filename (`_titleize(src.stem)`, `main.py:1684`), so
`openkos list` shows `01-introduction` instead of the document's own title. The document already
carries a better one. Derive it at ingest time, honestly, or keep today's slug.

## Scope

### In Scope

- Derive the Source `title` from raw content at ingest, with precedence: (a) first ATX H1
  (`# `) outside fenced code; (b) else the first **title-plausible** line; (c) else
  `_titleize(src.stem)`, unchanged.
- Normalize, then validate the candidate; any failure falls back to (c).
- Skip a leading, well-formed `---` YAML block; skip fenced code.

### Out of Scope (non-goals)

- **Backfill of existing bundles.** Already-ingested Sources keep slug titles. **Consequence,
  stated plainly: the exact symptom issue #248 opens with (`openkos list` showing
  `01-introduction`) remains for every existing Source until a follow-up ships.** Backfill is a
  full-document regeneration (frontmatter `title:` *and* the body `# {title}` at `okf.py:217`),
  plus an `index.md` bullet-label rewrite, plus the novel question of whether historical `log.md`
  link labels are retroactively rewritten. Its own design problem.
- Lint check "Source title still equals its slug" — candidate follow-up.
- Escaping at render sites (`index.py:102`, `log.py`); reading `title:` out of a source's own
  YAML block; setext headings.

## Decisions

| Question | Decision | Trade-off accepted |
|---|---|---|
| "Title-plausible line" | ALL must hold: non-empty after strip; followed by a blank line or EOF; ≤ 120 chars; does not end in `.` `,` `;` `:`; does not start with markdown block syntax (`-`, `*`, `>`, `#`, a table pipe, or a code fence). | Deliberately biased to false negatives. `Chapter 1.` falls back to the slug. Both `examples/good-life-demo/raw/*.txt` first lines pass; a wrapped prose paragraph fails on the blank-line rule. |
| Sanitisation | **Reject and fall back**, at the derivation site. Forbidden: control chars, `\n` `\r`, `[` `]` `(` `)` `` ` `` `*` `_` `<` `>` `\|`. Normalize first: strip, collapse whitespace runs, strip a trailing ATX `#` sequence. | Not escaping and not stripping: a stripped title misquotes the document. A filename-derived title can still carry `[`/`]` into bullets — that pre-existing gap is unchanged, and `okf.build_source_concept`'s docstring stance (consumers validate at READ time) still governs it. |
| Length | Cap **120** chars, post-normalization; over it, reject → slug. No truncation. | Ellipsis would produce a title that is neither the heading nor the filename. 120 fits `openkos list`'s untruncated last column. |
| Leading `---` block | Skipped as frontmatter when a closing `---` exists; otherwise the `---` line is ordinary content (and fails the predicate). | A source whose real content starts with a horizontal rule loses one line of candidacy. |
| Fenced code | In scope. Reuse `bundle/links.py:50-74` fence masking. | None. |
| Idempotence | **Contract:** re-ingesting byte-identical bytes MUST produce a byte-identical Source document. Derivation is pure over the raw bytes. Pin it with a test. | None. |
| LLM prompt | `extraction/concept.py:189` (`SOURCE TITLE: {source_title}`, fed from `main.py:1764`) changes its first line for every future ingest. Accepted, non-blocking. | Extraction is already non-deterministic and degrades gracefully; no test pins it; the new line is strictly more informative. |

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `ingestion`: "Ingest Raw Copy and Source Concept Generation" gains the title-derivation rule,
  its fallback, and the idempotence invariant.

## Approach

One pure helper — `raw text -> str | None` — called after the UTF-8 decode (`main.py:1689`) and
before `build_source_concept`. `slug` stays filename-derived at `main.py:1646`; it has no
dependency on `title`, so moving title computation below the decode is structurally free.
`None` means "no usable candidate" and the caller keeps `_titleize(src.stem)`. No validation is
added to `okf.build_source_concept`: it stays the unvalidating builder its docstring describes.
Strict TDD (`uv run pytest`), branch coverage ≥ 90.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/cli/main.py:1684` | Modified | title derived from decoded content; slug fallback |
| new derivation helper | New | pure function; module placement is design's call |
| `src/openkos/bundle/links.py:50-74` | Reused | fence-masking prior art, unchanged |
| `src/openkos/model/okf.py` | Unchanged | no new validation; docstring stance holds |
| `tests/unit/cli/test_ingest.py` | Modified | fixtures using `"# Note\n\nRaw material.\n"` now yield `Note`; expectations update |
| `examples/good-life-demo/` | Behavior | future re-ingests get real titles; committed bundle untouched |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Predicate (b) promotes a prose line to a title | Med | Blank-line + punctuation + length rules; raw/ is immutable so it is always re-derivable; wrong guesses are cosmetic |
| Reviewers read (b) as scope creep past #248 | Med | Maintainer-settled; rationale recorded here and in the naming caveat |
| Rejection set too aggressive, common titles fall back | Low | Fallback is today's behavior — never a regression |
| Existing ingest tests churn on new titles | High | Expected; visible in the diff, not silent |

## Rollback Plan

Rollback is unusually cheap **because there is no backfill**: revert the commit and ingest
returns to `_titleize(src.stem)`. Nothing in the bundle re-derives titles on read, so no existing
document changes on revert.

The one residue: Sources ingested while the feature was live keep their content-derived title in
frontmatter, in the body `# {title}` line, and in their `index.md`/`log.md` labels. Nothing
breaks — those are valid titles — but restoring slug titles for them is manual, per document, and
is the same work as the deferred backfill. Bound the exposure by reverting before a large ingest
run. `raw/` is immutable, so the original bytes are always available.

## Dependencies

None. No new runtime dependency.

## Success Criteria

- [ ] A source whose first heading is `# Introduction to Stoicism` ingests with that title.
- [ ] Both `examples/good-life-demo/raw/*.txt` first lines are accepted as titles.
- [ ] A `# ` inside a fenced code block is not treated as a heading.
- [ ] A candidate containing `[`, `]`, `(`, `)`, or exceeding 120 chars falls back to the slug.
- [ ] A source with no usable candidate produces exactly today's title, byte-identical.
- [ ] Re-ingesting a byte-identical file produces a byte-identical Source document.
- [ ] `uv run pytest` green; branch coverage ≥ 90; ruff and mypy strict clean.
