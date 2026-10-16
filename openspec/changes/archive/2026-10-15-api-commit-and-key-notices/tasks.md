# Tasks: Surface The Confidential-Commit Notice And The Insecure-Key Warning

- [x] 1. Add `committed_confidential` to `Applied` and set it from the commit
  outcome through the API wiring, for every commit path.
- [x] 2. Add the `insecure_key` quote notice to all three quote kinds, reusing
  `insecure_key_warning`.
- [x] 3. Test both, including the negative cases (nothing confidential, no commit,
  `https`, no key).
- [x] 4. Regenerate `tests/unit/api/api_surface.json` (classified additive).
- [x] 5. Modify the requirements in `specs/` and merge the deltas into
  `openspec/specs/`.
