# Design: `ingest-source-body` — embed raw source content into the Source concept body

## Technical Approach

`ingest` reads the source's UTF-8 text and passes it to `okf.build_source_concept`
via a new body-only param `raw_content: str | None`. The function renders it under
a `## Source content` heading between the honest intro line and the existing
`# Citations` section. `description` stays a short single-line string (satisfies
`bundle/index.py::_reject_newline`). Because `state.fts.build_index` indexes the
body and `retrieval.answer._assemble_context` (answer.py:69-77) feeds the whole
body verbatim + cites it, embedded content becomes retrievable/citable with ZERO
changes to `fts.py`/`answer.py`. `lint.check_stale_stamps` gains a
`freshness == "snapshot"` skip. Touches `okf.py`, `cli/main.py`, `lint.py`,
`docs/cli.md`, tests only.

## Architecture Decisions

| # | Decision | Alternatives rejected | Rationale |
|---|---|---|---|
| **D1** | New body-only param `raw_content: str | None = None` on `build_source_concept`; body = intro (`description`) + content section + `# Citations`. | Stuff content into `description`; a second `<slug>-raw.md` doc. | `description` must stay single-line (`_reject_newline`); a second doc breaks the "one Source concept per ingest" invariant existing tests assert. Reusing `description` as the intro adds no new param. |
| **D2** | Decode in `ingest` with an explicit `except UnicodeDecodeError → None` guard placed BEFORE the generic `except (OSError, ValueError)`. | Rely on the generic handler. | `UnicodeDecodeError` IS a `ValueError` subclass — the existing handler would fail the whole ingest instead of degrading. The explicit guard is load-bearing. |
| **D3** | Three body renderings: text → `## Source content`; `None` → binary/non-text note; empty/whitespace → "source is empty" note. `description` branches text-vs-binary. | One body shape; silent blank body. | Honest, distinct rendering for each case; empty file (a valid 0-byte `is_file()`) must not render a silently blank section. |
| **D4** | `lint`: add `freshness: str` to `LintDoc`, populate in `collect_docs` (already parses frontmatter — change `_, body` to `metadata, body`), skip `snapshot` docs in `check_stale_stamps`. | Fence-aware regex in `lint`. | `LintDoc` does NOT carry `freshness` today; the field is the minimal honest fix. Snapshot docs carry no freshness claim by design — matches lint.py:156-158's already-documented intent. `check_orphans` is untouched. |

**ADR gate — verdict: NO ADR.** (1) Pattern/interface/tradeoff? Only an additive
param reusing the established body-template pattern — not a technology/architecture
choice. (2) Hard-to-reverse? No — additive, `git revert` removes it, no persisted
state/schema/migration, `raw/` untouched. Both must hold; (2) fails. Matches the
zero-ADR precedent of `add-query-command`/`add-fts-state`.

## Interfaces / Contracts

```python
def build_source_concept(*, title, description, resource, tags, timestamp,
                         sensitivity, provenance, raw_content: str | None = None) -> str:
    ...
    if raw_content is None:
        section = ("_Source content could not be embedded as text "
                   "(binary or non-UTF-8); see the linked resource._\n\n")
    elif not raw_content.strip():
        section = "_The source file is empty._\n\n"
    else:
        section = f"## Source content\n\n{raw_content}\n\n"
    body = f"# {title}\n\n{description}\n\n{section}# Citations\n"
```

Body WITH content: `# {title}` / `{description}` / `## Source content` / `{raw_content}` / `# Citations`.
Body WITHOUT (None): the intro + the italic "could not be embedded" note + `# Citations`.

`ingest` (main.py ~256-274), decode guard + honest single-line description:

```python
try:
    raw_content: str | None = src.read_text(encoding="utf-8")
except UnicodeDecodeError:
    raw_content = None
if raw_content is None:
    description = (f"Raw source imported from '{src}' as {resource}; binary/non-text "
                  "content could not be embedded, not yet extracted into concepts.")
else:
    description = (f"Raw source imported from '{src}' as {resource}; full text embedded "
                  "verbatim below, not yet extracted into concepts.")
```

Both descriptions are single-line (pass `_reject_newline`) and honest ("not yet
extracted" — never claims extraction/compilation). Pass `raw_content=raw_content`
to `build_source_concept`.

## OKF conformance / escaping

`load_frontmatter` (`frontmatter.loads`) parses only the LEADING `---` block; the
Source doc's own frontmatter is at the top, so an embedded markdown source's inner
`---`, `# Citations`, or headings later in the body are inert to the parser — no
second-frontmatter misparse, no conformance hazard (`check_conformance` checks only
`type`). A source containing a literal `# Citations` yields two headings — cosmetic
only, accepted for MVP-1. No escaping needed.

## Zero-change confirmation

`state/fts.py` and `retrieval/answer.py` both operate generically on
`frontmatter + body` via `okf`. `fts` inserts the whole body into the FTS5 `body`
column; `answer._assemble_context` reads the whole body into the LLM context block
and emits a `Citation`. Embedding in the body is therefore sufficient — content is
indexed, fed, and cited with no signature or logic change to either module.

## Data / control flow

```
ingest <src>
  ├─ Phase A gates (is_file, workspace, no-collision) — unchanged
  ├─ src.read_text(utf-8) ── UnicodeDecodeError ─→ raw_content = None
  ├─ description (text vs binary branch) ── single-line, honest
  ├─ build_source_concept(raw_content=...) ─→ body embeds content under ## Source content
  ├─ insert_source_entry(description) — single-line OK
  └─ Phase B writes (copy_exclusive raw/, write concept, index, log) — unchanged
```

Sequence — ingest → bundle → query:

```
user → ingest notes.md
  read_text → raw_content → build_source_concept → bundle/sources/notes.md (body has real text)
user → query "<q>"
  fts.build_index → indexes body (embedded text) → FtsHit(concepts/sources/notes)
  answer._assemble_context → reads body verbatim into CONTEXT block + Citation
  LLMBackend.chat(CONTEXT+question) → grounded reply
  → answer + Citations: → notes (Source)
```

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/model/okf.py` | Modify | `raw_content` param + 3-way body section; docstring honesty shift |
| `src/openkos/cli/main.py` | Modify | `ingest` reads text, `UnicodeDecodeError` guard, branched honest `description` |
| `src/openkos/lint.py` | Modify | `LintDoc.freshness`; populate in `collect_docs`; snapshot skip in `check_stale_stamps` |
| `docs/cli.md` | Modify | ingest section (56-72): body embeds queryable verbatim content, still no extraction |
| `tests/unit/cli/test_ingest.py` | Modify | verbatim body, updated honesty, undecodable + empty cases |
| `tests/unit/retrieval/test_answer.py` | Modify | ingest→fts→answer loop cites embedded content |
| `tests/unit/` (lint) | Modify | snapshot-skip stale-stamp test |

## Testing Strategy (strict TDD, ≥90 branch, no network)

| Layer | What | Approach |
|---|---|---|
| Unit | `build_source_concept` | text → `## Source content` + verbatim text before `# Citations`; `None` → binary note; empty/whitespace → empty note |
| Unit | `ingest` | text source → body has real text + honest description; undecodable (write non-UTF-8 bytes) → no crash, fallback body/description; empty file → distinct note |
| Unit | `check_stale_stamps` | a `freshness: snapshot` doc containing `(as of 2000-01-01)` → zero findings; a non-snapshot doc still flagged |
| Integration | ingest→fts→answer loop | `CliRunner ingest --auto` a source with a distinctive fact into `tmp_path`, then `answer(q, bundle_dir, llm=_FakeLLM())`: assert citations reference the Source (not `NO_MATCH`) AND `_FakeLLM.calls[0]` context contains the real text |

## docs/cli.md plan (56-72)

Reword the "just the raw copy plus one honest Source stub" line: the Source concept
now embeds the source's verbatim text in its body under `## Source content`, making
it queryable via `openkos query`; still NO LLM extraction/concept-splitting (MVP-2).
Note binary/non-text sources copy to `raw/` but their content cannot be embedded as
text (honest fallback body), and empty files render a distinct note.

## Threat Matrix

**N/A** — no shell, subprocess, routing, VCS/PR automation, or executable-file
classification. The only new surface is reading a local file as UTF-8, whose sole
failure mode (`UnicodeDecodeError`) is explicitly handled (D2). Embedded text reaches
FTS via parameterized SQL (`fts._quote_query`) and the LLM via list assembly — never executed.

## Migration / Rollout

No migration. Additive and local; `git revert` removes the param, the wiring, the
lint skip, docs, and tests. No persisted state, config-schema, or dependency change.

## Open Questions

- [ ] None blocking. Param name/shape (D1), decode guard (D2), rendering (D3), and
      lint field (D4) resolved. Size bounding stays an accepted MVP-1 non-goal (verbatim, no truncation).
