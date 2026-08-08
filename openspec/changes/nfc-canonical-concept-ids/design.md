# Design: nfc-canonical-concept-ids (issue #430)

## D1 — NFC, not NFD, and why no ADR

NFC is what `_slugify` has emitted since #414, so it is the canonical spelling
**by construction**: openkos never writes a decomposed filename. Choosing NFD
instead would make every id openkos itself produces non-canonical. Choosing
"normalize at comparison sites only" restores the eleven-site drift #453 just
consolidated away.

ADR gate: this decides a trade-off, but it is not hard to reverse — the
derivation lives in exactly one helper (`okf.concept_id_for`), the change
never rewrites a bundle file, and reverting is a one-line change plus a
revert of the inverse helper. Both ADR conditions are not met; no ADR.

## D2 — The tolerant inverse (`okf.concept_path_for`)

Normalizing ids alone converts a comparison bug into a silent-content bug:
nine sites rebuild `bundle_dir / f"{id}.md"`, and on a byte-exact filesystem
an NFC id cannot open an NFD file. Each of those sites was infallible before
normalization (the id came straight from the walked path), and most of them
swallow a read miss as an empty body handed to an LLM prompt.

Shape: probe the direct path first; on a miss, resolve the id segment by
segment against the real directory entries, matching each non-ASCII segment
by NFC-normalized name (a leaf-only parent scan would raise on a decomposed
*ancestor* directory — whose NFC spelling does not exist on a byte-exact
volume — and silently miss an existing file; found in review, R3-001). A
miss at any segment or an unreadable directory returns the direct path
unchanged. The helper resolves a **spelling** — it does not assert
existence, and every caller keeps its own absence handling.

Two guards, both load-bearing:

1. **ASCII skips the scan.** ASCII has no distinct decomposed form, so a miss
   on an ASCII id can never be a normalization mismatch. A dangling id is an
   ordinary, documented case reached per candidate inside loops that drive
   `llm.chat` (up to `_MAX_PAIRS` = 200 per contradiction run); without the
   guard, every such miss pays an `iterdir()` to learn nothing. Almost every
   real id is ASCII.
2. **The fallback admits strictly less than the direct probe.** A regular
   non-symlink file at the leaf, a non-symlink directory at every inner
   segment. The fallback is a guess keyed on normalization, not an exact
   name the caller asked for, so it fails closed: `_resolve_concept_path`
   is the path-safety gate `forget` deletes through, and every LLM verb
   reads what it resolves — a symlink planted under a decomposed spelling
   must not stand in for an absent concept.

The fallback is deliberately silent (no counter, no log line): it reports a
spelling. Detection of decomposed on-disk names belongs to `lint`, which
walks the bundle anyway — follow-up, with the rename migration.

## D3 — No automated rename migration

Renaming bundle files is consequential and stays human-reviewed (AGENTS.md:
human curates, engine maintains). This change makes reads correct on either
spelling; making the bundle *consistent* is a separate, detectable, reviewable
act. Follow-up issue covers: a `lint` finding for non-NFC on-disk names, and
rename tooling if warranted.

## D4 — Known disclosed edge

`concept_id_for` keeps #453's `with_suffix("")` spelling and its one
disclosed divergence (a document named literally `.md` keeps its name as id).
Normalization composes with it; the pinned tests still hold.
