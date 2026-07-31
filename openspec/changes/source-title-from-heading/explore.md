# Exploration: source-title-from-heading (GitHub #248)

## Current State

`title = _titleize(src.stem)` — `src/openkos/cli/main.py:1684` — is the sole producer of a
Source's title. `_titleize` (`main.py:1083-1085`) only maps runs of `-`/`_` to a single space
and strips; it has zero direct tests. `slug = _slugify(src.stem)` is computed earlier, at
`main.py:1646`, from the SAME `src.stem` — both derive from the filename, never the content.
`raw_content` is read even later, at `main.py:1689`, via `src.read_text(encoding="utf-8")`,
which can raise `UnicodeDecodeError` (→ `raw_content = None`, handled at `main.py:1690-1695`)
or yield blank text (handled at `okf.py:213-214`). Nothing structurally blocks moving title
computation below the decode — `slug` (needed for `concept_path`/`raw_dest` checks before the
decode) has no dependency on `title`.

`okf.build_source_concept` (`src/openkos/model/okf.py:123-218`) validates NOTHING on its
inputs. Its docstring (rewritten in issue #285, commit `c1a1350`) states explicitly: **the
filename is NOT a trusted input**, and every consumer that interpolates `title` into generated
prose or any other delimited context MUST validate or escape at READ time. This directly
supersedes #248's premise that there is an unresolved trust-boundary question to litigate —
that argument is already settled, in the proposal's favor, in the current docstring.
`okf.build_concept` (`okf.py:258-269`), by contrast, is the fail-closed LLM-output gate: it
raises on an empty-after-strip title and on `\n`/`\r` in title. This asymmetry is real and
precise, and is the shape any new Source-title validation should imitate if validation is
added.

The raw source body is embedded **UNFENCED** under a `## Source content` H2
(`okf.py:216`: `section = f"## Source content\n\n{raw_content}\n\n"`). A source's own `# `
heading therefore already renders as a live, un-neutralized H1 inside the Source document,
below the engine's own `# {title}` (`okf.py:217`). The issue's claim that the body is
"already embedded verbatim in a fenced body" is factually wrong — there is no fence.

## Affected Areas (render/consumption sites for a Source's `title`)

| Site | File:line | Escaping today |
|---|---|---|
| Own document H1 | `okf.py:217` (`# {title}`) | none |
| `index.md` Sources bullet | `bundle/index.py:102` (`* [{title}](/{link_dir}/{slug}.md) - ...`) | newline-only reject (`_reject_newline`, `index.py:42-52`) |
| `log.md` link labels (5 sites) | `main.py:1790`, `:1795`, `:1819-1821`, `:1830-1833`, `:2289-2290`, `:7096-7097` | newline-only reject (`bundle/log.py:32-43`) |
| `openkos list` last column | `main.py:5522-5526` | whitespace-collapse ONLY on read (`bundle/listing.py:117`: `" ".join(str(raw_title).split())`) — this incidentally also neutralizes embedded newlines for THIS site alone, no other guard, no truncation, no ljust (last column) |
| `query` plain terminal output | `main.py:7058` | none |
| LLM extraction prompt | `extraction/concept.py:189` (`f"SOURCE TITLE: {source_title}\n\nSOURCE TEXT:\n{source_text}"`), fed from `main.py:1764` `source_title=title` | none — plain string interpolation into a 2-message chat prompt with no delimiter/escaping; a title containing text resembling `SOURCE TEXT:` or a fence could confuse a local LLM's parse of the prompt's own structure |
| 3 more LLM prompts | `resolution/adjudication.py:138`, `resolution/edge_typing.py:183`, `resolution/contradiction.py:277` | none |
| FTS5 `docs` table | `state/fts.py:230,234` (`title = str(metadata.get("title") or "")`, inserted via parameterized SQL) | SQL-injection-safe (bound param), but content itself is raw — no type filter excludes Source, so Source titles ARE full-text searchable |
| `duplicates`/`adjudicate` `group.trigger` | `resolution/candidates.py:100-104`, `main.py:5739`, `:5979` | N/A — see correction below |

**Guards that exist**: `bundle/index.py::_reject_newline` (`:42-52`) and
`bundle/log.py::_reject_newline` (`:32-43`) both REJECT (raise `ValueError`) on `\n`/`\r` —
they do not escape. Nothing anywhere escapes markdown link-label metacharacters (`[`, `]`,
`(`, `)`), inline emphasis (`*`/`_`), inline code backticks, or heading metacharacters (a
trailing ATX closing `#` sequence). An H1 commonly contains exactly these characters (e.g.
`# Q&A: What's Next? (Draft)`), so a naive extraction would produce titles that break the
`index.md`/`log.md` link syntax silently (no exception — only bare `\n`/`\r` raises).

**No length cap exists on any frontmatter string anywhere in the codebase** (confirmed: no
`MAX_TITLE`/`MAX_LEN`-shaped constant exists). `openkos list` does not truncate its title
column. The `...` in issue #248's example output is the issue author's own elision, not
engine behavior.

## Correction to the prior exploration pass

**The "new exact-title Source collisions via `normalize_key`" concern is NOT viable — Sources
are structurally excluded from duplicate detection entirely, today and regardless of this
change.** `resolution/candidates.py::_iter_eligible` (`:80-105`) explicitly filters:

```python
if not okf_type or okf_type == "Source":
    continue
```

at line 98. Both `find_candidates` (used by `duplicates`/`adjudicate`, `main.py:5721`,
`:5896`) and `find_exact_title_groups` (used elsewhere, `main.py:5315`) build on this SAME
`_iter_eligible` walk. `normalize_key(title)` (`resolution/normalize.py:12-29`) is therefore
NEVER computed for a Source's title by any candidate-finding path — the `group.trigger`
printed at `main.py:5739`/`:5979` can never be a Source's normalized title, only a
Concept/Entity/etc.'s. This change CANNOT create new Source-vs-Source duplicate-detection
collisions, because that detection surface does not exist for Sources at all, independent of
where the title comes from. This should be dropped from the design's risk list rather than
carried forward as an open question.

(A DIFFERENT, real interaction remains: whether an H1-derived title makes MULTIPLE Sources
LOOK similar to a human scanning `openkos list`, or whether it changes `normalize_key`
grouping for a Concept extracted FROM a Source whose title changed — the LLM extraction
prompt now carries different `SOURCE TITLE:` text, which could shift what titles the LLM
assigns to derived Concepts, which DOES feed `_iter_eligible`. That's a second-order,
LLM-mediated effect, not a structural title-collision.)

## Evidence for the issue's five open questions (not answered here — gathered for design)

### 1. What counts as the H1?

The repo's OWN example corpus (`examples/good-life-demo/raw/*.txt`, the only two real
non-test source files in the tree) has **NO `# ` ATX heading and no setext heading in either
file** — each is a bare first line of plain text (`Call with Maria Salazar — 2026-07-14`,
`Reading notes — Epictetus, Enchiridion — 2026-07-05`), used as-is. Yet the CURATED
`bundle/sources/*.md` Source documents for these same two files have rich hand-written titles
("Call with Maria Salazar — 2026-07-14", "Reading notes — Enchiridion, 2026-07-05") that
closely match that first plain line, not any `#`-prefixed heading. **This means: if "H1"
means strictly a `# `-prefixed ATX line, the two canonical examples shipped with this
repository gain NOTHING from this feature — both would fall through to the slug fallback
unchanged**, since neither has a `#` anywhere. Only synthetic test fixtures
(`tests/unit/cli/test_ingest.py:3728,3754,3784,3813`, all `"# Note\n\nRaw material.\n"`) use
real ATX headings. This is a material design tension: a strict "first `# ` line" rule serves
book-chapter-style sources but does nothing for the free-form call-notes/journal-entry style
that is this project's own flagship example. A broader "first non-blank line, heading or not"
rule would serve both but is a materially different, larger feature than "read the H1."

Setext headings (`Title\n=====`) are untested territory — no code in the repo currently
parses them. A leading YAML-like `---` block in a plain-text/markdown source file is not
guarded against by any existing scanner; `bundle/links.py::_mask_fenced_code_blocks` (fence-
aware line masking, `:50-74`) is the nearest reusable primitive but only masks fenced code,
not frontmatter blocks — a naive line-1 scan of a source that begins with `---\ntitle: x\n---`
would need an explicit decision on whether that counts as "front matter to skip" or "the
document's actual first paragraph," since raw sources are arbitrary user files, not OKF
documents themselves.

### 2. What must be stripped/escaped?

Concretely, an H1 can contain (and a filename cannot): `[`, `]`, `(`, `)` (breaks the
`index.md`/`log.md` bullet's `[title](url)` syntax, silently, since only `\n`/`\r` raise
today); `*`/`_`/`` ` `` (inline emphasis/code, renders unexpectedly in the H1's own document
and in `index.md`/`log.md` bullets, since neither site escapes markdown); a trailing ATX
closing sequence (`# Title #`, per CommonMark this is stripped as part of the heading itself
if extraction reads only the "clean" heading text, but a naive "strip leading `# ` only"
implementation would leave a dangling `#`); leading/trailing whitespace (cosmetic only,
trivially stripped); an emoji or other non-ASCII grapheme (renders fine everywhere tested —
`normalize_key` already NFKD-decomposes and casefolds Unicode; no evidence this breaks
anything); a very long line (no length guard exists anywhere — see below).

### 3. Length

No practical-distribution data exists in-repo (no real-world corpus beyond the two example
files, both short single-line "titles"). No cap exists anywhere in the codebase — not in
`build_source_concept`, not in `build_concept` (which validates non-empty and single-line but
never length), not in `openkos list`'s rendering (`main.py:5518-5526`, un-ljust-ed last
column, no truncation). A first heading in a real Markdown document (e.g. a book chapter
title, a research-paper title) is typically well under 200 characters, but nothing in this
codebase currently enforces or even measures that.

### 4. Existing bundles / backfill

`raw/` is confirmed immutable and the untouched original text remains readable for every
already-ingested Source, so a backfill CAN re-derive an H1-based title from the same raw
bytes `ingest` originally read. `backfill-sensitivity` (`main.py:3521-3697`, core
`bundle/provenance.py:333` via `resolve_backfill_raises`) is the closest precedent but is
STRUCTURALLY SMALLER than what a title backfill would require. A title backfill would need
to:

1. re-read `raw/{name}` for each existing Source (not `bundle/sources/*.md` — the canonical
   source of truth for the H1 is the immutable raw file, not the current Source document);
2. rewrite the Source document's frontmatter `title:` field;
3. rewrite the Source document's own body `# {title}` H1 line (NOT just frontmatter —
   `okf.py:217` renders it into the body too, so a title backfill is a full-document
   regeneration, not a scalar patch);
4. update the `index.md` Sources-section bullet's `[title]` label to match;
5. decide what happens to PAST `log.md` entries that already reference the old title in
   `[title](/sources/{slug}.md)` link labels — no rename/retitle precedent exists anywhere in
   this codebase (`grep` for `retitle`/`rename`/`update_index_entry` found nothing), so "does
   index.md/log.md get retroactively rewritten, or does history keep stale labels forever" is
   a genuinely novel question this change introduces.

`backfill-sensitivity` only ever rewrites one frontmatter scalar (`sensitivity`) on descendant
documents, and reads a snapshot of `bundle/**/*.md` — it never touches `raw/`. So quoting it
as "a close, strong precedent" is correct for the write-scaffold SHAPE (read-only snapshot
phase, pure resolver, empty-result short-circuit, preview, confirm gate with `--auto`, one
`log.md` entry, one autocommit) but understates the blast radius.

### 5. Lint check

No such check exists today (confirmed: `lint.py` has no title/slug-comparison logic
anywhere). It would be well-defined as "Source `title` frontmatter value equals
`_titleize(slug)` (or equivalently, `_titleize(src.stem)` reconstructed from the concept
id)" — since `slug` and the CURRENT `title` both derive from the identical `src.stem`
(`main.py:1646` and `:1684`), this equality is exact and mechanical, not fuzzy. Before a
backfill, this would flag every pre-existing Source (100% positive rate) unless the check is
scoped to only Sources ingested after the cutover; after a backfill, it would be near-empty
except where a document genuinely has no first heading and legitimately fell back to the
slug-derived title (a TRUE negative the lint check must not flag).

## Additional findings not in the issue

**LLM prompt blast radius.** `extraction/concept.py:189` builds
`f"SOURCE TITLE: {source_title}\n\nSOURCE TEXT:\n{source_text}"` as the user turn, fed
`source_title=title` from `main.py:1764`. Changing title derivation changes this prompt's
first line for every ingest that runs extraction — a real, if soft, behavior change (an LLM's
extraction output CAN vary with a differently-worded "SOURCE TITLE:" line, since local models
are known to be sensitive to framing). No test in the repo pins extraction output against a
literal title string, so no test would fail mechanically, but a design should call out that
the extraction prompt's semantic content changes. The 3 other LLM prompt sites interpolate
Concept titles, not Source titles directly, but see downstream second-order effects through
the same causal chain.

**Idempotence.** `regenerate = True` (`main.py:1665`) fires whenever `raw_dest.exists()` AND
the re-ingested bytes are byte-identical to the existing `raw/` copy — this is unconditional,
checked BEFORE title computation. Title computation (`main.py:1684`) runs on EVERY ingest
call regardless of `regenerate`. Today this is a no-op concern because `_titleize(src.stem)`
is deterministic and re-ingesting the identical file necessarily re-derives the identical
title. If title derivation moves to reading the raw content's first heading, this remains
equally deterministic FOR THE SAME BYTES, so a re-ingest of an UNCHANGED file still produces
a byte-identical title. No test currently asserts this invariant explicitly for the title
field, so it is worth an explicit pinning test in the design's task list, not an assumption.

**Interaction with `merge`.** `okf.build_merged_document` keeps the SURVIVOR's scalar fields
(title included) unless the survivor is missing that field. Since Sources are excluded from
`_iter_eligible` (see correction above), `merge`'s candidate-detection path is unaffected.
`build_merged_document` itself has no `type`-conditional logic that treats a Source
differently from a Concept, so no change in behavior from this proposal either way.

## Approaches

### 1. First-`# `-line-only extraction, slug fallback

Scan raw content line-by-line (reusing `bundle/links.py`'s fence-masking pattern) for the
first line matching `^#\s+(.+)$`, stripping a trailing closing `#` sequence and surrounding
whitespace; fall back to today's `_titleize(src.stem)` when no such line exists or the source
is binary/empty.

- **Pros**: matches the issue's literal ask; smallest, most mechanical extraction rule;
  trivially testable; leaves non-heading raw sources unaffected — no regression risk for
  files that never had a `#`.
- **Cons**: gives ZERO benefit to the repo's own flagship example corpus (no `#` present in
  either shipped raw file) — may under-deliver on the issue's actual UX motivation for
  free-form notes; still needs a decision on sanitization and a length cap independently.
- **Effort**: Low-Medium.

### 2. First-`# `-line, OR first non-blank line if no heading exists

Broadens fallback so free-form notes (this repo's actual example content) also get a readable
title, only falling to the filename slug when the file is truly empty/binary.

- **Pros**: serves BOTH the issue's literal book-chapter scenario and the repo's own
  call-notes/journal style; matches what the curated example bundle titles already look like
  by hand.
- **Cons**: materially larger scope than the issue's literal wording — risks scope creep into
  "derive title from content, headings optional"; needs its own sanitization/length rules
  layered on arbitrary prose, not just heading text; needs explicit product sign-off since it
  exceeds #248's stated ask.
- **Effort**: Medium.

### 3. Ingest-time change only; defer backfill and lint check to separate follow-ups

Ship title derivation for NEW ingests only, explicitly punting existing bundles.

- **Pros**: smallest, safest first slice; matches this repo's established pattern
  (`propagate-sensitivity-to-derived` shipped before `backfill-sensitivity`); avoids the
  full-document-rewrite + index/log-retitle blast radius until a follow-up change can design
  it properly.
- **Cons**: leaves `openkos list`'s slug-title UX complaint unresolved for every pre-existing
  Source — the exact symptom #248 opens with — until a later change ships.
- **Effort**: Low.

## Recommendation

Sequence as:

1. Ingest-time heading extraction with slug fallback, with sanitization rules for
   link-metacharacters and a length cap decided explicitly in design (neither exists as
   precedent today). The strict-H1 vs. first-non-blank-line choice is a product question to
   raise explicitly, not to decide silently in design.
2. A read-only lint/status detection finding for "title still equals slug" as its own slice,
   mirroring `check_unextracted`'s shape.
3. A backfill verb as a LATER, separately-scoped slice, once its larger blast radius
   (full-document rewrite + index bullet retitle + the novel "does history in log.md get
   rewritten" question) has its own design pass — do not fold it into slice 1.

## Risks

- No sanitization exists today for markdown link-breaking characters (`[`, `]`, `(`, `)`) in
  `index.md`/`log.md` bullet labels — an H1 is far more likely to contain these than a
  filename ever was. **This is the single highest-priority gap design must close.**
- No length cap exists anywhere; a very long H1 would render unbounded in `openkos list`'s
  un-truncated last column and in every bullet/prompt site.
- Backfill's true scope is a full-document regeneration plus an `index.md` bullet rewrite,
  with no rename/retitle precedent anywhere in the codebase to reuse.
- The repo's own example corpus has no `#`-headed raw sources, so a strict "H1-only" rule
  yields no visible improvement on the project's own flagship demo.
- The LLM extraction prompt's first line changes for every future ingest; no test pins this
  today, but it is a real (if soft) behavior change worth naming explicitly to reviewers.
- The prior exploration pass's "new Source-vs-Source duplicate collision via `normalize_key`"
  concern is INVALID — Sources are excluded from all candidate-detection code paths
  (`candidates.py:98`) regardless of title source. Dropped from the risk list.

## Ready for Proposal

Yes. The trust-boundary question the issue raises is already settled in the codebase's favor
(`okf.py:145-167`). The five open questions now have concrete evidence attached for a design
to decide against, rather than needing further investigation.
