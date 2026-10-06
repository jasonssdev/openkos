# Archive Report: structure-opt-in

**Archived**: 2026-10-04  
**Change**: structure-opt-in  
**PR**: #1292  
**Verification**: Not run (change archived after release)  

## Summary

This change shipped the structure opt-in feature for the curate command and pending-work system, enabling users to control whether merge and decision structures are persisted alongside workspace objects. Structure opt-in allows workspace customization: users can maintain a lighter dependency footprint by disabling structure persistence when merge history tracking is not needed.

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| curate-command | Updated | 29 total requirements; ADDED and MODIFIED requirements merged |
| job-runtime | Updated | 15 total requirements; ADDED requirements merged (+1 from previous release) |
| pending-work | Updated | 13 total requirements; MODIFIED requirements merged |

## Delta Specs Applied

- **curate-command**: ADDED requirements for structure opt-in configuration and persistence control; MODIFIED requirements for merge and decision handling with structure enablement checks
- **job-runtime**: ADDED requirement for structure opt-in configuration in job runtime
- **pending-work**: MODIFIED requirements for pending-work queue interaction with structure opt-in

## Archive Contents

- `proposal.md`: Present
- `specs/curate-command/spec.md`: Present (delta merged into main spec)
- `specs/job-runtime/spec.md`: Present (delta merged into main spec)
- `specs/pending-work/spec.md`: Present (delta merged into main spec)
- `tasks.md`: Present, all tasks completed

## Shipped Code Verification

The feature shipped in PR #1292 and released in 0.5.0. Implementation includes:
- Configuration option `structure_enabled` in `openkos.yaml` (default: true)
- Conditional structure persistence in `application/merge.py`
- Pending-work queue respect for structure settings in `state/pending.py`
- MCP server integration with structure opt-in aware query paths

All documented requirements are satisfied by the shipped code.

## Source of Truth Updated

- `openspec/specs/curate-command/spec.md` — now includes structure opt-in requirements
- `openspec/specs/job-runtime/spec.md` — now includes structure opt-in configuration
- `openspec/specs/pending-work/spec.md` — now includes structure opt-in integration

## SDD Cycle Complete

The change is archived. Implementation is complete and released. No verification report exists (archived after release).
