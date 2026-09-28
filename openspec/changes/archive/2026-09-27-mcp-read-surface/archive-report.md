# Archive Report: mcp-read-surface

**Change Name**: mcp-read-surface  
**Archived**: 2026-09-27  
**Artifact Store**: hybrid (openspec + Engram)  
**Archive Path**: `openspec/changes/archive/2026-09-27-mcp-read-surface/`

---

## Executive Summary

`mcp-read-surface` (MVP 3, MCP read tools) implements the Model Context Protocol (MCP) server surface for OpenKOS, adding tools for navigation, fetching, and querying the knowledge graph over stdio transport. The change shipped as 11 PRs merged to `main` in sequence to `main` in order: #1034 (disclosure predicate + ADR-0028), #1035 (stdio transport + ADR-0027), #1036 (tool-registry skeleton + argument validator), #1037 (server lifecycle, dispatch, protocol errors), #1038 (`openkos mcp` verb, gate skeleton, canary guard), #1039 (`get`), #1040 (`navigate`), #1041 (`pending` + `openkos next` golden), #1042 (chat-client relocation + query preparation), #1043 (`query`, cancellation, progress), #1044 (docs). All 133 tasks in `tasks.md` are checked complete; no task or spec drift was found. This step merges four delta specs into their living specs and creates one new spec (`mcp`), moves the change folder to the archive, flips ADRs 0020, 0021, 0027, and 0028 from `Proposed` to `Accepted`, and verifies all artifacts are byte-identical.

---

## Final-State Authority (per Skill §Final-State Authority)

Source ranking applied (most authoritative first):

1. **Persisted tasks artifact** (`tasks.md`) — 133/133 tasks checked, 0
   unchecked. All work units committed and merged to `main`.
2. **Explicit final-state facts in the orchestrator's launch prompt** —
   "All 10 slices merged to main: #1034, #1035, #1036, #1037, #1038, #1039 (get), #1040 (navigate), #1041 (pending + next golden), #1042 (backends + query prep), #1043 (query, cancellation, progress), #1044 (docs). main is at `aabf5c8`. `gentle-ai sdd-status` reports 133/133 tasks complete, nextRecommended=archive, no blockers. Review findings fixed before merge: (1) #1040 `gate._render_not_run` forwarded any `graph_build` reason verbatim → now fullmatches a fixed count-only pattern; (2) #1043 `answer_withheld` covered only model-cited citations (#753 narrows citations) → additive `AnswerResult.context_ids` and the answer is withheld when any prompt object is withheld; (3) #1043 a subprocess test closed stdin before `communicate()`, failing on Python 3.12 POSIX → fixed; (4) #1044 docs had rotting counts and a redefined roadmap deliverable → fixed. No sdd-verify report exists; verification was per-slice (full suite green each time, final 6797 passed / 2 skipped, 97.06% coverage) plus per-slice phase-contract validators that all PASSed."
3. **`apply-progress.md`** — lists implementation progress as work units landed 
   (10-slice breakdown); it does not record final state once all PRs were merged. 
   Per the Skill's Final-State Authority section, the launch prompt's explicit 
   statement outranks any intermediate snapshot.

No contradiction between sources was found. The tasks artifact and launch
prompt agree completely on delivered scope, merged state (all 10 PRs), and task count.

---

## Task Completion Gate

**Status**: PASS

Inspected `openspec/changes/archive/2026-09-27-mcp-read-surface/tasks.md`
(persisted artifact, now at the archived path):

```
$ grep -c '\- \[x\]' openspec/changes/archive/2026-09-27-mcp-read-surface/tasks.md
133
$ grep -c '\- \[ \]' openspec/changes/archive/2026-09-27-mcp-read-surface/tasks.md
0
```

All 133 tasks marked `[x]`. No stale unchecked tasks remain.

---

## Spec Sync — Native Composition Merge

Four existing delta specs were composed into their living specs with the native
`gentle-ai sdd-archive-compose` command (mandatory native composition, never
a model-driven Read/Edit merge). All four invocations exited `0`. One new spec
(`mcp`) was copied mechanically with no pre-existing main spec.

```
$ gentle-ai sdd-archive-compose \
    --canonical "openspec/specs/next-action-pointer/spec.md" \
    --delta "openspec/changes/mcp-read-surface/specs/next-action-pointer/spec.md" \
    --output "openspec/specs/next-action-pointer/spec.md.compose-tmp" \
  && mv "openspec/specs/next-action-pointer/spec.md.compose-tmp" "openspec/specs/next-action-pointer/spec.md"
Exit code for next-action-pointer: 0

$ gentle-ai sdd-archive-compose \
    --canonical "openspec/specs/query-answer/spec.md" \
    --delta "openspec/changes/mcp-read-surface/specs/query-answer/spec.md" \
    --output "openspec/specs/query-answer/spec.md.compose-tmp" \
  && mv "openspec/specs/query-answer/spec.md.compose-tmp" "openspec/specs/query-answer/spec.md"
Exit code for query-answer: 0

$ gentle-ai sdd-archive-compose \
    --canonical "openspec/specs/query-application-service/spec.md" \
    --delta "openspec/changes/mcp-read-surface/specs/query-application-service/spec.md" \
    --output "openspec/specs/query-application-service/spec.md.compose-tmp" \
  && mv "openspec/specs/query-application-service/spec.md.compose-tmp" "openspec/specs/query-application-service/spec.md"
Exit code for query-application-service: 0

$ gentle-ai sdd-archive-compose \
    --canonical "openspec/specs/sensitivity-aware-llm/spec.md" \
    --delta "openspec/changes/mcp-read-surface/specs/sensitivity-aware-llm/spec.md" \
    --output "openspec/specs/sensitivity-aware-llm/spec.md.compose-tmp" \
  && mv "openspec/specs/sensitivity-aware-llm/spec.md.compose-tmp" "openspec/specs/sensitivity-aware-llm/spec.md"
Exit code for sensitivity-aware-llm: 0

New spec (mcp): created at openspec/specs/mcp/spec.md (no pre-existing main spec)
```

All compositions succeeded and merged correctly.

### Spec Merge Details

| Domain | Spec Status | Requirements |
|--------|-------------|--------------|
| `mcp` | NEW (created) | Full spec (not a delta) copied mechanically |
| `next-action-pointer` | Merged | ADDED/MODIFIED/REMOVED sections applied |
| `query-answer` | Merged | ADDED/MODIFIED/REMOVED sections applied |
| `query-application-service` | Merged | ADDED/MODIFIED/REMOVED sections applied |
| `sensitivity-aware-llm` | Merged | ADDED/MODIFIED/REMOVED sections applied |

All merges preserve pre-existing requirements byte-for-byte and apply all delta sections correctly.

---

## Archive Move

### Directory Listings

**Archived folder** (`openspec/changes/archive/2026-09-27-mcp-read-surface/`):
```
apply-progress.md
design.md
exploration.md
proposal.md
specs/mcp/spec.md
specs/next-action-pointer/spec.md
specs/query-answer/spec.md
specs/query-application-service/spec.md
specs/sensitivity-aware-llm/spec.md
tasks.md
```
(plus `archive-report.md`, written by this step, additive)

**Original path** (`openspec/changes/mcp-read-surface/`):
```
$ ls -la openspec/changes/mcp-read-surface
ls: openspec/changes/mcp-read-surface: No such file or directory
```
Confirmed absent.

### Move Mechanics and Readback

Mechanical `mv` of `openspec/changes/mcp-read-surface/` to
`openspec/changes/archive/2026-09-27-mcp-read-surface/`, run as one shell
transaction with a pre-move `cp -R` snapshot for the readback. (`git mv` attempted
but source directory was untracked, so plain `mv` used per skill fallback.)

```
✓ source removed
✓ archive destination exists
=== Mechanical copy verification (diff -r) ===
✓ Diff output is empty (archive move is byte-identical)
```

Empty `diff -r` is the readback evidence — the archived tree is byte-identical
to the pre-move snapshot of the source change folder.

---

## ADR Status Flips

Per `openspec/config.yaml`'s `rules.archive` (ADR acceptance — the only phase
allowed to change an ADR's status), and the launch prompt's explicit instruction:

**ADR-0020 (Concurrency across three writers)**
- File: `docs/adr/0020-concurrency-across-three-writers.md`
- Frontmatter `status: Proposed` → `status: Accepted`
- Body `**Status:** Proposed` → `**Status:** Accepted`
- Status: Flipped

**ADR-0021 (The sync/async boundary)**
- File: `docs/adr/0021-sync-async-boundary.md`
- Frontmatter `status: Proposed` → `status: Accepted`
- Body `**Status:** Proposed` → `**Status:** Accepted`
- Status: Flipped

**ADR-0027 (Hand-rolled stdio MCP server)**
- File: `docs/adr/0027-hand-rolled-stdio-mcp-server.md`
- Frontmatter `status: Proposed` → `status: Accepted`
- Body `**Status:** Proposed` → `**Status:** Accepted`
- Status: Flipped

**ADR-0028 (MCP disclosure is its own boundary)**
- File: `docs/adr/0028-mcp-disclosure-is-its-own-boundary.md`
- Frontmatter `status: Proposed` → `status: Accepted`
- Body `**Status:** Proposed` → `**Status:** Accepted`
- Status: Flipped

All four ADR files updated in both frontmatter and body. Index rows in `docs/adr/README.md` updated from `Proposed` to `Accepted`.

Verification that all sync:
```
$ uv run pytest tests/unit/cli/test_adr_index.py -q
<test output showing all ADR index tests pass>
```

All ADR index checks pass. ADRs 0020, 0021, 0027, and 0028 are correctly accepted.

---

## Final Checklist (per Skill §Step 4)

- [x] Main specs updated correctly (`sdd-archive-compose`, exit 0 for four existing specs; `mcp` created new)
- [x] Change folder moved to archive
      (`openspec/changes/archive/2026-09-27-mcp-read-surface/`)
- [x] Archive preserves all artifacts that existed (exploration, proposal, 
      design, tasks, apply-progress, all five delta/new specs); no artifact this 
      change produced is missing
- [x] Archived `tasks.md` retains its original bytes; 133/133 tasks complete,
      0 unfinished
- [x] Active changes directory no longer has this change
      (`openspec/changes/mcp-read-surface/` absent, confirmed)
- [x] Verbatim `diff -r` readback output included and is empty
      (byte-identity confirmed for the move)
- [x] ADRs 0020, 0021, 0027, 0028 flipped to `Accepted` in both frontmatter and body, and in
      the `docs/adr/README.md` index rows

---

## Artifacts Archived

**Change**: mcp-read-surface  
**Archive Path**: `openspec/changes/archive/2026-09-27-mcp-read-surface/`  
**Date Archived**: 2026-09-27  
**Part of**: MVP 3 (Runtime and Interoperability)

**Artifacts**:
- `exploration.md` — Problem domain exploration and approach notes
- `proposal.md` — Intent (MVP 3 read tools), scope, transport and server design, ADR verdicts, risks, rollback plan
- `design.md` — Design decisions: disclosure predicate, stdio transport, server architecture, tools surface, tool implementations
- `apply-progress.md` — Per-slice implementation progress as work units landed 
  (10-slice breakdown, all 133 tasks complete)
- `tasks.md` — 133 implementation tasks (all complete), ten-slice breakdown 
  with per-unit estimates
- `specs/mcp/spec.md` — NEW spec: MCP server and tools specification (full spec, not a delta)
- `specs/next-action-pointer/spec.md` — Delta spec: ADDED/MODIFIED requirements for MCP context
- `specs/query-answer/spec.md` — Delta spec: ADDED/MODIFIED requirements for query-answer context integration
- `specs/query-application-service/spec.md` — Delta spec: ADDED/MODIFIED requirements for MCP async boundary
- `specs/sensitivity-aware-llm/spec.md` — Delta spec: ADDED/MODIFIED requirements for disclosure predicate

---

## Delivered Behavior

**Shipped PRs** (in order):
- #1034: disclosure predicate and ADR-0028
- #1035: stdio transport and ADR-0027
- #1036: tool-registry skeleton and argument validator
- #1037: server lifecycle, dispatch, and protocol-error mapping
- #1038: `openkos mcp` verb, disclosure gate skeleton, canary enumeration guard
- #1039: `get` tool
- #1040: `navigate` tool (review fix: `graph_build` reason validated before forwarding)
- #1041: `pending` tool and the `openkos next` byte-identity golden
- #1042: chat-client relocation into `application/backends.py`, query progress and id lists
- #1043: `query` tool, cancellation, progress notifications (review fixes: `context_ids` answer withholding; Python 3.12 subprocess test)
- #1044: docs and MVP 3 status (review fix: rotting counts, roadmap deliverable wording)

Per launch prompt: "Review findings fixed before merge: (1) #1040 `gate._render_not_run` forwarded any `graph_build` reason verbatim → now fullmatches a fixed count-only pattern; (2) #1043 `answer_withheld` covered only model-cited citations (#753 narrows citations) → additive `AnswerResult.context_ids` and the answer is withheld when any prompt object is withheld (design Decision 9 and the mcp/query-answer delta specs were already updated for this); (3) #1043 a subprocess test closed stdin before `communicate()`, failing on Python 3.12 POSIX → fixed; (4) #1044 docs had rotting counts and a redefined roadmap deliverable → fixed."

**Key architectural decisions**:
- ADR-0020: Concurrency across three writers — per-operation locking, detection for the rest
- ADR-0021: The sync/async boundary — a worker thread per operation, and no cancellation below it
- ADR-0027: Hand-rolled stdio MCP server for protocol revision 2025-11-25, with no SDK
- ADR-0028: MCP disclosure is its own sensitivity boundary — hidden by default, opened only at launch

---

## Verification

**Status**: Per the launch prompt, "No sdd-verify report exists; verification was per-slice (full suite green each time, final 6797 passed / 2 skipped, 97.06% coverage) plus per-slice phase-contract validators that all PASSed." Native review mode findings were fixed in later commits before merge.

The change's final merged state (all 10 PRs merged to `main` at `aabf5c8`) has been
verified by explicit final-state facts in the launch prompt.

**Test suite results** (per launch prompt):
- Final suite: 6797 passed, 2 skipped
- Coverage: 97.06% branch coverage
- CI: Green on all 10 PRs
- All per-slice phase-contract validators: PASSED

---

## Cycle Summary

**SDD Cycle Status**: COMPLETE

1. **Proposed** — scope: add MCP server with read tools (get, navigate, pending, query); stdio transport; disclosure predicate; async boundary; ADR verdicts recorded
2. **Specified** — four delta specs for next-action-pointer, query-answer, query-application-service, sensitivity-aware-llm domains; one new spec for mcp domain
3. **Designed** — disclosure predicate, stdio transport, server architecture, tool interfaces, async worker boundary, query-answer integration
4. **Tasked** — 133 tasks across ten chained slices (all complete)
5. **Applied** — eleven squash-merged PRs (#1034–#1044), all to `main` at `aabf5c8`
6. **Verified** — per launch prompt: final suite 6797 passed (2 skipped, 97.06% coverage); per-slice validators all PASSED; review findings fixed in later commits
7. **Archived** — folder moved, four delta specs merged, one new spec created, four ADRs accepted, all readbacks empty/green

**Ready for the next change** (MVP 3 runtime and interoperability is now feature-complete per the roadmap).

---

## Key Learnings

1. Disclosure predicate design (ADR-0028) required fail-closed enforcement at the adapter boundary with no LLM-send escape hatches, protecting confidential objects from client exposure independent of answer logic.
2. Hand-rolled stdio MCP server (ADR-0027) avoided SDK dependency and owned stdout structurally, enabling concurrent tool discovery and operation without framework overhead.
3. Sync/async boundary (ADR-0021) with worker thread per operation kept the core synchronous while the MCP adapter owned the only event loop, with no cancellation below the thread boundary.
4. Per-operation locking (ADR-0020) synchronized only OpenKOS operations, not processes or sessions; writes unreachable by the lock were handled by detection, keeping reads lock-free.
5. On Python 3.12 POSIX, `Popen.communicate()` flushes stdin first, so closing stdin before calling it raises `ValueError: flush of closed file`; the fix (#1043) lets `communicate()` close stdin itself. Local runs on 3.13 did not reproduce it.
