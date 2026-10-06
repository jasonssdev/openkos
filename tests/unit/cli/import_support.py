"""Probes shared by the `openkos import` CLI and end-to-end tests (okf-import,
#1314).

A workspace here is a real one: `openkos init` in a real git repository with a
pinned identity (`GIT_CONFIG_COUNT` is what `git config` reads back;
`GIT_AUTHOR_*` alone is not), and no model is reachable (`OLLAMA_HOST` points at
the discard port), so a test that accidentally needs one fails instead of
passing on whatever is listening locally.
"""

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos.cli.main import app
from openkos.model import okf

runner = CliRunner()

POISONED_OLLAMA = "http://127.0.0.1:9"


def pin_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the git identity and make every model call unreachable."""
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "user.name")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "openkos tests")
    monkeypatch.setenv("GIT_CONFIG_KEY_1", "user.email")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", "tests@openkos.invalid")
    monkeypatch.setenv("OLLAMA_HOST", POISONED_OLLAMA)


def git(root: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def commit_count(root: Path) -> int:
    return int(git(root, "rev-list", "--count", "HEAD"))


def tree_oid(root: Path) -> str:
    """The OID of the committed tree at `HEAD`."""
    return git(root, "rev-parse", "HEAD^{tree}")


def new_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str = "ws"
) -> Path:
    """An initialized, committed workspace; the process is left inside it."""
    pin_environment(monkeypatch)
    root = tmp_path / name
    root.mkdir()
    monkeypatch.chdir(root)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    return root


def foreign_doc(type_: str = "Concept", body: str = "# Body\n", **meta: object) -> str:
    return okf.dump_frontmatter({"type": type_, **meta}, body)


def write_text(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def small_foreign(tmp_path: Path, name: str = "foreign", **docs: str) -> Path:
    """A foreign bundle of `docs` (`a__b` stands for `a/b`), under `name`."""
    root = tmp_path / name
    root.mkdir()
    for rel, text in docs.items():
        write_text(root, rel.replace("__", "/") + ".md", text)
    return root


def import_args(
    source: Path, namespace: str = "demo", *extra: str, auto: bool = True
) -> list[str]:
    args = ["import", str(source), "--namespace", namespace, *extra]
    if auto:
        args.append("--auto")
    return args
