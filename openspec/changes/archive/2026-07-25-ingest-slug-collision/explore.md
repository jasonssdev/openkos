# Exploration: ingest slug collision (issue #131)

Design/discussion issue. When two+ sources each extract a derived concept that
slugs to the SAME filename, the first source creates `bundle/concepts/<slug>.md`
and every later same-slug candidate is SILENTLY dropped by a create-only guard.
The collapse happens at INGEST time, so `duplicates → adjudicate → merge` never
sees the collision, and (per the issue comment) the `contradictions` detector is
also defeated. This exploration grounds the three-option decision; it prescribes
nothing.

## Current State (file:line)

- Create-only guard: `src/openkos/cli/main.py:1024-1033`, in `_stage_derived_objects`.
  When `derived_path = bundle_dir / link_dir / f"{derived_slug}.md"` exists, the
  candidate is dropped with the issue's exact message (main.py:1028-1030) —
  stdout/stderr only, no state written.
- Slug: `_slugify(extraction.title)` (main.py:844-854) — purely from the
  LLM-extracted title, no source qualifier, no collision-avoidance loop.
- Concept write: `write_exclusive` (create-only) at main.py:1023/1164. The Source
  page for the current raw file is always written regardless (main.py:1156-1161);
  only derived candidates hit the guard.
- `provenance:` is set once at creation (main.py:1041, single-entry list). No
  ingest-time path ever appends to an existing concept's provenance.

## Provenance / reconciliation flow

- The only provenance-union logic lives in `model/okf.py:296`, invoked exclusively
  through `bundle/merge.py::plan_merge` (merge.py:75) — the human-driven
  `duplicates → adjudicate → merge` flow. `merge.py` has zero LLM references —
  merge is fully deterministic and reversible (`MergeLedgerEntry` + `unmerge`).
- A reusable deterministic provenance-union primitive already exists, but only
  reachable via the explicit merge path, never from `ingest`.

## Determinism boundary (AGENTS.md:42 / :28)

- (a) fully deterministic. (c) fully deterministic (different slug/id + same
  create-only write). (b) is deterministic ONLY if scoped to provenance-append
  (reusing merge-style union); the issue's unresolved "how is the body treated"
  question, if answered with LLM-reconcile, introduces a NEW bounded LLM seam
  inside the currently-deterministic ingest write path, with no confirm gate over
  "let an LLM rewrite this existing file's body."

## Entity-resolution + contradictions consumption (confirms the issue)

- `resolution/candidates.py::find_candidates` (candidates.py:119, 127-195) needs
  ≥2 distinct on-disk documents of the same type to emit a group
  (`if len(members) < 2: continue`). One collapsed concept (b) never appears; a
  distinct second file (c) does, forming a HIGH/LOW candidate group for `adjudicate`.
- `resolution/contradiction.py::_candidate_pairs` (contradiction.py:149-185) pairs
  come from typed edges between two distinct concept ids — same ≥2 requirement.
- Confirms: option (b)'s single enriched file PERMANENTLY defeats both
  duplicates/adjudicate AND contradictions (two raw statements never get compared);
  option (c) restores visibility to both.

## Slug disambiguation for (c)

- No existing uniquification helper in `src/openkos` (grep `-2|suffix|uniquify|
  disambiguat` → no hits). New code: a collision loop replacing the single
  `.exists()` check (main.py:1024), plus an id-scheme decision (numeric
  `claude-code-2` vs source-qualified `claude-code--<source_slug>`).

## Breaking-change / re-ingest surface

- Today ingest is idempotent for byte-identical raw re-ingest (main.py:1122-1130);
  derived objects always hit the create-only guard, so repeat ingests are
  side-effect-free on existing concepts (D5, main.py:1025-1027).
- (b) REVERSES the D5 invariant — a new source can mutate an existing, possibly
  hand-edited concept, with no reversibility ledger (unlike merge).
- (c) preserves D5 exactly; only the new candidate gets a new file.
- (a) changes no bundle shape; adds a durable record via
  `bundle_log.insert_log_entry` (log.py:45), already rendered by `status` under
  "Recent activity". `status`'s "Needs attention" (main.py:4149-4168) is computed
  live from disk today (no persisted ledger), so (a) adds one (small, reuses the
  existing durable log.md mechanism).

## Effort

- (a) Low — one log call at the skip site + optional status/lint wiring.
- (b) Medium (provenance-only) to High (LLM body reconciliation) — needs a new
  non-merge provenance-union entry point, a body policy decision, and a new
  reversibility story; permanently forecloses contradictions/duplicates for the pair.
- (c) Medium — new collision loop + id scheme, docs/tests; resolution/merge
  machinery already generalizes (title/id-based), likely no changes there.

## Recommendation

**(c) distinct file + resolution, paired with (a)'s audit logging.** Rationale:
(c) is the only option with zero determinism risk, routes the decision through the
existing reversible human-in-the-loop merge flow ("human curates, engine
maintains"), does not foreclose the multi-source-synthesis end state (still
reachable via `adjudicate --apply` using okf.py:290-317 union), and is the only
option restoring visibility to BOTH duplicates/adjudicate and contradictions —
decisive given the issue comment. (a) alone makes the drop auditable but leaves the
information loss unresolved. (a)+(c): (c) fixes correctness; (a)'s log also records
WHY ingest picked a disambiguated slug.

## Hybrid feasibility

(b) and (c) are not strictly exclusive (a future "auto-merge on HIGH-tier trust"
could layer onto (c)'s adjudicate flow), but that's out of scope now.

## Ready for proposal

Yes — once the maintainer picks among (a) / (b) / (c) / (a)+(c). For (c), a
secondary id-scheme decision remains (numeric `-2` vs source-qualified).
