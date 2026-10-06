# Design: okf-import

MVP 5, second deliverable. ADR-0050 (written by apply slice 1, status
`Proposed`). Inputs: `proposal.md` (its Decisions are resolved),
`exploration.md`, Engram `sdd/okf-import/research` (verified OKF v0.2 facts),
the owner decision that `type_sensitivity_defaults` apply to imported
concepts, ADR-0030, ADR-0036, ADR-0037, ADR-0038, ADR-0041, ADR-0045,
ADR-0048, ADR-0049, and the archived `okf-export` design as the model. Code
anchors are at `8f5db84e` (0.5.1).

## Technical approach

```
openkos import <dir> --namespace <ns> [--sensitivity <level>] [--auto]        (cli/main.py)
        |
        v
 usage checks: namespace slug, --sensitivity choice ----------------------- exit 2
        |
        v
 import_service.plan_import(...)   PHASE A, no lock, no write   (application/import_service.py)
   1. input checks: existing dir, not inside bundle/, not an ancestor of the workspace
   2. okf.read_foreign_bundle(dir)    bounded walk + guarded parse   -> ForeignBundle | ForeignRefusal
   3. namespace free: imports/<ns>/ absent, anchors absent or torn-ours  -> else refuse
   4. per conformant document:
        label = config.type_birth_sensitivity(cfg, type,
                    combine(floor, okf.fold_foreign_sensitivity(mapping)))
        body  = links.rewrite_links_into_namespace(body, foreign_id, "imports/<ns>")
        text  = okf.adopt_foreign_document(doc, body, label, anchor_id(ns, label), prefix)
   5. anchors: okf.build_import_anchor(...) once per effective label present
   6. PROOF: links.namespace_link_violations + okf.adopted_violations   -> any hit: refuse
   -> ImportPlan (texts, anchors, skipped+reasons, labels, type raises, fingerprints)
        |
        v
 preview -> confirm unless --auto (or review: false); non-TTY without --auto -> exit 1
        |
        v
 import_service.publish_import(plan, commit_section=..., autocommit=...)   PHASE B, under the lock
   a. re-read foreign bundle; manifest differs        -> exit 3 (retry-safe drift)
   b. reload config; label fingerprint differs        -> exit 3
   c. namespace now exists / anchor path not ours     -> exit 1
   d. remove stale staging bundle/imports/.<ns>.openkos-import-*
   e. write every adopted document into a new staging dot-directory
   f. okf.check_conformance(staging)                  -> violation: rm staging, exit 1
   g. write anchors, then index.md and log.md (re-applied to the current bytes)
   h. os.replace(staging, bundle/imports/<ns>)        <- the completion point
   i. any failure in e..h: rm staging, restore snapshot of anchors/index/log, exit 1
   j. one autocommit "openkos: import <ns> (+N concepts)", commit disclosure
```

All format knowledge stays in `model/okf.py` (reader, fold, adopt, anchor,
proof of frontmatter). Link knowledge stays in `bundle/links.py` (rewrite and
proof of bodies). The workspace layout of imports (the `imports/` directory,
the namespace rule, anchor ids, the imported predicate) is a new leaf of the
canonical layer, `bundle/imports.py`. The use case is one narrow synchronous
service (ADR-0018). The CLI keeps parsing, the preview text, the confirm
gate and exit codes. Nothing calls a model, and the service imports no LLM
module.

## Architecture decisions

### D1. The foreign reader is a new bounded walker in the OKF seam

**Choice.** `okf.read_foreign_bundle(root: Path) -> ForeignBundle` in
`model/okf.py`, a new section beside `_iter_docs` (`okf.py:3822`). It never
calls `_parse_post` (`okf.py:678`), `load_frontmatter` (`okf.py:687`) or
`concept_metadata` (`okf.py:708`) on foreign bytes. Every frontmatter block
goes through `parse_incoming_frontmatter` (`okf.py:943`), which caps the
block at 64 KiB (`okf.py:846`) and depth at 32 (`okf.py:851`), rejects every
anchor and alias (`okf.py:923`), requires plain data that survives a round
trip (`okf.py:981-992`), and never raises. The body is split with the same
`frontmatter_block_end` rule (`okf.py:812`) through a new
`split_incoming_document(text) -> tuple[IncomingFrontmatter, str]`, so the
block the guarded parser judged is exactly the block the body excludes.

The walk is `os.walk(root, followlinks=False)` with sorted names, depth
first, and works on relative POSIX paths only. Each entry is classified in
this order; the first matching rule decides:

| # | Entry | Outcome | Reason code |
|---|---|---|---|
| 1 | any entry that is a symlink (`lstat`), file or directory, wherever it points | refuse import | `symlink` |
| 2 | a non-regular, non-directory entry (FIFO, socket, device) | refuse import | `special-file` |
| 3 | a relative path with an empty, `.`, `..` or absolute component (defence in depth: unreachable from a link-free walk, tested on the pure validator) | refuse import | `traversal` |
| 4 | a name starting with `.` (file or directory; a directory is not descended) | skip, report | `dot-entry` |
| 5 | directory depth > `FOREIGN_MAX_DEPTH` (32) | refuse import | `too-deep` |
| 6 | entries walked > `FOREIGN_MAX_ENTRIES` (50 000) | refuse import | `too-many-entries` |
| 7 | a file whose name does not end in `.md` | skip, report, never opened | `not-markdown` |
| 8 | a `.md` file whose name casefolds to `index.md` or `log.md`, at any depth | skip, report (the root `index.md` is read for `okf_version` only) | `reserved-file` |
| 9 | a path segment containing a control character, `\`, `:`, `[`, `]`, `(`, `)`, `<`, `>`, `#`, `%`, `?`, `*`, `"`, `\|`, or leading/trailing whitespace | refuse import | `unsafe-name` |
| 10 | a segment over 255 UTF-8 bytes, or a namespaced path over 1024 bytes | refuse import | `name-too-long` |
| 11 | `.md` documents > `FOREIGN_MAX_DOCUMENTS` (10 000) | refuse import | `too-many-files` |
| 12 | a file over `FOREIGN_MAX_FILE_BYTES` (8 MiB), checked by `fstat` before reading and by reading at most cap + 1 bytes | refuse import | `file-too-large` |
| 13 | running total of `.md` bytes > `FOREIGN_MAX_TOTAL_BYTES` (256 MiB) | refuse import | `bundle-too-large` |
| 14 | two paths (files or directory prefixes) equal after NFC but different as written | refuse import | `nfc-collision` |
| 15 | two paths equal after `NFC` then `casefold()` but different after NFC | refuse import | `case-collision` |
| 15b | a segment with whitespace is renamed to a slug (D2, slice 4b); two paths equal after that rename (NFC then `casefold()`) but different as written, at least one renamed | refuse import | `rename-collision` |
| 15c | a renamed segment over 255 UTF-8 bytes, or a renamed namespaced path over 1024 bytes (casefolding can grow a name) | refuse import | `name-too-long` |
| 16 | bytes that are not UTF-8 (a single leading BOM is stripped) | skip, report | `not-utf8` |
| 17 | guarded parse status `alias`, `too-deep`, `too-large`, `not-a-mapping`, `unsupported-value` | refuse import | `frontmatter-<status>` |
| 18 | guarded parse status `absent`, `empty` or `malformed` (a YAML syntax error) | skip, report (§11 rule 1) | `frontmatter-absent` / `frontmatter-empty` / `frontmatter-malformed` |
| 19 | parsed mapping with a missing, empty or non-string `type` | skip, report (§11 rule 2) | `missing-type` |
| 20 | otherwise | adopt | — |

Files are opened with `os.open(path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)`,
then `fstat` must report a regular file (so a FIFO swapped in after the walk
cannot hang the read), and `fsio.symlinked_segment(path, root)` (`fsio.py:372`)
must find no linked segment. The constants are code constants (`Final`),
never settings, named in ADR-0050.

**Why the line between refuse and skip falls where it does.** OKF §11 asks
consumers to tolerate missing optional fields, unknown types and keys,
broken links and a missing `index.md`; it does not ask them to accept a
document without parseable frontmatter or `type`. Those are skipped and
reported, and so is frontmatter that is not valid YAML. One sloppy file
therefore never blocks an import, and `bundle/` stays conformant by
construction (spec: "A Non-Conformant Document Is Tolerated, Not Adopted").
Everything that is a property of the tree (symlinks, special files, names,
sizes, counts, collisions) or a frontmatter hazard the guarded parser exists
to stop (aliases, depth, size, non-mapping roots, non-plain values) refuses
the whole import with its own code,
because adopting the rest of a tree that contains a hostile entry is not a
state a human asked for.

**Reason codes refine the spec's wording.** The spec says a refusal has a
"named reason" and exits "non-zero". The reason codes above, and the exit
split in D9 (1 refusal, 2 usage, 3 retry-safe drift or busy), are this
design's refinement of that wording; they do not change it. Each
hostile-input code maps to the `okf-import` spec scenario it satisfies
(requirement "Hostile Bundles Are Refused With A Named Reason", unless
another requirement is named):

| Reason code | Exit | Spec scenario |
|---|---|---|
| `traversal` | 1 | Path traversal is refused |
| `symlink` | 1 | A symlink is refused |
| `file-too-large` | 1 | Size and count caps are enforced (per-file cap) |
| `bundle-too-large` | 1 | Size and count caps are enforced (total cap) |
| `too-many-files`, `too-many-entries` | 1 | Size and count caps are enforced (file-count cap) |
| `frontmatter-alias` | 1 | A frontmatter alias bomb is refused |
| `frontmatter-too-large`, `frontmatter-too-deep` | 1 | The requirement's clause "a frontmatter block over the guarded parser's bounds" |
| `frontmatter-not-a-mapping` | 1 | A non-mapping frontmatter root is refused |
| `rename-collision` | 1 | A collision after the rename is refused (requirement "A Name With Whitespace Is Renamed To A Slug") |
| `case-collision`, `nfc-collision` | 1 | Case-fold and NFD collisions are refused (one code each, distinct as the scenario requires) |
| `frontmatter-unsupported-value`, `special-file`, `unsafe-name`, `name-too-long`, `too-deep`, `unreadable` | 1 | No dedicated scenario: design hardening under the same requirement ("refused with a named reason, workspace unchanged") |
| `frontmatter-malformed`, `frontmatter-absent`, `frontmatter-empty`, `missing-type`, `not-utf8` (skip, not refuse) | — | "A Non-Conformant Document Is Tolerated, Not Adopted" (`frontmatter-malformed` satisfies "Unparseable frontmatter is skipped, not fatal"; `missing-type` satisfies "A document with no `type` is skipped and reported") |
| `dot-entry`, `not-markdown`, `reserved-file` (skip, not refuse) | — | "Skipped Files Are Reported": non-concept files are reported, not copied |

A YAML syntax error is a skip, not a refusal, because the spec scenario
"Unparseable frontmatter is skipped, not fatal" requires it. The guarded
parser still refuses the hazards it exists for (aliases, depth, size, a
non-mapping root, non-plain values). Skipping a malformed block is safe
even when it also hides an alias: the block is never loaded into a mapping,
and the document is never adopted (`okf.py:964-982`).

**Collisions are refused on every filesystem.** Collision keys are computed
over strings, not by probing the disk, so the result does not depend on
whether the workspace sits on case-insensitive APFS or ext4. Every adopted
file is written under its NFC name (`_slugify`'s convention, which
`concept_id_for` at `okf.py:3469` assumes), so an NFD foreign name never
reaches `bundle/` and never trips lint's `non-nfc-name`.

**Unsafe names are refused, not escaped.** The deny-list is exactly the set
of characters that some engine reader treats as structure:
`lifecycle.canonicalize_concept_id` refuses `\` and `:`
(`lifecycle.py:132-135`), so such a concept could never be forgotten; index
bullets refuse `(`/`)` in slugs (`index.py:168`); the graph link pattern stops
at `#`, `)` and whitespace (`sqlite_graph.py:157`). Renaming silently would
break the foreign bundle's own links; refusing names the file to fix.

**Foreign reserved files.** The root `index.md` is read for `okf_version`
only (§12): its frontmatter goes through the guarded parser; any non-`parsed`
status degrades to "version unknown" with a preview warning rather than a
refusal, because the file is never adopted. Nested `index.md`/`log.md` and
the root `log.md` are skipped and reported. Matching is case-insensitive
(rule 8), because `RESERVED_FILENAMES` (`okf.py:72`) is matched exactly by
`_iter_docs` while `canonicalize_concept_id` refuses reserved names
case-insensitively (`lifecycle.py:142-144`): an adopted `INDEX.md` would be a
concept nobody can forget.

**Version handling.** `okf_version` `"0.2"` is known. Absent, another
string, or a non-string is imported best-effort (§12 SHOULD) and the
preview states what was observed. `migrate_document` (`okf.py:3337`) is not
run at import: it migrates engine-written legacy shapes and would stamp
`generated: {by: openkos/legacy}` from a foreign `timestamp` (R1,
`okf.py:3402-3430`), a false authorship claim. D4 moves `timestamp` under the
inert key, so a later `openkos repair` cannot apply R1 to an imported
document either.

**Alternatives considered.** Reusing `_iter_docs` with a size check bolted
on (rejected: `frontmatter.loads` auto-detects TOML/JSON fences and expands
aliases before any check could run). Skipping symlinks instead of refusing
(rejected: the proposal lists symlinks as refused, and a link inside an
untrusted tree is a signal, not noise). Probing the target filesystem for
collisions (rejected: the outcome would differ by machine).

### D2. Namespace placement and validation

**Choice.** `bundle/imports.py` (new, canonical layer, imports only `okf`)
owns the layout:

```python
IMPORTS_DIR: Final = "imports"
NAMESPACE_RE: Final = re.compile(r"\A[a-z0-9]+(?:-[a-z0-9]+)*\Z")   # max 64 chars

def namespace_reason(namespace: str) -> str | None: ...   # None when valid
def namespace_prefix(namespace: str) -> str: ...          # "imports/<ns>"
def anchor_id(namespace: str, label: str) -> str: ...     # "imports/<ns>--<label>"
def staging_name_prefix(namespace: str) -> str: ...       # ".<ns>.openkos-import-"
def is_imported_concept(concept_id: str) -> bool: ...     # first segment == "imports"
```

Every adopted Concept ID is `imports/<ns>/<foreign id>`, with each segment that
holds whitespace renamed to a slug (below). `--namespace` is
required and has no default. A default derived from the directory name
would put a local directory name into every Concept ID, which travels in an
export, and would take effect with no preview under `--auto`.

The slug is ASCII lowercase with single hyphens: it has no NFC/NFD or case
variants, so two namespaces cannot collide on any filesystem, and it cannot
contain `--`, which makes the anchor ids `imports/<ns>--<label>` collision-free
against every other namespace's directory and anchors. An invalid value is a
usage error (exit 2) raised by a Typer callback before any read.

The namespace is "taken" when `bundle/imports/<ns>/` exists (as anything,
even an empty directory or a file), or when an anchor path
`bundle/imports/<ns>--<label>.md` exists and is not a torn anchor of this
same namespace (D6). Both refuse with exit 1 and a reason naming the
namespace and stating that re-import is unsupported (owner decision 4).
`bundle/imports` itself must not be a symlink or sit under one
(`config.symlink_boundary_reason`, as `forget` checks at `lifecycle.py:186`).

**A name with whitespace is renamed (slice 4b, owner decision 2026-10-06).**
The graph link reader (`sqlite_graph._LINK_RE`) needs a `/` straight after `(`,
stops at whitespace and never decodes, so a link to a name with a space cannot
be represented in any spelling (plain, `<...>` or `%20`) and the anchor-to-
document edge would silently vanish. Slice 4 first refused such a name
(`whitespace-in-name`); the owner chose to rename instead. The rule is
`okf.rename_foreign_segment`: a segment holding whitespace has every whitespace
run (`str.split()`, so every `isspace()` character) replaced by one `-`, then
`casefold()` and NFC; a segment without whitespace keeps its bytes. Casefolding
rather than lowercasing is what stops a renamed name from differing from another
only by case. The engine's `source_titles.slugify` was considered and rejected:
it also rewrites `_`, `.` and every other non-alphanumeric, which the foreign
name did not need. The rename applies to directory segments too. Two names equal
after the rename (NFC then `casefold()`, against renamed AND unchanged names,
files and directory prefixes) are refused as `rename-collision`, naming both
foreign paths: it runs in the reader beside the other collision checks
(`okf.validate_foreign_renames`), so the Phase B re-read judges it again. The
original path is kept as `imported.path` (only when the document was renamed;
`imported.id` stays the foreign id, which also keeps the anchor digest stable),
and `ImportPlan.renames` lists each `(foreign path, new path)` for the preview.

**Alternatives considered.** Keep foreign ids and refuse on collision
(rejected by the owner, decision 2). Prefix only on collision (rejected:
whether a document is namespaced would depend on the local bundle at import
time). Allow Unicode namespaces (rejected: reintroduces the normalization and
case questions the slug rule removes for free).

### D3. Link rewrite: anchor on the delimiter, over-rewrite rather than miss

**The hazard is real and multi-headed.** The engine has five independent
link recognizers, and they disagree:

| Recognizer | Pattern | Scope | Masks fences | Reads |
|---|---|---|---|---|
| `bundle/links.py::_LINK_RE` (`links.py:43`) | `\[[^\]]*\]\(/…\.md(#…)?\)` | per line | yes | absolute `.md` only |
| `bundle/links.py::_INLINE_LINK_RE` + `_REFERENCE_DEFINITION_RE` (`links.py:291-305`) | inline, image, definitions | per line | yes | absolute and relative, unquoted |
| `graph/sqlite_graph.py::_LINK_RE` (`sqlite_graph.py:157`, used at `:529`) | `\[[^\]]*\]\(/…\.md…\)` | whole body: a label may span lines | yes | absolute `.md` only |
| `lint.py::_LINK_RE` + `normalize_link` (`lint.py:707`, `:671`) | `\[[^\]]*\]\(([^)]+)\)` | whole body: target may hold spaces | no | absolute, relative, extension-less (`concepts/x` names `concepts/x.md`), not unquoted |
| `bundle/index.py::_LINK_RE` + `_link_identity` (`index.py:216-257`) | index bullets | root `index.md` only | n/a | root-relative |

A rewriter built on any one of these misses a form another one resolves:
a per-line rewrite misses the graph's multi-line label; a `.md`-only rewrite
misses lint's extension-less form; an unquoting rewrite and a raw rewrite
disagree on `%2F`. A missed form makes a foreign link resolve to an
unrelated local document, which is the failure the spec forbids.

**Choice.** Every recognizer requires the two characters `](` (inline and
image) or a definition `]:`. The rewriter therefore anchors on the
delimiter, not on the label, and scans the whole body without a line split
and without a fence mask. Each `](` or `]:` occurrence is a pointer site.
Its destination is read as CommonMark does (skip spaces and tabs and at most
one line ending; then `<…>`, or a run of non-whitespace characters with
balanced parentheses for `](`). Each site is classified by a shared resolver
in the foreign frame (the referring document's foreign id):

| Destination (after `<>` strip; decision on the unquoted form) | Rewrite |
|---|---|
| empty, `#anchor` only, or `scheme:` (`_SCHEME_RE`, `links.py:289`) | unchanged |
| relative, and it stays inside the foreign root | unchanged (it resolves inside `imports/<ns>/` by translation), unless a segment the link itself spells holds whitespace: then those segments are renamed in place and the destination re-quoted (a relative link inherits the whitespace of its own document's directory, and that directory moves with the document) |
| relative, and it climbs above the foreign root | replaced by `/imports/<ns>/<path clamped per RFC 3986 §5.2.4>` |
| absolute (`/…`, including `//…`), any extension or none | `/imports/<ns>` inserted before it when the raw path has no dot segment, no percent-escape and no whitespace segment (byte-preserving); otherwise replaced by `/imports/<ns>/<quote(renamed clamped path)>` |

The fragment, a query and an optional title after the destination are kept
byte for byte. Fenced code and inline code are rewritten too: lint reads
links without a fence mask, and over-rewriting an example in a code block
is visible and harmless, while leaving one out is not provable. Raw HTML
`href`/`src` attributes and wiki links (`[[x]]`) are not pointer sites: no
engine reader resolves them, so they cannot point the engine at a local
document. The preview counts documents that carry a bundle-absolute HTML
attribute, as an informational warning.

**The resolver is shared, not duplicated.** The core of `_bundle_target_id`
(`links.py:308-336`) is refactored into
`resolve_link_target(target, *, file_id) -> LinkTarget` (kind, normalized
path with suffix, `escaped`, RFC-clamped path). `_bundle_target_id` becomes a
thin wrapper with unchanged behaviour (pinned by
`tests/unit/bundle/test_export_pointers.py`). The rewrite and the proof both
call `resolve_link_target`.

**The proof refuses.** After the transform,
`namespace_link_violations(body, *, concept_id, prefix)` re-scans every
pointer site of the output in the local frame under every reading a
recognizer can take: the CommonMark end, the first `)` (lint), the first
whitespace (graph, links), each raw and unquoted, with lint's ` "title"`
strip. Each reading must be external, an anchor, empty, or a path that
starts with `imports/<ns>/` and does not escape the bundle. Any violation
refuses the whole import (exit 1) with the document and the offending
destination. The rewriter is the mechanism; the proof is the guarantee,
the same split the export design used (D3 of `okf-export`).

**`relations:`.** Decoded with `okf.decode_relations` (`okf.py:2353`). A
target is a bundle-absolute Concept ID; `okf.namespaced_concept_id(target,
prefix)` strips one leading `/` and one trailing `.md`, clamps dot segments
at the root, and prefixes `imports/<ns>/`. Entries whose type is engine-owned
(`relations.ENGINE_OWNED_RELATION_TYPES`, today `derived_from`,
`model/relations.py:46`) are not rewritten: they are provenance claims, and
lint's `unbacked-provenance` (`lint.py:873`) would flag every one of them
against the anchor-only local provenance. They move under the inert key
(D4). A `relations:` value that does not decode moves under the inert key
whole. `okf.adopted_violations` proves every remaining target starts with
`imports/<ns>/`.

**Alternatives considered.** Extend `withhold_links`' per-line regexes
(rejected: misses the multi-line label the graph follows, and lint's
spaced and extension-less forms). Mask fenced code as merge and export do
(rejected: lint does not mask, so the proof would need a carve-out for
exactly the recognizer it must cover). Neutralize escaping links to `#`
(rejected: throws away the target; clamping is how a browser serving the
bundle at its root resolves the same link). Refuse escaping links
(rejected: §6.1 says consumers must tolerate broken links).

### D4. Frontmatter transform and the inert key

**Choice.** `okf.adopt_foreign_document(doc, *, body, sensitivity,
anchor_id, prefix) -> str` builds the adopted text from the parsed foreign
mapping and the already-rewritten body. Each foreign key is classified by a
total table; a guard test asserts that every engine key constant in
`okf.py` (`*_KEY: Final`, plus the literal keys `status`, `generated`,
`verified`, `version`, `timestamp`, `resource`, `sensitivity`, `provenance`)
appears in exactly one row, so a key added to the engine later cannot be
trusted from foreign input by default.

| Foreign key | Treatment | Why |
|---|---|---|
| `type`, `title`, `description`, `tags`, `aliases`, `freshness`, `event_date`, `type_alternative`, and every key the engine does not read | kept verbatim | §4.1; descriptive, and no consumer treats them as trust |
| `sensitivity` | replaced by the effective label (D5); original kept inert | fail-closed label |
| `provenance`, `sources`, `status`, `generated`, `verified`, `version`, `timestamp` | moved inert | ADR-0030's engine-owned set (plus `timestamp`, v0.1's `generated`, read at `okf.py:2470` and by `repair` R1) |
| `ingest_pending`, `extraction_status`, `extraction_notice`, `status_derived_from`, `source_frontmatter` | moved inert | engine-internal markers; each one drives a local consumer (retry hints, export markers, re-ingest convergence) |
| `resource` without a URL scheme (for example `raw/notes.txt`) | moved inert | see the audit below: it would name a local raw file |
| `resource` with a URL scheme (`https:`, `urn:`, …) | kept | every reader requires a `raw/` prefix or string equality with a raw path |
| `relations` entries of an engine-owned type, or an undecodable value | moved inert | D3 |
| other `relations` entries | targets rewritten into the namespace | D3 |
| `origin_key`, `merged_from` (`okf.EXPORT_STRIPPED_KEYS`, `okf.py:2612`) | dropped, counted in the preview | machine-local by their own definition; `merged_from` may hold bodies written at a higher label |
| `imported` (this design's key, found on a document that was itself imported elsewhere) | moved inert | nesting composes: the earlier origin stays readable |

The inert key is `imported` (`okf.IMPORTED_KEY`), one mapping:

```yaml
imported:
  namespace: demo
  id: concepts/stoicism              # foreign Concept ID (NFC)
  sha256: 3f1c…                      # sha256 of the foreign file's bytes, as read
  path: My Folder/Mi Nota.md         # the foreign path as written; present only when the name was renamed
  frontmatter:                       # the moved keys, verbatim; omitted when empty
    generated: {by: reference_agent/1.2, at: "2026-09-01T10:00:00Z"}
    provenance: [sources/x]
    sensitivity: public
    sources: [{resource: "https://example.org/a", title: A}]
    status: deprecated
    verified: [...]
```

The adopted document's own keys are then set by the builder:
`sensitivity: <effective label>`, `provenance: [<anchor id for that
label>]`, and `sources: project_sources(provenance)` (`okf.py:1154`), which
yields `[{id: imports/<ns>--<label>, resource: /imports/<ns>--<label>.md}]`
(§5.1). No `generated` is stamped: the engine did not write the content, and
`_union_frontmatter` (`okf.py:2915`) and `_next_version` (`okf.py:2904`)
already treat a missing `generated`/`version` as absent.

**`imported` does not join `EXPORT_STRIPPED_KEYS`.** It holds the foreign
bundle's own values, the namespace the user typed, the foreign id and a
content digest: nothing machine-local, no absolute path, no directory name.
An export of an imported concept carries it, which is what makes a second
import of that export lossless. No `okf-export` delta is needed.

**No local consumer reads it.** A guard test, in the shape of
`tests/unit/test_sources_key_guard.py`, asserts that no module under
`src/openkos/` reads `IMPORTED_KEY` outside `okf.adopt_foreign_document`,
`okf.build_import_anchor` and `okf.adopted_violations`. The same change adds
`adopt_foreign_document` and `build_import_anchor` to that file's
`_ALLOWED_PROVENANCE_WRITERS` (guard 2), as a deliberate, reviewed edit.

**Audit: a Source whose `resource` is not a `raw/` file.** Foreign
documents of `type: Source` are adopted as ordinary concepts, and the
anchors are Sources with no `resource` at all. Every consumer of `resource`:

| Consumer | No `resource` (anchor) | URL `resource` (kept) | Path `resource` if it were kept |
|---|---|---|---|
| `purge` raw expunge (`lifecycle.py:2636-2662`) | bundle file only, no warning | warns "absent/malformed", skips raw | `raw/notes.txt` passes the `raw/` check and the local raw file is expunged from all history |
| `forget` orphaned-raw disclosure (`lifecycle.py:2185-2209`) | skipped | skipped | names the local raw copy as this concept's |
| `lint` `unreferenced-raw` (`lint.py:1832-1845`) | contributes nothing | never equals `raw/<name>` | marks the local raw file referenced, hiding a real orphan |
| `lint` retry hints `unextracted`/`unjudged` (`lint.py:1088-1106`) | keyed on `extraction_status`, which is inert | same | same |
| `next_action` retry tiers (`next_action.py:758-820`) | require `extraction_status`, inert | same | same |
| `source_titles` backfill (`source_titles.py:316`) | skipped (`not resource.startswith("raw/")`) | skipped | read against the local raw file |
| export below-source walk (`sensitivity.py:429`) | the anchor is a document and is ranked | — | — |

The right-hand column is why a path-shaped foreign `resource` moves inert:
importing a workspace's own export back into it (a success criterion) would
otherwise give `imports/demo/sources/notes.md` a `resource: raw/notes.txt`
that names the local Source's raw file, and `openkos purge` of the imported
copy would erase the local original from history. A guard test runs the
purge plan, the forget plan, `lint` and `next` over a workspace holding an
imported URL-resource Source and an anchor, and asserts no raw path is
resolved and no retry hint is printed.

**Alternatives considered.** Drop the foreign trust keys (rejected:
lossless preservation is cheap and legal under §4.1, and a later human can
read what the producer claimed). Allow-list the keys that stay active
(rejected: §4.1 asks consumers to keep unknown keys; the danger is only
keys the engine reads, so the table classifies those and pins totality).
Keep every `resource` (rejected by the audit). Move every `resource` inert
(rejected: URL resources are descriptive, as on this repository's own ADRs,
and the audit shows no reader can mistake them for a local file).

### D5. Label fold

**Choice.** For each adopted document:

```
floor      = combine_sensitivity(cfg.default_sensitivity, flag or cfg.default_sensitivity)
foreign    = okf.fold_foreign_sensitivity(mapping)   # None when absent or explicit null
base       = floor if foreign is None else combine_sensitivity(floor, foreign)
effective  = config.type_birth_sensitivity(cfg, doc_type, base)
```

`fold_foreign_sensitivity` reads presence through `lift_incoming_frontmatter`
(`okf.py:1089-1117`), so "absent or explicit `null`" means no contribution,
exactly as ADR-0030 does for an ingested source; a present value is ranked by
`combine_sensitivity`/`_rank` (`okf.py:1589-1610`), so an unknown string or a
non-string folds to `confidential`, and a blank string ranks as `private`.
`--sensitivity` is a Click choice of the three levels (bad value: exit 2) and
can only raise, because it is combined with the default rather than replacing
it; `--sensitivity public` in a `private` workspace changes nothing, and the
preview says so. `config.type_birth_sensitivity` (`config.py:2091-2117`) is
the owner-confirmed birth seam: its offset is applied to the configured
floor and only raises, it matches the foreign `type` by exact name, and
`Source` is never offset because it is not buildable (`config.py:1780`). The
anchors are never type-defaulted. Import therefore never produces a label
below `default_sensitivity`, and `okf.adopted_violations` proves it.

Consequence, stated in the preview: in a stock `private` workspace a foreign
`public` document lands `private`; lowering it is a human `set-sensitivity`,
and exporting it at `public` then needs `--allow-below-source` (D6).

### D6. One anchor Source per effective label

**Choice.** The import writes one anchor per effective label present in the
import, at `bundle/imports/<ns>--<label>.md` (Concept ID
`imports/<ns>--<label>`), labelled `<label>`. Every adopted document cites
exactly the anchor of its own effective label. Anchors are built by
`okf.build_import_anchor`:

```yaml
type: Source
title: "Import demo (private)"
description: "Engine-written anchor for the documents imported into imports/demo at sensitivity private."
generated: {by: openkos/0.5.x, at: "2026-10-06T09:00:00Z"}
status: stable
version: 1
freshness: snapshot
sensitivity: private
tags: [import]
imported:
  role: anchor
  namespace: demo
  label: private
  bundle_sha256: 9a0e…        # sha256 over the sorted "<foreign id>\t<sha256>\n" lines of every adopted document
  okf_version: "0.2"          # as observed; null when absent
  generated_by: [reference_agent/1.2]   # distinct foreign generated.by values at this label, sorted
  documents: 12
```

The body lists, for this label only, one bullet per document:
`- [<sanitized title>](/imports/demo/<id>.md) — foreign id \`<id>\`, sha256 \`<hex>\``.
Titles pass `index.sanitize_link_label` and newlines are refused. The link names the renamed path
(`okf.renamed_foreign_id`); the text after `foreign id` keeps the foreign id as
written, so the digest and the origin stay verifiable.

**Why per label.** The export boundary withholds an object whose own rank
is below the highest rank among its provenance ancestors (ADR-0048, folded
into `sensitivity.export_boundary`, `sensitivity.py:469`). Each document's
only ancestor is its anchor, at the same rank, so no import is ever
below-source by construction. Two alternatives were weighed:

- **One anchor at the import's maximum label** (rejected): every
  lower-labelled document becomes below-source, the ADR-0048 trap the
  proposal names.
- **One anchor at the import's minimum label** (rejected): no document is
  below-source, but an anchor that records per-document facts (the spec
  requires each foreign id and digest) or lists the documents for catalog
  reachability would carry titles and ids of higher-labelled documents in a
  lower-labelled engine-written document. MCP discloses a document by its
  own label (ADR-0028), so the engine itself would break the high-water rule.

Per label, an anchor only ever names documents at its own label, so it
obeys the high-water rule at birth, and it doubles as the namespace's
catalog: every adopted document is linked from its anchor, so lint's
`orphan` check (`lint.py:710-745`) holds without listing thousands of
foreign concepts in the user's `index.md`.

**`resource`.** Anchors carry none. `raw/` is flat and basename-keyed
(`okf.py:217`), there is no raw artifact for a directory, and a
`raw/`-shaped value would enter every raw consumer in the audit (D4). The
origin is recorded by content (`bundle_sha256`, the per-document digests)
and by the namespace the user chose; the local directory path and name are
never written (spec: the anchor contains no absolute local path). The
`sources` entries that point at the anchor carry `resource:
/imports/<ns>--<label>.md`, satisfying §5.1's required `resource`.

**What a human relabel does.** Lowering an imported document below its
anchor makes it below-source at export, and it is withheld unless
`--allow-below-source`, the friction ADR-0048 deliberately puts on a
downgrade. Raising one above its anchor leaves its title in a
lower-labelled anchor body, the same exposure `index.md` already has for any
relabelled concept; export's `withhold_links` replaces such a link with
`[withheld]`. Both are disclosed in ADR-0050.

**Removal path.** `openkos forget imports/<ns>--<label> --scope source`
removes the anchor and every document whose only provenance is that anchor,
one label at a time, through the existing provenance-descendant walk.

### D7. Service and commit: rename last, no pending marker

**Choice.** `application/import_service.py`:

```python
class ImportRefusal(Exception):
    code: str            # e.g. "namespace-exists", "symlink", "link-outside-namespace"
    reason: str          # the human sentence the CLI prints
    retry_safe: bool     # True -> exit 3 (drift), False -> exit 1

@dataclass(frozen=True)
class AdoptedPlan:  foreign_id: str; concept_id: str; doc_type: str; label: str
                    type_raised: bool; foreign_label: object; text: str
@dataclass(frozen=True)
class AnchorPlan:   concept_id: str; label: str; text: str; title: str; description: str
@dataclass(frozen=True)
class ImportPlan:
    namespace: str
    source: Path                       # held in memory for the Phase B re-read, never written
    manifest: tuple[tuple[str, str], ...]   # (foreign rel path, sha256) for every .md read, plus skipped paths
    okf_version: object | None
    adopted: tuple[AdoptedPlan, ...]
    anchors: tuple[AnchorPlan, ...]
    skipped: tuple[tuple[str, str], ...]    # (foreign rel path, reason code)
    dropped_keys: int
    links_rewritten: int
    links_clamped: int
    html_link_documents: int
    label_fingerprint: tuple[str, tuple[tuple[str, int], ...]]  # default_sensitivity, type offsets
    floor: str

def plan_import(root: Path, layout: config.WorkspaceLayout, cfg: config.Config,
                source: Path, *, namespace: str, sensitivity_flag: str | None,
                now: datetime) -> ImportPlan: ...
def publish_import(root: Path, layout: config.WorkspaceLayout, plan: ImportPlan, *,
                   commit_section: CommitSection,
                   load_config: Callable[[Path], config.Config],
                   autocommit: Callable[[Path, Sequence[str], str], str | None],
                   after_commit: Callable[[], None] = no_after_commit,
                   ) -> ImportOutcome: ...
```

Phase A takes no lock and writes nothing. Phase B runs entirely inside the
commit section (ADR-0036), with no model call and no prompt, in this order:

1. Re-read the foreign bundle with the same reader; a different manifest
   (a changed, added or removed `.md`, or a changed skip list) refuses with
   exit 3, "the foreign bundle changed since the preview". The bytes are
   re-read rather than trusted, so the documents written are the documents
   the proof judged, as in `okf-export` D6.
2. Reload the config; a different `label_fingerprint` refuses with exit 3,
   because the labels in the preview would no longer be the labels written.
3. Namespace taken (D2) refuses with exit 1, the same reason as Phase A.
4. Remove stale staging directories `bundle/imports/.<ns>.openkos-import-*`
   (real directories only, never through a symlink, only directly under
   `bundle/imports`).
5. Snapshot the prior bytes (or absence) of `index.md`, `log.md` and each
   anchor path.
6. `tempfile.mkdtemp(prefix=".<ns>.openkos-import-", dir=bundle/imports)`;
   write every adopted document with `fsio.write_exclusive` under its NFC
   path.
7. `okf.check_conformance(staging)`; a violation is a defect and refuses
   with exit 1.
8. Write the anchors (`fsio.write_atomic`).
9. Re-read `index.md` and `log.md` under the lock and apply the insertions
   to their current bytes: one `# Sources` bullet per anchor through
   `index.insert_index_entry(section="Sources", link_dir="imports",
   slug="<ns>--<label>", …)` (`index.py:125`), skipped when
   `indexed_concept_ids` already lists that anchor, and one `**Import**`
   log entry through `log.insert_log_entry` (`log.py:61`) naming the
   namespace, each anchor link, the adopted count and the skipped count.
10. `os.replace(staging, bundle/imports/<ns>)`. This is the completion
    point: the namespace directory appears whole or not at all.
11. Any exception in steps 6 to 10 removes the staging directory, restores
    every snapshot from step 5, and refuses with exit 1, leaving the tree
    byte-identical to before.
12. One `autocommit(root, paths, "openkos: import <ns> (+N concepts)")` with
    the paths `bundle/imports/<ns>` (one directory pathspec, so a large
    import does not overflow the argument list), each anchor,
    `bundle/index.md` and `bundle/log.md`.

**Why rename last and no pending marker.** Because the namespace directory
is published by one rename after everything else is on disk, its existence
means the import completed, and the existing-namespace refusal can never be
caused by a half-written namespace. A process killed before step 10 leaves
only a dot-prefixed staging directory, which every bundle walk ignores by
construction (`excluded_from_bundle_walk`, `okf.py:3638`, ADR-0019) and lint
reports as `dot-dir-markdown`, plus possibly anchors and index/log lines. A
retry is not refused: the namespace is absent, step 4 removes the stale
staging, step 3 recognizes an anchor whose `imported.role` is `anchor` and
whose `imported.namespace` is this namespace as torn and lets step 8
overwrite it, and step 9 skips the already-present index bullet. The torn
run's `log.md` entry stays, followed by the retry's: the log is history. A
process killed after step 10 but before the commit leaves a complete,
uncommitted import; the retry is refused as an existing namespace, and the
refusal says "if an earlier import stopped before its commit, `git status`
shows it". An `ingest_pending`-style marker was rejected: it would add a
third state to every anchor reader and still not make the directory atomic.

**Residual, disclosed.** POSIX `rename` replaces an empty directory. A
non-OpenKOS process that creates an empty `bundle/imports/<ns>/` between
step 3 and step 10, inside the lock window, would be replaced silently. The
window is the duration of steps 4 to 9 under the workspace lock.

**Derived caches.** The import itself writes only canonical files. After the
commit, still inside the commit section, the CLI runs the LEXICAL half of the
shipped derived refresh through `publish_import`'s `after_commit` port
(`_refresh_derived_after_write_quietly`, the wiring `merge` uses): FTS and the
graph are pure SQLite projections of the bundle, so an imported document is
found by lexical search with no manual `reindex`. The VECTOR half is never run
by import: it calls the embedder, which the model-free guarantee forbids, so
embeddings catch up through `reindex`. A lexical failure is one stderr
advisory and never fails the import (the write is already committed). The
summary states what happened: "lexical index refreshed; embeddings catch up on
the next `openkos reindex`".

### D8. Entity-resolution exclusion by Concept ID

**Choice.** One predicate, `bundle.imports.is_imported_concept(concept_id)`:
true when the Concept ID's first segment is `imports`. It is a property of
the concept's own identity, needs no file read, and cannot be faked by
frontmatter content. It is consumed at exactly two sites:

1. **Attach-at-ingest targets.** `application/ingest.py::build_attach_lookup`
   (`ingest.py:153-180`) skips a keyed document when
   `doc_type in ATTACH_EXCLUDED_TYPES or not key or
   is_imported_concept(concept_id)`. Only the target side changes: an ingest
   candidate is never imported.
2. **The automatic merge class and the accept-recommended offer.**
   `application/auto_merge.py::in_structural_class` (`auto_merge.py:77-88`)
   gains one condition: `not any(is_imported_concept(m) for m in
   group.member_ids)`. It stays the ONE structural predicate, with no second
   copy and no split. Its three consumers therefore refuse an imported
   member identically:
   - `plan_auto_merges` (`auto_merge.py:274`, the automatic pass);
   - `recommended` (`auto_merge.py:348`, curate Identity's
     accept-recommended offer), which already gates on
     `in_structural_class` at `auto_merge.py:371`;
   - the `evals/auto_merge` harness self-test.

   Orchestrator decision: a group with an imported member is outside the
   #1298 measured population, and the pre-registration allows the
   accept-recommended offer only in class. Such a group is therefore
   neither auto-merged nor in the accept-recommended batch. It keeps its
   per-group prompt in curate Identity, like any group outside the class,
   and stays visible to `duplicates`, `adjudicate` and `merge`.

`ATTACH_EXCLUDED_TYPES` (`ingest.py:121`) is not widened: it is a type set
applied to candidates and targets alike, while the import exclusion is a
property of a member. A local-versus-imported pair can never be in the
structural shape anyway (`is_suffix_family` requires the same directory,
`normalize.py:34-39`); the exclusion bites on imported-versus-imported
`-N` pairs, whose `-N` a foreign producer chose, outside the population
ADR-0049 measured.

**How a human merge still works.** `duplicates`, `adjudicate`, `merge` and
curate Identity read every concept, imported ones included; nothing else
changes. Explicit `openkos merge <survivor> <absorbed>` keeps the human's
order (`main.py:8919-8927`). For ordered pairs (curate Identity, adjudicate
apply), `lifecycle.ordered_merge_pair` (`lifecycle.py:2903-2932`) gains one
rule after the suffix-family rule and before "richer body": when exactly one
member is imported, the local member survives (criterion `local over
imported`). The merged result then lives at a local Concept ID, is no longer
imported, and becomes an ordinary attach and auto-merge candidate: that is
what "until a human merges them" means in practice. The absorbed body is
stacked and reconciled as in any merge; nothing is lost, and the ledger keeps
the absorbed snapshot. The survivor's provenance gains the anchor through
the existing union, and its label is recomputed as the high-water mark, so
it is never below the anchor. `IMPORTED_KEY` joins `_union_frontmatter`'s
`_SPECIAL_KEYS` (`okf.py:2945-2956`), so an absorbed imported document's
`imported` block is not gap-filled into a local survivor, where it would
misstate the survivor's origin; it survives in the ledger snapshot.

**Alternatives considered.** A frontmatter flag (rejected: the merge union
gap-fills unknown keys into the survivor, so a local survivor would inherit
"imported"). Provenance naming an anchor (rejected: the union gives a local
survivor the anchor too). Folding the exclusion into `keyed_documents`
(rejected: `duplicates` and adjudication must keep seeing imports).

### D9. CLI surface

```
openkos import <dir> --namespace <slug> [--sensitivity public|private|confidential] [--auto] [--wait SECONDS]
```

- Registered as `@app.command("import")` on a function named
  `import_bundle` (`import` is a keyword), decorated
  `@_guard_workspace_lock("import", commit_phase=True)` (`main.py:443-539`),
  which adds `--wait` and publishes the commit section. It is not in
  `_READ_ONLY_COMMANDS` (`main.py:354`), so the fail-safe classification
  already treats it as locked; the `workspace-lock` delta names it, and
  `test_every_command_is_classified` covers it without edits.
- Preview (plain `typer.echo` lines, the export verb's style): namespace;
  `okf_version` observed and whether it is known; adopted count; label
  distribution, and how many foreign labels were raised by the floor; per-type
  raises (the born-above-floor disclosure shape ingest uses); skipped files
  by path and reason code; machine-local keys dropped; links rewritten and
  links clamped; documents with HTML links left as written; "the lexical
  index is refreshed after the commit; embeddings are not (no model call): run
  `openkos reindex` to embed"; and the undo sentence.
- Confirm: `if not auto and cfg.review:` then `typer.confirm(..., abort=True)`
  on a TTY, or on a non-TTY print a refusal that names `--auto` and exit 1
  (the `forget` shape, `main.py:5601-5606`). With `review: false` the run
  proceeds without a prompt, as every other mutating verb does.
- Summary: the adopted and skipped counts, then
  `_echo_commit_disclosure(sha, prefix="openkos import: ")`
  (`main.py:1360`), which joins import to the recovery-critical disclosure
  and says the revert is safe only while it is the latest commit.
- Exit codes (`docs/cli.md` conventions): 0 success; 1 refusal (input not a
  directory, input inside `bundle/` or containing the workspace, hostile
  tree, namespace taken, proof violation, conformance violation, write
  failure restored, declined prompt, non-TTY without `--auto`); 2 usage
  (missing or invalid `--namespace`, invalid `--sensitivity`); 3 retry-safe
  (workspace busy, foreign bundle or config changed between preview and
  commit).
- No daemon, pending-queue, inbox-watch or MCP path: `import_service` is
  imported only by `cli/main.py` (AST pin), the MCP tool list contains no
  import tool, and `state/pending_queue.py`'s kinds and CHECK constraint
  (`pending_queue.py:59,80`) are unchanged (pin on the kind tuple).

### D10. ADR-0050

`docs/adr/` ends at 0049, so the next free number is **0050**. Working title:
"OKF import adopts a foreign bundle under its own namespace, labelled
fail-closed, anchored per label, and outside automatic identity". It passes
both gate conditions (it decides a policy, and it is hard to reverse once
namespaces, anchors and the `imported` key exist in users' bundles and
exports). Its Decision records, in present tense:

1. **Fidelity.** Foreign concepts are adopted as written, with no model call
   and no re-compilation; non-concept files are skipped and reported.
2. **Collision.** Everything lands under `imports/<namespace>/`; the
   namespace is a required ASCII slug; an existing namespace is refused; there
   is no re-import, and "revert the import commit, then import again" is the
   undo.
3. **Links.** Every pointer site is rewritten into the namespace, escaping
   relative links are clamped per RFC 3986, code is rewritten too, and a
   post-transform proof refuses the whole import if any reading of any
   pointer resolves outside the namespace.
4. **Sensitivity.** The label is the high-water mark of the default, the
   raise-only flag, the folded foreign label (absent contributes nothing,
   unknown is `confidential`) and the per-type offset; never below the
   default.
5. **Trust.** Every foreign key the engine reads is moved under `imported`,
   verbatim; machine-local keys are dropped; engine-owned relation types are
   inert; nothing reads `imported` as a local fact.
6. **Provenance.** One engine-written anchor Source per effective label;
   each document cites its own label's anchor; an anchor names only
   documents at its label; no local path is recorded.
7. **Identity.** Imported concepts (Concept ID under `imports/`) are never
   attach targets, never in the automatic merge class, and never in the
   accept-recommended batch (outside the #1298 measured population); humans
   reconcile them per group; a local member survives an imported one in an
   ordered pair.
8. **Hostile input.** A bounded reader refuses tree and frontmatter hazards
   with named reasons; non-conformant documents are skipped.
9. **Human-only.** No daemon, queue, watch or MCP path (ADR-0037).

Consequences to record: prompt injection in foreign bodies reaches later
model calls with the same exposure as ingested text; large imports meet the
50-group candidate cap and resolve at human pace; a downgraded imported
document needs `--allow-below-source` to export; a stock workspace raises
foreign `public` to `private`. Alternatives: ingest as sources; keep ids and
refuse collisions; prefix on collision; trust foreign labels; one anchor at
the maximum or minimum label; no anchor; a frontmatter or provenance-based
imported predicate; a pending marker instead of rename-last.

## Data flow and sequence

```
CLI            import_service              okf / links / imports / config           filesystem
 |  plan() ---> read_foreign_bundle ------> walk, guards, guarded parse  <----------- <dir>/ (read only)
 |              namespace free? -----------> imports.namespace_reason / exists  <------ bundle/imports/
 |              per doc: fold -> type_birth_sensitivity -> rewrite links -> adopt
 |              anchors per label --------> build_import_anchor
 |              proof --------------------> namespace_link_violations / adopted_violations
 |  <---------- ImportPlan  (or ImportRefusal -> exit 1)
 |  preview, confirm (TTY) / refuse (non-TTY, no --auto)
 |  publish() --[commit section: workspace lock]---------------------------------------------
 |              re-read bundle, compare manifest ------------- changed? -> exit 3
 |              reload config, compare label fingerprint ------ changed? -> exit 3
 |              namespace still free? ------------------------- no      -> exit 1
 |              rm stale .<ns>.openkos-import-* ------------------------------------> bundle/imports/
 |              write docs into staging ----------------------------------------------> .<ns>.openkos-import-XXXX/
 |              check_conformance(staging) -------------------- violation -> restore, exit 1
 |              write anchors, index.md, log.md --------------------------------------> bundle/
 |              os.replace(staging, imports/<ns>) ------------------------------------> bundle/imports/<ns>/
 |              autocommit (one commit) ------------------------------------------------> git
 |  <---------- ImportOutcome; commit disclosure
```

## File changes

| File | Action | Description |
|---|---|---|
| `src/openkos/model/okf.py` | Modify | Foreign reader (constants, `ForeignRefusal`, `ForeignDocument`, `ForeignBundle`, `split_incoming_document`, path and collision validators, `read_foreign_bundle`); adopt section (`IMPORTED_KEY`, the key classification, `fold_foreign_sensitivity`, `namespaced_concept_id`, `adopt_foreign_document`, `build_import_anchor`, `adopted_violations`); `IMPORTED_KEY` added to `_union_frontmatter`'s `_SPECIAL_KEYS` |
| `src/openkos/bundle/links.py` | Modify | `resolve_link_target` (refactor of `_bundle_target_id`'s core, behaviour unchanged), pointer-site scanner, `rewrite_links_into_namespace`, `namespace_link_violations` |
| `src/openkos/bundle/imports.py` | Create | `IMPORTS_DIR`, namespace rule, prefix, anchor ids, staging prefix, `is_imported_concept` |
| `src/openkos/application/import_service.py` | Create | `plan_import`, `publish_import`, `ImportPlan`, `ImportRefusal`, `ImportOutcome` |
| `src/openkos/application/ingest.py` | Modify | `build_attach_lookup` skips imported targets |
| `src/openkos/application/auto_merge.py` | Modify | `in_structural_class` refuses a group with an imported member; the automatic pass, `recommended` and the eval harness all keep calling it |
| `src/openkos/application/lifecycle.py` | Modify | `ordered_merge_pair`: local over imported |
| `src/openkos/cli/main.py` | Modify | `import` verb, preview, confirm, exit codes, commit disclosure |
| `docs/adr/0050-…md`, `docs/adr/README.md` | Create / Modify | ADR-0050 (`Proposed`) and its index row |
| `tests/unit/model/test_okf_foreign_reader.py` | Create | Reader guards, one test per reason code |
| `tests/unit/model/test_okf_adopt.py` | Create | Key classification, fold, anchors, proof |
| `tests/unit/model/test_imported_key_guard.py` | Create | No reader of `imported` outside the seam; classification totality |
| `tests/unit/test_sources_key_guard.py` | Modify | Allow-list the two new provenance writers |
| `tests/unit/bundle/test_links_namespace.py` | Create | Rewrite corpus, proof, recognizer cross-check |
| `tests/unit/bundle/test_link_recognizer_inventory.py` | Create | Every `](`/`]:` regex in `src/` is in the cross-checked list |
| `tests/unit/bundle/test_imports_layout.py` | Create | Namespace rule, anchor ids, predicate |
| `tests/unit/application/test_import_service.py` | Create | Plan, refusals, drift, torn runs, byte-identical refusals |
| `tests/unit/application/test_import_entity_resolution.py` | Create | Attach exclusion, auto-merge exclusion, accept-recommended, survivor rule |
| `tests/unit/cli/test_import_cmd.py` | Create | Preview, confirm, non-TTY, exit codes, lock classification, disclosure |
| `tests/unit/e2e/test_import_round_trip.py` | Create | Round trips through export |
| `tests/unit/fixtures/okf_third_party_v02/` | Create | Hand-built third-party-shaped bundle |
| `docs/cli.md`, `docs/okf-alignment.md`, `docs/roadmap.md`, `docs/knowledge-object-model.md` | Modify | Shape-level docs, last slice |

## Interfaces / contracts

```python
# model/okf.py
FOREIGN_MAX_FILE_BYTES: Final = 8 * 1024 * 1024
FOREIGN_MAX_TOTAL_BYTES: Final = 256 * 1024 * 1024
FOREIGN_MAX_DOCUMENTS: Final = 10_000
FOREIGN_MAX_ENTRIES: Final = 50_000
FOREIGN_MAX_DEPTH: Final = 32
IMPORTED_KEY: Final = "imported"

class ForeignRefusal(ValueError):
    code: str           # "symlink", "special-file", "traversal", "unsafe-name", "name-too-long",
                        # "too-deep", "too-many-entries", "too-many-files", "file-too-large",
                        # "bundle-too-large", "nfc-collision", "case-collision", "frontmatter-alias",
                        # "frontmatter-too-deep", "frontmatter-too-large",
                        # "frontmatter-not-a-mapping", "frontmatter-unsupported-value", "unreadable"
    path: str           # foreign-relative POSIX path, never absolute

@dataclass(frozen=True)
class ForeignDocument:
    rel_path: str; foreign_id: str; sha256: str; mapping: Mapping[str, object]; body: str

@dataclass(frozen=True)
class ForeignBundle:
    documents: tuple[ForeignDocument, ...]
    skipped: tuple[tuple[str, str], ...]
    okf_version: object | None
    manifest: tuple[tuple[str, str], ...]

def read_foreign_bundle(root: Path) -> ForeignBundle: ...
def fold_foreign_sensitivity(mapping: Mapping[str, object]) -> str | None: ...
def namespaced_concept_id(target: str, prefix: str) -> str | None: ...
def adopt_foreign_document(doc: ForeignDocument, *, body: str, sensitivity: str,
                           anchor_id: str, prefix: str) -> str: ...
def build_import_anchor(*, namespace: str, label: str, entries: Sequence[AnchorEntry],
                        bundle_sha256: str, okf_version: object | None,
                        generated: Generated) -> str: ...
def adopted_violations(text: str, *, prefix: str, anchor_id: str, floor: str) -> list[str]: ...

# bundle/links.py
@dataclass(frozen=True)
class LinkTarget:
    kind: Literal["empty", "anchor", "external", "path"]
    path: str | None      # normalized, suffix kept, RFC-clamped
    escaped: bool
def resolve_link_target(target: str, *, file_id: str) -> LinkTarget: ...
def rewrite_links_into_namespace(body: str, *, foreign_id: str, prefix: str) -> NamespacedBody: ...
def namespace_link_violations(body: str, *, concept_id: str, prefix: str) -> list[str]: ...
```

## Testing strategy

Strict TDD (`openspec/config.yaml`), runner `uv run pytest`. Every RED test
below is written first and must be seen failing. No test reaches a model.

| Layer | What | Approach |
|---|---|---|
| Unit | Reader guards | One test per reason code in D1's table, each asserting the exact `code` (not "refused"), the offending relative path, and that no absolute path appears in the message. Boundaries at cap and cap + 1 for file size, total size, document count, entries and depth. Symlinks to a file, to a directory, and to a target inside the tree are all refused. A FIFO is refused without hanging (test timeout). |
| Unit | Collisions | The pure collision validator over string lists (NFC/NFD, case, directory-prefix collisions). The filesystem variant is gated on a runtime probe that both spellings can coexist; on APFS it skips, so it is first verified on Linux CI. |
| Unit | Guarded parsing only | Patch `okf._parse_post` and `frontmatter.loads` to raise, first proving the patch is live (patched `_iter_docs` fails), then run `plan_import` over a full fixture: it succeeds. |
| Unit | Key classification | Table-driven: every row of D4's table, with the moved values compared verbatim under `imported.frontmatter`. A totality test fails when an engine key constant is unclassified. |
| Unit | Label fold | Each D5 row, run in a `public` workspace so a fail-closed `confidential` is distinguishable from the floor. `--sensitivity public` in a `private` workspace leaves labels unchanged. The type offset applies to `Person` and not to `Source` or an unconfigured type. |
| Unit | Link rewrite corpus | A product of forms (inline, image, angle-bracket, titled, reference definition, multi-line label, spaced target, extension-less, `%2F`-encoded, `//`, dot segments, fragment, query, fenced, inline code) × positions (root document, nested document) × targets (inside, escaping, external, anchor). Each case asserts the exact output bytes. |
| Unit | Recognizer cross-check | For every output in the corpus, run each real engine recognizer (links `_LINK_RE`, `_INLINE_LINK_RE` + `_bundle_target_id`, `_REFERENCE_DEFINITION_RE`, graph `_LINK_RE`, lint `_LINK_RE` + `normalize_link`) and assert every target it extracts is outside the bundle, external, or under `imports/<ns>/`. |
| Unit | Recognizer inventory | Scan `src/openkos/**/*.py` for regex literals containing `\](` or `\]:`; the set must equal the cross-checked list, so a new recognizer fails until it is added. |
| Unit | Proof refuses | Feed `namespace_link_violations` and `adopted_violations` hand-made bad outputs (a link left at `/concepts/x.md`, a relation outside, a provenance naming a foreign id, a label below the floor) and assert one violation each; then break the rewriter with a monkeypatch that returns the body unchanged and assert `plan_import` refuses with `link-outside-namespace`. |
| Unit | Entity resolution | An imported `Concept` titled like a candidate is never an attach target while a local twin is; an imported base/`-N` pair is out of `in_structural_class` (each member, then both); `ordered_merge_pair` returns the local member for both argument orders; `is_imported_concept` is proven used by both seams (attach lookup, structural class) by patching it to `True` for a local id and observing both exclusions. |
| Unit | Accept-recommended refuses imported members | Given fresh SAME verdicts for an in-shape base/`-N` pair with one imported member, then both, plus an otherwise identical local pair, `recommended(...)` returns only the local pair and `plan_auto_merges(...)` plans only the local pair. Patching `in_structural_class` to return `True` makes the imported pair appear in BOTH results, which proves the offer and the automatic pass go through the one predicate. In curate Identity the imported group still gets its per-group prompt. |
| Unit | Resource audit | Purge plan, forget plan, `lint` and `next` over a workspace with an anchor and an imported URL-resource Source resolve no raw path and print no retry hint; an imported foreign `resource: raw/notes.txt` lands under `imported.frontmatter`. |
| Service | Phase split | `plan_import` writes nothing (tree hash equal) and takes no lock; refusals leave `bundle/`, `raw/` and `openkos.yaml` byte-identical. Drift: a foreign file edited, added or removed between plan and publish refuses with exit-3 semantics; a config label change refuses with exit-3 semantics; a namespace created between plan and publish refuses. |
| Service | Torn runs | Inject a failure at each of steps 6 to 10: the tree is restored byte for byte. Simulate a kill before step 10 (leave staging, anchors and an index bullet): a retry succeeds, removes the staging, leaves one index bullet per anchor. A kill after step 10: a retry is refused as an existing namespace. |
| Service | Model-free | AST guard: `import_service` imports nothing from `openkos.llm`, `openkos.application.backends` or `openkos.extraction`; plus a run with `backends` resolvers patched to raise. |
| CLI | Surface | Preview contents, TTY confirm and decline (exit 1, nothing written), non-TTY refusal naming `--auto`, `review: false`, exit 2 for a bad namespace and a bad `--sensitivity`, exit 3 when the lock is held, the commit disclosure line. Any help or rich-output assertion uses the `plain_rich_output` fixture (`tests/unit/cli/conftest.py:566`), because CI forces colour. |
| CLI | Pins | `import` is locked with a commit phase; `import_service` is imported only by `cli/main.py`; the MCP tool list has no import tool; the pending-queue kinds are unchanged. |
| E2E | Fresh round trip | Copy `examples/good-life-demo`, `export --include-private --auto`, `init` a fresh workspace, `import <export> --namespace demo --auto` under a poisoned `OLLAMA_HOST`: every exported concept exists under `bundle/imports/demo/`; `check_conformance` is empty; `lint` reports no `orphan`, `dangling`, `dangling-provenance`, `unbacked-provenance`, `below-source-sensitivity`, `dot-dir-markdown` or `non-nfc-name` finding on an imported path; `status` succeeds; one commit exists and the tree is clean. Then `export --include-private` again: no imported document is withheld as below-source. |
| E2E | Mixed labels | A `default_sensitivity: public` workspace imports the same export: public documents cite the public anchor, private ones the private anchor; `export` without flags exports the public imported documents and withholds the private ones by their own label, never as below-source. This is the ADR-0048 regression pin: under a single maximum-label anchor it fails. |
| E2E | Origin round trip | Import the export back into its origin workspace: no pre-existing concept file changes (byte hashes), only `index.md` and `log.md` change outside the namespace, a second import into `demo` is refused, and `git revert <sha>` restores the pre-import tree byte for byte. |
| E2E | Third-party shape | `tests/unit/fixtures/okf_third_party_v02/` is hand-written from the SPEC's examples (no network, no copied sample data, so no attribution file): a root `index.md` with `okf_version: "0.2"`, `generated: {by: reference_agent/…}`, a `verified:` attestation list, URL `sources:`, one `status: deprecated`, an unknown `type` with a space, nested directories, absolute, relative, escaping and reference-style links, a broken link, a fenced link, `viz.html`, `references/data.csv`, a `.git/` directory and a v0.1-style `timestamp` document. It imports, every skip is reported, the deprecated document is not effective-deprecated, and `repair`'s plan migrates none of the imported documents. The existing v0.1 fixture `tests/unit/fixtures/good_life_demo_v01/bundle` also imports best-effort, with "version absent" in the preview. |

Mutation discipline: each guard is mutated once (delete the check, flip the
comparison, change the cap by one) and the named killing test must fail;
`__pycache__` is purged between runs so a same-size mutation does not run
stale bytecode.

## Threat matrix

| Boundary | Applicability | Design response | Planned RED tests |
|---|---|---|---|
| Documentation-like paths | Applicable: the foreign tree may hold `README.sh`, `viz.html`, executable Markdown or MDX | Classification is by `.md` suffix only; nothing is executed, rendered or copied; every non-`.md` file is skipped and reported; `.md` content is data | A tree with `README.sh`, `viz.html`, `page.mdx` and an executable-bit `.md`: none is executed or copied, the first three are reported `not-markdown`, the `.md` is adopted as text |
| Git repository selection | Applicable: the foreign directory may itself be a git repository or contain a `.git` file | The commit always targets the workspace repository through the existing `_autocommit(root, …)` (`main.py:1447`); a foreign `.git` (directory or file) is a dot-entry, skipped and never consulted | A foreign tree with a `.git/` directory and another with a `.git` file: both skipped and reported, the commit lands in the workspace repository, the foreign repository is unchanged |
| Commit state | Applicable: import adds a commit | Pathspec-scoped commit (`git add -- <paths>`, `git commit -- <paths>`, never `-A`/`-a`); the namespace is one directory pathspec | A pre-staged unrelated file stays staged and uncommitted; every adopted file is in the commit; a degraded commit (no repository, no identity) still succeeds with the usual warning and no disclosure line |
| Push state | N/A: import never pushes | — | — |
| PR commands | N/A: import composes no PR command | — | — |

## Slice plan (auto-chain, stacked to main, about 400 authored lines each)

| # | Slice | Files owned | Est. | Done when |
|---|---|---|---|---|
| 0 | Planning PR (this change's artifacts) | `openspec/changes/okf-import/**` | artifacts | merged |
| 1 | Foreign reader + ADR | `model/okf.py` (reader section), `tests/unit/model/test_okf_foreign_reader.py`, `docs/adr/0050-…md`, `docs/adr/README.md` | ~420 | one RED-then-GREEN test per reason code; ADR indexed as `Proposed` |
| 2 | Link rewrite and proof | `bundle/links.py`, `tests/unit/bundle/test_links_namespace.py`, `tests/unit/bundle/test_link_recognizer_inventory.py` | ~380 | corpus and recognizer cross-check green; export tests unchanged |
| 3 | Frontmatter transform and anchors | `model/okf.py` (adopt section, `_SPECIAL_KEYS`), `tests/unit/model/test_okf_adopt.py`, `tests/unit/model/test_imported_key_guard.py`, `tests/unit/test_sources_key_guard.py` | ~400 | classification totality, fold, anchors and proof green |
| 4 | Layout and service | `bundle/imports.py`, `application/import_service.py`, `tests/unit/bundle/test_imports_layout.py`, `tests/unit/application/test_import_service.py`, `tests/unit/fixtures/okf_third_party_v02/` | ~420 | refusals byte-identical, drift and torn-run tests green |
| 5 | Entity-resolution exclusion | `application/ingest.py`, `application/auto_merge.py`, `application/lifecycle.py`, `tests/unit/application/test_import_entity_resolution.py` | ~250 | attach and auto-merge exclusions and the survivor rule green |
| 6 | CLI verb and e2e | `cli/main.py`, `tests/unit/cli/test_import_cmd.py`, `tests/unit/e2e/test_import_round_trip.py` | ~400 | e2e green under a poisoned `OLLAMA_HOST` |
| 7 | Docs | `docs/cli.md`, `docs/okf-alignment.md`, `docs/roadmap.md`, `docs/knowledge-object-model.md` | ~80 | shape described, no counts, no "since" markers |

The adjustment from the proposal: the transform splits into links (slice 2)
and frontmatter (slice 3), because the link proof alone is a full review
unit, and the service gains `bundle/imports.py`. Slices 1 to 5 add no
reachable surface; the verb appears in slice 6, after the exclusion (slice
5) is on `main`. Slice 1 and slice 4 sit near the budget; the line count is
advisory, and neither is split artificially.

## Migration / rollout

No migration. Import adds no derived-store schema, no pending-queue kind and
no config key. Existing workspaces are unaffected until a human runs the
verb. The only behaviour changes to existing verbs are inert for a
workspace with no `imports/` directory: the attach filter, the auto-merge
class and the survivor rule all test `is_imported_concept`, which is false
for every existing Concept ID, and the `_SPECIAL_KEYS` addition only affects
documents that carry `imported`.

## Open questions

- [x] **Resolved: index catalog.** The spec now says the index lists the
  per-label anchors and each anchor lists its documents, as D6 and D7
  step 9 design it.
- [x] **Resolved: inert set.** The spec now says every foreign key the
  engine reads moves under the inert key, with `origin_key` and
  `merged_from` dropped, as D4's table designs it.
- [x] **Resolved (orchestrator decision): accept-recommended for imported
  groups.** It is not offered. `in_structural_class` refuses an imported
  member, and `recommended` keeps gating on it (D8).
- [ ] **Spec delta needed: survivor rule.** "Local over imported" in
  `ordered_merge_pair` modifies `entity-resolution-adjudication`'s survivor
  requirement (`openspec/specs/entity-resolution-adjudication/spec.md:916-954`).
  No delta exists for that domain yet.
