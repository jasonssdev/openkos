# Apply progress: `source-title-backfill`

## Slice 1 — `bundle/source_titles.py` (pure core)

Landed separately, on a different branch, in PR #302 (merged to `main` before
this branch's baseline `61b50ce`). All of Slice 1's tasks (1.0-1.13) are
complete there. This branch does not carry `src/openkos/bundle/source_titles.py`
or `tests/unit/bundle/test_source_titles.py` — that is expected; the two
slices are independent by design and this branch only implements Slice 2.

## Slice 2 — `relabel_index_entry` in `bundle/index.py`

Status: complete (tasks 2.1-2.5).

- Implemented `relabel_index_entry(index_text, concept_id, new_title) -> tuple[str, int]`
  in `src/openkos/bundle/index.py`, shaped as `remove_index_entry`'s twin per
  design D5: identity via `_link_identity(target) == concept_id` on the
  bullet's first markdown link, never by label text; only the label span
  between `[` and `]` is rewritten; slug, link target, ` - ` separator,
  description, indentation, bullet marker, and line ending round-trip
  verbatim; zero matches returns `(index_text, 0)` unchanged; multiple
  matches relabels all and reports the total; `_reject_newline` guards
  `new_title`; malformed frontmatter raises `ValueError`.
- Added a new module-level `_LABELLED_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")`
  used only by `relabel_index_entry`. The existing `_LINK_RE` (single
  capture group, read by `remove_index_entry`) is untouched.
- Tests in `tests/unit/bundle/test_index.py`: rewrite-with-distinguishable-
  second-bullet (byte-for-byte, and the original label shares no text with
  `concept_id` so identity-via-target is proven, not identity-via-label);
  zero matches unchanged; duplicate matches relabeled together with
  indentation/bullet-marker round-trip; frontmatter byte-identical;
  newline rejection; malformed-frontmatter `ValueError`; non-first-link on
  a bullet line never matched.
- Quality gate: whole suite `uv run pytest` — 2865 passed. `uv run ruff
  check .` — All checks passed. `uv run ruff format --check .` — 157 files
  already formatted. `uv run mypy .` — Success, no issues in 157 source
  files. New code (`relabel_index_entry` + `_LABELLED_LINK_RE`) has 100%
  branch coverage; the only coverage gap in `index.py` (lines 172/174/176/
  181-183, in the pre-existing `_link_identity`) predates this change.
- Changed lines: `src/openkos/bundle/index.py` +56, `tests/unit/bundle/test_index.py`
  +138 → 194 total (`git diff --stat`), under the 200-line budget.

## Slice 3 — `backfill-source-titles` CLI verb

Not started. Depends on Slice 1 (landed via PR #302) and Slice 2 (this
branch) both being present before it can be implemented.
