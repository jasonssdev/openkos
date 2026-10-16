# Tasks: Report A Never-Existing Decision Key As Unknown

- [x] 1. Add `curate_queue.key_was_queued`, a read-only any-status key lookup.
- [x] 2. Return `Stale("unknown_key")` from `preview_decision` and
  `apply_decision` when no row ever carried the key; keep `row_resolved` for a
  settled row and for an unreadable queue.
- [x] 3. Test the service and the API paths, including a fabricated key that
  writes nothing.
- [x] 4. Regenerate `tests/unit/api/api_surface.json` (classified additive).
- [x] 5. Modify the requirements in `specs/` and merge the deltas into
  `openspec/specs/`.
