# Design: Harden `openkos init` Workspace Creation

## Technical Approach

Seven follow-ups land as surgical edits to three source modules plus the test snapshot helper. Three are behavioral (#3, #4, #8) and get spec scenarios; the rest are behavior-neutral refactor/doc (#5, #6, #7) or test-only (#2). All refusal logic stays centralized in `config._refusal_conditions` — the single place refusals are defined — so `is_workspace` and `refusal_reason` extend together, never drift. Verified against HEAD (config.py:42-66, bundle.py:25-31, okf.py:38-57, main.py:47-67).

## Architecture Decisions

### Decision: #3 symlink escape — pre-flight refusal, not non-following mkdir

**Choice**: Add a `path.is_symlink()` branch to `_refusal_conditions` for `raw`/`bundle`, ordered BEFORE the existing `exists()/is_dir()` checks, yielding `marks_workspace=False` (a symlink is not "a workspace", same class as the plain-file fifth condition). `is_symlink()` does not follow the link, so it catches symlink-to-dir, symlink-to-file, and broken symlinks that `exists()`/`is_dir()` silently follow.
**Alternatives considered**: A non-following `mkdir` (check/`os.mkdir` guard) in Phase B at the two `mkdir` sites (main.py:59 `raw_dir`, bundle.py:25 `bundle_dir`).
**Rationale**: The proposal's stated intent is a clean pre-flight refusal with a specific stderr reason and the "writes nothing" guarantee. A Phase-B guard would surface as the generic `failed while creating the workspace` write-failure message, conflate a deliberate symlink with an incidental race, and split the guard across two `mkdir` sites instead of the one refusal generator. Residual Phase-A→B TOCTOU (a symlink swapped in after pre-flight) is the pre-existing accepted D2 limitation, not newly introduced.
**ADR?** No. ADR gate (`openspec/config.yaml` rules.design) requires a decision that is BOTH significant AND hard-to-reverse. This adds one branch to an existing five-condition generator inside an established pattern; it is trivially reversible. Recorded here as an inline design decision, not an ADR.

### Decision: #4 conformance vs. I/O boundary

**Choice**: In `check_conformance`, move `path.read_text(encoding="utf-8")` OUT of the `try`. `OSError` (permission/race) and `UnicodeDecodeError` (bad encoding) propagate out of `check_conformance` as inspection failures. Keep the `try` around only `frontmatter.loads(text)`, recording its parse failure as the rule-1 violation.
**Alternatives considered**: Keep the broad `except Exception` but tag messages; return a richer result object.
**Rationale**: The finding is conflation — an unreadable/undecodable file is not a conformance verdict. Narrowing the boundary keeps the `list[str]` return meaning "conformance violations only" and lets a caller distinguish "could not inspect" from "violates OKF".

### Decision: #6 named refusal-condition type — `NamedTuple`

**Choice**: `class RefusalCondition(NamedTuple): marks_workspace: bool; reason: str`; return type becomes `Iterator[RefusalCondition]`.
**Alternatives considered**: frozen `@dataclass` (breaks the existing `for marks_workspace, _ in ...` unpacking, forces consumer rewrites); bare type alias `tuple[bool, str]` (names the type but not the fields — the actual complaint).
**Rationale**: `NamedTuple` names both fields at zero runtime cost and stays tuple-unpackable, so `is_workspace`/`refusal_reason` consumers work unchanged.

## Contract: `check_conformance`

`check_conformance(bundle_dir: Path) -> list[str]` returns ONLY conformance violations (rules 1-2). It MAY raise `OSError` or `UnicodeDecodeError` when a candidate `.md` cannot be read or decoded; those are never appended as violations.

## Other Changes

- **#5 shared exclusive-create helper**: `write_exclusive(path: Path, content: str) -> None` in a new leaf module `src/openkos/fsio.py`, imported by both `bundle.py` and `config.py` (avoids a `bundle`→`config` layering dependency). Body is verbatim: `with path.open("x", encoding="utf-8", newline="") as f: f.write(content)`. Preserving `newline=""` and `encoding="utf-8"` keeps byte-identity — output is unchanged, so the pinned byte-identical-template spec still holds. Replaces bundle.py:26,30 and config.py:108,123.
- **#8 stray-bundle message**: reword config.py:66 to name the likely crashed-init cause and remediation, e.g. `"'{name}/' already exists and is not empty; a previous init may have crashed mid-write — inspect and remove it before retrying"`. Still `yield True` (marks_workspace) — refusal classification unchanged; assert on a stable substring.
- **#7 docstring**: correct config.py:50-53. True rationale for the non-workspace conditions (plain-file + new symlink): they convert what Phase B would otherwise report as a caught-but-generic `OSError` write failure (main.py:63 DOES catch it — `FileExistsError` is an `OSError`) into a specific pre-flight refusal, preserving the writes-nothing guarantee. The "uncaught FileExistsError" claim is false; rewrite it.
- **#2 snapshot**: `_snapshot` in tests records directories, not only `is_file()` entries, so refusal tests detect stray directory creation (e.g. a write through a symlink). Strengthens existing no-partial-output assertions.

## File Changes

| File | Action | Findings |
|------|--------|----------|
| `src/openkos/config.py` | Modify | #3, #6, #7, #8, #5-import |
| `src/openkos/bundle/bundle.py` | Modify | #3 (via pre-flight), #5-import |
| `src/openkos/model/okf.py` | Modify | #4 |
| `src/openkos/fsio.py` | Create | #5 helper |
| `tests/unit/cli/test_init.py` | Modify | #2 + #3/#4/#8 RED scenarios |

## Testing Strategy

| Layer | What | Approach |
|-------|------|----------|
| Unit | #3 symlink `raw`/`bundle` refused pre-flight, nothing written outside root | RED test; assert exit 1 + snapshot (incl. dirs, #2) unchanged |
| Unit | #4 unreadable/undecodable `.md` raises, malformed frontmatter → violation | Two RED tests at the boundary |
| Unit | #8 stray non-empty `bundle/` message names cause + remediation | RED, stable substring |
| Unit | #5/#6/#7 behavior-neutral | Existing suite + byte-identity tests guard |

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, or executable-file classification boundary. #3 is a filesystem symlink-escape (write-outside-root) fix; its adversarial cases live in the #3 RED tests above, not in the git/shell matrix.

## Migration / Rollout

No migration. No persisted state. Rollback = revert the change commit(s), independently per finding.

## Open Questions

- [ ] Delta spec `specs/workspace-init/spec.md` was not present at design time — design follows the proposal's behavioral contract; cross-check #3/#4/#8 scenarios against it once written (dependency, not a blocker).
