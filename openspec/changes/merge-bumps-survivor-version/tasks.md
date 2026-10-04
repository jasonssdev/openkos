# Tasks: merge-bumps-survivor-version

- [x] 1.1 Red tests: `build_merged_document` bumps the survivor's own version
      (1->2, 4->5, missing/non-int/bool->2, absorbed value ignored); a
      merged-then-attached concept continues the count; two CLI merges then
      `unmerge --to` restore the survivor byte for byte. (`okf`)
- [x] 1.2 `okf._next_version` shared by `build_merged_document` and
      `build_attached_document`. (`okf`)
- [x] 1.3 Update the merged-document golden and the merge characterization
      goldens by exactly the `version` line. (`okf`)
- [x] 2.1 `docs/knowledge-object-model.md`: the counter's rule. (`docs`)
- [ ] 3.1 Archive: merge the delta into
      `openspec/specs/entity-resolution-merge/spec.md`.
