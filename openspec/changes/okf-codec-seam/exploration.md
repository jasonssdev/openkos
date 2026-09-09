# Exploration: okf-codec-seam

Issue [#919](https://github.com/jasonssdev/openkos/issues/919) (P2, `design` +
`discussion`). Read-only exploration against the tree at `eefe8a1`; no edits.

## Premise check

#919 asks to "restore a single OKF on-disk codec seam", naming four files:
`model/okf.py`, `bundle/index.py`, `bundle/source_titles.py`, `bundle/log.py`.
Checked against the code, that premise is **only narrowly true**, and the
difference reshapes the change.

- **The frontmatter seam already exists and is already central.**
  `model/okf.py` exposes `load_frontmatter`/`dump_frontmatter`
  (`okf.py:425-447`). CodeGraph reports **247 call sites** for
  `load_frontmatter` repo-wide. There is nothing to restore here; it is the
  shipped seam.
- **One genuine duplicate, and it is small.** `_FRONTMATTER_RE` and
  `_split_frontmatter_verbatim` are copy-pasted between `index.py:15,20-31`
  and `source_titles.py:125,147-158`. Verified byte-identical regex:
  `re.compile(r"\A---\n.*?\n---\n", re.DOTALL)` in both.
- **The copy is admitted in the source.** `source_titles.py:149-153` says it
  mirrors `index.py`'s helper and is "A deliberate separate copy, not an
  import, to avoid cross-module private coupling."
- **`log.py` is not a duplicate at all.** It already imports the shared
  primitives it needs: `from openkos.bundle.index import _BULLET_MARKERS,
  _LINK_RE, _link_identity` (`log.py:7`).

This is the same shape as #918, whose stated first goal already existed in
`retrieval/answer.py`. The lesson applies: do not read the issue title as a
spec.

## The one behavioural difference the two copies carry

The bodies are identical except the error message:

| File | Raised message |
| --- | --- |
| `index.py:30` | `"index.md: missing or malformed frontmatter block"` |
| `source_titles.py:157` | `"Source document: missing or malformed frontmatter block"` |

That prefix is **not incidental** — it names which file failed, which is the
only thing that distinguishes the two failure paths for an operator. A naive
consolidation that simply imports one helper into the other silently rewords
`source_titles.py`'s refusal to say `index.md:`.

This repository has shipped exactly that defect before: two display paths
folded into one during an extraction, silently rewording a refusal, with the
test asserting only the unchanged half. **The consolidated helper must take
the caller's label as a parameter, and both messages must be pinned by test
before the move.**

## Answers to #919's two open questions

**Q: Is `log.py`'s ledger-entry framing close enough to share the codec?**
No — keep it a sibling. The two framings differ on every axis that matters:

| | `index.py` | `log.py` |
| --- | --- | --- |
| frontmatter | present | absent, and banned by OKF §6/§7 |
| heading level | `#` topic sections | `##` dated sections |
| vocabulary | open canonical topics | ISO dates |
| reversal key | resolved link identity | exact bullet text, LIFO |

`log.py` already shares the one rule that genuinely is the same
(link/bullet matching) by import. Forcing a single "section" abstraction over
these would need a frontmatter-optional parameter nothing needs, and would
misrepresent `log.md` as an ordinary OKF document, which §6/§7 define it as
not being. That is drift-by-generalization — the same failure the issue is
trying to prevent, moved one level up.

**Q: Sequencing against the application-service extraction?** Independent.
That work is finished and archived (#918 closed); this touches the canonical
layer only.

## `source_titles.py`'s direct YAML parsing — not accidental drift

`_patch_title_line` calls `yaml.safe_load(f"title:{value}\n")` on a single
line, to decide whether the title is a self-contained scalar safe to patch
surgically (no anchor, no block scalar, no multi-line value, no trailing
comment). It exists precisely because `load_frontmatter`/`dump_frontmatter`
round-tripping is **not** byte-preserving: a re-dump re-sorts keys, flattens
`tags`, and converts an ISO-8601 `Z` timestamp into a `datetime`.

This is a distinct concern from splitting the frontmatter boundary and must
not be folded into the moved helper. Conflating them removes the byte
preservation `source_titles.py` exists to provide.

## What pins the bytes today, and what does not

Each copy is pinned indirectly through its own module's public API:
`test_index.py`'s `test_insert_source_entry_preserves_frontmatter_verbatim`,
`test_remove_index_entry_preserves_frontmatter_verbatim`,
`test_relabel_index_entry_preserves_frontmatter_verbatim`; `test_source_titles.py`'s
`retitle_document` CRLF / anchored-title / long-title tests.

**No cross-module parity test exists**, and no golden round-trip fixture
covers OKF on-disk framing. So "keep behaviour byte-identical" is currently
**unfalsifiable at the boundary this change moves**. The pin must land
*before* the refactor, or the change verifies itself against nothing.

Two precedents to follow rather than invent:

- **Parity**: `tests/unit/vcs/test_scrub_snippet_parity.py` pins
  `_link_identity` against its unavoidable re-implementation inside
  `vcs/git.py`'s filter-repo snippet — the repo's established answer to
  "two copies that must not drift".
- **Golden**: `tests/unit/cli/test_ingest_characterization.py` with
  `ingest_characterization_goldens.json` — the repo's established golden-file
  shape. A golden mechanism exists here; it just does not cover OKF framing.

## Layering

Clean. `model/okf.py` imports nothing from `openkos.bundle`; both bundle
modules already import `openkos.model.okf`. The moved helper needs only `re`.
No new or reversed dependency.

Noted as an adjacent gap, not this change's job:
`tests/unit/bundle/test_layering.py` is an AST guard, but it currently only
forbids `bundle` importing `openkos.graph`, not the full canonical/derived
boundary.

## Blast radius

`load_frontmatter` has 247 callers but its signature does not change.
`render_index`/`insert_index_entry` (~20 callers, mostly `bundle/bundle.py`)
and `render_log`/`insert_log_entry` (6-7 callers) keep their signatures under
the recommended scope. `templates/` holds no OKF documents.

## Approaches

1. **Narrow consolidation (recommended).** Move `_split_frontmatter_verbatim`
   into `model/okf.py` with the caller label as a parameter; convert both call
   sites to delegations; add the parity test and the golden round-trip pin
   *first*; record the `log.py` sibling decision as a design note with no
   `log.py` code change. ~150-250 lines, one PR, inside the 400-line budget.
2. **Full consolidation as literally titled.** Also unify `index.py`'s `#` and
   `log.py`'s `##` section splitting behind one parameterized primitive.
   High effort, 3-5 slices, and it forces a shared abstraction over two rules
   that are genuinely different. No measured evidence it is needed.
3. **Close as already-addressed.** Defensible on the seam question, but leaves
   the admitted duplicate in place and leaves byte-identity unfalsifiable.

## Recommendation

Approach 1. It removes the only measured duplicate, adds the protection the
issue's own "keep behaviour byte-identical" language presupposes but does not
have, and answers both open questions with evidence instead of deferring them.

The scope is materially narrower than #919's title. That narrowing is
evidence-based, not convenience: the broad goal is largely already shipped,
and the remainder is a different rule that should not be unified. It must be
stated on the issue, not applied quietly.

## Risks

- Reviewers reading the title may expect the four-file consolidation. The
  proposal must carry the measurement, the way #918's reshape did.
- The two YAML concerns must stay separate; folding `_patch_title_line`'s
  scalar check into the moved helper would destroy the byte-preservation
  guarantee.
- If the parity and golden pins land *after* the move, the change's central
  claim is unverified exactly while it is being made.
- The error-message prefix is the one real behavioural difference between the
  copies and the easiest thing to lose silently.
