# Tasks: Walk An Identity Group In `curate`

Tracking issue: #1438. One PR (`Closes #1438`), forecast about 600 authored lines
(source about 250, tests about 350); the figure is advisory.

- [ ] 1. RED: `tests/unit/cli/test_curate_group_walk.py` — a group of three with
  two marked commits through `commit_merge_group_decision`, keeps the third and
  records the ruling; `a` marks all; `d` keeps all distinct; Enter skips; an
  invalid answer is re-asked; a cross-type pair asks the type and the member of
  that type survives; a cross-type group with two candidates of the chosen type
  asks the name; a group whose row does not offer `merge_group` keeps the old skip
  line; drift exits 3; an interrupted group exits 1 and names the kept merges.
- [ ] 2. GREEN: the walk step in `cli/curate.py`; the two old tests that pinned the
  skip lines rewritten to the new behavior.
- [ ] 3. `docs/cli.md` Identity paragraph, CHANGELOG.
- [ ] 4. Gates and mutation checks.
- [ ] 5. Archive: merge the deltas, move the folder, write `archive-report.md`.
