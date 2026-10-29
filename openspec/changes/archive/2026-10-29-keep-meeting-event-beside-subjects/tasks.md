# Tasks: keep-meeting-event-beside-subjects

Refs #1689. ADR-0085. Proposal: `proposal.md`. Spec: `specs/extraction-union-judge`.
Strict TDD is ON, runner `uv run pytest`. No test reaches a model.

- [x] 1. [TEST+IMPL] `speaker_roster`: AMI labels and the meeting detector's recurring
  speakers through one shared line reader (`_speaker_turn_label`), which
  `_transcript_shaped_text` now also uses. RED observed (collection error before the module).
- [x] 2. [TEST+IMPL] `extraction/meeting_event.py`: candidate choice by title grounding,
  participant check, frozen prompt `extraction/meeting_event` (hash `4f93593ea5bea022`,
  pinned in `tests/unit/llm/test_prompts.py`), fail-closed compose with one re-ask.
- [x] 3. [TEST+IMPL] Wiring in `extract_concept` and `extract_concept_union`: candidates from
  the framing drop, Event appended after the cap and after the backstop and pass Decisions,
  report field and failure/bounded entries, produced/retained counts. RED observed.
- [x] 4. [TEST+IMPL] `max_extraction_calls` counts two more calls.
- [x] 5. [DOC] ADR-0085 and its index row, `docs/cli.md`, CHANGELOG.
- [x] 6. [SPEC] Delta for `extraction-union-judge`, merged on archive.
- [ ] 7. [GATE] ruff check, ruff format --check, mypy, pytest --cov, eval self-tests.
