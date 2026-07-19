# Delta Specs: align-user-journey-and-init-coverage

## Domain: cli-init (ADDED Requirements)

### Requirement: TTY next-step hint exact-string coverage
`tests/unit/cli/test_init.py` MUST include a test that simulates a TTY on a fresh empty directory and asserts the verbatim `init` next-step hint (`src/openkos/cli/main.py:129`), not a substring.

#### Scenario: Fresh empty dir, simulated TTY, default input
- GIVEN an empty `tmp_path` directory and `_simulate_tty(monkeypatch)`
- WHEN `runner.invoke(app, ["init"], input="\n")` runs
- THEN `exit_code == 0`
- AND `result.output` contains exactly: `Next: run \`openkos ingest <path>\` to import your first source.`

## Domain: user-journey-doc (ADDED Requirements)

Each requirement below is a documentation-accuracy contract for `docs/user-journey.md`. Verifiability = no residual overclaim text for X remains in the file, AND an accurate MVP-1 statement for Y is present, AND (where applicable) the richer flow is fenced under explicit later-MVP framing.

### Requirement: Compile/review/commit steps reflect actual MVP-1 pipeline
The doc MUST NOT claim the compiler drafts an LLM summary, creates multiple concept types (person/decision pages), applies freshness stamps, or performs a distinct "commit" phase as today's default. It MUST describe MVP-1's actual pipeline (null-compiler, single `Source` concept, verbatim embed) and MUST fence the multi-concept/summarizing pipeline (loop diagram, Steps 2-4 as currently worded, lines 23-30 and 77-117) as later-MVP.

#### Scenario: Loop diagram and Steps 2-4 reworded
- GIVEN the published doc
- WHEN Steps 2-4 and the loop diagram are inspected
- THEN they describe the null-compiler/single-Source/verbatim-embed MVP-1 flow
- AND any richer compile/multi-concept behavior is explicitly labeled later-MVP

### Requirement: Review panel matches actual MVP-1 confirmation UX
The doc MUST NOT depict a `[Y]es/[e]dit/[n]o` review panel with a high-water-mark reclassification note (lines 82-101) as MVP-1 default behavior. It MUST describe MVP-1's actual plain yes/no confirm and MUST fence the edit-option/reclassification panel as later-MVP.

#### Scenario: Review prompt reworded
- GIVEN the "Review and confirm" section
- WHEN the confirm prompt is inspected
- THEN it shows a plain yes/no confirm, not a three-way `[e]dit` option
- AND no high-water-mark reclassification claim appears as current behavior

### Requirement: No non-existent `--sensitivity` CLI flag claimed
The doc MUST NOT show `--sensitivity` as a working `ingest` CLI flag (lines 69, 75, 106). It MUST state that sensitivity is set via `openkos.yaml` (`default_sensitivity`) only in MVP-1, and MAY fence a future `--sensitivity` flag as later-MVP.

#### Scenario: All `--sensitivity` command examples removed or fenced
- GIVEN every ingest command example in the doc
- WHEN searched for `--sensitivity`
- THEN no example presents it as a working MVP-1 flag
- AND the doc states sensitivity is set via `openkos.yaml`

### Requirement: No batch/glob ingest claimed as MVP-1
The doc MUST NOT claim `openkos ingest ./inbox/` or glob ingest (`./inbox/*.txt`, line 74) works today. It MUST state MVP-1 ingest is one-source-at-a-time and MUST fence batch/glob ingest as later-MVP.

#### Scenario: Batch ingest line fenced
- GIVEN the "Batch is optional" bullet
- WHEN inspected
- THEN it states MVP-1 ingest is single-source-only
- AND batch/glob ingest is explicitly marked later-MVP

### Requirement: Query example citations match actual MVP-1 concept shape
The doc MUST NOT cite `bundle/concepts/stoicism.md` (lines 134, 93) as an extracted concept page produced by MVP-1. It MUST use a citation path consistent with MVP-1's single `Source` concept output.

#### Scenario: Citation chain reworded
- GIVEN the query example's "Sources:" block
- WHEN inspected
- THEN the cited path reflects the actual MVP-1 `Source`-concept output shape, not an extracted `concepts/*` topic page

### Requirement: No hand-edit content-hash reconciliation claimed
The doc MUST NOT claim the engine detects and reconciles hand-edited concept files via content hash on the next command (lines 150-158) as MVP-1 behavior, unless this is confirmed implemented. It MUST state actual MVP-1 behavior for hand-edited files and MUST fence any unconfirmed reconciliation behavior as later-MVP.

#### Scenario: Editing-by-hand section reworded
- GIVEN the "Editing by hand" section
- WHEN inspected
- THEN it does not assert automatic content-hash reconciliation as current behavior
- AND any such reconciliation is explicitly marked later-MVP

### Requirement: "Text only" statement framed as intent, not enforced allowlist
The doc MUST NOT state `.txt`/`.md` as an enforced input allowlist (line 193) if MVP-1 does not reject other extensions. It MUST state MVP-1's actual behavior as an accepted-format intent (plain text is the target use case; other files may be accepted with a UTF-8 embed or binary-fallback note), without claiming enforcement that does not exist.

#### Scenario: MVP-1 scope line reworded
- GIVEN the "MVP 1 scope" section
- WHEN inspected
- THEN it does not claim `.txt`/`.md` is an enforced allowlist
- AND it states the actual accept/fallback behavior for other file types

### Requirement: No unconditional git auto-commit claim
The doc MUST NOT state changes are automatically committed to git as unconditional MVP-1 behavior (lines 110, 113, 117: "committed", "every change is a git commit") unless this is confirmed implemented. It MUST describe MVP-1's actual persistence/commit behavior and MUST fence any automatic git-commit-on-save behavior as later-MVP if unconfirmed.

#### Scenario: Commit-related claims reworded
- GIVEN Step 4 and the `--auto` success line
- WHEN inspected
- THEN no unconditional "committed to git" claim remains unless verified as actual MVP-1 behavior
- AND any unconfirmed auto-commit behavior is fenced as later-MVP

### Requirement: Document-wide MVP-1/later-MVP internal consistency
After all above requirements are applied, the doc AS A WHOLE MUST be internally consistent: every remaining claim describes actual MVP-1 behavior, and every richer-flow claim is unambiguously fenced as later-MVP, with no contradiction between sections (e.g., Step 3 fixed while Step 4, the query example, or "Two ways to work" table still imply the old behavior).

#### Scenario: Full-document consistency pass
- GIVEN the fully reframed `docs/user-journey.md`
- WHEN all sections are cross-checked against each other
- THEN no section contradicts another on what is MVP-1 vs later-MVP
- AND no cited line from the proposal's scope list (23-30, 69, 74, 75, 77-79, 82-113, 115-117, 119-141, 150-158, 191) still overclaims

## Non-Goals (explicit)
- No CLI/product code changes (no `--sensitivity` flag added).
- No automated doc-vs-CLI-help consistency check (future follow-up only).
- Lines 17, 146 (lint), 180 (forget), and the `--auto` row are already honest and MUST remain unchanged.
