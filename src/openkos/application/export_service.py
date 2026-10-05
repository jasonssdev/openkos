"""`openkos export`'s use case (okf-export, #1301; ADR-0048).

Writes a standalone OKF bundle holding only what the export boundary
admits (`sensitivity.export_boundary`), with every pointer into a withheld
object removed, and publishes it only after the staged tree passes OKF §11
conformance, a byte scan for withheld ids, and a re-read of every input.

Two steps, so the CLI can show a preview and ask between them:

- `plan_export` walks `bundle/` once, keeps the bytes of every file it read,
  and computes every output text in memory. It writes nothing.
- `publish_export` writes the plan into a dot-prefixed staging directory
  beside the target, runs the three checks, and renames the staging
  directory to the target. Any refusal removes the staging directory, so the
  target is either absent or complete.

The workspace is never written: no lock, no commit, no `log.md` entry
(`workspace-lock`: `export` is read-only). Format knowledge stays in the OKF
seam (`okf.concept_metadata`, `okf.export_document`,
`okf.check_conformance`); this module only orchestrates.
"""

import os
import re
import shutil
import tempfile
from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import unquote

from openkos import lifecycle, sensitivity
from openkos.bundle import index as bundle_index
from openkos.bundle import links as bundle_links
from openkos.bundle import log as bundle_log
from openkos.model import okf

RefusalKind = Literal["conformance", "leak", "drift"]


class ExportRefusal(Exception):
    """`publish_export` refused to publish; nothing was written at the target.

    `kind` is `conformance` or `leak` (the staged tree failed a self-check,
    exit 1) or `drift` (an input changed while the export ran, the
    retry-safe exit 3). `details` name files relative to the bundle root,
    never the staging directory's own path."""

    def __init__(self, kind: RefusalKind, details: list[str]) -> None:
        self.kind: RefusalKind = kind
        self.details = details
        super().__init__(f"{kind}: " + "; ".join(details))


@dataclass(frozen=True)
class ExportPlan:
    """Everything `publish_export` needs, computed without writing anything.

    `files` maps each output path (bundle-relative, POSIX, sorted) to its
    text. `inputs` holds the bytes of every file the plan read, and
    `walked` every concept-document path the walk saw, for the drift check.
    `withheld_ids` is every concept id in the bundle that is not exported --
    the set the leak scan looks for. `status_projected` names exported
    concepts whose deprecated status differs from disk (`openkos repair`
    fixes the workspace). `skipped` names bundle files that are not
    exported because they are not concept documents or root reserved files."""

    bundle_dir: Path
    files: Mapping[str, str]
    inputs: Mapping[Path, bytes]
    walked: frozenset[Path]
    boundary: sensitivity.ExportBoundary
    withheld_ids: frozenset[str]
    status_projected: tuple[str, ...]
    skipped: tuple[str, ...]


def _read(path: Path) -> tuple[bytes, str] | None:
    """The bytes and decoded text of `path`, or `None` if it cannot be read
    as UTF-8. A symlink is never followed: it could point outside the bundle."""
    if path.is_symlink():
        return None
    try:
        data = path.read_bytes()
        return data, data.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _skipped_files(bundle_dir: Path) -> tuple[str, ...]:
    """Every entry under `bundle_dir`, outside dot-directories, that an export
    does not carry: non-`.md` files, reserved files below the root, and the
    symlinks the bundle walk refuses to read through
    (`okf.scan_symlinked_bundle_entries`)."""
    skipped: set[str] = {
        path.relative_to(bundle_dir).as_posix()
        for path in okf.scan_symlinked_bundle_entries(bundle_dir)
    }
    for dirpath, dirnames, filenames in os.walk(bundle_dir):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        here = Path(dirpath)
        for name in sorted(filenames):
            rel = (here / name).relative_to(bundle_dir).as_posix()
            if not name.endswith(".md") or (
                name in okf.RESERVED_FILENAMES and here != bundle_dir
            ):
                skipped.add(rel)
    return tuple(sorted(skipped))


def plan_export(
    bundle_dir: Path,
    *,
    include_private: bool,
    allow_below_source: bool,
    today: date,
) -> ExportPlan:
    """Walk `bundle_dir` once and compute the export, writing nothing.

    Every concept document is judged by `sensitivity.export_boundary` over
    the metadata parsed from the very bytes kept in `inputs`. The superseded
    set is computed over the WHOLE bundle, withheld concepts included, so a
    concept superseded by a withheld one is still exported deprecated. Each
    exported concept passes through `okf.export_document` (frontmatter) and
    `links.withhold_links` (body); the root `index.md` is filtered and a
    fresh `log.md` is written for `today`."""
    inputs: dict[Path, bytes] = {}
    texts: dict[str, tuple[str, str]] = {}  # concept id -> (relative path, text)
    docs: dict[str, Mapping[str, object] | None] = {}
    walked: set[Path] = set()
    index_text: str | None = None

    for path in okf.iter_bundle_markdown(bundle_dir):
        if path.name in okf.RESERVED_FILENAMES:
            if path.parent == bundle_dir and path.name == "index.md":
                read = _read(path)
                if read is not None:
                    inputs[path] = read[0]
                    index_text = read[1]
            continue
        walked.add(path)
        concept_id = okf.concept_id_for(path, bundle_dir)
        read = _read(path)
        if read is None:
            docs[concept_id] = None
            continue
        inputs[path] = read[0]
        texts[concept_id] = (path.relative_to(bundle_dir).as_posix(), read[1])
        docs[concept_id] = okf.concept_metadata(read[1])

    boundary = sensitivity.export_boundary(
        docs, include_private=include_private, allow_below_source=allow_below_source
    )
    exported = boundary.allowed
    superseded = lifecycle.superseded_from_metadata(docs)

    files: dict[str, str] = {}
    status_projected: list[str] = []
    for concept_id in sorted(exported):
        rel, text = texts[concept_id]
        result = okf.export_document(
            text,
            allowed=exported,
            superseded=concept_id in superseded.ids,
            walk_complete=superseded.complete,
        )
        if result.status_projected:
            status_projected.append(concept_id)
        block, body = okf.split_frontmatter_verbatim(result.text, label=rel)
        new_body = bundle_links.withhold_links(
            body, file_id=concept_id, exported=exported
        )
        files[rel] = result.text if new_body is body else block + new_body

    files["index.md"] = bundle_index.filter_index_for_export(
        index_text if index_text is not None else bundle_index.render_index(),
        exported,
    )
    files["log.md"] = bundle_log.render_export_log(today)

    return ExportPlan(
        bundle_dir=bundle_dir,
        files=dict(sorted(files.items())),
        inputs=inputs,
        walked=frozenset(walked),
        boundary=boundary,
        withheld_ids=frozenset(docs) - exported,
        status_projected=tuple(status_projected),
        skipped=_skipped_files(bundle_dir),
    )


_TOKEN_RE = re.compile(r"[^\s()\[\]<>\"'`,;|{}*!]+")
"""A run of characters that can make up a path or an id: everything but
whitespace, brackets, quotes and list or table punctuation."""


def _token_ids(token: str, *, file_dir: PurePosixPath) -> set[str]:
    """The concept ids `token` could point at from a file in `file_dir`: the
    token as a bundle-relative id (leading `/` and trailing `.md` removed)
    when it contains `/` or ends in `.md`, and, when it ends in `.md`, the
    path it resolves to relative to the file. A bare word is never an id
    here, so prose cannot trip the scan by naming a root-level concept."""
    token = unquote(token.split("#", 1)[0]).rstrip(".:")
    if not token or ("/" not in token and not token.endswith(".md")):
        return set()
    ids = {token.lstrip("/").removesuffix(".md")}
    if token.endswith(".md") and not token.startswith("/"):
        parts: list[str] = []
        for part in (file_dir / token).parts:
            if part in ("", "."):
                continue
            if part == "..":
                if not parts:
                    return ids
                parts.pop()
            else:
                parts.append(part)
        ids.add("/".join(parts).removesuffix(".md"))
    return ids


def leak_findings(files: Mapping[str, str], withheld: AbstractSet[str]) -> list[str]:
    """Every `"<file>: <id>"` where an output file's text carries a withheld
    concept id as a token, in any of the forms a pointer takes: `/<id>.md`,
    `<id>.md` relative to the file, or the bare `<id>` (a YAML value, a
    relation target). Sorted and de-duplicated; empty means no leak.

    This is the proof behind the filters: it does not know which channel a
    pointer came from, so a channel the filters missed still refuses the
    export."""
    findings: set[str] = set()
    for rel, text in files.items():
        file_dir = PurePosixPath(rel).parent
        for token in _TOKEN_RE.findall(text):
            for concept_id in _token_ids(token, file_dir=file_dir) & withheld:
                findings.add(f"{rel}: {concept_id}")
    return sorted(findings)


def check_target(target: Path, *, workspace_root: Path) -> str | None:
    """Why `target` cannot receive an export, or `None` when it can.

    It must not be the workspace or anything inside it (an export there
    would put non-concept or duplicate files inside `bundle/` or beside
    `raw/`), its parent must exist, and it must be absent or an empty
    directory: an export never merges into or overwrites anything."""
    resolved = target.resolve()
    workspace = workspace_root.resolve()
    if resolved == workspace or resolved.is_relative_to(workspace):
        return f"{target} is inside the workspace"
    if not resolved.parent.is_dir():
        return f"the parent of {target} does not exist"
    if resolved.exists() or resolved.is_symlink():
        if not resolved.is_dir():
            return f"{target} exists and is not a directory"
        if any(resolved.iterdir()):
            return f"{target} is not empty"
    return None


def _drifted(plan: ExportPlan) -> list[str]:
    """Every input that changed, vanished or appeared since `plan` was made."""
    changed: list[str] = []
    for path, data in plan.inputs.items():
        try:
            current = None if path.is_symlink() else path.read_bytes()
        except OSError:
            current = None
        if current != data:
            changed.append(path.relative_to(plan.bundle_dir).as_posix())
    now = {
        path
        for path in okf.iter_bundle_markdown(plan.bundle_dir)
        if path.name not in okf.RESERVED_FILENAMES
    }
    for path in now ^ plan.walked:
        changed.append(path.relative_to(plan.bundle_dir).as_posix())
    return sorted(set(changed))


def publish_export(plan: ExportPlan, target: Path) -> None:
    """Write `plan` and publish it at `target`, or raise `ExportRefusal`.

    The tree is written into a dot-prefixed staging directory in `target`'s
    parent, then checked: OKF §11 conformance (`okf.check_conformance`), the
    withheld-id byte scan (`leak_findings`, over the bytes read back from
    staging), and the input re-read. Only when all three pass is the staging
    directory renamed to `target` (an empty `target` directory is removed
    first). Any failure removes the staging directory. The caller has
    already accepted `target` through `check_target`."""
    target = target.resolve()
    staging = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.openkos-export-", dir=target.parent)
    )
    try:
        for rel, text in plan.files.items():
            path = staging / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode("utf-8"))

        prefix = f"{staging}{os.sep}"
        violations = [v.replace(prefix, "") for v in okf.check_conformance(staging)]
        if violations:
            raise ExportRefusal("conformance", violations)

        staged = {
            path.relative_to(staging).as_posix(): path.read_bytes().decode("utf-8")
            for path in sorted(staging.rglob("*"))
            if path.is_file()
        }
        leaks = leak_findings(staged, plan.withheld_ids)
        if leaks:
            raise ExportRefusal("leak", leaks)

        drifted = _drifted(plan)
        if drifted:
            raise ExportRefusal("drift", drifted)

        if target.is_dir():
            target.rmdir()
        staging.rename(target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
