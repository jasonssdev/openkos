# Tasks: Typed Relationships (Slice 1)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~530 total (PR1 ~180, PR2 ~150, PR3 ~90, PR4 ~110) |
| 400-line budget risk | Low (per-PR; each PR well under 400) |
| Chained PRs recommended | Yes |
| Suggested split | PR1 → PR2 → PR3 → PR4 (stacked on tracker) |
| Delivery strategy | auto-chain |
| Chain strategy | feature-branch-chain |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: Low

Tracker branch `feat/typed-relationships`; only the tracker merges to `main` (human checkpoint). PR1 base = tracker; PR2 base = PR1 branch; PR3 base = PR2 branch; PR4 base = PR3 branch.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `relations:` vocabulary + encode/decode + §9 rule | PR1 | `uv run pytest tests/unit/model/` | N/A — pure codec/parsing, no I/O side effects to exercise beyond unit tests | Revert `relations.py`, `okf.py` relations hunks; no callers yet |
| 2 | `openkos relate` CLI verb | PR2 | `uv run pytest tests/unit/cli/test_relate.py` | `openkos relate <src> references <tgt> --auto` against a scratch workspace | Revert `relate` command + log line; PR1 codec untouched |
| 3 | Graph projection typed edges | PR3 | `uv run pytest tests/unit/graph/` | `openkos query`/graph rebuild against a fixture bundle with `relations:` | Revert `sqlite_graph.py` typed-edge pass; untyped `_LINK_RE` path unaffected |
| 4 | Merge guard refusal | PR4 | `uv run pytest tests/unit/bundle/test_merge.py tests/unit/cli/test_merge.py` | `openkos merge <survivor> <absorbed>` against a fixture with typed relations | Revert `find_relation_conflicts` + Phase A hook; merge behaves as before this slice |

## Phase 1 (PR1 → tracker): Relation Vocabulary + Codec + §9 Rule

Spec: `relations:` Frontmatter Field Shape; OKF §9 Conformance — `relations:` Field Shape.

- [x] 1.1 RED: `tests/unit/model/test_relations.py` — `SEEDED_RELATION_TYPES` contains the 8 KOM defaults, module has zero `openkos` imports.
- [x] 1.2 GREEN: create `src/openkos/model/relations.py` mirroring `types.py::REGISTRY` shape with `SEEDED_RELATION_TYPES`.
- [x] 1.3 RED: `tests/unit/model/test_okf.py` — `encode_relations`/`decode_relations` round-trip a well-formed `relations: [{target, type}]` list (Scenario: well-formed entry parses).
- [x] 1.4 RED: `decode_relations` raises `ValueError` on newline in `target`/`type` (Scenario: newline rejected).
- [x] 1.5 RED: absent `relations:` key decodes to empty list, no error (Scenario: absent key is valid).
- [x] 1.6 RED: `encode_relations` sorts entries by `(target, type)` and strips `.md`/canonicalizes target deterministically.
- [x] 1.7 GREEN: implement `encode_relations`/`decode_relations` in `src/openkos/model/okf.py`, mirroring `encode_merged_from`/`decode_merged_from`; insert `relations` key after `provenance`, before `merged_from`.
- [x] 1.8 RED: `check_conformance` reports a violation for malformed `relations:` shape in the existing `f"{path}: {message}"` form (Scenario: malformed relations entry reported).
- [x] 1.9 RED: regression — `check_conformance` output for documents without `relations:` is byte-identical before/after this rule (Scenario: byte-identical output when relations absent).
- [x] 1.10 RED: well-formed `relations:` passes conformance with no violation.
- [x] 1.11 GREEN: wire the additive §9 rule into `check_conformance` in `okf.py`, gated on `scan.metadata` containing `relations`.
- [x] 1.12 DOC: author `docs/adr/0004-typed-relationships-frontmatter.md` recording frontmatter storage over roadmap alt, seeded-open vocabulary (WARN not reject), and merge-REFUSE-in-slice-1 with rewiring deferred to slice 2.
- [x] 1.13 VERIFY: `uv run pytest tests/unit/model/` green; `ruff check` and `mypy --strict` clean on `relations.py` and `okf.py`.

## Phase 2 (PR2 → PR1): `relate` CLI Verb

Spec: `relate` CLI Verb Writes A Typed Relation; Seeded-But-Extensible Relation Vocabulary; Target Containment Consistent With Existing Verbs.

- [ ] 2.1 RED: `tests/unit/cli/test_relate.py` — successful `relate a references b` (confirmed/`--auto`) appends `{target: b, type: references}` to `a`'s `relations:`, updates log.md (Scenario: successful relate writes into source frontmatter).
- [ ] 2.2 RED: missing target fails closed — no write, non-zero exit, clear error (Scenario: missing target fails closed).
- [ ] 2.3 RED: missing source fails closed — no write, non-zero exit, clear error (Scenario: missing source fails closed).
- [ ] 2.4 RED: non-TTY without `--auto` refuses, nothing written (Scenario: non-TTY without --auto refuses).
- [ ] 2.5 RED: known relation type accepted silently; unknown type accepted with WARN to stderr; empty/whitespace type rejected with no write (Scenarios: known/unknown/empty type).
- [ ] 2.6 RED: traversal-shaped id (`../../evil`) for source or target is refused, no write (Threat Matrix: path traversal).
- [ ] 2.7 RED: `source == target` is rejected before any write.
- [ ] 2.8 RED: duplicate `(target, rel)` relate call is idempotent (no duplicate entry).
- [ ] 2.9 GREEN: implement `relate` command in `src/openkos/cli/main.py`, mirroring the `forget` verb scaffold: Phase A `require_workspace` + `_resolve_concept_path` on both source/target, preview/confirm/`--auto`/`review:` gate; Phase B writes source concept file + `**Relate**` log.md line only (no index.md).
- [ ] 2.10 VERIFY: `uv run pytest tests/unit/cli/test_relate.py` green; `ruff check` and `mypy --strict` clean on `main.py` relate hunk.

## Phase 3 (PR3 → PR2): Graph Projection Typed Edges

Spec: Edge `relation_type` Populated From Frontmatter `relations:`.

- [ ] 3.1 RED: `tests/unit/graph/test_sqlite_graph.py` — typed relation edge carries its `relation_type` (`relations: [{target: concepts/x, type: depends_on}]` → edge `relation_type == "depends_on"`) (Scenario: typed relation edge carries its relation_type).
- [ ] 3.2 RED: untyped-link edge (no `relations:` key, bundle-relative link in body) keeps `relation_type` `NULL`, unchanged (Scenario: untyped-link edge remains NULL relation_type).
- [ ] 3.3 RED: regression — existing untyped `_LINK_RE` edge extraction output is byte-identical for objects without `relations:` (before/after this change).
- [ ] 3.4 RED: `relations:` entry whose target is unknown/unresolvable is dropped (consistent with existing untyped-link drop-if-unknown behavior); dedup key is `(source_id, target_id, relation_type)` so typed and untyped edges between the same pair coexist as two rows.
- [ ] 3.5 GREEN: extend `build_graph` in `src/openkos/graph/sqlite_graph.py` with a second pass populating `Edge.relation_type` from decoded `relations:`, keyed on `(source, target, relation_type)`; leave the existing untyped `_LINK_RE` pass unchanged.
- [ ] 3.6 GREEN: extend `_SELECT_EDGES_SQL` ORDER BY to include `relation_type` (NULLs first).
- [ ] 3.7 VERIFY: `uv run pytest tests/unit/graph/` green; `ruff check` and `mypy --strict` clean on `sqlite_graph.py`.

## Phase 4 (PR4 → PR3): Merge Guard — Fail-Closed Refusal

Spec: Non-Silent Guard For Edge-Bearing Merge.

- [ ] 4.1 RED: `tests/unit/bundle/test_merge.py` — `find_relation_conflicts` detects the absorbed object's own outbound `relations:` entries as non-empty (Scenario: merge of an object with outbound relations surfaces a guard).
- [ ] 4.2 RED: `find_relation_conflicts` detects an inbound typed relation from another bundle file targeting the absorbed object (Scenario: merge of an inbound relation target surfaces a guard).
- [ ] 4.3 RED: merge of an object with no typed relations (inbound or outbound) proceeds unaffected, per existing merge requirements (Scenario: merge of an object with no typed relations proceeds unaffected).
- [ ] 4.4 GREEN: implement `find_relation_conflicts(absorbed_id, files, absorbed_text)` in `src/openkos/bundle/merge.py`, mirroring `links.find_inbound_link_rewrites` for the outbound/inbound scan.
- [ ] 4.5 RED: `tests/unit/cli/test_merge.py` — `merge` Phase A refuses (fail-closed) with a clear error and non-zero exit before any write when `find_relation_conflicts` reports a hit; no rewiring attempted.
- [ ] 4.6 GREEN: wire the refusal hook into `cli/main.py::merge` Phase A, after resolving survivor/absorbed and the existing `other_files` bundle scan.
- [ ] 4.7 VERIFY: `uv run pytest tests/unit/bundle/test_merge.py tests/unit/cli/test_merge.py tests/unit/cli/test_merge_roundtrip.py tests/unit/cli/test_unmerge.py` green; `ruff check` and `mypy --strict` clean on `merge.py` and `main.py` merge hunk.

## Cross-PR Regression Guard

- [ ] 5.1 After each PR: `uv run pytest` (full suite) green; branch/line coverage stays at or above the existing ~90% branch bar.
- [ ] 5.2 After each PR: `build_concept`/LLM ingest output remains byte-identical (no `relations:` emitted by `build_concept`) — assert via existing ingest golden/regression tests.
