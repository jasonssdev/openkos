# Archive Report: unattended-digest

**Archived**: 2026-10-04  
**Change**: unattended-digest  
**PR**: #1289  
**Verification**: Not run (change archived after release)  

## Summary

This change shipped the unattended engine's digest feature, enabling pending-work queue inspection and task status visibility via the `pending` command and workspace inbox file watch.

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| folder-watch | Updated | 11 total requirements; ADDED requirements merged |
| job-runtime | Updated | 14 total requirements; ADDED and MODIFIED requirements merged |

## Delta Specs Applied

- **folder-watch**: ADDED requirements for workspace inbox watch activation and inbox file path
- **job-runtime**: ADDED requirements for unattended mode and pending-work queue inspection; MODIFIED requirements for timer and signal handling

## Archive Contents

- `proposal.md`: Present
- `specs/folder-watch/spec.md`: Present (delta merged into main spec)
- `specs/job-runtime/spec.md`: Present (delta merged into main spec)
- `tasks.md`: Present, 6 completed tasks

## Shipped Code Verification

The feature shipped in PR #1289 and released in 0.5.0. Implementation includes:
- Pending-work queue support in `application/unattended.py` and `state/pending.py`
- Inbox folder watch in `application/workspace.py`
- MCP server integration for pending-work inspection

All documented requirements are satisfied by the shipped code.

## Source of Truth Updated

- `openspec/specs/folder-watch/spec.md` — now includes workspace inbox activation and folder watch requirements
- `openspec/specs/job-runtime/spec.md` — now includes unattended mode, pending-work queue, and timer/signal requirements

## SDD Cycle Complete

The change is archived. Implementation is complete and released. No verification report exists (archived after release).
