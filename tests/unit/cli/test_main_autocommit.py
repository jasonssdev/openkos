"""Unit tests for auto-commit-writes (git-lifecycle Slice 2): the shared
`_autocommit`/`_commit_has_confidential` helpers in `cli/main.py`, and their
wiring into the six mutating verbs (`ingest`, `forget`, `relate`, `merge`,
`unmerge`, `reconcile`).

Phase 1 (below) exercises `_autocommit`/`_commit_has_confidential` directly
-- no CLI invocation needed, since both take an explicit `root: Path`
argument (mirrors `openkos.vcs.git`'s own `cwd`-explicit primitives).

Phase 2/3/4 exercise the six verbs end to end via `CliRunner`, mirroring
`test_init.py`'s Slice 1 git-lifecycle section: real temporary git
repositories (`tmp_path`), git identity isolated via
`isolate_git_identity` (`GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM`), never the
host machine's real `~/.gitconfig`.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos.bundle import index as bundle_index
from openkos.cli import main
from openkos.cli.main import app
from openkos.model import okf as okf_module
from openkos.vcs import git as vcs_git
from tests.unit.vcs.conftest import isolate_git_identity

runner = CliRunner()

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MAIN_PY = _REPO_ROOT / "src" / "openkos" / "cli" / "main.py"
_GIT_PY = _REPO_ROOT / "src" / "openkos" / "vcs" / "git.py"


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------


def _init_workspace(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    *,
    name: str = "Isolated Tester",
    email: str = "tester@example.invalid",
) -> None:
    """`chdir` into `tmp_path`, isolate git identity to a SET identity, and
    run `openkos init` -- Slice 1's own git-setup step then makes its own
    initial commit, so every verb invoked afterward starts from a clean,
    git-tracked workspace with a real configured identity.

    The isolated git config lives in a SEPARATE `tmp_path_factory` dir, not
    `tmp_path` itself (mirrors `test_init.py`'s Slice 1 git tests) --
    `isolate_git_identity` writes `isolated-gitconfig-global` directly
    under whatever directory it is given, so pointing it at the workspace
    root itself would leave that config file sitting there as a forever-
    untracked file, permanently breaking every "clean tree" assertion for
    a reason unrelated to auto-commit."""
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path_factory.mktemp("git-identity-config")
    isolate_git_identity(monkeypatch, config_dir, name=name, email=email)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _ingest_source(tmp_path: Path, name: str) -> str:
    """Ingest one Source concept via `ingest --auto`, returning its
    concept-id. `name`'s file is written OUTSIDE the workspace (a sibling
    directory of `tmp_path`) -- see `_mk_ingest`'s docstring for why: only
    the `raw/` COPY `ingest` makes is ever committed, so a source file
    left sitting inside the workspace root would stay forever untracked
    and break every "clean tree" assertion downstream. Extraction is
    never mocked here -- the autouse `_default_llm` fixture below declines
    extraction, so no derived objects are staged (`derived_plans == []`)."""
    external_dir = tmp_path.parent / f"{tmp_path.name}-external"
    external_dir.mkdir(exist_ok=True)
    source = external_dir / name
    source.write_text("content", encoding="utf-8")
    result = runner.invoke(app, ["ingest", str(source), "--auto"])
    assert result.exit_code == 0
    slug = Path(name).stem
    return f"sources/{slug}"


def _write_concept(
    tmp_path: Path,
    concept_id: str,
    *,
    title: str,
    section: str = "Concepts",
    sensitivity: str | None = None,
    body: str = "Body.",
) -> None:
    """Hand-author a concept file directly under `bundle/` plus its
    matching `index.md` bullet (mirrors `test_merge.py::_write_concept`)."""
    concept_path = tmp_path / "bundle" / f"{concept_id}.md"
    concept_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: Concept", f"title: {title}"]
    if sensitivity is not None:
        lines.append(f"sensitivity: {sensitivity}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {title}")
    lines.append("")
    lines.append(body)
    lines.append("")
    concept_path.write_text("\n".join(lines), encoding="utf-8")

    link_dir, slug = concept_id.rsplit("/", 1)
    index_path = tmp_path / "bundle" / "index.md"
    index_text = index_path.read_text(encoding="utf-8")
    new_index_text = bundle_index.insert_index_entry(
        index_text,
        section=section,
        link_dir=link_dir,
        title=title,
        slug=slug,
        description=f"{title}.",
    )
    index_path.write_text(new_index_text, encoding="utf-8")


def _seed_commit(tmp_path: Path, rel_paths: list[str], message: str = "seed") -> None:
    """Commit `rel_paths` directly via `commit_paths` -- test setup only,
    used to pre-track a concept file that a later DESTRUCTIVE verb (e.g.
    `merge`) will delete, since `git add -- <path>` on an untracked,
    already-deleted path fails (mirrors real usage, where every prior
    mutating verb already auto-committed its own writes)."""
    vcs_git.commit_paths(tmp_path, rel_paths, message)


def _commit_count(root: Path) -> int:
    result = vcs_git._run(["git", "log", "--format=%H"], cwd=root)
    return len([line for line in result.stdout.splitlines() if line])


def _last_commit_subject(root: Path) -> str:
    result = vcs_git._run(["git", "log", "-1", "--format=%s"], cwd=root)
    return result.stdout.strip()


def _last_commit_files(root: Path) -> set[str]:
    result = vcs_git._run(["git", "show", "--name-only", "--format=", "-1"], cwd=root)
    return {line for line in result.stdout.splitlines() if line}


def _status_porcelain(root: Path) -> str:
    return vcs_git._run(["git", "status", "--porcelain"], cwd=root).stdout


@pytest.fixture(autouse=True)
def _default_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Protect every test in this module from a real Ollama network call:
    `ingest`'s extraction step always declines (mirrors
    `test_ingest.py::_default_llm`)."""

    class _FakeLLM:
        def chat(self, messages: object) -> str:
            return '{"extract": false}'

    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient", lambda *args, **kwargs: _FakeLLM()
    )


# --------------------------------------------------------------------------
# Phase 1: Helper Foundation -- `_autocommit` / `_commit_has_confidential`
# --------------------------------------------------------------------------


def test_autocommit_not_a_repo_warns_and_returns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """1.1: `repo_root(root) is None` -> stderr WARNING, no exception, no
    commit attempted (spec "Not a git repository")."""
    main._autocommit(tmp_path, ["file.txt"], "openkos: test")

    captured = capsys.readouterr()
    assert "WARNING" in captured.err
    assert "not a git repository" in captured.err.lower()


def test_autocommit_identity_unset_warns_no_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """1.2: identity unset -> stderr WARNING, no commit attempted (spec
    "Git identity unset")."""
    vcs_git.init_repo(tmp_path)
    isolate_git_identity(monkeypatch, tmp_path)
    (tmp_path / "file.txt").write_text("content", encoding="utf-8")

    main._autocommit(tmp_path, ["file.txt"], "openkos: test")

    captured = capsys.readouterr()
    assert "WARNING" in captured.err
    assert "identity" in captured.err.lower()
    assert _commit_count(tmp_path) == 0


@pytest.mark.parametrize("exc_type", [vcs_git.GitError, OSError])
def test_autocommit_commit_error_warns_no_raise(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    exc_type: type[Exception],
) -> None:
    """1.3: `commit_paths` raising `GitError`/`OSError` -> caught, stderr
    WARNING, no raise (spec "Commit step raises a git error")."""
    vcs_git.init_repo(tmp_path)
    isolate_git_identity(
        monkeypatch, tmp_path, name="Tester", email="t@example.invalid"
    )

    def _raise(root: Path, rel_paths: list[str], message: str) -> None:
        raise exc_type("boom")

    monkeypatch.setattr("openkos.cli.main.vcs_git.commit_paths", _raise)

    main._autocommit(tmp_path, ["file.txt"], "openkos: test")

    captured = capsys.readouterr()
    assert "WARNING" in captured.err
    assert "auto-commit did not complete" in captured.err


def test_autocommit_success_scoped_add_single_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1.4: success path -> exactly one commit via scoped `git add --
    <paths>`, never `-A`/`-a`, and an unrelated dirty file elsewhere is
    left untouched (spec "Post-Phase-B Commit", "Scoped Staging Only")."""
    vcs_git.init_repo(tmp_path)
    isolate_git_identity(
        monkeypatch, tmp_path, name="Tester", email="t@example.invalid"
    )
    (tmp_path / "file.txt").write_text("content", encoding="utf-8")
    (tmp_path / "unrelated.txt").write_text("dirty", encoding="utf-8")

    main._autocommit(tmp_path, ["file.txt"], "openkos: test commit")

    assert _commit_count(tmp_path) == 1
    assert _last_commit_subject(tmp_path) == "openkos: test commit"
    assert _last_commit_files(tmp_path) == {"file.txt"}
    assert "unrelated.txt" in _status_porcelain(tmp_path)


def _write_frontmatter_file(
    tmp_path: Path, rel_path: str, *, sensitivity: str | None
) -> None:
    file_path = tmp_path / rel_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: Concept", "title: X"]
    if sensitivity is not None:
        lines.append(f"sensitivity: {sensitivity}")
    lines.extend(["---", "", "# X", ""])
    file_path.write_text("\n".join(lines), encoding="utf-8")


def test_commit_has_confidential_true_skips_reserved_and_missing(
    tmp_path: Path,
) -> None:
    """1.5: a single staged concept file with `sensitivity: confidential`
    -> `True`; `bundle/index.md`/`bundle/log.md`, `raw/**`, and a missing
    path are all skipped without raising."""
    _write_frontmatter_file(
        tmp_path, "bundle/concepts/secret.md", sensitivity="confidential"
    )
    (tmp_path / "bundle").mkdir(exist_ok=True)
    (tmp_path / "bundle" / "index.md").write_text("no frontmatter", encoding="utf-8")
    (tmp_path / "bundle" / "log.md").write_text("no frontmatter", encoding="utf-8")
    (tmp_path / "raw").mkdir(exist_ok=True)
    (tmp_path / "raw" / "source.txt").write_text("raw content", encoding="utf-8")

    result = main._commit_has_confidential(
        tmp_path,
        [
            "bundle/concepts/secret.md",
            "bundle/index.md",
            "bundle/log.md",
            "raw/source.txt",
            "bundle/concepts/missing.md",
        ],
    )

    assert result is True


def test_commit_has_confidential_multiple_files_still_true(tmp_path: Path) -> None:
    """1.6: multiple confidential-ranked staged files -> the helper still
    returns `True` (the at-most-once NOTICE guarantee is exercised at the
    `_autocommit` level below, since the helper is a pure predicate)."""
    _write_frontmatter_file(
        tmp_path, "bundle/concepts/one.md", sensitivity="confidential"
    )
    _write_frontmatter_file(
        tmp_path, "bundle/concepts/two.md", sensitivity="confidential"
    )

    result = main._commit_has_confidential(
        tmp_path, ["bundle/concepts/one.md", "bundle/concepts/two.md"]
    )

    assert result is True


@pytest.mark.parametrize("sensitivity", ["public", "private", None])
def test_commit_has_confidential_false_when_below_confidential(
    tmp_path: Path, sensitivity: str | None
) -> None:
    """1.7: no confidential-ranked content staged -> `False` (public,
    private, and missing `sensitivity` all rank below confidential)."""
    _write_frontmatter_file(tmp_path, "bundle/concepts/one.md", sensitivity=sensitivity)

    result = main._commit_has_confidential(tmp_path, ["bundle/concepts/one.md"])

    assert result is False


def test_commit_has_confidential_skips_unparseable_file_without_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A staged file that exists but fails frontmatter parsing (`ValueError`)
    is skipped, not raised -- a later, confidential file in the same list
    still makes the predicate `True`."""
    _write_frontmatter_file(tmp_path, "bundle/concepts/broken.md", sensitivity=None)
    _write_frontmatter_file(
        tmp_path, "bundle/concepts/secret.md", sensitivity="confidential"
    )
    real_load_frontmatter = okf_module.load_frontmatter

    def _flaky_load_frontmatter(text: str) -> tuple[dict[str, object], str]:
        if "broken" in text:
            raise ValueError("malformed frontmatter")
        return real_load_frontmatter(text)

    (tmp_path / "bundle" / "concepts" / "broken.md").write_text(
        "broken sentinel content", encoding="utf-8"
    )
    monkeypatch.setattr(
        "openkos.cli.main.okf.load_frontmatter", _flaky_load_frontmatter
    )

    result = main._commit_has_confidential(
        tmp_path, ["bundle/concepts/broken.md", "bundle/concepts/secret.md"]
    )

    assert result is True


def test_autocommit_single_confidential_file_emits_exactly_one_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Spec "Single confidential file triggers exactly one notice"."""
    vcs_git.init_repo(tmp_path)
    isolate_git_identity(
        monkeypatch, tmp_path, name="Tester", email="t@example.invalid"
    )
    _write_frontmatter_file(
        tmp_path, "bundle/concepts/secret.md", sensitivity="confidential"
    )

    main._autocommit(tmp_path, ["bundle/concepts/secret.md"], "openkos: test")

    captured = capsys.readouterr()
    assert captured.err.count("NOTICE") == 1
    assert "confidential" in captured.err


def test_autocommit_multiple_confidential_files_still_one_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Spec "Multiple confidential files still emit only one notice"."""
    vcs_git.init_repo(tmp_path)
    isolate_git_identity(
        monkeypatch, tmp_path, name="Tester", email="t@example.invalid"
    )
    _write_frontmatter_file(
        tmp_path, "bundle/concepts/one.md", sensitivity="confidential"
    )
    _write_frontmatter_file(
        tmp_path, "bundle/concepts/two.md", sensitivity="confidential"
    )

    main._autocommit(
        tmp_path,
        ["bundle/concepts/one.md", "bundle/concepts/two.md"],
        "openkos: test",
    )

    captured = capsys.readouterr()
    assert captured.err.count("NOTICE") == 1


def test_autocommit_no_confidential_content_no_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Spec "No confidential content, no notice"."""
    vcs_git.init_repo(tmp_path)
    isolate_git_identity(
        monkeypatch, tmp_path, name="Tester", email="t@example.invalid"
    )
    _write_frontmatter_file(tmp_path, "bundle/concepts/one.md", sensitivity="private")

    main._autocommit(tmp_path, ["bundle/concepts/one.md"], "openkos: test")

    captured = capsys.readouterr()
    assert "NOTICE" not in captured.err


# --------------------------------------------------------------------------
# Phase 2/3: per-verb wiring
# --------------------------------------------------------------------------


@dataclass
class _VerbSpec:
    name: str
    success_args: list[str]
    decline_args: list[str]
    message_re: re.Pattern[str]


def _mk_ingest(tmp_path: Path) -> _VerbSpec:
    """`src` is written OUTSIDE the workspace (a sibling directory) --
    `ingest` copies it into `raw/`, so writing it INSIDE `tmp_path` would
    leave an extra, forever-untracked file at the workspace root that no
    verb ever cleans up, permanently breaking the "clean tree" assertion
    for a reason unrelated to auto-commit."""
    name = "note.txt"
    external_dir = tmp_path.parent / f"{tmp_path.name}-ingest-source"
    external_dir.mkdir(exist_ok=True)
    src = external_dir / name
    src.write_text("content", encoding="utf-8")
    return _VerbSpec(
        name="ingest",
        success_args=["ingest", str(src), "--auto"],
        decline_args=["ingest", str(src)],
        message_re=re.compile(r"^openkos: ingest note\.txt \(\+0 concepts\)$"),
    )


def _mk_forget(tmp_path: Path) -> _VerbSpec:
    source_id = _ingest_source(tmp_path, "note.txt")
    return _VerbSpec(
        name="forget",
        success_args=["forget", source_id, "--auto"],
        decline_args=["forget", source_id],
        message_re=re.compile(rf"^openkos: forget {re.escape(source_id)}$"),
    )


def _mk_relate(tmp_path: Path) -> _VerbSpec:
    source_id = _ingest_source(tmp_path, "src.txt")
    target_id = _ingest_source(tmp_path, "dst.txt")
    return _VerbSpec(
        name="relate",
        success_args=["relate", source_id, "related_to", target_id, "--auto"],
        decline_args=["relate", source_id, "related_to", target_id],
        message_re=re.compile(
            rf"^openkos: relate {re.escape(source_id)} -> "
            rf"{re.escape(target_id)} \(related_to\)$"
        ),
    )


def _mk_merge(tmp_path: Path) -> _VerbSpec:
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    _seed_commit(
        tmp_path,
        [
            "bundle/concepts/survivor.md",
            "bundle/concepts/absorbed.md",
            "bundle/index.md",
        ],
        "seed concepts",
    )
    return _VerbSpec(
        name="merge",
        success_args=["merge", "concepts/survivor", "concepts/absorbed", "--auto"],
        decline_args=["merge", "concepts/survivor", "concepts/absorbed"],
        message_re=re.compile(
            r"^openkos: merge concepts/absorbed into concepts/survivor$"
        ),
    )


def _mk_unmerge(tmp_path: Path) -> _VerbSpec:
    merge_spec = _mk_merge(tmp_path)
    merge_result = runner.invoke(app, merge_spec.success_args)
    assert merge_result.exit_code == 0
    return _VerbSpec(
        name="unmerge",
        success_args=["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"],
        decline_args=["unmerge", "concepts/survivor", "concepts/absorbed"],
        message_re=re.compile(r"^openkos: unmerge concepts/absorbed$"),
    )


def _mk_set_sensitivity(tmp_path: Path) -> _VerbSpec:
    """`ingest` seeds `sensitivity: private` (workspace default); raising
    to `confidential` needs no `--allow-downgrade`, so this is a plain
    raise -- the shared-contract cases below don't need the downgrade gate,
    which is exercised on its own in `test_set_sensitivity.py`."""
    source_id = _ingest_source(tmp_path, "note.txt")
    return _VerbSpec(
        name="set-sensitivity",
        success_args=["set-sensitivity", source_id, "confidential", "--auto"],
        decline_args=["set-sensitivity", source_id, "confidential"],
        message_re=re.compile(
            rf"^openkos: set-sensitivity {re.escape(source_id)} -> confidential$"
        ),
    )


def _mk_reconcile(tmp_path: Path) -> _VerbSpec:
    id_a = _ingest_source(tmp_path, "a.txt")
    id_b = _ingest_source(tmp_path, "b.txt")
    return _VerbSpec(
        name="reconcile",
        success_args=["reconcile", id_a, id_b, "--auto"],
        decline_args=["reconcile", id_a, id_b],
        message_re=re.compile(
            rf"^openkos: reconcile {re.escape(id_a)} <-> {re.escape(id_b)}$"
        ),
    )


_VERB_BUILDERS: list[Callable[[Path], _VerbSpec]] = [
    _mk_ingest,
    _mk_forget,
    _mk_relate,
    _mk_merge,
    _mk_unmerge,
    _mk_reconcile,
    _mk_set_sensitivity,
]


@pytest.mark.parametrize("builder", _VERB_BUILDERS, ids=lambda b: b.__name__[4:])
def test_verb_success_commits_once_and_leaves_clean_tree(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    builder: Callable[[Path], _VerbSpec],
) -> None:
    """2.1-2.3, 3.1-3.3: each mutating verb's successful Phase B produces
    exactly one auto-commit with the pinned message, leaving a clean tree
    (spec: "Ingest commits...", "Forget commits...", "Remaining mutating
    verbs...")."""
    _init_workspace(tmp_path, tmp_path_factory, monkeypatch)
    spec = builder(tmp_path)
    before = _commit_count(tmp_path)

    result = runner.invoke(app, spec.success_args)

    assert result.exit_code == 0, result.stderr
    assert _commit_count(tmp_path) == before + 1
    subject = _last_commit_subject(tmp_path)
    assert spec.message_re.match(subject), subject
    assert vcs_git.is_clean(tmp_path) is True


@pytest.mark.parametrize("builder", _VERB_BUILDERS, ids=lambda b: b.__name__[4:])
def test_verb_declined_confirm_makes_no_commit(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    builder: Callable[[Path], _VerbSpec],
) -> None:
    """2.4, 3.4: the confirm gate refusing (non-TTY, no `--auto`) means
    Phase B never runs, so `_autocommit` is never invoked and no new
    commit exists (spec "Declined confirm gate makes no commit")."""
    _init_workspace(tmp_path, tmp_path_factory, monkeypatch)
    spec = builder(tmp_path)
    before = _commit_count(tmp_path)

    result = runner.invoke(app, spec.decline_args)

    assert result.exit_code == 1
    assert _commit_count(tmp_path) == before


@pytest.mark.parametrize("builder", _VERB_BUILDERS, ids=lambda b: b.__name__[4:])
def test_verb_unrelated_dirty_file_left_untouched(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    builder: Callable[[Path], _VerbSpec],
) -> None:
    """2.5, 3.5: an unrelated pre-existing dirty file elsewhere in the
    workspace is never swept into the verb's auto-commit -- scoped `git
    add -- <paths>`, never `-A`/`-a` (spec "Unrelated dirty file is left
    untouched")."""
    _init_workspace(tmp_path, tmp_path_factory, monkeypatch)
    spec = builder(tmp_path)
    (tmp_path / "unrelated.txt").write_text(
        "pre-existing dirty content", encoding="utf-8"
    )

    result = runner.invoke(app, spec.success_args)

    assert result.exit_code == 0, result.stderr
    assert "unrelated.txt" not in _last_commit_files(tmp_path)
    assert "unrelated.txt" in _status_porcelain(tmp_path)
    assert (tmp_path / "unrelated.txt").read_text(encoding="utf-8") == (
        "pre-existing dirty content"
    )


@pytest.mark.parametrize("builder", _VERB_BUILDERS, ids=lambda b: b.__name__[4:])
def test_verb_not_a_repo_warns_but_exits_normal_success(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    builder: Callable[[Path], _VerbSpec],
) -> None:
    """2.6, 3.6: `repo_root(root) is None` at auto-commit time -> stderr
    WARNING, verb still exits its normal success code, canonical writes
    already landed (spec "Not a git repository")."""
    _init_workspace(tmp_path, tmp_path_factory, monkeypatch)
    spec = builder(tmp_path)
    monkeypatch.setattr("openkos.cli.main.vcs_git.repo_root", lambda root: None)

    result = runner.invoke(app, spec.success_args)

    assert result.exit_code == 0
    assert "warning" in result.stderr.lower()


@pytest.mark.parametrize("builder", _VERB_BUILDERS, ids=lambda b: b.__name__[4:])
def test_verb_identity_unset_warns_but_exits_normal_success(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    builder: Callable[[Path], _VerbSpec],
) -> None:
    """2.6, 3.6: identity unset at auto-commit time -> stderr WARNING,
    verb still exits its normal success code, no commit made by
    `_autocommit` (spec "Git identity unset")."""
    _init_workspace(tmp_path, tmp_path_factory, monkeypatch)
    spec = builder(tmp_path)
    before = _commit_count(tmp_path)
    monkeypatch.setattr("openkos.cli.main.vcs_git.has_git_identity", lambda root: False)

    result = runner.invoke(app, spec.success_args)

    assert result.exit_code == 0
    assert "warning" in result.stderr.lower()
    assert _commit_count(tmp_path) == before


@pytest.mark.parametrize("builder", _VERB_BUILDERS, ids=lambda b: b.__name__[4:])
def test_verb_commit_error_warns_but_exits_normal_success(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    builder: Callable[[Path], _VerbSpec],
) -> None:
    """2.6, 3.6: `commit_paths` raising -> stderr WARNING, verb still
    exits its normal success code, canonical writes intact (spec "Commit
    step raises a git error")."""
    _init_workspace(tmp_path, tmp_path_factory, monkeypatch)
    spec = builder(tmp_path)

    def _raise(root: Path, rel_paths: list[str], message: str) -> None:
        raise vcs_git.GitError("git commit failed: pre-commit hook rejected")

    monkeypatch.setattr("openkos.cli.main.vcs_git.commit_paths", _raise)

    result = runner.invoke(app, spec.success_args)

    assert result.exit_code == 0
    assert "warning" in result.stderr.lower()


# --------------------------------------------------------------------------
# Phase 4: Cross-Cutting Guards
# --------------------------------------------------------------------------


def test_reindex_never_autocommits(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """4.1: `reindex` MUST NOT call `_autocommit` -- no new commit is made
    by `reindex`, and `.openkos/*.db` never appears in any commit made by
    the six mutating verbs (spec "Exclusions and Unconditional Behavior",
    "Derived index database is never committed")."""
    _init_workspace(tmp_path, tmp_path_factory, monkeypatch)
    before = _commit_count(tmp_path)

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0, result.stderr
    assert _commit_count(tmp_path) == before

    for builder in _VERB_BUILDERS:
        spec = builder(tmp_path)
        verb_result = runner.invoke(app, spec.success_args)
        assert verb_result.exit_code == 0, verb_result.stderr
        stat_result = vcs_git._run(["git", "show", "--stat", "-1"], cwd=tmp_path)
        assert ".openkos/" not in stat_result.stdout


def _collect_call_argvs(source: str) -> list[list[ast.expr]]:
    """Every literal-list argument passed as the first positional arg to a
    call whose callee ends in `.commit_paths` or `._run` (best-effort AST
    scan, mirrors `test_layering.py`'s import-collector shape)."""
    tree = ast.parse(source)
    argvs: list[list[ast.expr]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.args:
            first = node.args[0]
            if isinstance(first, ast.List):
                argvs.append(first.elts)
    return argvs


@pytest.mark.parametrize("path", [_MAIN_PY, _GIT_PY])
def test_no_blanket_add_flags_anywhere(path: Path) -> None:
    """4.2: no `git add -A` / `git add -a` anywhere in `_autocommit`'s
    module or `commit_paths`' module -- scoped staging only."""
    source = path.read_text(encoding="utf-8")
    for argv in _collect_call_argvs(source):
        string_literals = [
            elt.value
            for elt in argv
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
        ]
        if "add" in string_literals:
            assert "-A" not in string_literals
            assert "-a" not in string_literals


def test_canonical_layer_still_does_not_import_vcs() -> None:
    """4.3: extends Slice 1's layering guard -- the canonical layer
    (`model`/`bundle`/`state`) still imports no `openkos.vcs` after
    Slice 2's wiring (design: "Dependency direction stays `cli -> vcs`;
    the canonical layer imports no `vcs`")."""
    src_root = _REPO_ROOT / "src" / "openkos"
    for layer in ("model", "bundle", "state"):
        layer_dir = src_root / layer
        for file_path in layer_dir.rglob("*.py"):
            tree = ast.parse(file_path.read_text(encoding="utf-8"))
            modules: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules.add(node.module)
            assert not any(
                module == "openkos.vcs" or module.startswith("openkos.vcs.")
                for module in modules
            ), f"{file_path} imports openkos.vcs"


def test_no_cli_flag_or_config_option_disables_autocommit(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """4.4: no CLI flag or config option exists to disable auto-commit
    (spec "No opt-out exists") -- every mutating verb's `--help` output
    carries no autocommit-disabling flag, and `config.read_config`'s
    parsed fields carry no such switch either."""
    for verb in (
        "ingest",
        "forget",
        "relate",
        "merge",
        "unmerge",
        "reconcile",
        "set-sensitivity",
    ):
        result = runner.invoke(app, [verb, "--help"])
        assert result.exit_code == 0
        lowered = result.stdout.lower()
        assert "no-commit" not in lowered
        assert "no-autocommit" not in lowered
        assert "skip-commit" not in lowered
        assert "disable-commit" not in lowered

    _init_workspace(tmp_path, tmp_path_factory, monkeypatch)
    spec = _mk_ingest(tmp_path)
    before = _commit_count(tmp_path)

    result = runner.invoke(app, spec.success_args)

    assert result.exit_code == 0
    assert _commit_count(tmp_path) == before + 1
