"""`openkos import`'s use case (okf-import, #1314; ADR-0050).

Adopts a foreign OKF bundle, as written, under its own namespace
`imports/<namespace>/` of the workspace bundle. Two steps, so the CLI can show
a preview and ask between them:

- `plan_import` (Phase A) takes no lock and writes nothing. It checks the
  input and the namespace, reads the foreign tree through the bounded reader,
  adopts every conformant document in memory, builds one anchor Source per
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

import os
import re
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from openkos import config
from openkos.bundle import imports as bundle_imports
from openkos.bundle import index as bundle_index
from openkos.bundle import links as bundle_links
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
    for document in bundle.documents:
        if any(char.isspace() for char in document.foreign_id):
            raise ImportRefusal(
                "whitespace-in-name",
                f"{document.path} contains whitespace: a link to such a name "
                f"cannot be read by every engine link reader, so the anchor "
                f"could not list it; rename it in the foreign bundle and retry",
            )

    prefix = bundle_imports.namespace_prefix(namespace)
    floor = okf.import_floor(cfg.default_sensitivity, sensitivity_flag)

    def birth(doc_type: str, base: object) -> str:
        return config.type_birth_sensitivity(cfg, doc_type, base)

    adopted: list[AdoptedPlan] = []
    bodies: dict[str, str] = {}
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
        concept_id = f"{prefix}/{document.foreign_id}"
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
