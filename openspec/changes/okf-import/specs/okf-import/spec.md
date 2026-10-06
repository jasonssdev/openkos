# OKF Import Specification

## Purpose

`openkos import <dir>` adopts a foreign Open Knowledge Format bundle into a
workspace as written: the same text, structure and links, with no model call,
under a namespace that cannot collide with local concepts, labelled
fail-closed, and anchored to an engine-written import Source that records
where the documents came from. OKF v0.2 says nothing about combining bundles,
so the collision, trust and identity policy here is an OpenKOS decision (the
import-policy ADR records why). Duplicates against local knowledge are
resolved afterwards by the existing duplicates, adjudicate, merge and curate
Identity tools. This domain states WHAT must hold; the anchor's granularity,
the inert key's name and shape, and the torn-run mechanism belong to design.

## Requirements

### Requirement: Import Is A Human-Invoked, Model-Free, Local Verb

`openkos import <dir>` MUST adopt a foreign bundle from a local directory. It
MUST make no model call and no network call, and MUST NOT require a running
model backend. It MUST be invocable only by a human from the command line:
the daemon, the pending-work queue, the inbox watch and the MCP server MUST
NOT import, and MUST NOT expose an import operation. The verb MUST accept
`--namespace`, `--sensitivity <level>` and `--auto`, and MUST follow the
preview, confirm and exit-code conventions of `docs/cli.md`: it prints a
preview, asks for confirmation unless `--auto` is given, and a refusal exits
non-zero with a named reason and writes nothing.

#### Scenario: A full import makes no model call

- GIVEN a workspace and a foreign bundle, with `OLLAMA_HOST` pointing at an
  unreachable address
- WHEN `openkos import <dir> --namespace demo --auto` runs
- THEN it succeeds and no model or network call is attempted

#### Scenario: The daemon and MCP cannot import

- GIVEN the daemon's job kinds and the MCP server's tool list
- WHEN they are enumerated
- THEN neither contains an import operation, and the pending queue accepts no
  import kind

#### Scenario: Declining the confirmation writes nothing

- GIVEN a valid foreign bundle and a workspace
- WHEN `openkos import <dir> --namespace demo` runs and the user declines
  the confirmation
- THEN no file under the workspace changes and no commit is made

### Requirement: The Input Is A Local Directory

The `<dir>` argument MUST name an existing local directory. A path that does
not exist, a regular file, an archive (zip, tar) or a URL MUST be refused
with a reason naming the problem, before any workspace write.

#### Scenario: A file is refused

- GIVEN a path to a regular file such as `bundle.zip`
- WHEN `openkos import bundle.zip` runs
- THEN it exits non-zero with a reason stating that a directory is required,
  and the workspace is unchanged

#### Scenario: A URL or missing path is refused

- GIVEN `https://example.com/bundle` or a path that does not exist
- WHEN `openkos import` runs on it
- THEN it exits non-zero naming the input as not an existing local
  directory, and the workspace is unchanged

### Requirement: Every Concept Lands Under One Namespace

Every adopted concept MUST be placed under `imports/<namespace>/` inside
`bundle/`, with Concept ID `imports/<namespace>/<foreign Concept ID>` (the
foreign file path minus `.md`, with each path segment that holds whitespace
renamed as the requirement "A Name With Whitespace Is Renamed To A Slug"
states). The foreign directory structure MUST be preserved under the
namespace. The namespace MUST be a single valid slug
segment (lowercase ASCII letters, digits and single hyphens), given by
`--namespace`, which is required and has no default. A missing or invalid
namespace MUST be refused as a usage error with a reason, before the foreign
directory is read. A foreign document MUST NOT be placed at any path outside
`imports/<namespace>/`.

#### Scenario: Nested structure is preserved under the namespace

- GIVEN a foreign bundle with `concepts/skills/python.md`
- WHEN it is imported with `--namespace acme`
- THEN `bundle/imports/acme/concepts/skills/python.md` exists with Concept ID
  `imports/acme/concepts/skills/python`

#### Scenario: An invalid namespace is refused

- GIVEN `--namespace "a/b"`, `--namespace ".."`, `--namespace Acme`, or an
  empty value
- WHEN `openkos import` runs
- THEN it is refused with a reason naming the namespace rule, and the
  workspace is unchanged

#### Scenario: A missing namespace is refused

- GIVEN `openkos import <dir>` with no `--namespace`
- WHEN it runs
- THEN it is refused as a usage error and nothing is read or written

#### Scenario: A foreign path equal to a local path does not collide

- GIVEN a local `concepts/foo.md` and a foreign `concepts/foo.md`
- WHEN the foreign bundle is imported under `--namespace x`
- THEN `bundle/concepts/foo.md` is byte-identical to before and
  `bundle/imports/x/concepts/foo.md` is the foreign document

### Requirement: A Name With Whitespace Is Renamed To A Slug

A foreign file or directory whose name contains whitespace MUST be adopted
under a slug of that name, because no spelling of a link to a name with
whitespace is read by every engine link reader, so the anchor could not list
the document and the graph would lose its edge. The slug rule MUST be
deterministic: for each path segment that holds whitespace, every run of
whitespace characters becomes one `-` and the result is casefolded and
normalized to NFC; a segment without whitespace MUST keep its bytes. The
rename MUST apply to a directory segment as much as to a file name. Every link
in an adopted body that names a renamed file or directory MUST be rewritten to
the new name, in every form the engine reads (absolute, relative,
reference-style, percent-encoded and angle-bracketed), so that it resolves to
the same document it named before, and the anchor MUST link the renamed
document so the anchor-to-document graph edge exists. The preview MUST report
each rename. The original foreign path MUST be kept in the inert `imported`
block of the renamed document. When two names are equal after the rename
(compared as NFC then casefolded, against both renamed and unchanged names,
files and directories alike), the import MUST be refused with the reason code
`rename-collision` naming both foreign paths, and the workspace MUST be
unchanged.

#### Scenario: A file name with whitespace is renamed to a slug

- GIVEN a foreign document at `Mi Nota.md`, or under a directory `My Folder/`
- WHEN the bundle is imported under `--namespace demo`
- THEN the document is adopted at `imports/demo/mi-nota` (or
  `imports/demo/my-folder/<name>`), the preview reports the rename, and the
  document's `imported` block records the original path `Mi Nota.md`

#### Scenario: A segment without whitespace keeps its bytes

- GIVEN foreign documents `Keep/Mi Nota.md` and `Plain.md`
- WHEN the bundle is imported
- THEN they are adopted at `imports/<ns>/Keep/mi-nota` and
  `imports/<ns>/Plain`, and `Plain.md` records no original path

#### Scenario: Links to a renamed name are rewritten in every form

- GIVEN an adopted body that links to `My Folder/Mi Nota.md` as an absolute
  path, a `%20`-encoded path, an angle-bracketed path, a relative path and a
  reference-style definition
- WHEN the bundle is imported
- THEN each link names `my-folder/mi-nota.md` under the namespace, every engine
  link reader resolves every link inside `imports/<ns>/`, and the anchor's link
  to the document yields an anchor-to-document graph edge

#### Scenario: A collision after the rename is refused

- GIVEN foreign documents `Mi Nota.md` and `mi-nota.md` (or `MI  NOTA.md`, or
  directories `My Folder/` and `my-folder/`)
- WHEN the bundle is imported
- THEN the import is refused with the reason code `rename-collision` naming
  both foreign paths, and the workspace is unchanged

### Requirement: Importing Into An Existing Namespace Is Refused

WHEN `bundle/imports/<namespace>/` already exists (even empty), the import
MUST be refused with a reason naming the namespace and stating that
re-import is not supported, and MUST write nothing. The refusal MUST happen
before any file is written.

#### Scenario: A second import under the same namespace is refused

- GIVEN a workspace where `openkos import dirA --namespace acme` succeeded
- WHEN `openkos import dirB --namespace acme` runs
- THEN it exits non-zero with a reason that `acme` already exists and
  re-import is unsupported, and the tree is byte-identical to before

#### Scenario: A namespace claimed between preview and commit is refused

- GIVEN a preview was shown for namespace `acme` and the namespace comes to
  exist before the write phase
- WHEN the write phase begins
- THEN the import is refused with the same reason and writes nothing

### Requirement: Hostile Bundles Are Refused With A Named Reason

The foreign bundle MUST be read by a bounded reader that parses every
frontmatter block with the guarded incoming-frontmatter parser, never an
unguarded loader. The reader MUST refuse the whole import, each with its own
named reason and leaving the workspace unchanged, for: a path that escapes
the foreign root (traversal, absolute or `..` components); any symbolic link
under the tree; a single file over the per-file size cap; a total size over
the total cap; a file count over the file-count cap; frontmatter containing
an anchor or alias; a frontmatter block over the guarded parser's bounds; a
frontmatter root that is not a mapping; two paths that collide when
case-folded; and two paths that collide under Unicode NFC/NFD
normalization.

#### Scenario: Path traversal is refused

- GIVEN a foreign tree containing an entry resolving outside the foreign
  root
- WHEN import reads it
- THEN it is refused with a traversal reason and nothing is written

#### Scenario: A symlink is refused

- GIVEN a foreign tree containing a symbolic link to a file or directory
- WHEN import reads it
- THEN it is refused with a symlink reason and nothing is written

#### Scenario: Size and count caps are enforced

- GIVEN one file over the per-file cap; separately a tree whose total size
  is over the total cap; separately a tree with more files than the cap
- WHEN each is imported
- THEN each is refused with its own distinct named reason and nothing is
  written

#### Scenario: A frontmatter alias bomb is refused

- GIVEN a document whose frontmatter uses a YAML anchor and alias
- WHEN import reads it
- THEN it is refused with a reason naming the alias, without expanding it,
  and nothing is written

#### Scenario: A non-mapping frontmatter root is refused

- GIVEN a document whose frontmatter parses to a list or scalar
- WHEN import reads it
- THEN it is refused with a named reason and nothing is written

#### Scenario: Case-fold and NFD collisions are refused

- GIVEN a foreign tree holding `Concepts/Foo.md` and `concepts/foo.md`
  (case collision), and separately one holding `café.md` (NFC) and
  `café.md` (NFD)
- WHEN each is imported
- THEN each is refused with its own named collision reason and nothing is
  written

#### Scenario: The unguarded loader is never used on foreign input

- GIVEN the import code path
- WHEN a foreign bundle is read
- THEN no frontmatter block is parsed by the unguarded bundle readers

### Requirement: A Non-Conformant Document Is Tolerated, Not Adopted

OKF §11 requires consumers to tolerate non-conformant documents, so a
document that fails §11 (frontmatter that does not parse, or a missing or
empty `type`) MUST NOT cause the whole import to be refused. It MUST be left
out of `bundle/`, so that `bundle/` stays conformant by construction, and it
MUST be listed in the preview and the summary with its foreign path and the
reason. Every other conformant document MUST still be adopted. A foreign
`type` value that OpenKOS does not know MUST be kept verbatim and the
document adopted.

#### Scenario: A document with no `type` is skipped and reported

- GIVEN a foreign bundle of three documents, one of which has no `type`
- WHEN it is imported
- THEN two concepts are adopted, the third is absent from `bundle/`, and the
  preview and summary list its path with the reason "missing type"

#### Scenario: Unparseable frontmatter is skipped, not fatal

- GIVEN one document whose frontmatter block is not valid YAML
- WHEN the bundle is imported
- THEN the import succeeds for the other documents and reports that
  document as skipped with a parse reason

#### Scenario: An unknown type is kept

- GIVEN a document with `type: Recipe`
- WHEN it is imported
- THEN it is adopted with `type: Recipe` unchanged

#### Scenario: The result conforms

- GIVEN any import that succeeds
- WHEN `okf.check_conformance` runs on the resulting `bundle/`
- THEN it reports no conformance failure caused by the import

### Requirement: Skipped Files Are Reported

Files that are not concept documents MUST NOT be copied into `bundle/` or
`raw/`, and MUST be reported. This covers non-`.md` files (for example
`viz.html` or `references/`), files inside dot-directories, and the foreign
`index.md` and `log.md` files. Every skipped file MUST appear in the preview
by relative path with its reason. The foreign root `index.md` MUST be read
for `okf_version` only. WHEN `okf_version` is absent or not a version this
engine knows, the import MUST proceed best-effort (OKF §12) and the preview
MUST state the version observed.

#### Scenario: Non-concept files are reported, not copied

- GIVEN a foreign bundle with `viz.html`, `references/a.pdf`,
  `.git/config`, `index.md`, `log.md` and two concept documents
- WHEN the preview is shown
- THEN it lists each of the five skipped paths with a reason, and after
  import none of them exists under `bundle/` or `raw/`

#### Scenario: An unknown okf_version is read best-effort

- GIVEN a foreign root `index.md` declaring an `okf_version` this engine
  does not know
- WHEN the bundle is imported
- THEN the import proceeds and the preview states the observed version

### Requirement: Link Targets Are Rewritten Into The Namespace

Every link and `relations:` target in an adopted document that points into
the foreign bundle MUST be rewritten so it resolves to the same document
under `imports/<namespace>/`. This MUST cover bundle-absolute inline links,
relative links (including those that climb out of the foreign root),
reference-style link definitions, and `relations:` targets. Relative links
that already stay inside the imported subtree MUST remain valid as written.
After the transform, no pointer in any adopted document MAY resolve outside
`imports/<namespace>/`; WHEN any does, the whole import MUST be refused with
a reason and the workspace left unchanged. The link text and the rest of the
body MUST be preserved byte for byte.

#### Scenario: A bundle-absolute inline link is rewritten

- GIVEN a foreign document with `[X](/concepts/x.md)`
- WHEN imported under `--namespace acme`
- THEN the link resolves to `bundle/imports/acme/concepts/x.md`

#### Scenario: A relative link inside the subtree is left valid

- GIVEN a foreign `concepts/a.md` linking `[B](b.md)`
- WHEN imported
- THEN the link still resolves to the imported `concepts/b.md`

#### Scenario: A relative link that escapes the foreign root is contained

- GIVEN a foreign `concepts/a.md` linking `[Up](../../outside.md)`, which
  climbs above the foreign root
- WHEN imported
- THEN the adopted link does not resolve to any path outside
  `imports/<namespace>/`, or the import is refused

#### Scenario: A reference-style link is rewritten

- GIVEN a foreign document with `[X][ref]` and `[ref]: /concepts/x.md`
- WHEN imported
- THEN the definition resolves inside the namespace

#### Scenario: A relations target is rewritten

- GIVEN a foreign document whose `relations:` names `/concepts/x`
- WHEN imported
- THEN the target resolves to `imports/<namespace>/concepts/x`

#### Scenario: A foreign link never lands on a local document

- GIVEN a local `concepts/x.md` and a foreign document linking
  `/concepts/x.md`
- WHEN imported under any namespace
- THEN the adopted link resolves to the imported copy, never the local one

### Requirement: Broken Links Are Tolerated

A link whose target does not exist in the foreign bundle MUST NOT refuse the
import (OKF §6.1). It MUST be kept pointing inside the namespace (still
unresolved) and MUST NOT resolve to any document outside the namespace.

#### Scenario: A dangling link is kept inside the namespace

- GIVEN a foreign document linking `/concepts/missing.md`, which the bundle
  does not contain, while the workspace has a local `concepts/missing.md`
- WHEN imported
- THEN the import succeeds, and the link does not resolve to the local
  `concepts/missing.md`

### Requirement: Every Imported Document Is Labelled Fail-Closed

Every adopted document MUST carry a recognized `sensitivity` label. The
effective label MUST be the high-water mark of: the workspace
`default_sensitivity`; the `--sensitivity` value when given; and the folded
foreign label. A foreign label that is absent MUST contribute nothing, so the
workspace default applies. A foreign label that is one of `public`,
`private`, `confidential` MUST only raise. A foreign label that is present
but not one of those (an unknown string, a non-string value) MUST fold to
`confidential`. `--sensitivity` MUST only raise: a value below the workspace
default MUST NOT lower any label, and an invalid value MUST be refused.
No import MUST ever produce a label lower than `default_sensitivity`.

#### Scenario: An absent label takes the workspace default

- GIVEN `default_sensitivity: private` and a foreign document with no label
- WHEN imported
- THEN its `sensitivity` is `private`

#### Scenario: A foreign public label does not lower the floor

- GIVEN `default_sensitivity: private` and a foreign document labelled
  `public`
- WHEN imported
- THEN its `sensitivity` is `private`

#### Scenario: A foreign confidential label raises

- GIVEN `default_sensitivity: private` and a foreign document labelled
  `confidential`
- WHEN imported
- THEN its `sensitivity` is `confidential`

#### Scenario: An unknown foreign label fails closed

- GIVEN a foreign document with `sensitivity: secret`, and another with a
  numeric `sensitivity: 3`
- WHEN imported with `default_sensitivity: public`
- THEN both are `confidential`

#### Scenario: `--sensitivity` raises and never lowers

- GIVEN `default_sensitivity: private`, a foreign `confidential` document
  and a foreign unlabelled document
- WHEN imported with `--sensitivity private`
- THEN labels are `confidential` and `private`; and with
  `--sensitivity public` the result is unchanged
- AND with `--sensitivity confidential` both are `confidential`

#### Scenario: An invalid `--sensitivity` is refused

- GIVEN `--sensitivity secret`
- WHEN `openkos import` runs
- THEN it is refused with a reason listing the valid levels, and the
  workspace is unchanged

### Requirement: Per-Type Sensitivity Offsets Apply To Imported Concepts

Import MUST be a birth seam for `type_sensitivity_defaults`. For an adopted
concept whose OKF `type` has a configured offset, the effective label MUST
be at least `raise_by(default_sensitivity, offset)`, in addition to the
high-water mark of the previous requirement. The offset MUST be raise-only
and floor-relative, MUST NOT lower a label already higher, and MUST apply to
foreign `type` values by exact name. An empty or absent mapping MUST apply no
offset. The import-written anchor Source is never type-defaulted.

#### Scenario: A Person is raised by its offset

- GIVEN `default_sensitivity: public`, `type_sensitivity_defaults:
  {Person: 1}` and an unlabelled foreign `Person`
- WHEN imported
- THEN its `sensitivity` is `private`

#### Scenario: A higher foreign label still wins

- GIVEN the same config and a foreign `Person` labelled `confidential`
- WHEN imported
- THEN its `sensitivity` is `confidential`

#### Scenario: An unconfigured type is unaffected

- GIVEN the same config and an unlabelled foreign `Concept`
- WHEN imported
- THEN its `sensitivity` is `public`

#### Scenario: The summary names type-defaulted objects

- GIVEN an import in which one concept is raised by a per-type offset
- WHEN the summary is printed
- THEN it states how many concepts were raised and their type, as the
  other birth seams do

### Requirement: Every Foreign Key The Engine Reads Is Kept Inert

Every foreign frontmatter key that an OpenKOS consumer reads as a local fact
MUST be preserved verbatim under one extension key, `imported` (legal under
OKF §4.1), and MUST NOT be read by any local consumer as local provenance,
lifecycle, freshness, trust, extraction state or file reference. This
covers, at least: `provenance`, `sources`, `status`, `generated`,
`verified`, `version`, `timestamp`, the engine's internal markers
(`ingest_pending`, `extraction_status`, `extraction_notice`,
`status_derived_from`, `source_frontmatter`), engine-owned `relations`
entries (provenance-type relations), and a `resource` that has no URL scheme
(for example `raw/notes.txt`). A `resource` with a URL scheme MAY be kept as
written. A foreign key the engine does not read MUST be kept as written. The
set MUST be total: a key the engine reads, including one added to the engine
later, MUST NOT be trusted from foreign input by default.

The machine-local keys `origin_key` and `merged_from` MUST be dropped, not
preserved, and the preview MUST report that they were dropped. The `imported`
key MUST carry only the foreign bundle's own values, the chosen namespace,
the foreign Concept ID and a content digest, never an absolute path or a
local directory name, so it may travel in an export.

The adopted concept's own `provenance` MUST name an import anchor, and its
`sources` MUST be re-projected from that provenance (OKF §5.1). A foreign
`status: deprecated` MUST NOT make the adopted concept locally deprecated,
and a foreign `verified` MUST NOT make it locally verified. A foreign
`resource` MUST NOT cause any local file to be treated as that concept's
source.

#### Scenario: Foreign provenance is preserved but not trusted

- GIVEN a foreign document with `provenance: [sources/x]`,
  `verified: ...` and `status: deprecated`
- WHEN imported
- THEN the originals appear verbatim under the inert key, the local
  `provenance` names only the anchor, the local `sources` is derived from it,
  and the concept is not effective-deprecated nor verified

#### Scenario: Foreign trust keys are not read by local consumers

- GIVEN an adopted concept carrying foreign `verified` and `generated`
- WHEN lint, status, query and freshness evaluation run
- THEN none of them reads the inert key as a local value

#### Scenario: Engine markers and version keys are inert

- GIVEN a foreign document carrying `version: 7`, `timestamp`,
  `extraction_status: degraded` and `ingest_pending: true`
- WHEN imported
- THEN each appears verbatim under `imported`, and no retry hint, repair
  migration or version bump is derived from them

#### Scenario: A foreign raw-file `resource` is inert

- GIVEN a local workspace with `raw/notes.txt`, and a foreign `Source`
  document with `resource: raw/notes.txt`
- WHEN the foreign bundle is imported, and `purge`, `forget`, `lint` and
  `next` are then evaluated for the imported concept
- THEN the `resource` appears only under `imported`, no consumer resolves it
  to the local `raw/notes.txt`, a purge of the imported copy never touches
  that local raw file, and `lint` does not count the local raw file as
  referenced by it

#### Scenario: A URL `resource` may stay

- GIVEN a foreign document with `resource: https://example.org/a`
- WHEN imported
- THEN the value may be kept as written and no consumer treats it as a
  local file

#### Scenario: Machine-local keys are dropped and reported

- GIVEN a foreign document carrying `origin_key` and `merged_from`
- WHEN imported
- THEN neither appears in the adopted document nor under `imported`, and the
  preview reports that machine-local keys were dropped

#### Scenario: Engine-owned relations are inert, others are rewritten

- GIVEN a foreign `relations:` with a provenance-type entry and an ordinary
  entry targeting `/concepts/x`
- WHEN imported
- THEN the provenance-type entry is preserved verbatim under `imported`, and
  the ordinary entry targets `imports/<namespace>/concepts/x`

#### Scenario: A foreign Source-typed document is an ordinary concept

- GIVEN a foreign document of `type: Source` with a `resource` that is not a
  `raw/` path
- WHEN imported, and `lint` and `status` then run
- THEN it is adopted as an ordinary concept, is not treated as the import
  anchor, and causes no import-related lint or status error

### Requirement: Imported Concepts Cite An Engine-Written Anchor Source

Every adopted concept MUST cite an engine-written import anchor Source in its
local `provenance`. There MUST be one anchor per effective label present in
the import, carrying that label, and each adopted concept MUST cite exactly
the anchor of its own effective label, so no concept is below-source at
export. An anchor MUST list only documents at its own label. The anchors
MUST record: the origin identity of the imported bundle, each adopted
document's foreign Concept ID and content digest, the import time, the
engine version, the foreign `okf_version` and the foreign `generated.by`
where present. An anchor MUST NOT contain an absolute local path, a local
directory name or any machine-local identifier, because it travels in an
export, and MUST NOT carry a `resource` that names a local file.

#### Scenario: The anchor records origin without a local path

- GIVEN an import from `/home/a/private/bundle`
- WHEN the anchor is read
- THEN it records the foreign Concept ID and digest of each adopted
  document, the import time, engine version and `okf_version`, and the string
  `/home/a/private/bundle` appears nowhere in the workspace

#### Scenario: Every adopted concept cites an anchor

- GIVEN a completed import
- WHEN every adopted concept's local `provenance` is read
- THEN each id resolves to an existing import anchor Source, and none points
  at a foreign id

#### Scenario: One anchor per effective label

- GIVEN an import whose documents land at `public` and `private`
- WHEN it completes
- THEN two anchors exist, one labelled `public` and one `private`, each
  concept cites the anchor of its own label, and each anchor lists only the
  documents at its label

#### Scenario: Each document is reachable from its anchor

- GIVEN a completed import
- WHEN the anchors' bodies are read
- THEN every adopted document is linked from exactly one anchor, so lint
  reports no orphan for an imported document

### Requirement: Export After Import Does Not Withhold For The Anchor's Label

An imported concept MUST NOT be withheld by `openkos export` merely because
its anchor carries a higher label than the concept (the below-source rule).
Exporting a workspace that contains a mixed-label import MUST withhold an
imported document only for the same reasons any concept is withheld (its own
label, or the `--include-private` gate), never solely because of its anchor.

#### Scenario: Mixed labels export without below-source withholding

- GIVEN an import with documents labelled `public`, `private` and
  `confidential`
- WHEN `openkos export out/ --include-private` runs
- THEN the `public` and `private` documents are exported and are not
  reported as below-source, and the `confidential` one is withheld by its own
  label

#### Scenario: A public imported document exports by default

- GIVEN a stock workspace (`default_sensitivity: private`) with a
  foreign `public` document imported, then raised to `public` by a human
  `set-sensitivity`
- WHEN `openkos export out/` runs
- THEN that document is exported without `--allow-below-source`

### Requirement: Import Previews, Confirms, Then Publishes Under The Lock

Import MUST be split into a preview phase and a write phase. The preview
phase MUST NOT take the workspace lock and MUST NOT write; it reads the
bundle, builds the plan and shows: the namespace, counts of adopted and
skipped files, each skipped file and reason, the label distribution, and any
type-defaulted raises. The write phase MUST take the workspace lock, re-read
and re-validate the foreign bundle and the namespace's absence, and MUST be
refused with a named reason, writing nothing, when the foreign tree changed
since the preview (a drift guard) or the namespace now exists. WHEN the lock
is contended the import MUST fail as every locked verb does.

#### Scenario: The preview shows the plan and writes nothing

- GIVEN a valid foreign bundle
- WHEN the preview is shown and the user declines
- THEN the preview listed namespace, counts, skipped files and the label
  distribution, and no file was written

#### Scenario: A foreign tree changed after the preview is refused

- GIVEN a preview was computed and a foreign file then changes before the
  write phase
- WHEN the write phase re-validates
- THEN the import is refused with a drift reason and writes nothing

#### Scenario: A busy workspace refuses the write phase

- GIVEN another process holds the workspace lock
- WHEN the write phase would begin
- THEN the import exits `3` with the existing busy wording and writes nothing

### Requirement: A Torn Import Never Strands The Namespace

An import interrupted at any point MUST leave the workspace in one of two
states: no trace of the namespace, or a state the user can recover from
without manual file surgery. In particular, a failed or interrupted run MUST
NOT leave a partially populated `imports/<namespace>/` that causes the
existing-namespace refusal to block a retry of the same import. A refused run
MUST leave the tree byte-identical to before.

#### Scenario: A failure mid-write leaves no blocking half-namespace

- GIVEN an import that fails after some adopted files were written
- WHEN the failure is reported
- THEN a retry of the same import under the same namespace is not refused
  as an existing namespace

#### Scenario: A refused import changes nothing

- GIVEN any refusal from the hostile-input, namespace, link or drift rules
- WHEN it is reported
- THEN `bundle/`, `raw/` and `openkos.yaml` are byte-identical to before

### Requirement: One Import Is One Commit, With Index And Log Entries

A successful import MUST make exactly one autocommit containing the adopted
concepts, the anchor(s), and the `bundle/index.md` and `bundle/log.md`
updates, with the message `openkos: import <namespace> (+N concepts)`. The
bundle `index.md` MUST list the import's anchor Sources, and each anchor MUST
list its documents; the index MUST NOT carry one entry per adopted concept.
The log MUST receive one `**Import**` entry naming the namespace, the count
adopted and the count skipped. WHEN the
commit is degraded (no repository or identity) the import MUST still succeed
and report the usual non-fatal warning. The import MUST write only canonical
files, no derived store; FTS and embeddings catch up through the existing
derived-index path or `reindex`.

#### Scenario: One commit, revertable while latest

- GIVEN a git-backed workspace with identity
- WHEN an import succeeds
- THEN exactly one new commit exists with that message and the working tree
  is clean
- AND `git revert <sha>` while it is the latest commit restores the
  pre-import tree byte for byte

#### Scenario: The index lists anchors, not every concept

- GIVEN an import of 500 documents at two labels
- WHEN `bundle/index.md` is read after the import
- THEN it gains exactly two entries, one per anchor, and no entry for an
  individual adopted document

#### Scenario: The log records the import

- GIVEN a completed import of 5 adopted and 2 skipped files
- WHEN `bundle/log.md` is read
- THEN it holds one `**Import**` entry naming the namespace, 5 and 2

#### Scenario: No derived store is written

- GIVEN a completed import
- WHEN the workspace's derived stores are inspected
- THEN import has written none of them

### Requirement: Imported Concepts Are Not Attach Targets Or Automatic-Merge Candidates

An imported concept MUST NOT be chosen as an attach-at-ingest target
(`ingestion`), and a candidate group with an imported member MUST NOT be in
the automatic-merge class (`identity-auto-merge`). Imported concepts MUST
remain visible to `duplicates`, `adjudicate`, `merge` and `curate` Identity's
per-group prompt, so a human can reconcile them. A group with an imported
member MUST NOT be offered through `curate` Identity's accept-recommended
answer, because it is outside the population the automatic class was
measured on; it keeps its per-item prompt. In a human merge of one local and
one imported concept, the local concept MUST survive (see
`entity-resolution-adjudication`).

#### Scenario: Ingest does not attach to an imported concept

- GIVEN an imported `Concept` titled "Atlas" and a local ingest yielding a
  `Concept` titled "Atlas"
- WHEN ingest stages the candidate
- THEN it does not attach to the imported one

#### Scenario: Curate auto-merge skips an imported pair

- GIVEN a HIGH-tier base/`-N` pair where one member is imported
- WHEN `curate --auto-merge` runs
- THEN the group is not merged automatically, is not offered through
  accept-recommended, and is still prompted individually in Identity

#### Scenario: A human can still merge imported with local

- GIVEN an imported concept and a local concept that are duplicates
- WHEN a human runs `merge` on them
- THEN the merge is permitted and recorded as any merge, and the local
  concept is the survivor

### Requirement: Round Trip Through Export

The export of a workspace MUST be importable. Importing a workspace's own
export into a fresh workspace under any valid namespace MUST place every
exported concept under `bundle/imports/<namespace>/`, and the result MUST
conform to OKF §11. Importing that export back into the workspace that made
it MUST succeed, and MUST leave every pre-existing concept file
byte-identical, changing only `index.md` and `log.md` besides the new
namespace.

#### Scenario: Good-life demo round trip into a fresh workspace

- GIVEN an export of `examples/good-life-demo` made with `--include-private`
- WHEN it is imported into a fresh workspace with `--namespace demo`
- THEN every exported concept exists under `bundle/imports/demo/`, the result
  passes `okf.check_conformance`, and `lint` and `status` report no
  import-caused error

#### Scenario: Importing an export back into its origin workspace

- GIVEN a workspace and its own export
- WHEN the export is imported into that workspace under a new namespace
- THEN it succeeds, no pre-existing concept file changes, and only
  `index.md` and `log.md` gain lines outside the namespace
