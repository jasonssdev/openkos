# Design: Privacy Purge — History Content-Scrub (Slice 2, completes RTBF)

## Technical Approach

Extend the SAME single `git filter-repo` run that Slice 1 uses to whole-file-expunge raw+bundle
paths so it ALSO content-scrubs `bundle/index.md` and `bundle/log.md` across all history — via
`--file-info-callback` (the only path-scoped content mechanism; `--replace-text`/`--blob-callback`
are path-blind). `expunge_paths` grows an optional `scrub_identities` param; when present it writes
a STATIC callback snippet to a temp file (fixed argv `--file-info-callback <file>`) and passes the
purge-set link-identities to that snippet via a SIDECAR temp file whose PATH travels in an env var —
never interpolated into source. Live `log.md` gets a new best-effort `_purge_clean_live_log`
mirroring `_purge_clean_live_index`, backed by a new `remove_log_entry` twin of `remove_index_entry`.
`_PURGE_RESIDUAL_WARNING` is deleted; purge is now complete RTBF.

## Architecture Decisions

### Decision: Snippet is fully static; targets arrive via env-referenced sidecar file
**Choice**: A module-level constant `_FILE_INFO_CALLBACK_SNIPPET` (byte-identical every run) written
to a temp file. It reads `os.environ["OPENKOS_SCRUB_IDS_FILE"]`, loads one-identity-per-line targets
into a set. **Alternatives**: (a) newline-delimited env value directly — env cannot hold NUL and
mixes data with the shell env surface; (b) string-interpolating ids into the snippet — injection
vector. **Rationale**: the snippet source never contains subject data, so no id/title can inject
Python; the sidecar reuses the exact fixed-argv/temp-file discipline of the existing paths file.

### Decision: Match by markdown link-identity, plus `(id: <id>)` anchor for tombstones
**Choice**: Re-implement `_link_identity` inside the snippet (filter-repo's own process) over bytes;
a line is dropped iff it starts with a list marker AND (its FIRST link's identity ∈ target set OR its
structured `(id: <x>)` anchor's `x` ∈ target set). **Alternatives**: bare id substring (rejected —
over-scrubs prose), passing pre-resolved raw strings (rejected — brittle to link/anchor variants).
**Rationale**: identity match is collision-safe; the anchor is a belt-and-suspenders for tombstones
(`(id:)` is structured openkos output, never free prose), so a live and historical tombstone are both
caught even if its title made the link unusual. FULL LINE removal (no redaction marker — a marker
leaks the fact-of-erasure).

### Decision: Reuse ONE matcher across live + history
**Choice**: `remove_log_entry` imports `_LINK_RE`, `_BULLET_MARKERS`, `_link_identity` from
`bundle/index.py` (same `openkos.bundle` package — intra-package reuse, not a layer inversion) and
adds an `_ANCHOR_RE`. The snippet re-implements the identical logic in bytes because it runs in
filter-repo's process. **Rationale**: the collision-safety-critical logic must not diverge between
the live-tree path and the history path.

## Data Flow

    purge Phase B ──► expunge_paths(root, targets, scrub_identities=purge_ids)
                          │  writes sidecar(ids) + snippet(static) temp files
                          │  env OPENKOS_SCRUB_IDS_FILE=<sidecar>
                          ▼
        git filter-repo --force --invert-paths --paths-from-file <paths>
                        --file-info-callback <snippet>   (ONE pass)
                          │        │
             whole-file delete   per-blob, filename-gated line-scrub of
             (raw+bundle)        bundle/index.md + bundle/log.md across history
                          ▼
        _finalize ──► _purge_clean_live_index ──► _purge_clean_live_log ──► rebuild

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `src/openkos/vcs/git.py` | Modify | `expunge_paths(cwd, rel_paths, *, scrub_identities=None)`; add `_validate_scrub_identities` (reject empty/newline/CR/control); add `_FILE_INFO_CALLBACK_SNIPPET`; when scrub set, write snippet + sidecar temp files, append `--file-info-callback <snippet>`, pass env; `finally` unlink both. One-pass (compatible with `--invert-paths --paths-from-file`). |
| `src/openkos/bundle/log.py` | Modify | New `remove_log_entry(log_text, concept_id) -> (str, int)`; add `_ANCHOR_RE`; import matcher from index. No frontmatter split (log.md has none). |
| `src/openkos/cli/main.py` | Modify | `expunge_paths(root, expunge_targets, scrub_identities=purge_ids)` (:1721); new `_purge_clean_live_log` wired after `_purge_clean_live_index` at both Phase B sites (:1728 finalize-error, :1739 success); DELETE `_PURGE_RESIDUAL_WARNING` (:1265) + all echoes (:1586, :1730, :1752); success message stays, optional plain "complete erasure" confirmation. |
| `openspec/specs/privacy-purge/spec.md` | Modify | +history-content-scrub requirement; −residual-warning requirement. |
| `tests/unit/vcs/conftest.py` | Modify | Multi-commit fixture (residual bullet/tombstone in an earlier commit; index/log rewritten later). |

## Interfaces / Contracts

```python
# git.py — the STATIC snippet body (compiled by filter-repo as
# def file_info_callback(filename, mode, blob_id, value): <BODY>). No interpolation.
_FILE_INFO_CALLBACK_SNIPPET = r'''
import os, re
_p = os.environ.get("OPENKOS_SCRUB_IDS_FILE")
if _p and filename in (b"bundle/index.md", b"bundle/log.md"):
    with open(_p, "r", encoding="utf-8") as _fh:
        _targets = {ln.rstrip("\n") for ln in _fh if ln.rstrip("\n")}
    _link = re.compile(rb"\[[^\]]*\]\(([^)]+)\)")
    _scheme = re.compile(rb"\A[A-Za-z][A-Za-z0-9+.-]*:")
    _anchor = re.compile(rb"\(id: ([^)]+)\)")
    _markers = (b"* ", b"- ")
    def _identity(raw):
        raw = raw.split(b"#", 1)[0].strip()
        if raw.endswith(b'"') and b' "' in raw:
            raw = raw.rsplit(b' "', 1)[0].strip()
        if not raw or _scheme.match(raw):
            return None
        raw = raw[1:] if raw.startswith(b"/") else raw
        parts = []
        for part in raw.split(b"/"):
            if part in (b"", b"."):
                continue
            if part == b"..":
                if not parts:
                    return None
                parts.pop()
            else:
                parts.append(part)
        ident = b"/".join(parts)
        ident = ident[:-3] if ident.endswith(b".md") else ident
        try:
            return ident.decode("utf-8")
        except UnicodeDecodeError:
            return None
    _contents = value.get_contents_by_identifier(blob_id)
    _out = []
    for _line in _contents.splitlines(keepends=True):
        _s = _line.lstrip()
        _drop = False
        if _s.startswith(_markers):
            _m = _link.search(_s)
            if _m is not None and _identity(_m.group(1)) in _targets:
                _drop = True
            if not _drop:
                _a = _anchor.search(_s)
                if _a is not None:
                    try:
                        _drop = _a.group(1).decode("utf-8") in _targets
                    except UnicodeDecodeError:
                        _drop = False
        if not _drop:
            _out.append(_line)
    _new = b"".join(_out)
    if _new != _contents:
        blob_id = value.insert_file_with_contents(_new)
return (filename, mode, blob_id)
'''
```

Matching precision proof: (1) a surviving concept's bullet — its FIRST link resolves to its OWN
identity (not in `_targets`); a later prose mention of the purged title/id in the description is not
the first link, and index bullets carry no `(id:)` anchor → KEPT. (2) a log line that only mentions
the purged id in PROSE has no link whose identity is a target and no structured `(id: target)` anchor
(anchors are written only inside tombstones) → KEPT. (3) a purged bullet/tombstone matches by its
link identity (and, for tombstones, redundantly by anchor) → DROPPED across all history.

## Testing Strategy (Strict TDD — `uv run pytest`, real git + git-filter-repo)

RED tests to write first:
- **history-scrub, index.md**: purged id/title absent from `bundle/index.md` in EVERY historical
  blob (walk `git log -p` / `git cat-file` per commit).
- **history-scrub, log.md**: purged id/title/tombstone absent from `bundle/log.md` across ALL history.
- **collision — sibling bullet**: a surviving concept's `index.md` bullet is byte-identical across all
  history.
- **collision — prose mention**: a `log.md` line that only mentions the purged id in prose (no
  matching link/anchor) is byte-identical across all history.
- **live log tombstone gone**: `_purge_clean_live_log` removes the live tombstone; live sibling lines
  round-trip byte-identical.
- **body untouched**: a surviving concept file that legitimately contains the purged id in its body is
  unchanged (path gate proof).
- **no residual warning**: purge stdout/stderr never contains the old warning text.
- **one-pass**: single `git filter-repo` invocation performs both expunge and scrub.
- **adapter unit**: `expunge_paths(scrub_identities=[...])` scrubs matching lines; `scrub_identities`
  empty/None ⇒ Slice-1 behavior unchanged (no `--file-info-callback` argv); invalid identities raise
  `ValueError` before any subprocess.

Multi-commit fixture: earlier commit writes the purged concept's bullet + a forget tombstone; a later
commit rewrites index.md/log.md — so history actually holds the residual to scrub.

## Threat Matrix

Applicable (VCS/subprocess/process integration):

| Row | Status | Safe behavior / RED test |
|-----|--------|--------------------------|
| Argv injection (snippet/ids) | Applicable | Snippet + ids via fixed-argv temp files only; ids never in argv/source. Test: identity with metachar still matched byte-exact, never re-parsed. |
| Code injection into callback | Applicable | Snippet is a static constant; targets via env-referenced sidecar file. Test: id/title containing Python is inert. |
| Path-blind over-scrub | Applicable | filename gate + link-identity match. Tests: sibling bullet, prose mention, body-with-id all byte-identical. |
| Injected extra scrub target | Applicable | `_validate_scrub_identities` rejects newline/CR/control/empty before write. Test: raises `ValueError`, no subprocess. |
| Partial-failure window | Applicable (unchanged) | `GitError` vs `GitFinalizeError` semantics preserved; scrub is inside the same pass. |
| Temp-file cleanup | Applicable | `finally` unlinks snippet + sidecar even on failure. |

## Migration / Rollout

No migration. Irreversible in-place history rewrite (same as Slice 1, now coarser). Fail-closed rails
refuse BEFORE any write; there is no post-rewrite rollback.

## Slice / PR Structure

Estimated LOC: snippet+expunge_paths change ~140, `remove_log_entry` ~35, `_purge_clean_live_log` ~30,
warning removal ~ −20, multi-commit fixture ~40, tests ~300 ⇒ ~525 net. Under the 800/400-review
budget with headroom ⇒ ONE PR. If the fixture+test surface inflates past budget at apply time, fall
back to the Slice-1-style split: PR#1 adapter callback-plumbing (`git.py` + adapter tests), PR#2
verb/live-log wiring (`main.py` + `log.py` + cli tests + warning removal).

## Open Questions

- [ ] None blocking. Confirm `git-filter-repo` in CI supports `--file-info-callback` (verified against
      the vendored copy; assert availability in the adapter test).
