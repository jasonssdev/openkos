# Tasks: Reference-Aware Forget + Tombstones

## Review Workload Forecast

Estimated changed lines: ~260-330.

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Low

Work units: none (single PR). Test:
`pytest tests/unit/bundle/test_references.py tests/unit/cli/test_forget.py -q`.
Harness: `openkos forget <id>` on scratch workspace.
Rollback: `bundle/references.py` + `forget()` in `cli/main.py`.

## Phase 1: `bundle/references.py`

- [x] 1.1 RED: inbound link → `InboundReference(kind="link")`
- [x] 1.2 RED: inbound typed relation → correct `relation_type`
- [x] 1.3 RED: referrer with link+relation → two records
- [x] 1.4 RED: no refs → `[]`
- [x] 1.5 RED: fenced-code link ignored (fence-mask holds)
- [x] 1.6 RED: self-reference excluded (target key absent from `files`)
- [x] 1.7 GREEN: new `bundle/references.py` — frozen `InboundReference(referrer_id, kind, relation_type)`
- [x] 1.8 GREEN: `find_inbound_references(files, *, target_id)` — links via `find_inbound_link_rewrites`
- [x] 1.9 GREEN: + relations via `find_inbound_relation_rewrites`/`okf.decode_relations`, filter `target == target_id`
- [x] 1.10 REFACTOR: match `links.py`/`relations.py` conventions; no `graph` import
- [x] 1.11 Verify: `test_references.py` green; no regression in `test_links.py`/`test_relations.py`

## Phase 2: `forget` scan, tombstone

- [x] 2.1 RED: no refs, no `supersedes` → succeeds, no extra lines
- [x] 2.2 RED: tombstone = `**Tombstone** (HH:MM:SSZ): Removed [<title>](/<id>.md) (id: <id>).`, title pre-delete
- [x] 2.3 RED: idempotent re-run doesn't duplicate/overwrite prior tombstone
- [x] 2.4 RED: outbound `supersedes` X→Y → preview names Y; self-supersedes excluded
- [x] 2.5 RED: no outbound `supersedes` → no resurrection line
- [x] 2.6 GREEN: add `force: bool = typer.Option(False, "--force")` to `forget`
- [x] 2.7 GREEN: after `_resolve_concept_path`, read text/title; build `other_files` (mirror `merge`'s rglob, ~L1330-1337)
- [x] 2.8 GREEN: decode concept's own `relations:`, collect outbound `supersedes` (excl. self)
- [x] 2.9 GREEN: replace `**Forget**` line with the tombstone format
- [x] 2.10 GREEN: one preview line per resurrection target when non-empty
- [x] 2.11 Verify: tombstone/supersedes/resurrection tests green

## Phase 3: `--force` gate

- [x] 3.1 RED: inbound link, no `--force` → refuses, zero writes
- [x] 3.2 RED: inbound relation, no `--force` → same refusal
- [x] 3.3 RED: inbound link + `--force` → proceeds; referrer link left dangling
- [x] 3.4 RED: inbound relation + `--force` → same, dangling
- [x] 3.5 GREEN: call `find_inbound_references(other_files, target_id=canonical_id)`; add preview lines (id, kind, type)
- [x] 3.6 GREEN: gate (preview→confirm): exit iff `inbound_refs and not force`
- [x] 3.7 Verify: inbound-* tests green; no writes on refusal

## Phase 4: orthogonality

- [x] 4.1 RED: TTY + `--force` alone → gate bypassed, confirm still prompts
- [x] 4.2 RED: `--force --auto` + refs → neither gate blocks
- [x] 4.3 RED: non-TTY, `--force`, no `--auto`/refs → refuses at confirm gate
- [x] 4.4 GREEN: `force`/`auto` read independently; confirm block untouched
- [x] 4.5 Verify: force×auto×TTY matrix green

## Phase 5: path-safety + integration

- [x] 5.1 RED: invalid `concept_id` refused by `_resolve_concept_path` before any read (no scan)
- [x] 5.2 GREEN: confirm ordering unchanged; adjust only if 5.1 fails
- [x] 5.3 Verify: `test_forget.py` fully green
- [x] 5.4 Verify: `pytest -q` full suite green
- [x] 5.5 REFACTOR: refresh `forget`'s docstring for the new behavior
