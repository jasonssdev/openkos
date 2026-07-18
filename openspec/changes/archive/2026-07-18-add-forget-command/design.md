# Design: `openkos forget <concept-id>` (MVP-1 simplified delete)

## Technical Approach
`forget` is the mirror-image of `ingest` (`cli/main.py:142-320`): Phase A (pure validate+build+preview) -> confirm gate -> Phase B (non-transactional write; git=recovery). It adds the missing removal counterpart to `bundle/index.py:53 insert_source_entry`. Reuses `config.require_workspace`, `config.read_config`, `config.WorkspaceLayout`, `bundle_log.insert_log_entry`, `okf.RESERVED_FILENAMES`. `okf.py` byte-unchanged.

## Architecture Decisions

### D1 — Removal primitive: line-drop by resolved link target, frontmatter verbatim
**Choice**: New `bundle/index.py::remove_index_entry(index_text, concept_id) -> tuple[str, int]`. Split frontmatter off byte-for-byte via existing `_split_frontmatter_verbatim` (raises `ValueError` on malformed, matching convention); walk BODY lines only; drop each bullet line whose FIRST markdown link resolves to `concept_id`; rejoin. NO section-splitting needed — matching is by link identity, not by section, which satisfies #922 "generic across Sources/Concepts/People/Decisions" with less machinery and no `_section_header` malformed-chunk raise.
**Alternatives**: (a) section-aware, Sources-only — rejected by #922; (b) match on link TEXT — rejected: text is the human title, not identity.
**Rationale**: engine Source format is `* [title](/sources/slug.md) - desc` (index.py:76); hand-authored Concepts/People/Decisions bullets have no engine format but follow the same "linked title first" markdown convention. Matching the FIRST link's normalized TARGET (not text, not trailing description cross-refs) prevents over-matching a bullet that merely mentions the concept in its prose.

### D2 — Bullet-match contract (the hard one)
- **Candidate line**: stripped line starts with a list marker `* ` or `- ` (accept both; engine writes `*`, hand-authors may use `-`).
- **Target extraction**: `_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")` — take the FIRST match on the line (the linked title).
- **Normalization**: self-contained `_link_identity(target) -> str | None` in the bundle layer (NOT imported from `lint`, per #922; lint imports `config`+`okf` and is the higher "health" layer — importing it would invert layering). Minimal: strip trailing `#fragment` and ` "title"`; reject `scheme:` URLs -> None; strip a single leading `/` (index.md links are bundle-root-rooted, e.g. `/sources/x.md`); resolve `..` segments against bundle root (escape -> None); `removesuffix(".md")`. index.md is at bundle root so rel_dir is always `""` — no source-dir parameter needed. This is a deliberately narrower twin of `lint.normalize_link`.
- **Match**: `_link_identity(first_target) == concept_id`.
- **Count semantics**: `0` -> return `(index_text, 0)` UNCHANGED, not an error (a file with no catalog entry is drift; deletion still safe). `1` -> drop that line. `>1` -> drop ALL matches (duplicate catalog entries all point at the same now-deleted file; leaving any would create a dangling catalog ref). Only the matched line + its trailing newline are removed; every other byte (blank lines, empty sections) is preserved verbatim — no section pruning (avoids reflow risk; `insert_source_entry` already tolerates empty sections).

### D3 — `fsio.remove_file` over inline `Path.unlink`
**Choice**: New leaf primitive `fsio.remove_file(path) -> None` = `path.unlink()` (default `missing_ok=False`). **Rationale**: symmetry — `ingest` routes ALL IO through `fsio` (`copy_exclusive`/`write_exclusive`/`write_atomic`), never touches `Path` write ops directly. Phase A already asserts the file exists, so a Phase-B `FileNotFoundError` (race) is a real error worth surfacing via the existing `except (OSError, ValueError)`.

### D4 — Phase B ordering + invariant (mirror of ingest)
Order: `write_atomic(index_path, new_index)` FIRST (drops the reference), then `write_atomic(log_path, new_log)`, then `fsio.remove_file(concept_path)` LAST. This inverts ingest's content-then-catalog (index.py:305-310) into catalog-then-content, preserving the invariant "the catalog never references a missing file." Non-transactional: a crash after index-write but before unlink leaves a benign orphan (file present, no catalog ref) — same recoverable class as ingest's partial. Recovery = `git status`/`checkout`/`clean`.

### D5 — Confirm gate: reuse ingest verbatim
`--auto` param; gate is exactly `cli/main.py:294-303`: `if not auto and cfg.review:` -> TTY `typer.confirm(..., abort=True)` else non-TTY refuse (exit 1, "re-run with --auto"). No new `--yes`/`--force`.

### D6 — No new Report/Result type
Mirror `ingest`, a MUTATING command: compute strings inline, print preview inline. `LintReport`/`BundleSurvey` exist only for read commands that render structured findings. `remove_index_entry` returns a plain `tuple[str, int]` (mirrors `lint.resolve_window`'s tuple convention). Keeps `forget` lean.

## File Changes
| File | Action | Description |
|------|--------|-------------|
| `src/openkos/cli/main.py` | Modify | New `forget` command; new `_resolve_concept_path(bundle_dir, concept_id) -> Path` validator |
| `src/openkos/bundle/index.py` | Modify | New `remove_index_entry`; new private `_link_identity`, local `_LINK_RE`/bullet regex |
| `src/openkos/fsio.py` | Modify | New `remove_file(path)` |
| `src/openkos/bundle/log.py` | Reuse | New call site `insert_log_entry(... "**Forget**: Removed [Title](/id.md).")` |
| `src/openkos/config.py`, `src/openkos/model/okf.py` | Reuse | Unchanged (RESERVED_FILENAMES, require_workspace, read_config) |
| `tests/unit/cli/test_forget.py` | Create | Mirror `test_ingest.py` |
| `tests/unit/bundle/test_index.py` | Modify | `remove_index_entry` cases: all 4 sections, 0/1/>1 match, link forms, verbatim frontmatter |
| `tests/unit/test_fsio.py` | Modify | `remove_file` cases |

## Interfaces
```python
# bundle/index.py
def remove_index_entry(index_text: str, concept_id: str) -> tuple[str, int]: ...
def _link_identity(target: str) -> str | None: ...          # narrow, bundle-local
# fsio.py
def remove_file(path: Path) -> None: ...                     # path.unlink()
# cli/main.py
def _resolve_concept_path(bundle_dir: Path, concept_id: str) -> Path: ...  # raises ValueError
```

## Phase A flow (`forget`)
1. `require_workspace(root)` -> refuse if not `None`.
2. `_resolve_concept_path`: reject absolute (`startswith('/')`), any `..` part, reserved basename (`f"{PurePosixPath(concept_id).name}.md" in okf.RESERVED_FILENAMES`); build `bundle_dir/f"{concept_id}.md"`; refuse (exit 1) if not `is_file()` (nonexistent = clear error, NOT no-op).
3. Read `index.md`, `log.md`; `(new_index, removed) = remove_index_entry(...)`; `new_log = insert_log_entry(...)`.
4. Preview (glyphs: `-` delete, `~` modify):
```
openkos forget: proposed changes:
  ~ index.md (remove entry)   # only if removed >= 1
  ~ log.md (new dated entry)
  - bundle/<concept-id>.md
```
5. Confirm gate (D5). 6. Phase B (D4).

## Error Taxonomy (all exit 1, stderr, no traceback; `except (OSError, ValueError)`)
| Cause | Handling |
|------|----------|
| Not a workspace | `require_workspace` reason |
| concept-id absolute/`..`/reserved | `ValueError` from `_resolve_concept_path` |
| concept file missing | explicit refuse (ingest-collision style) |
| index/log unreadable or malformed frontmatter | `OSError`/`ValueError` (`_split_frontmatter_verbatim`) |
| Phase B write/unlink failure | caught; benign-orphan invariant + git recovery |

## Testing Strategy
| Layer | What | Approach |
|------|------|----------|
| Unit `remove_index_entry` | 0/1/>1 match; each of 4 sections; link forms `/x.md`,`x.md`,`x`,`../`; frontmatter verbatim; text-not-matched | pure text-in/out |
| Unit `_resolve_concept_path` | `..`, absolute, reserved (`index`/`log`), missing, valid | tmp bundle |
| Unit `fsio.remove_file` | success, missing raises | tmp file |
| CLI `test_forget.py` | Phase-A refusals leave snapshot unchanged; gate (`--auto`/`review:false`/TTY/non-TTY); Phase-B ordering; partial-failure orphan | mirror `test_ingest.py` `_snapshot` |

## Threat Matrix
N/A for routing/shell/subprocess/VCS/PR/executable-classification/process-integration. ONE security requirement remains: **path-traversal deletion** — `_resolve_concept_path` MUST reject `..`, absolute, and bundle-escaping ids BEFORE any `remove_file`. RED tests required: `forget ../../evil`, `forget /etc/passwd`, `forget index` all refuse with exit 1 and zero filesystem mutation.

## Migration / Rollout
No migration. New command, additive. Undo = git (bundle is version-controlled).

## Open Questions
None blocking. Known limitation (deferred to MVP-2): dangling INBOUND links from other concepts are not rewritten (proposal Non-Goal).
