# Concept Identity Specification

## Purpose

Define the canonical spelling of a concept id and the two shared helpers that
enforce it: the path→id derivation every bundle reader uses, and the id→path
reconstruction every document lookup uses. NFC is the canonical spelling by
construction — `_slugify` has emitted NFC since #414 — and this spec makes the
read side honor it, so two on-disk spellings of the same logical name (NFC,
or the NFD a normalizing filesystem such as HFS+ rewrites on write) can never
disagree with the ids spelled in `relations:` targets, ledger entries, or
`provenance:` references. (Merged from change `nfc-canonical-concept-ids`,
issue #430.)

## Requirements

### Requirement: Concept Id Derivation Is NFC-Normalized

A concept id derived from a filesystem path — the document's bundle-relative
POSIX path with the `.md` suffix removed — MUST be NFC-normalized, and every
reader that derives an id from a walked path MUST derive it through the one
shared helper (`okf.concept_id_for`). Two on-disk spellings of the same
logical name (NFC, or the NFD a normalizing filesystem such as HFS+ rewrites
on write) MUST yield the same id string, so that comparisons against ids
spelled elsewhere — a `relations:` target, a ledger entry, a `provenance:`
reference — cannot fail on normalization alone. Derivation MUST remain pure:
no I/O, and the path need not exist.

#### Scenario: A decomposed filename yields the NFC id

- GIVEN a document whose on-disk filename stem is `café` in NFD
  (`e` followed by a combining acute)
- WHEN a concept id is derived from its path
- THEN the id is `concepts/café` spelled NFC (precomposed `é`)

#### Scenario: Both spellings collapse to the same id

- GIVEN two paths naming the same logical concept, one NFC and one NFD
- WHEN a concept id is derived from each
- THEN the two ids are the same string

#### Scenario: A decomposed directory segment is normalized too

- GIVEN a document under a subdirectory whose name is stored NFD
- WHEN a concept id is derived from its path
- THEN every segment of the id is NFC

#### Scenario: An already-NFC path is byte-identical

- GIVEN a document whose path is entirely NFC (every path openkos itself
  writes, since `_slugify` emits NFC)
- WHEN a concept id is derived from it
- THEN the id is byte-identical to the un-normalized derivation

### Requirement: Concept Path Reconstruction Tolerates A Decomposed On-Disk Name

Every site that reconstructs a document path from a concept id MUST resolve it
through the one shared inverse helper (`okf.concept_path_for`). The helper
MUST probe the direct path (`bundle_dir / f"{id}.md"`) first and return it on
a hit. On a miss for a non-ASCII id, it MUST resolve the id segment by
segment against the real directory entries, matching each non-ASCII segment
(directory or leaf) by NFC-normalized name and admitting only a non-symlink
directory at an inner segment and a regular non-symlink file at the leaf —
strictly less than the direct probe admits. On a miss at any segment, an
unreadable directory, or any `OSError`, it MUST return the direct path
unchanged rather than raise: the helper resolves a spelling, it does not
assert existence, and each caller keeps its own absence handling. For an
ASCII id the fallback MUST be skipped entirely, since ASCII has no distinct
decomposed form and a scan could only confirm the miss.

#### Scenario: An NFC id resolves a decomposed on-disk name

- GIVEN a byte-exact filesystem holding `concepts/café.md` with an NFD
  filename (e.g. a bundle authored on HFS+, committed, and cloned onto ext4)
- WHEN the path for the NFC id `concepts/café` is reconstructed
- THEN the returned path names the existing NFD file

#### Scenario: An NFC id resolves a decomposed ancestor directory

- GIVEN a byte-exact filesystem holding a document under a directory whose
  on-disk name is NFD
- WHEN the path for the NFC-spelled id is reconstructed
- THEN the returned path names the existing file beneath the NFD directory

#### Scenario: A missing concept returns the direct path, not an error

- GIVEN an id with no corresponding document on disk
- WHEN its path is reconstructed
- THEN the direct path is returned and no exception is raised

#### Scenario: A symlink is never admitted through the fallback scan

- GIVEN a symlink planted under a decomposed spelling of an id
- WHEN the path for the NFC id is reconstructed and the direct probe misses
- THEN the fallback returns the direct path, not the symlink

#### Scenario: An ASCII id never pays a directory scan

- GIVEN an ASCII id whose document is absent (an ordinary dangling
  edge endpoint inside a per-candidate LLM loop)
- WHEN its path is reconstructed
- THEN no directory listing is performed
