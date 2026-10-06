"""`openkos import`'s use case (okf-import, #1314; ADR-0050).

Adopts a foreign OKF bundle, as written, under its own namespace
`imports/<namespace>/` of the workspace bundle. Two steps, so the CLI can show
a preview and ask between them:

- `plan_import` (Phase A) takes no lock and writes nothing. It checks the
  input and the namespace, reads the foreign tree through the bounded reader,
  adopts every conformant document in memory (a name with whitespace is
  renamed to a slug, every link to it follows), builds one anchor Source per
  effective label, and PROVES the result (every link of every adopted body
  stays inside the namespace; every adopted frontmatter is sound) before
  returning an `ImportPlan`.
- `publish_import` (Phase B) runs inside the workspace commit section. It
  re-reads the foreign tree and the config and refuses (retry-safe) when
  either moved, rechecks the namespace, then writes every document into a
  dot-prefixed staging directory and publishes the namespace with ONE final
  rename. The namespace directory's existence is therefore the completion
  marker: a run killed before the rename leaves no namespace, so it can never
  block its own retry; a refused run leaves the tree byte-identical.

Nothing here calls a model, a network or the VCS layer (the autocommit is a
port). Format knowledge stays in the OKF seam (`okf.*`) and link knowledge in
`bundle.links`; this module only orchestrates (ADR-0018: a narrow synchronous
service, no engine).
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import stat
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from openkos import config, fsio
from openkos.application import commit_phase
from openkos.application.lock_wait import CommitSection
from openkos.bundle import imports as bundle_imports
from openkos.bundle import index as bundle_index
from openkos.bundle import links as bundle_links
from openkos.bundle import log as bundle_log
from openkos.model import okf

_URL_RE = re.compile(r"\A[A-Za-z][A-Za-z0-9+.-]+:/")
"""A scheme of two or more characters followed by `:/` (`Path` collapses the
`//` of `https://x`). One character is a Windows drive letter, not a URL."""

_ARCHIVE_SUFFIXES = (
    ".zip",
    ".tar",
    ".tgz",
    ".tar.gz",
    ".tar.bz2",
    ".tbz2",
    ".tar.xz",
    ".txz",
    ".gz",
    ".bz2",
    ".xz",
    ".7z",
    ".rar",
)

_RE_IMPORT_HINT = (
    "re-import into an existing namespace is not supported; if an earlier "
    "import stopped before its commit, `git status` shows it"
)

_MAX_LISTED = 5
"""How many violations a refusal reason lists before summarizing the rest."""


class ImportRefusal(Exception):
    """An import refused; nothing was written (or, for `restore-failed`, the
    workspace may be partly written and the reason says so).

    `code` is a stable machine name, `reason` the sentence the CLI prints, and
    `retry_safe` says whether re-running the same command can succeed: `True`
    for a drift refusal (the foreign tree or the config moved since the
    preview, so a fresh preview is needed: the CLI's exit 3), `False` for
    everything else (exit 1)."""

    def __init__(self, code: str, reason: str, *, retry_safe: bool = False) -> None:
        self.code = code
        self.reason = reason
        self.retry_safe = retry_safe
        super().__init__(reason)


@dataclass(frozen=True)
class AdoptedPlan:
    """One foreign document as it will be written."""

    foreign_id: str
    concept_id: str
    doc_type: str
    label: str
    """The effective sensitivity label it is written at."""
    type_raised: bool
    """Whether the per-type birth offset raised `label` above what the floor
    and the foreign label alone give."""
    foreign_label: str | None
    """The foreign document's own label folded to one level, `None` when it
    carried none."""
    text: str


@dataclass(frozen=True)
class AnchorPlan:
    """One anchor Source, one per effective label present in the import."""

    concept_id: str
    label: str
    text: str
    title: str
    description: str
    documents: int


@dataclass(frozen=True)
class ImportPlan:
    """Everything `publish_import` needs and the preview shows, computed
    without writing anything."""

    namespace: str
    source: Path
    """Held in memory for the Phase B re-read; never written anywhere."""
    now: datetime
    """The instant the anchors were stamped and the log entry is dated with."""
    manifest: tuple[tuple[str, str], ...]
    """The reader's manifest: `(foreign path, sha256)` for every file read and
    `(foreign path, "skip:<code>")` for every path skipped unopened."""
    okf_version: object | None
    adopted: tuple[AdoptedPlan, ...]
    anchors: tuple[AnchorPlan, ...]
    skipped: tuple[tuple[str, str], ...]
    """`(foreign path, reason code)` for every file left out."""
    renames: tuple[tuple[str, str], ...]
    """`(foreign path, path below the namespace)` for every document whose name
    held whitespace and was renamed to a slug, in the reader's order."""
    dropped_keys: int
    links_rewritten: int
    links_clamped: int
    html_link_documents: int
    label_fingerprint: tuple[str, tuple[tuple[str, int], ...]]
    """`default_sensitivity` and the sorted type offsets: the inputs the labels
    were computed from, compared again under the lock."""
    floor: str

    @property
    def label_counts(self) -> dict[str, int]:
        """How many documents land at each label, in sensitivity order."""
        counts = {label: 0 for label in okf.SENSITIVITY_ORDER}
        for adopted in self.adopted:
            counts[adopted.label] += 1
        return {label: n for label, n in counts.items() if n}

    @property
    def type_raises(self) -> tuple[AdoptedPlan, ...]:
        """The documents whose label a per-type offset raised."""
        return tuple(a for a in self.adopted if a.type_raised)


@dataclass(frozen=True)
class ImportOutcome:
    """What `publish_import` did."""

    namespace: str
    adopted: int
    skipped: int
    anchors: tuple[str, ...]
    """The anchors' Concept IDs."""
    commit: str | None
    """The abbreviated sha of the one commit, or `None` when the commit was
    degraded (no repository, no identity)."""


# --- Phase A -----------------------------------------------------------------


def _lstat_or_none(path: Path) -> os.stat_result | None:
    """`os.lstat(path)`, or `None` when nothing is there. Any other failure is
    a refusal: a path that cannot be inspected is not known to be free."""
    try:
        return os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        raise ImportRefusal(
            "unreadable", f"cannot inspect {path.name}: {exc.strerror or exc}"
        ) from exc


def _check_input(layout: config.WorkspaceLayout, source: Path) -> None:
    """Refuse an input that is not an existing local directory outside the
    bundle and not an ancestor of the workspace (design D7 step 1)."""
    if _URL_RE.match(str(source)):
        raise ImportRefusal(
            "input-url", "the input must be a local directory, not a URL"
        )
    try:
        exists = source.exists()
        is_directory = source.is_dir()
    except OSError as exc:
        raise ImportRefusal(
            "input-unreadable", "the input directory could not be read"
        ) from exc
    if not exists:
        raise ImportRefusal(
            "input-missing", "the input is not an existing local directory"
        )
    if not is_directory:
        if source.name.lower().endswith(_ARCHIVE_SUFFIXES):
            raise ImportRefusal(
                "input-archive",
                "a directory is required: an archive must be unpacked first",
            )
        raise ImportRefusal(
            "input-not-directory", "a directory is required, not a file"
        )
    resolved = source.resolve()
    bundle = layout.bundle_dir.resolve()
    if resolved == bundle or resolved.is_relative_to(bundle):
        raise ImportRefusal(
            "input-inside-bundle",
            "the input is inside the workspace bundle: an import adopts a "
            "foreign bundle from outside it",
        )
    if layout.root.resolve().is_relative_to(resolved):
        raise ImportRefusal(
            "input-contains-workspace",
            "the input contains the workspace: import a foreign bundle, not "
            "a directory that holds this workspace",
        )


def _check_imports_dir(layout: config.WorkspaceLayout) -> Path:
    """The `bundle/imports` directory, refused when it or anything above it
    (below the workspace) is a symlink or when it is not a directory."""
    imports_dir = layout.bundle_dir / bundle_imports.IMPORTS_DIR
    reason = config.symlink_boundary_reason(imports_dir, layout.root)
    if reason is not None:
        raise ImportRefusal("symlink", reason)
    state = _lstat_or_none(imports_dir)
    if state is not None and not stat.S_ISDIR(state.st_mode):
        raise ImportRefusal(
            "imports-not-directory",
            f"bundle/{bundle_imports.IMPORTS_DIR} exists and is not a directory",
        )
    return imports_dir


def _anchor_path(imports_dir: Path, namespace: str, label: str) -> Path:
    return imports_dir / f"{namespace}--{label}.md"


def _is_own_anchor(path: Path, namespace: str, label: str) -> bool:
    """Whether the file at `path` is `namespace`'s own anchor at `label` (so a
    torn run's leftover is overwritten and any other file is never touched)."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return okf.is_import_anchor_of(text, namespace=namespace, label=label)


def _check_namespace_free(imports_dir: Path, namespace: str) -> None:
    """Refuse when `namespace` is taken: its directory exists as anything (an
    empty directory or a file included), or an anchor path holds a file that
    is not this namespace's own torn anchor (design D2)."""
    if _lstat_or_none(imports_dir / namespace) is not None:
        raise ImportRefusal(
            "namespace-exists",
            f"namespace '{namespace}' already exists under "
            f"bundle/{bundle_imports.IMPORTS_DIR}; {_RE_IMPORT_HINT}",
        )
    for label in okf.SENSITIVITY_ORDER:
        path = _anchor_path(imports_dir, namespace, label)
        state = _lstat_or_none(path)
        if state is None:
            continue
        if not (stat.S_ISREG(state.st_mode) and _is_own_anchor(path, namespace, label)):
            raise ImportRefusal(
                "anchor-path-taken",
                f"{bundle_imports.anchor_id(namespace, label)}.md already exists "
                f"and is not an anchor of namespace '{namespace}'; {_RE_IMPORT_HINT}",
            )


def _foreign_reason(exc: okf.ForeignRefusal) -> str:
    if exc.code == "rename-collision":
        return (
            f"the foreign bundle was refused ({exc.code}): {exc.path} would be "
            f"written under the same name once names with whitespace are "
            f"renamed to slugs; rename one of them in the foreign bundle and retry"
        )
    if exc.path:
        return f"the foreign bundle was refused ({exc.code}): {exc.path}"
    return f"the foreign bundle was refused ({exc.code}): the input could not be read"


def _entry_title(document: okf.ForeignDocument) -> str:
    """The label an anchor lists the document under: its own `title` on one
    line with link delimiters neutralized, else its foreign id."""
    title = document.mapping.get("title")
    text = " ".join(title.split()) if isinstance(title, str) else ""
    return bundle_index.sanitize_link_label(text or document.foreign_id)


def _generated_by(document: okf.ForeignDocument) -> str | None:
    generated = document.mapping.get("generated")
    if isinstance(generated, Mapping):
        by = generated.get("by")
        if isinstance(by, str) and by:
            return by
    return None


def _no_type_offset(_doc_type: str, base: object) -> str:
    return okf.combine_sensitivity(base, base)


def _summarize(problems: Sequence[str]) -> str:
    listed = "; ".join(problems[:_MAX_LISTED])
    extra = len(problems) - _MAX_LISTED
    return f"{listed} (and {extra} more)" if extra > 0 else listed


def label_fingerprint(cfg: config.Config) -> tuple[str, tuple[tuple[str, int], ...]]:
    """The config inputs an adopted document's label is computed from."""
    return (
        str(cfg.default_sensitivity),
        tuple(sorted(cfg.type_sensitivity_defaults.items())),
    )


def plan_import(
    root: Path,
    layout: config.WorkspaceLayout,
    cfg: config.Config,
    source: Path,
    *,
    namespace: str,
    sensitivity_flag: str | None,
    now: datetime,
) -> ImportPlan:
    """Plan the import of the foreign bundle at `source` into `namespace`, or
    raise `ImportRefusal`. Takes no lock and writes nothing.

    The namespace is validated and found free BEFORE the foreign tree is read
    (a cheap refusal first); `publish_import` re-checks it under the lock."""
    reason = bundle_imports.namespace_reason(namespace)
    if reason is not None:
        raise ImportRefusal("namespace-invalid", reason)
    _check_input(layout, source)
    imports_dir = _check_imports_dir(layout)
    _check_namespace_free(imports_dir, namespace)

    try:
        bundle = okf.read_foreign_bundle(source)
    except okf.ForeignRefusal as exc:
        raise ImportRefusal(exc.code, _foreign_reason(exc)) from exc
    if not bundle.documents:
        raise ImportRefusal(
            "nothing-to-import",
            f"the foreign bundle holds no conformant document to adopt "
            f"({len(bundle.skipped)} path(s) skipped)",
        )
    prefix = bundle_imports.namespace_prefix(namespace)
    floor = okf.import_floor(cfg.default_sensitivity, sensitivity_flag)

    def birth(doc_type: str, base: object) -> str:
        return config.type_birth_sensitivity(cfg, doc_type, base)

    adopted: list[AdoptedPlan] = []
    bodies: dict[str, str] = {}
    renames: list[tuple[str, str]] = []
    dropped = rewritten = clamped = html_documents = 0
    for document in bundle.documents:
        label = okf.effective_import_sensitivity(
            document.mapping,
            document.doc_type,
            default_sensitivity=cfg.default_sensitivity,
            flag=sensitivity_flag,
            birth=birth,
        )
        without_offset = okf.effective_import_sensitivity(
            document.mapping,
            document.doc_type,
            default_sensitivity=cfg.default_sensitivity,
            flag=sensitivity_flag,
            birth=_no_type_offset,
        )
        namespaced = bundle_links.rewrite_links_into_namespace(
            document.body, foreign_id=document.foreign_id, prefix=prefix
        )
        text = okf.adopt_foreign_document(
            document,
            body=namespaced.text,
            sensitivity=label,
            anchor_id=bundle_imports.anchor_id(namespace, label),
            prefix=prefix,
        )
        concept_id = f"{prefix}/{okf.renamed_foreign_id(document.foreign_id)}"
        bodies[concept_id] = text
        adopted.append(
            AdoptedPlan(
                foreign_id=document.foreign_id,
                concept_id=concept_id,
                doc_type=document.doc_type,
                label=label,
                type_raised=label != without_offset,
                foreign_label=okf.fold_foreign_sensitivity(document.mapping),
                text=text,
            )
        )
        if concept_id != f"{prefix}/{document.foreign_id}":
            renames.append(
                (document.path, f"{concept_id.removeprefix(prefix + '/')}.md")
            )
        dropped += len(okf.dropped_foreign_keys(document.mapping))
        rewritten += namespaced.links_rewritten
        clamped += namespaced.links_clamped
        html_documents += int(namespaced.html_links)

    anchors = _build_anchors(
        namespace, bundle, adopted, now=now, okf_version=bundle.okf_version
    )
    _prove(adopted, anchors, prefix=prefix, namespace=namespace, floor=floor)
    return ImportPlan(
        namespace=namespace,
        source=source,
        now=now,
        manifest=bundle.manifest,
        okf_version=bundle.okf_version,
        adopted=tuple(adopted),
        anchors=anchors,
        skipped=bundle.skipped,
        renames=tuple(renames),
        dropped_keys=dropped,
        links_rewritten=rewritten,
        links_clamped=clamped,
        html_link_documents=html_documents,
        label_fingerprint=label_fingerprint(cfg),
        floor=floor,
    )


def _build_anchors(
    namespace: str,
    bundle: okf.ForeignBundle,
    adopted: Sequence[AdoptedPlan],
    *,
    now: datetime,
    okf_version: object | None,
) -> tuple[AnchorPlan, ...]:
    """One anchor per effective label present, each listing only the
    documents at its own label (so it obeys the high-water rule at birth and
    no document is below-source at export). Its digest covers that label's
    documents only: a digest over the whole import would let a lower-labelled
    anchor confirm the ids and bytes of higher-labelled documents."""
    by_foreign = {doc.foreign_id: doc for doc in bundle.documents}
    generated = okf.Generated(
        by=okf.engine_actor(), at=now.strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    anchors: list[AnchorPlan] = []
    for label in okf.SENSITIVITY_ORDER:
        members = [a for a in adopted if a.label == label]
        if not members:
            continue
        entries = [
            okf.AnchorEntry(
                foreign_id=a.foreign_id,
                title=_entry_title(by_foreign[a.foreign_id]),
                sha256=by_foreign[a.foreign_id].sha256,
                generated_by=_generated_by(by_foreign[a.foreign_id]),
            )
            for a in members
        ]
        text = okf.build_import_anchor(
            namespace=namespace,
            label=label,
            entries=entries,
            bundle_sha256=okf.import_bundle_digest(
                (e.foreign_id, e.sha256) for e in entries
            ),
            okf_version=okf_version,
            generated=generated,
        )
        metadata, _ = okf.load_frontmatter(text)
        anchors.append(
            AnchorPlan(
                concept_id=bundle_imports.anchor_id(namespace, label),
                label=label,
                text=text,
                title=str(metadata["title"]),
                description=str(metadata["description"]),
                documents=len(entries),
            )
        )
    return tuple(anchors)


def _prove(
    adopted: Sequence[AdoptedPlan],
    anchors: Sequence[AnchorPlan],
    *,
    prefix: str,
    namespace: str,
    floor: str,
) -> None:
    """The proof, run on the FINAL bytes: every link of every body stays
    inside the namespace, and every adopted frontmatter is sound."""
    link_problems: list[str] = []
    frontmatter_problems: list[str] = []
    for item in adopted:
        _, body = okf.load_frontmatter(item.text)
        link_problems.extend(
            bundle_links.namespace_link_violations(
                body, concept_id=item.concept_id, prefix=prefix
            )
        )
        frontmatter_problems.extend(
            f"{item.concept_id}: {problem}"
            for problem in okf.adopted_violations(
                item.text,
                prefix=prefix,
                anchor_id=bundle_imports.anchor_id(namespace, item.label),
                floor=floor,
            )
        )
    for anchor in anchors:
        _, body = okf.load_frontmatter(anchor.text)
        link_problems.extend(
            bundle_links.namespace_link_violations(
                body, concept_id=anchor.concept_id, prefix=prefix
            )
        )
    if link_problems:
        raise ImportRefusal(
            "link-outside-namespace",
            f"a link would leave imports/{namespace}: {_summarize(link_problems)}",
        )
    if frontmatter_problems:
        raise ImportRefusal(
            "adopted-violation",
            f"an adopted document is unsound: {_summarize(frontmatter_problems)}",
        )


# --- Phase B -----------------------------------------------------------------


def _remove_tree(path: Path) -> None:
    """Remove a directory tree. A seam of its own so a test can fail it."""
    shutil.rmtree(path)


def _publish_directory(staging: Path, target: Path) -> None:
    """Publish `staging` as `target` with one rename: the completion point of
    an import. `Path.replace` (`os.replace`) is atomic on POSIX and, on Windows,
    cannot replace an existing directory, which is exactly what is wanted here (the caller
    has just checked that `target` is absent)."""
    staging.replace(target)


def _read_or_none(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _take_snapshot(paths: Sequence[Path]) -> dict[Path, bytes | None]:
    """The bytes of every path a failed run must put back, `None` for a path
    that did not exist."""
    return {path: _read_or_none(path) for path in paths}


def _restore_bytes(path: Path, data: bytes | None) -> None:
    """Put `path` back as it was: its bytes, or absent. Written through a
    temporary file and a rename, so a restore never leaves a half-written
    file either."""
    if data is None:
        path.unlink(missing_ok=True)
        return
    tmp_path = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        tmp_path.write_bytes(data)
        tmp_path.replace(path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _remove_stale_staging(imports_dir: Path, namespace: str) -> None:
    """Remove the staging directories a killed run of THIS namespace left:
    real directories directly under `imports_dir` named with the staging
    prefix. A symlink, a file, another namespace's staging and anything
    nested deeper are never touched."""
    prefix = bundle_imports.staging_name_prefix(namespace)
    try:
        with os.scandir(imports_dir) as entries:
            stale = [
                Path(entry.path)
                for entry in entries
                if entry.name.startswith(prefix) and entry.is_dir(follow_symlinks=False)
            ]
    except FileNotFoundError:
        return
    for path in stale:
        try:
            _remove_tree(path)
        except OSError as exc:
            raise ImportRefusal(
                "stale-staging",
                f"cannot remove the leftover of an earlier import ({path.name}): {exc}",
            ) from exc


def _revalidate(
    layout: config.WorkspaceLayout,
    plan: ImportPlan,
    load_config: Callable[[Path], config.Config],
) -> None:
    """The guards that run under the lock (design D7 steps 1 to 3): the
    foreign tree and the label inputs are what the preview judged, and the
    namespace is still free."""
    try:
        current = okf.read_foreign_bundle(plan.source)
    except okf.ForeignRefusal as exc:
        raise ImportRefusal(
            "foreign-changed",
            f"the foreign bundle changed since the preview and is now refused "
            f"({exc.code}); run the import again",
            retry_safe=True,
        ) from exc
    if current.manifest != plan.manifest:
        raise ImportRefusal(
            "foreign-changed",
            "the foreign bundle changed since the preview; run the import again",
            retry_safe=True,
        )
    if label_fingerprint(load_config(layout.root)) != plan.label_fingerprint:
        raise ImportRefusal(
            "labels-changed",
            "the workspace's sensitivity configuration changed since the "
            "preview, so the labels shown are no longer the labels that would "
            "be written; run the import again",
            retry_safe=True,
        )
    _check_namespace_free(_check_imports_dir(layout), plan.namespace)


def _index_with_anchors(
    index_text: str, plan: ImportPlan, *, stale_anchor_ids: Sequence[str]
) -> str:
    """`index_text` with one `# Sources` bullet per anchor (one already listed
    is left alone, so a retry never doubles it) and without the bullets of this
    namespace's torn anchors that the import no longer writes."""
    listed = bundle_index.indexed_concept_ids(index_text)
    for anchor in plan.anchors:
        if anchor.concept_id in listed:
            continue
        index_text = bundle_index.insert_index_entry(
            index_text,
            section="Sources",
            link_dir=bundle_imports.IMPORTS_DIR,
            title=anchor.title,
            slug=PurePosixPath(anchor.concept_id).name,
            description=anchor.description,
        )
    for concept_id in stale_anchor_ids:
        index_text, _ = bundle_index.remove_index_entry(index_text, concept_id)
    return index_text


def _import_log_entry(plan: ImportPlan) -> str:
    anchors = ", ".join(f"[{a.title}](/{a.concept_id}.md)" for a in plan.anchors)
    count = len(plan.adopted)
    noun = "document" if count == 1 else "documents"
    return (
        f"**Import**: Imported {count} {noun} into "
        f"`{bundle_imports.namespace_prefix(plan.namespace)}` "
        f"(anchors: {anchors}); {len(plan.skipped)} skipped."
    )


def _relative_posix(layout: config.WorkspaceLayout, path: Path) -> str:
    return path.relative_to(layout.root).as_posix()


def _write_namespace(layout: config.WorkspaceLayout, plan: ImportPlan) -> list[str]:
    """Steps 4 to 10 of design D7: stage, check, write the anchors, index and
    log, and publish the namespace with one rename. Returns the workspace-
    relative paths the commit takes. Any failure puts every touched path back
    and removes the staging directory."""
    namespace = plan.namespace
    imports_dir = layout.bundle_dir / bundle_imports.IMPORTS_DIR
    target = imports_dir / namespace
    anchor_paths = {
        label: _anchor_path(imports_dir, namespace, label)
        for label in okf.SENSITIVITY_ORDER
    }
    index_path = layout.bundle_dir / "index.md"
    log_path = layout.bundle_dir / "log.md"

    _remove_stale_staging(imports_dir, namespace)
    try:
        snapshot = _take_snapshot([index_path, log_path, *anchor_paths.values()])
    except OSError as exc:
        raise ImportRefusal(
            "snapshot-failed", f"cannot read the files the import updates: {exc}"
        ) from exc

    created_imports = _lstat_or_none(imports_dir) is None
    staging: Path | None = None
    try:
        imports_dir.mkdir(exist_ok=True)
        staging = imports_dir / (
            f"{bundle_imports.staging_name_prefix(namespace)}{uuid.uuid4().hex[:12]}"
        )
        staging.mkdir()
        for item in plan.adopted:
            path = staging / f"{okf.renamed_foreign_id(item.foreign_id)}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            fsio.write_exclusive(path, item.text)

        prefix = f"{staging}{os.sep}"
        violations = [v.replace(prefix, "") for v in okf.check_conformance(staging)]
        if violations:
            raise ImportRefusal(
                "conformance",
                f"the staged import is not a conformant OKF bundle: "
                f"{_summarize(violations)}",
            )

        written = {a.label for a in plan.anchors}
        for anchor in plan.anchors:
            fsio.write_atomic(anchor_paths[anchor.label], anchor.text)
        stale_anchor_ids: list[str] = []
        for label, path in anchor_paths.items():
            if label not in written and snapshot[path] is not None:
                path.unlink()
                stale_anchor_ids.append(bundle_imports.anchor_id(namespace, label))

        fsio.write_atomic(
            index_path,
            _index_with_anchors(
                index_path.read_text(encoding="utf-8"),
                plan,
                stale_anchor_ids=stale_anchor_ids,
            ),
        )
        fsio.write_atomic(
            log_path,
            bundle_log.insert_log_entry(
                log_path.read_text(encoding="utf-8"),
                plan.now.date(),
                _import_log_entry(plan),
            ),
        )

        _check_namespace_free_at_rename(target)
        _publish_directory(staging, target)
    except BaseException as exc:
        failures = _roll_back(
            snapshot, staging, imports_dir if created_imports else None
        )
        if not isinstance(exc, Exception):
            raise
        if failures:
            raise ImportRefusal(
                "restore-failed",
                f"the import failed ({exc}) and the workspace could not be "
                f"fully restored ({'; '.join(failures)}); `git status` shows "
                f"what was left",
            ) from exc
        if isinstance(exc, ImportRefusal):
            raise
        raise ImportRefusal(
            "write-failed", f"writing the import failed ({exc}); nothing was kept"
        ) from exc

    return [
        _relative_posix(layout, target),
        *(_relative_posix(layout, anchor_paths[a.label]) for a in plan.anchors),
        _relative_posix(layout, index_path),
        _relative_posix(layout, log_path),
    ]


def _check_namespace_free_at_rename(target: Path) -> None:
    """The last look before the rename: POSIX `rename` replaces an EMPTY
    directory, so one created in the window since the first check would be
    replaced silently."""
    if _lstat_or_none(target) is not None:
        raise ImportRefusal(
            "namespace-exists",
            f"namespace '{target.name}' appeared while the import ran; "
            f"{_RE_IMPORT_HINT}",
        )


def _roll_back(
    snapshot: Mapping[Path, bytes | None],
    staging: Path | None,
    created_imports: Path | None,
) -> list[str]:
    """Undo a failed run: put every snapshotted path back (only the ones that
    differ), remove the staging directory, and remove the `imports/` directory
    the run created. Every step is attempted; what could not be undone is
    returned, so the caller can say so instead of claiming a clean refusal."""
    failures: list[str] = []
    for path, data in snapshot.items():
        try:
            if _read_or_none(path) != data:
                _restore_bytes(path, data)
        except OSError as exc:
            failures.append(f"{path.name}: {exc}")
    if staging is not None and staging.exists():
        try:
            _remove_tree(staging)
        except OSError as exc:
            failures.append(f"{staging.name}: {exc}")
    if created_imports is not None and not failures:
        with contextlib.suppress(OSError):
            created_imports.rmdir()
    return failures


def publish_import(
    root: Path,
    layout: config.WorkspaceLayout,
    plan: ImportPlan,
    *,
    commit_section: CommitSection,
    load_config: Callable[[Path], config.Config],
    autocommit: Callable[[Path, Sequence[str], str], str | None],
    after_commit: Callable[[], None] = commit_phase.no_after_commit,
) -> ImportOutcome:
    """Write `plan` into the workspace and make the one commit, or raise
    `ImportRefusal`. The whole of Phase B runs inside `commit_section`, with no
    model call and no prompt: the foreign tree and the label inputs are judged
    again, the namespace is rechecked, and only then is anything written, so a
    refusal leaves the tree byte-identical.

    `after_commit` runs last inside the section and must not call a model.

    The commit follows the rename and is outside the restore: an import killed
    after the rename is complete and uncommitted (a retry is refused as an
    existing namespace, and `git status` shows it)."""
    with commit_section():
        _revalidate(layout, plan, load_config)
        paths = _write_namespace(layout, plan)
        commit = autocommit(
            root,
            paths,
            f"openkos: import {plan.namespace} (+{len(plan.adopted)} concepts)",
        )
        # Still under the lock, after the commit: the adapter's model-free
        # refresh of the derived stores that are pure projections of the bundle.
        after_commit()
    return ImportOutcome(
        namespace=plan.namespace,
        adopted=len(plan.adopted),
        skipped=len(plan.skipped),
        anchors=tuple(a.concept_id for a in plan.anchors),
        commit=commit,
    )
