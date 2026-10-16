# Tasks: Enforce The API_VERSION Bump In CI

- [x] 1. Add `base_snapshot` and `unbumped_breaking` to `tests/unit/api/surface.py`.
- [x] 2. Test the three acceptance cases (breaking without a bump fails, with a
  bump passes, additive without a bump passes) against temporary git
  repositories, plus the merge-base, absent-ref, absent-snapshot and unrelated
  history cases.
- [x] 3. Add the base-branch bump test to `tests/unit/api/test_api_surface.py`,
  skipping locally and failing under `OPENKOS_REQUIRE_API_BASE_REF=1`.
- [x] 4. Check out the full history in the CI test job and set
  `OPENKOS_REQUIRE_API_BASE_REF=1`.
- [x] 5. Modify the two requirements in `specs/stable-python-api/spec.md` and
  merge the delta into `openspec/specs/stable-python-api/spec.md`.
