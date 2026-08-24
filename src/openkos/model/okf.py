"""OKF (Open Knowledge Format) adapter.

The one seam that knows the on-disk shape of OKF v0.1: frontmatter framing,
reserved filenames, and the conformance rules of §9. Nothing outside this
module parses or emits frontmatter, or reasons about reserved files
(AGENTS.md:41, docs/architecture.md:113).

All three §9 rules are implemented here: rules 1-2 walk every non-reserved
`.md` file (`_iter_docs`), and rule 3 walks the reserved files themselves
(`index.md`/`log.md`) to check their fixed structure per §6/§7/§11.
"""

import hashlib
import os
import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Final, Literal, get_args

import frontmatter

from openkos.model.types import BUILDABLE_TYPES as _CONCEPT_TYPES

OKF_VERSION: Final = "0.1"
"""The OKF version this engine targets and declares, per §11."""

RESERVED_FILENAMES: Final[frozenset[str]] = frozenset({"index.md", "log.md"})
"""§6/§7 give these a fixed structure; §9 rule 1 exempts them from frontmatter."""

STATE_DIRNAME: Final = ".state"
"""The bundle-relative directory holding derived, non-`.md` runtime state --
today `bundle/.state/ledger/` (durable-derived-state slice 1a; ADR-0013).
Never walked by `_iter_docs`/`rglob("*.md")` (this name carries no `.md`
suffix of its own), so nothing under it is a concept document by
construction; `lint` separately flags any `.md` file that turns up here as a
structural-exclusion regression."""

_LOG_HEADING_RE: Final = re.compile(r"^## (.+)$", re.MULTILINE)
"""Every level-2 heading in a `log.md`, per §7. `### ` cannot false-match:
`^## ` requires a space in the 3rd position."""

_ISO_DATE_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")
"""§7's date-heading format, checked for shape only -- not calendar-validated
(e.g. `2026-13-45` matches)."""

SENSITIVITY_ORDER: Final[tuple[str, str, str]] = ("public", "private", "confidential")
"""Least-to-most-restrictive sensitivity ordering (ADR-0003, KOM
docs/knowledge-object-model.md:255-272): a derived object is at least as
sensitive as its most sensitive source."""

RELATIONS_KEY: Final = "relations"
"""The optional frontmatter key holding a document's outbound typed edges
(spec: "`relations:` Frontmatter Field Shape"). Ordinary OKF data, per §4.1
tolerance -- logically placed after `provenance` and before `merged_from`
in a metadata dict literal, though `dump_frontmatter`'s YAML emission
always re-sorts keys alphabetically regardless of that insertion order."""

MERGED_FROM_KEY: Final = "merged_from"
"""The survivor frontmatter key holding the reversibility ledger (ADR-0002):
an ordinary OKF data key, not a new file type, per §4.1 tolerance."""

MERGED_CONTENT_HEADING_PREFIX: Final = "\n\n## Merged content ("
"""The heading `build_merged_document` writes above an absorbed body, up to
the absorbed id (#445).

Shared because two modules need the same string for opposite reasons: this
module WRITES it, and `resolution/contradiction.py` FINDS it in order to cut
a ledger snapshot back down to the survivor's own content. It lived as two
independent literals until #409's review flagged the duplication; one
constant is better than two literals that must agree.
`tests/unit/cli/test_merge_core.py::
test_build_merged_document_body_layout_is_pinned` pins the rendered layout
against its own literal, so a change here still fails loudly there rather
than silently altering merged documents."""


def merged_content_heading(absorbed_id: str) -> str:
    """The complete `\\n\\n## Merged content (<absorbed_id>)` heading for one
    absorbed id -- the one spelling of the probe string every consumer must
    agree on byte-for-byte (#685): `build_merged_document` WRITES it (plus a
    trailing blank line), `bundle.merge.plan_merge`'s carried-content
    annotation and the forget sweep's structural excision both FIND it. Each
    site previously assembled `f"{MERGED_CONTENT_HEADING_PREFIX}{id})"` by
    hand; a one-character drift there was a silent scrub or annotation
    miss."""
    return f"{MERGED_CONTENT_HEADING_PREFIX}{absorbed_id})"


TYPE_ALTERNATIVE_KEY: Final = "type_alternative"
"""The optional frontmatter key a derived object carries WHEN the model
reported a runner-up type it also weighed (issue #401); ABSENT otherwise --
no sentinel, so absence means "the classification was not near a boundary",
mirroring `EXTRACTION_STATUS_KEY`'s convention.

The type is not cosmetic: it decides the bundle subdirectory, the `index.md`
catalog section, and the default volatility tier (`model/types.py` gives
`Event` the `static` tier and `Project` the `volatile` one). The same
sentence classified twice can land in different directories under different
refresh expectations, and today each run records its own answer as
definitive. This key does not make the classification stable -- an ambiguous
subject genuinely is ambiguous -- it stops a coin flip from being recorded
as a settled fact.

`type` remains the engine's answer. This is a note beside it, never a
competing value: nothing reads it to route a document."""

EXTRACTION_STATUS_KEY: Final = "extraction_status"
"""The optional frontmatter key a Source concept carries WHEN a single
`ingest` run wrote zero derived objects (issue #187); ABSENT when at least
one derived object was written -- no `ok`/`none` sentinel, so absence means
exactly one thing: healthy. Stamped onto freshly built content only, never
merged onto on-disk frontmatter (a merge would make a stale marker sticky
forever); never read back from disk by any writer, unlike `sensitivity`
(#229)."""

ExtractionStatus = Literal[
    "no-extractable-text", "blocked-by-sensitivity", "failed", "no-concepts-found"
]
"""The closed four-token vocabulary for `EXTRACTION_STATUS_KEY`, keyed on
WHY extraction produced nothing rather than on which gate condition fired.
Only `"failed"` is retryable debt; the other three are deliberate policy or
simply nothing to extract."""

EXTRACTION_STATUS_VALUES: Final[tuple[ExtractionStatus, ...]] = get_args(
    ExtractionStatus
)
"""For specs and tests, not a runtime validation gate -- the writer is typed
via `ExtractionStatus`/mypy-strict, and readers match a single literal
(`== EXTRACTION_STATUS_FAILED`) rather than membership-testing this tuple,
so an unrecognized on-disk value is structurally ignored."""

ORIGIN_KEY_KEY: Final = "origin_key"
"""The optional frontmatter key recording WHICH FILE ON DISK a Source was
ingested from (#552), as a digest -- never a path.

`resource` cannot answer this. It names the copy INSIDE the workspace
(`raw/<name>`), and that flat namespace is exactly what collided: two
different files from two different folders sharing a basename resolved to
one `resource`, so one was refused and the other silently absorbed into the
first one's Source. Telling them apart needs an identity for the ORIGIN,
which nothing on the document carried.

ABSENT on any Source written before this key existed, and absence has
exactly one meaning: ingested before origins were recorded. `ingest` treats
such a Source as a legacy match on identical bytes -- today's behaviour,
preserved -- and backfills the key on the next re-ingest, so the migration
costs nothing and needs no verb.

A DIGEST rather than the path itself, deliberately. The value's only job is
EQUALITY; nothing reads it for location. A structured absolute-path field
would put `$HOME` and the machine's directory layout into every Source's
frontmatter, in git history, removable only by `purge`, and would extend to
one more consumer the interpolation surface #274/#285 had to harden. The
human-readable origin already lives in `description`, as free text, where it
has always been."""

_ORIGIN_KEY_HEX_CHARS: Final = 32
"""128 bits of the digest -- unambiguous for any realistic workspace, and
short enough that `origin_key: <value>` clears the YAML emitter's ~80-column
fold width with room to spare. A folded scalar would turn one frontmatter
line into two, the same hazard `_TITLE_DUMP_WIDTH` exists for."""


def origin_key_for(path: Path) -> str:
    """The `ORIGIN_KEY_KEY` value for the file at `path` (#552).

    Keyed on the RESOLVED path, so `./notes.txt` from inside a folder and
    `folder/notes.txt` from its parent are ONE file. Without resolution,
    re-ingesting the same file from a different working directory would look
    like a new source and spawn a disambiguated copy on every run -- the
    unbounded-suffix bug `_family_owns_source` exists to prevent one layer
    down, reintroduced at the raw layer.

    `strict=False`: this is called on a path already validated as a readable
    file, and a resolution that cannot stat is not this function's failure to
    report. Identity of a MISSING file is still well-defined -- the resolved
    string -- and refusing here would turn a benign race into a crash.

    Not stable across machines, and deliberately not: a bundle cloned
    elsewhere carries keys naming paths that do not exist there, so
    re-ingesting the same logical file on a second machine writes a new
    Source. That is correct -- it IS a different file on that machine -- and
    harmless, since `raw/` already holds the bytes.
    """
    resolved = str(path.resolve(strict=False))
    digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()
    return digest[:_ORIGIN_KEY_HEX_CHARS]


EXTRACTION_NOTICE_KEY: Final = "extraction_notice"
"""The optional frontmatter key a Source concept carries when its
extraction succeeded but produced output worth disclosing (#585); ABSENT
otherwise, following `EXTRACTION_STATUS_KEY`'s convention exactly -- no
`ok`/`none` sentinel, stamped onto freshly built content only, never merged
onto on-disk frontmatter, never read back by a writer.

A SEPARATE key rather than a fifth `ExtractionStatus` token, and the split
is semantic, not cosmetic. `extraction_status` answers "why did this run
write zero derived objects"; every one of its values presupposes an empty
result, and `lint.check_unextracted` reads `failed` as retryable debt.
This key fires on the opposite precondition -- an object WAS written -- so
one field cannot honestly hold both, and a reader matching `failed` must
never have to skip past a value that means "extraction worked"."""

ExtractionNotice = Literal[
    "sole-object-restates-source",
    "judge-selection-unavailable",
    "judge-selection-empty",
    "objects-without-evidence",
    "candidates-dropped-in-staging",
]
"""The closed vocabulary for `EXTRACTION_NOTICE_KEY`.

The first token is #585's: the source's SOLE derived object restates the
source's own topic, so the bundle stores an object that adds nothing the
Source did not already say. The object is kept -- see #585 for why a
degrade to `[]` was rejected -- and this key is the disclosure that keeps
the output honest about it.

The two judge tokens are #772's quarantine markers: the union+judge run
kept its FULL merged union because no quality selection was applied --
either the judge call itself was unusable after every attempt
(`judge-selection-unavailable`, `judge_status == "failed"`) or the judge
replied and its admitted set matched no candidate
(`judge-selection-empty`, `judge_status == "empty"`). Two tokens rather
than one for the same reason #754 split the terminal notices: the causes
carry different retry expectations, and a persistent marker that erased
the distinction would force the reader back to a terminal transcript that
no longer exists. `lint.check_unjudged` reads BOTH as retryable debt.

The fourth token is #801's: at least one derived object the run STORED
carries no line quoted verbatim from the source, so it records that its
subject exists while dropping the fact worth storing -- and then becomes a
citation that provably cannot support the answer it is attached to. Its
own token rather than a widening of any above, because it answers a
different question: the three before it are statements about the PIPELINE
(a judge that never answered, a judge that named nothing, a set reduced to
a single restatement), while this one is a statement about the CONTENT of
objects the pipeline was otherwise happy with. Folding it into the judge
pair would tell a reader to re-run the judge, which repairs nothing here.
`lint.check_unevidenced` reads it, under its own section and its own
finding kind for the same reason.

The fifth token is #843's: at least one candidate the run's extraction
produced was DROPPED while staging (an unslugifiable title, an in-batch
slug collision, or content that failed `build_concept`'s stricter gate),
so the bundle stores LESS than extraction produced and nothing else about
the drop reaches disk. The create-only skip is deliberately NOT one of its
causes -- the slug this same source already owns is on disk, put there by
an earlier run, so no content was lost. `lint.check_staging_dropped`
reads it, under its own section and its own finding kind, again for the
different-question-different-repair reason above."""

EXTRACTION_NOTICE_VALUES: Final[tuple[ExtractionNotice, ...]] = get_args(
    ExtractionNotice
)
"""For specs and tests, mirroring `EXTRACTION_STATUS_VALUES` -- not a
runtime validation gate."""

EXTRACTION_NOTICE_SOLE_OBJECT_RESTATES: Final[ExtractionNotice] = (
    "sole-object-restates-source"
)
"""#585's notice token, named so both the writer (`cli/main.py`) and
any future reader spell it from the same constant."""

EXTRACTION_NOTICE_JUDGE_UNAVAILABLE: Final[ExtractionNotice] = (
    "judge-selection-unavailable"
)
"""#772's quarantine token for `judge_status == "failed"`: every judge
attempt raised, returned an empty reply, or returned an unparseable/
wrong-shape reply, so the stored objects were never quality-selected."""

EXTRACTION_NOTICE_JUDGE_EMPTY: Final[ExtractionNotice] = "judge-selection-empty"
"""#772's quarantine token for `judge_status == "empty"`: the judge
REPLIED with a valid shape whose admitted set matched no candidate, so the
full (backstop-capped) union was stored unfiltered."""

EXTRACTION_NOTICE_OBJECTS_WITHOUT_EVIDENCE: Final[ExtractionNotice] = (
    "objects-without-evidence"
)
"""#801's disclosure token: at least one RETAINED derived object's written
text carries no line quoted verbatim from the source
(`extraction.concept.ExtractionReport.unevidenced_titles`,
`extraction.evidence.evidence_line` underneath). The objects are kept --
#585's rejected degrade-to-`[]` settles that trade -- and this token is
what keeps the bundle honest about storing them."""

EXTRACTION_NOTICE_CANDIDATES_DROPPED: Final[ExtractionNotice] = (
    "candidates-dropped-in-staging"
)
"""#843's disclosure token: at least one candidate extraction produced was
dropped on a content-losing staging path (empty slug, in-batch slug
collision, or a `build_concept` validation failure) -- each drop already
echoes to stderr per candidate, and this token is the durable half, so
`lint`/`status` can keep reporting a source the bundle may under-represent
after the terminal has scrolled. NOT retryable debt (`objects-without-
evidence`'s grounds exactly): a plain re-ingest re-runs the same prompt
over the same bytes and is promised to fix nothing about the sample that
failed staging, so the named redo is `--re-extract`."""

EXTRACTION_STATUS_FAILED: Final[ExtractionStatus] = "failed"
"""The one `EXTRACTION_STATUS_VALUES` member that represents retryable
debt (an LLM backend error) -- the only value `lint`'s `check_unextracted`
flags."""

MERGE_LEDGER_SCHEMA_V1: Final = "openkos.merge_ledger/v1"
"""The `schema` value every pre-slice-2a `merged_from` entry carries -- a
durable on-disk contract (ADR-0002) that a future format change must
migrate rather than silently reinterpret. A V1 entry never carries
`relation_rewrites`; `decode_merge_ledger_entry` treats an absent key on
this schema as `[]` (design D1)."""

MERGE_LEDGER_SCHEMA_V2: Final = "openkos.merge_ledger/v2"
"""The `schema` value every `merged_from` entry written from slice 2a
onward carries (design D1; ADR-0005): the ONLY additive change from V1 is
the REQUIRED `relation_rewrites` key (whole-file third-party snapshots for
inbound typed-relation retargets). Superseded by `MERGE_LEDGER_SCHEMA_V3`;
the reader still accepts V1 and V2 (spec: "Pre-slice-2a v1 ledger entry
still unmerges exactly")."""

MERGE_LEDGER_SCHEMA_V3: Final = "openkos.merge_ledger/v3"
"""The `schema` value every `merged_from` entry written from
rewrite-provenance-on-merge onward carries (design; ADR-0011): the ONLY
additive change from V2 is the REQUIRED `provenance_rewrites` key
(whole-file third-party snapshots for inbound `provenance:` retargets/
dedupes). Superseded by `MERGE_LEDGER_SCHEMA_V4`; the reader accepts V1,
V2, V3 and V4 (spec: "A v1 and a v2 ledger entry are still readable after
the v3 bump")."""

MERGE_LEDGER_SCHEMA_V4: Final = "openkos.merge_ledger/v4"
"""The `schema` value every `merged_from` entry carries from #667 onward:
the ONLY additive change from V3 is the REQUIRED `carried_content_ids`
key -- the prior-absorbed ids whose content this entry's `survivor_before`
may carry WITHOUT its `## Merged content (<id>)` delimiter (a #645
reconciliation weaves the absorbed body into the live survivor, so a
LATER merge's snapshot embeds it undelimited, where `forget`'s #602
structural excision cannot reach it). `plan_merge` computes the set at
snapshot time; `forget`'s sweep redacts the whole snapshot for a match
(privacy over reversibility, #602's own rule)."""

MERGE_LEDGER_SCHEMA_V5: Final = "openkos.merge_ledger/v5"
"""The `schema` value every `merged_from` entry carries from #758 onward:
the catalog snapshots `index_before`/`log_before` are REPLACED by
`index_restores`, the DELTA the merge actually applied to `index.md`.

V1-V4 stored a full verbatim copy of `index.md` AND `log.md` in every
entry, so a sidecar scaled with the size of the BUNDLE rather than with
the size of the merge -- measured at 79.6% of a real sidecar's bytes on a
33-document workspace after a single merge, and growing super-linearly
because each successive merge photographs a larger catalog than the last.

`log.md` needs no stored field at all: a merge's only effect on it is one
`**Merge**: Merged [<absorbed>](...) into [<survivor>](...).` bullet,
fully derivable from `absorbed_id`, the survivor id, and `merged_at` (the
same reconstruction `cli._expected_post_merge_index_and_log` already
performed to detect drift). `index.md` needs only the bullet the merge
REMOVED plus the line that preceded it, because a catalog bullet carries a
title and description that no id can regenerate.

The reader still accepts V1-V4 entries, which keep their snapshots and
their original wholesale-restore behavior (issue #758's dual-shape
ruling); only entries written from #758 onward are V5."""

REDACTED_SNAPSHOT_SENTINEL: Final = (
    "[redacted by openkos forget: this snapshot carried reconciled "
    "content of a forgotten concept]"
)
"""The exact string `forget`'s ledger sweep writes IN PLACE OF a
`survivor_before` snapshot that carried a forgotten concept's reconciled
(undelimited) content (#667). `plan_unmerge` refuses to restore a
snapshot equal to this sentinel -- restoring it would replace the live
survivor body with this notice."""


def dump_frontmatter(
    metadata: dict[str, object], body: str = "", *, width: int | None = None
) -> str:
    """Render `metadata` as a YAML frontmatter block over `body`, per §4.1.

    `width` forwards to the YAML emitter's fold width when given. The one
    caller that needs it is `bundle/source_titles._patch_title_line` (#310):
    the emitter's default (~80 columns) folds a long scalar onto a
    continuation line, which is fine for a freshly built document but turns
    a single-line surgical patch into a multi-line one. `None` keeps the
    library default so every existing document keeps its historical shape.
    """
    post = frontmatter.Post(body)
    post.metadata = metadata
    if width is None:
        return frontmatter.dumps(post) + "\n"
    return frontmatter.dumps(post, width=width) + "\n"


def load_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Parse the frontmatter block and body out of `text`, per §4.1."""
    post = frontmatter.loads(text)
    return post.metadata, post.content


def build_source_concept(
    *,
    title: str,
    description: str,
    resource: str,
    tags: list[str],
    timestamp: str,
    sensitivity: str,
    provenance: list[str],
    raw_content: str | None = None,
    extraction_status: ExtractionStatus | None = None,
    extraction_notice: ExtractionNotice | None = None,
    origin_key: str | None = None,
) -> str:
    """Build a conformant OKF Source concept document (D4/ingest-source-body D1).

    Plain dict -> `dump_frontmatter`, no pydantic: no field here is
    untrusted STRUCTURED LLM output (contrast `build_concept`, the
    fail-closed gate for exactly that), and `dump_frontmatter` goes through
    `frontmatter.dumps`, which quotes and folds correctly -- so any byte
    sequence round-trips byte-exact through `load_frontmatter`.
    `check_conformance` (§9 rules 1-2: parseable frontmatter, non-empty
    `type`) is therefore the only gate this slice needs.

    THE SOURCE'S FILENAME IS NOT A TRUSTED INPUT (issue #285). It is
    unconstrained, user-chosen text, and `ingest` carries it unsanitised
    into FOUR of these values, plus `index.md` and `log.md`. State them
    precisely, because they do not all carry the same thing: `resource` and
    `provenance` are the raw basename verbatim under a `raw/` prefix;
    `description` interpolates `resource` AND the source path exactly as the
    caller typed it, so it carries MORE than the basename; `title` is
    `_titleize`d, which maps `-`/`_` to spaces and strips, and is cosmetic
    rather than sanitising -- every other character survives it.

    `_slugify` sanitises only the document's OWN filename (the file
    `openkos` creates). `resource` is deliberately left alone: it must keep
    naming the real file on disk. Do NOT slug it -- rewriting it would break
    the correspondence `purge`'s containment check depends on.

    So every CONSUMER that interpolates these values into generated prose, a
    shell command, or any other delimited context MUST validate or escape at
    READ time. Two live precedents, so the next consumer finds the shape
    rather than rediscovering the bug: `lint.check_unextracted` declines to
    spell the retry command at all when `resource` cannot appear whole
    inside a backtick span, and `next_action._tier_unextracted_source`
    corroborates the command it extracted against the document's own
    `resource` before printing it (#274, #285).

    Two things this does NOT mean, stated so nobody over-corrects: there is
    no YAML corruption (see the round-trip guarantee above), and no path
    traversal (`Path.name` has already stripped every directory component,
    so `raw/<name>` is contained by construction).

    `description` is passed through verbatim -- callers MUST phrase it as an
    honest description of the source's embedding state (embedded verbatim,
    or could not be embedded), never claiming extraction/compilation
    occurred, matching this slice's scope.

    `raw_content` (ingest-source-body D1/D3) renders one of three body
    shapes, each honest about what happened: `raw_content` holding
    non-blank text embeds it verbatim under a `## Source content` heading;
    `None` (a decode failure) renders a short note that the content could
    not be embedded as text; blank/whitespace-only text renders a distinct
    "source is empty" note. All three end with `# Citations`.

    `extraction_status` (issue #187) is emitted as `EXTRACTION_STATUS_KEY`
    ONLY when not `None`; the default `None` keeps a healthy Source's
    frontmatter byte-identical to before this parameter existed. Callers
    stamp this onto freshly built content -- never merge it onto an
    already-built document's frontmatter (that would make a stale marker
    sticky forever).

    `extraction_notice` (issue #585) is emitted as `EXTRACTION_NOTICE_KEY`
    under exactly the same rules, and is an INDEPENDENT parameter: this
    function does not enforce that the two never co-occur. They cannot
    today -- one presupposes zero derived objects and the other exactly one
    -- but that is `ingest`'s invariant to hold, and a writer that silently
    dropped one of them would hide a future caller's mistake instead of
    letting it surface on disk.
    """
    metadata: dict[str, object] = {
        "type": "Source",
        "title": title,
        "description": description,
        "resource": resource,
        "tags": tags,
        "timestamp": timestamp,
        "status": "active",
        "version": 1,
        "freshness": "snapshot",
        "sensitivity": sensitivity,
        "provenance": provenance,
    }
    if extraction_status is not None:
        metadata[EXTRACTION_STATUS_KEY] = extraction_status
    if extraction_notice is not None:
        metadata[EXTRACTION_NOTICE_KEY] = extraction_notice
    if origin_key is not None:
        metadata[ORIGIN_KEY_KEY] = origin_key
    if raw_content is None:
        section = (
            "_Source content could not be embedded as text "
            "(binary or non-UTF-8); see the linked resource._\n\n"
        )
    elif not raw_content.strip():
        section = "_The source file is empty._\n\n"
    else:
        section = f"## Source content\n\n{raw_content}\n\n"
    body = f"# {title}\n\n{description}\n\n{section}# Citations\n"
    return dump_frontmatter(metadata, body)


def build_concept(
    *,
    type: str,
    title: str,
    description: str,
    body: str,
    provenance: list[str],
    sensitivity: str,
    timestamp: str,
    related_note: str = "source this was extracted from",
    type_alternative: str | None = None,
) -> str:
    """Build a conformant OKF derived-object document from LLM-extracted,
    UNTRUSTED fields (design: "Builder validation").

    Unlike `build_source_concept` (whose inputs are not structured LLM
    output, so it skips validation -- see its docstring, which is also
    explicit that the filename it carries is NOT trusted), this builder is
    the fail-closed gate for `extraction.ExtractionResult` data: `type` MUST
    be a member of the closed classifiable vocabulary (see
    `openkos.model.types.CLASSIFIABLE_TYPES`, the single source of truth);
    `title`/`description` MUST be non-empty
    after stripping whitespace AND single-line (no embedded newlines, since
    each is a single Markdown/heading line); and `provenance` MUST be
    non-empty (a derived object always cites the Source it came from). Any
    violation raises `ValueError` rather than emitting a non-conformant or
    misleading document.

    `description` is a one-line lede; `body` follows it only when non-blank,
    so a blank body does not duplicate the description paragraph. A `## Related`
    section then backlinks every `provenance` entry -- each a Source concept-id
    path such as `sources/<slug>` -- using `related_note` as the trailing
    phrase (default: "source this was extracted from", today's ingest
    literal -- ingest never passes this kwarg, so its output stays
    byte-identical). A filed `query --save` answer passes a concept-to-concept
    phrasing instead (design: "Parameterize `## Related` wording (byte-identical
    ingest)"). `tags` is always `[]`: this slice has no tagging step.
    """
    if type not in _CONCEPT_TYPES:
        raise ValueError(f"type must be one of {sorted(_CONCEPT_TYPES)}, got {type!r}")
    if not title.strip():
        raise ValueError("title must be non-empty")
    if not description.strip():
        raise ValueError("description must be non-empty")
    if "\n" in title or "\r" in title:
        raise ValueError("title must not contain newlines")
    if "\n" in description or "\r" in description:
        raise ValueError("description must not contain newlines")
    if not provenance:
        raise ValueError("provenance must be non-empty for a derived object")
    if type_alternative is not None:
        # Validated as strictly as `type`: this value arrives from the same
        # untrusted LLM reply. `extraction._validate` already degrades a
        # malformed one to `None`, so anything reaching here is either
        # genuine or a caller bug -- and silently writing
        # `type_alternative: Sandwich` would make the bundle non-conformant
        # on the strength of a typo.
        if type_alternative not in _CONCEPT_TYPES:
            raise ValueError(
                f"type_alternative must be one of {sorted(_CONCEPT_TYPES)}, "
                f"got {type_alternative!r}"
            )
        if type_alternative == type:
            # Not silently dropped: a caller holding both values equal has
            # lost track of its own data, and the document would otherwise
            # assert a boundary between a type and itself.
            raise ValueError(
                f"type_alternative must differ from type, both were {type!r}"
            )

    metadata: dict[str, object] = {
        "type": type,
        "title": title,
        "description": description,
        "tags": [],
        "timestamp": timestamp,
        "status": "active",
        "version": 1,
        "freshness": "snapshot",
        "sensitivity": sensitivity,
        "provenance": provenance,
    }
    if type_alternative is not None:
        # Set only when present, so a document with no near-boundary call
        # stays byte-identical to what this builder emitted before #401.
        metadata[TYPE_ALTERNATIVE_KEY] = type_alternative
    related = "\n".join(f"- [{ref}](/{ref}.md) — {related_note}" for ref in provenance)
    # `description` is a one-line lede; append `body` only when it adds content,
    # so a blank-body fallback does not render the description paragraph twice.
    lede = description if not body.strip() else f"{description}\n\n{body}"
    doc_body = f"# {title}\n\n{lede}\n\n## Related\n\n{related}\n"
    return dump_frontmatter(metadata, doc_body)


def _rank(value: object) -> int:
    """Rank a raw sensitivity `value` into `SENSITIVITY_ORDER`'s index space,
    failing closed on anything dirty (ADR-0003).

    A missing (`None`) or blank/whitespace-only string ranks as `private`
    (the config default floor, docs/knowledge-object-model.md's
    `default_sensitivity`). A string matching (after stripping) one of
    `SENSITIVITY_ORDER`'s canonical members ranks at its position. Anything
    else -- a non-string value (e.g. an `int`/`list` from dirty frontmatter)
    or an unrecognized string -- ranks as `confidential`, the most
    restrictive level: a security field must fail toward MORE restrictive,
    never less.
    """
    if value is None:
        return SENSITIVITY_ORDER.index("private")
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return SENSITIVITY_ORDER.index("private")
        if stripped in SENSITIVITY_ORDER:
            return SENSITIVITY_ORDER.index(stripped)
    return SENSITIVITY_ORDER.index("confidential")


def sensitivity_direction(
    current: object, target: str
) -> Literal["raise", "same", "lower"]:
    """Classify a proposed sensitivity change from `current` to `target`.

    `target` MUST already be a validated member of `SENSITIVITY_ORDER`; the
    caller is responsible for that check -- this helper does not repeat it.
    `current` is deliberately typed `object`: it is raw, possibly dirty
    frontmatter (missing, blank, non-string, or an unrecognized string), and
    ranks through the same fail-closed `_rank` that `combine_sensitivity`
    uses (ADR-0003). A missing/blank `current` floors at `private`; anything
    else dirty floors at `confidential` -- the most restrictive level -- so a
    `target` below that floor always classifies as `"lower"`, never `"same"`
    or `"raise"`. This is the security-load-bearing behavior a downgrade
    gate depends on (ADR-0008).

    Returns a three-way verdict rather than an integer rank or a bool: `_rank`
    stays private (exporting it would invite ad-hoc call-site comparisons,
    the alternative ADR-0003 already rejected), and a bool `is_downgrade`
    cannot honestly label the dirty-but-equivalent `"same"` case in a caller's
    preview.
    """
    current_rank = _rank(current)
    target_rank = SENSITIVITY_ORDER.index(target)
    if target_rank > current_rank:
        return "raise"
    if target_rank == current_rank:
        return "same"
    return "lower"


def raise_by(level: object, offset: int) -> str:
    """Raise `level` by `offset` steps in `SENSITIVITY_ORDER`, clamped at the
    ceiling (`confidential`).

    Pure, stdlib-only, reuses `_rank`'s fail-closed ranking, so a missing,
    blank, non-string, or unrecognized `level` still resolves to a
    canonical member before the offset is applied. Clamps rather than
    raising on overflow: the offset is operator-configured against a
    workspace floor that may already be `confidential`, and that must not
    make every birth fail. A negative `offset` raises `ValueError`: a
    helper named `raise_by` that could LOWER a security level is a
    downgrade vector, and config-load validation (design D1) already
    refuses a negative offset earlier -- this is defence in depth at the
    pure layer (design D2).
    """
    if offset < 0:
        raise ValueError(f"raise_by: offset must be non-negative, got {offset!r}")
    return SENSITIVITY_ORDER[min(_rank(level) + offset, len(SENSITIVITY_ORDER) - 1)]


def combine_sensitivity(a: object, b: object) -> str:
    """Combine two sensitivity values into the more restrictive (max-rank)
    of the two, per ADR-0003's high-water-mark rule.

    Pure, deterministic, stdlib-only: no I/O. Always returns a canonical
    member of `SENSITIVITY_ORDER`, even when `a`/`b` are missing or
    malformed (`_rank` fails closed). This is the recompute step a merge
    invokes at build time -- the result is never a verbatim copy of either
    input's sensitivity.
    """
    return SENSITIVITY_ORDER[max(_rank(a), _rank(b))]


@dataclass(frozen=True)
class LinkRewrite:
    """One inbound-link rewrite performed (or to be reversed) by a merge
    (spec: Inbound-Link Rewrite; ADR-0002's `link_rewrites`).

    `file` is the bundle-relative path the rewrite happened in; `old_link`/
    `new_link` are the exact markdown link targets substituted -- the
    values `bundle/links.py` (U3) needs to bound its reversal to these
    specific recorded occurrences, never a blind replace-all.

    `offset` is the character offset, in the POST-merge `file` text, where
    THIS rewrite's `new_link` occurrence begins. It is the positional
    disambiguator `reverse_link_rewrites` needs: when a file links to BOTH
    the absorbed AND survivor concepts, after the merge there are TWO
    `](/survivor.md)`-shaped occurrences in that file (one just rewritten,
    one coincidentally pre-existing) and a target-string-only reverse
    cannot tell them apart -- it may revert the wrong one and break
    byte-parity. Reversing at the exact recorded `offset` instead removes
    the ambiguity entirely."""

    file: str
    old_link: str
    new_link: str
    offset: int


@dataclass(frozen=True)
class CatalogLineRestore:
    """One `index.md` bullet a merge REMOVED, plus the anchor needed to put
    it back exactly where it was (issue #758, `MERGE_LEDGER_SCHEMA_V5`).

    The V1-V4 ledger stored the whole pre-merge `index.md` and restored it
    wholesale, which both scaled with the bundle and destroyed any catalog
    work that landed between the merge and the unmerge. This records the
    DELTA instead: the exact line, and where it sat.

    `line` is the removed line INCLUDING its trailing newline, so
    reinsertion is a pure splice with no separator guessing.

    `preceded_by` is the nearest NON-BLANK line above it in the catalog BODY
    (frontmatter excluded), or `""` when none exists. It is a CONTENT anchor
    rather than a character offset -- unlike `LinkRewrite`, whose file is
    restored wholesale around it, a catalog is appended to by every
    `ingest`, so any offset recorded at merge time is stale by the time
    `unmerge` runs.

    Blank lines are skipped rather than used (review correction, reliability
    lens). A section's FIRST bullet is always preceded by the blank line
    `insert_index_entry` writes under the header, and blank lines are
    interchangeable and multiply with every new section -- so anchoring on
    one, and counting its occurrences over the whole body, pointed at a
    different blank line as soon as any unrelated section was catalogued
    above it. The bullet then went back in the wrong place, breaking
    byte-parity through an ordinary `ingest`. A section header or a sibling
    bullet carries identity; a blank line carries none.

    `blank_gap` is how many blank lines sat between that anchor and the
    removed line, so the exact position is restored rather than merely the
    right neighbourhood.

    `preceded_by_occurrence` is the 0-based index of WHICH occurrence of the
    anchor line this was, counted over NON-BLANK lines only, and exists for
    exactly the reason `LinkRewrite.offset` does. A catalog may legitimately
    hold two byte-identical bullets -- `remove_index_entry`'s own contract
    calls that "a duplicate catalog entry" and handles it rather than
    refusing -- and a content-only anchor cannot tell them apart, so it
    would either refuse a reversible merge or reinsert under the wrong one.
    Reinsertion fails closed when that occurrence no longer exists: a
    catalog that drifted past recognition must refuse, never guess."""

    line: str
    preceded_by: str
    preceded_by_occurrence: int = 0
    blank_gap: int = 0


@dataclass(frozen=True)
class RelationRewrite:
    """One third-party file's whole-file pre-merge snapshot, recorded when
    that file's `relations:` targeted the absorbed id (design D1/D3; spec:
    "Third-party inbound relations retarget to the survivor").

    Unlike `LinkRewrite` (reversed at an exact character `offset`),
    `snapshot` is the file's FULL verbatim bytes immediately BEFORE this
    merge -- a `relations:` retarget/drop/dedupe has no stable
    disambiguating position analogous to a link occurrence, so
    `bundle/relations.py::reverse_relation_rewrites` always restores by
    ABSOLUTE whole-file overwrite, never offset math (design D4's
    overlapping-LIFO proof relies on this)."""

    file: str
    snapshot: str


@dataclass(frozen=True)
class ProvenanceRewrite:
    """One third-party file's whole-file pre-merge snapshot, recorded when
    that file's `provenance:` targeted the absorbed id (design;
    rewrite-provenance-on-merge; spec: "Reversible Inbound-Provenance
    Rewiring").

    Mirrors `RelationRewrite` exactly: `snapshot` is the file's FULL
    verbatim bytes immediately BEFORE this merge -- `provenance:` is a YAML
    list field with no stable disambiguating position analogous to a link
    occurrence, so `bundle/provenance.py::reverse_provenance_rewrites`
    always restores by ABSOLUTE whole-file overwrite, never offset math."""

    file: str
    snapshot: str


@dataclass(frozen=True)
class DescendantRaise:
    """One staged raise-only descendant write, computed by
    `bundle.provenance.resolve_source_raises` (design: "Set-time propagation,
    Interfaces / Contracts"). `current` is the descendant's raw, possibly
    dirty `sensitivity` value (fail-closed ranked by `combine_sensitivity`,
    ADR-0003); `new_level` is always a strict raise over it -- a member with
    `combine_sensitivity(current, level) == current` is never staged.
    `content` is the descendant's full frontmatter-plus-body text, already
    re-rendered via `dump_frontmatter`, ready to write as-is.

    Deliberately WITHOUT a `path` field: `Path` is a filesystem concern, and
    this dataclass lives in the pure, `Path`-free model layer. Every caller
    derives its own write target as `layout.bundle_dir / f"{concept_id}.md"`.
    """

    concept_id: str
    current: object
    new_level: str
    content: str


@dataclass(frozen=True)
class MergeLedgerEntry:
    """One `merged_from` list entry: the FULL pre-merge snapshot set for one
    absorbed object (spec: Reversibility Ledger; ADR-0002).

    Round-trip parity is logically impossible from `absorbed_snapshot`
    alone -- provenance union, tag union, sensitivity high-water-mark, and
    freshness-most-recent are all lossy/non-invertible -- so every field
    below is required. `survivor_before` is the survivor's FULL verbatim
    bytes immediately prior to THIS merge's write, explicitly RETAINING any
    prior `merged_from` entries from earlier merges (it excludes ONLY this
    entry, which does not yet exist at snapshot time); it does NOT strip
    the whole `merged_from` key. This is what lets sequential pairwise
    merges reverse losslessly in LIFO order.

    `sensitivity_before` uses `""` (empty string) as the sentinel for
    "survivor had no `sensitivity` key at merge time" -- distinct from the
    canonical `public`/`private`/`confidential` values `SENSITIVITY_ORDER`
    defines.

    `relation_rewrites` (design D1, v2 addition) holds one whole-file
    snapshot per third-party file whose `relations:` were retargeted,
    dropped as a self-loop, or deduped by this merge. It defaults to `[]`
    so every pre-slice-2a (v1) construction of this dataclass -- including
    every existing test helper -- keeps working unchanged; `plan_merge`
    always populates it explicitly.

    `provenance_rewrites` (v3 addition, rewrite-provenance-on-merge) holds
    one whole-file snapshot per third-party file whose `provenance:` was
    retargeted or deduped by this merge. It defaults to `[]` for the same
    backward-compatibility reason as `relation_rewrites`; `plan_merge`
    always populates it explicitly and always writes
    `MERGE_LEDGER_SCHEMA_V3`."""

    schema: str
    merged_at: str
    absorbed_id: str
    absorbed_snapshot: str
    survivor_before: str
    index_before: str
    log_before: str
    link_rewrites: list[LinkRewrite]
    sensitivity_before: str
    sensitivity_after: str
    relation_rewrites: list[RelationRewrite] = field(default_factory=list)
    provenance_rewrites: list[ProvenanceRewrite] = field(default_factory=list)
    carried_content_ids: list[str] = field(default_factory=list)
    """V4 addition (#667): prior-absorbed ids whose content
    `survivor_before` may carry WITHOUT its delimited section -- computed
    by `plan_merge` as "absorbed earlier into this survivor, but its
    `## Merged content (<id>)` heading is absent from the snapshot being
    recorded" (the #645-reconciled shape). Defaults to `[]` for the same
    backward-compatibility reason as the other versioned fields;
    `plan_merge` always populates it explicitly and always writes
    `MERGE_LEDGER_SCHEMA_V4`."""

    index_restores: list[CatalogLineRestore] = field(default_factory=list)
    """V5 addition (#758): the `index.md` bullets this merge REMOVED, each
    with the anchor that puts it back. REPLACES `index_before`/`log_before`
    -- a V5 entry carries both of those as `""` and `unmerge` reverses the
    catalog SURGICALLY from this list, instead of overwriting `index.md`
    with a whole-bundle snapshot. `log.md` has no counterpart field at all,
    because a merge's only effect on it is one derivable `**Merge**`
    bullet (see `MERGE_LEDGER_SCHEMA_V5`). Defaults to `[]` for the same
    backward-compatibility reason as the other versioned fields; a V1-V4
    entry never carries it, and `plan_merge` always populates it
    explicitly."""


def encode_merge_ledger_entry(entry: MergeLedgerEntry) -> dict[str, object]:
    """Turn one `MergeLedgerEntry` into a plain-dict shape safe for
    `dump_frontmatter` -- never hand-spliced YAML (ADR-0002).

    Fails closed (`ValueError`, correction batch finding 2) when `entry.schema
    == MERGE_LEDGER_SCHEMA_V1` and `entry.relation_rewrites` is non-empty: a
    V1 entry never carries `relation_rewrites` (see `MERGE_LEDGER_SCHEMA_V1`'s
    docstring), so a caller that constructs one WITH populated
    `relation_rewrites` anyway holds a self-contradictory entry --
    `decode_merge_ledger_entry`'s V1 branch unconditionally discards that
    key, so silently encoding it here would let it round-trip to `[]`
    without any signal. Raising here, rather than silently dropping the
    field to match, surfaces the construction bug at its source instead of
    at a much later, harder-to-trace decode.

    A SECOND, mirrored guard (rewrite-provenance-on-merge) fails closed when
    `entry.schema` is V1 OR V2 and `entry.provenance_rewrites` is non-empty:
    neither schema's decoder reads `provenance_rewrites` (only V3 does), so
    the same silent-round-trip-loss risk applies -- only V3 may carry a
    non-empty `provenance_rewrites`."""
    if entry.schema == MERGE_LEDGER_SCHEMA_V1 and entry.relation_rewrites:
        raise ValueError(
            "a MERGE_LEDGER_SCHEMA_V1 entry must not carry relation_rewrites"
        )
    if (
        entry.schema in (MERGE_LEDGER_SCHEMA_V1, MERGE_LEDGER_SCHEMA_V2)
        and entry.provenance_rewrites
    ):
        raise ValueError(f"a {entry.schema} entry must not carry provenance_rewrites")
    if (
        entry.schema
        in (MERGE_LEDGER_SCHEMA_V1, MERGE_LEDGER_SCHEMA_V2, MERGE_LEDGER_SCHEMA_V3)
        and entry.carried_content_ids
    ):
        raise ValueError(f"a {entry.schema} entry must not carry carried_content_ids")
    if entry.schema != MERGE_LEDGER_SCHEMA_V5 and entry.index_restores:
        raise ValueError(f"a {entry.schema} entry must not carry index_restores")
    if entry.schema == MERGE_LEDGER_SCHEMA_V5 and (
        entry.index_before or entry.log_before
    ):
        raise ValueError(
            "a MERGE_LEDGER_SCHEMA_V5 entry must not carry index_before/log_before "
            "-- the catalog delta replaced them (#758)"
        )
    encoded: dict[str, object] = {
        "schema": entry.schema,
        "merged_at": entry.merged_at,
        "absorbed_id": entry.absorbed_id,
        "absorbed_snapshot": entry.absorbed_snapshot,
        "survivor_before": entry.survivor_before,
        "link_rewrites": [
            {
                "file": lr.file,
                "old_link": lr.old_link,
                "new_link": lr.new_link,
                "offset": lr.offset,
            }
            for lr in entry.link_rewrites
        ],
        "sensitivity_before": entry.sensitivity_before,
        "sensitivity_after": entry.sensitivity_after,
        "relation_rewrites": [
            {"file": rr.file, "snapshot": rr.snapshot} for rr in entry.relation_rewrites
        ],
        "provenance_rewrites": [
            {"file": pr.file, "snapshot": pr.snapshot}
            for pr in entry.provenance_rewrites
        ],
        "carried_content_ids": list(entry.carried_content_ids),
    }
    if entry.schema == MERGE_LEDGER_SCHEMA_V5:
        # The catalog DELTA replaces the two whole-file snapshots; the keys
        # are omitted outright rather than written empty, so a V5 sidecar
        # never carries a field whose name promises a snapshot it does not
        # hold (#758).
        encoded["index_restores"] = [
            {
                "line": restore.line,
                "preceded_by": restore.preceded_by,
                "preceded_by_occurrence": restore.preceded_by_occurrence,
                "blank_gap": restore.blank_gap,
            }
            for restore in entry.index_restores
        ]
    else:
        encoded["index_before"] = entry.index_before
        encoded["log_before"] = entry.log_before
    return encoded


def encode_merged_from(entries: list[MergeLedgerEntry]) -> list[dict[str, object]]:
    """Encode a full `merged_from` list (LIFO order preserved) for assignment
    onto a survivor's frontmatter metadata dict before `dump_frontmatter`."""
    return [encode_merge_ledger_entry(entry) for entry in entries]


def _decode_link_rewrite(raw: object) -> LinkRewrite:
    """Parse one `link_rewrites` list item back into a `LinkRewrite`, failing
    closed (`ValueError`) on anything malformed. `offset` is required (not
    defaulted to `0`) -- a ledger entry missing it must never be silently
    misread, since `reverse_link_rewrites` trusts it for exact positional
    reversal."""
    if not isinstance(raw, dict):
        raise ValueError(
            f"link_rewrites entry must be a mapping, got {type(raw).__name__}"
        )
    try:
        return LinkRewrite(
            file=str(raw["file"]),
            old_link=str(raw["old_link"]),
            new_link=str(raw["new_link"]),
            offset=int(raw["offset"]),
        )
    except KeyError as exc:
        raise ValueError(f"link_rewrites entry missing field {exc}") from exc


def _decode_relation_rewrite(raw: object) -> RelationRewrite:
    """Parse one `relation_rewrites` list item back into a
    `RelationRewrite`, failing closed (`ValueError`) on anything malformed
    -- mirrors `_decode_link_rewrite`."""
    if not isinstance(raw, dict):
        raise ValueError(
            f"relation_rewrites entry must be a mapping, got {type(raw).__name__}"
        )
    try:
        return RelationRewrite(file=str(raw["file"]), snapshot=str(raw["snapshot"]))
    except KeyError as exc:
        raise ValueError(f"relation_rewrites entry missing field {exc}") from exc


def _decode_provenance_rewrite(raw: object) -> ProvenanceRewrite:
    """Parse one `provenance_rewrites` list item back into a
    `ProvenanceRewrite`, failing closed (`ValueError`) on anything malformed
    -- mirrors `_decode_relation_rewrite`."""
    if not isinstance(raw, dict):
        raise ValueError(
            f"provenance_rewrites entry must be a mapping, got {type(raw).__name__}"
        )
    try:
        return ProvenanceRewrite(file=str(raw["file"]), snapshot=str(raw["snapshot"]))
    except KeyError as exc:
        raise ValueError(f"provenance_rewrites entry missing field {exc}") from exc


def _decode_catalog_line_restore(raw: object) -> CatalogLineRestore:
    """Parse one `index_restores` list item back into a
    `CatalogLineRestore`, failing closed (`ValueError`) on anything
    malformed -- mirrors `_decode_link_rewrite`. `preceded_by` is REQUIRED
    even though `""` is a legitimate value (the removed line was the body's
    first): a missing key and a deliberately empty anchor mean different
    things, and defaulting the former to the latter would silently
    reinsert at the top of a catalog it never belonged to. So is
    `preceded_by_occurrence`, for the same reason `_decode_link_rewrite`
    requires `offset`: a positional disambiguator silently defaulted to `0`
    would reinsert under the FIRST duplicate anchor rather than the
    recorded one."""
    if not isinstance(raw, dict):
        raise ValueError(
            f"index_restores entry must be a mapping, got {type(raw).__name__}"
        )
    try:
        return CatalogLineRestore(
            line=str(raw["line"]),
            preceded_by=str(raw["preceded_by"]),
            preceded_by_occurrence=int(raw["preceded_by_occurrence"]),
            blank_gap=int(raw["blank_gap"]),
        )
    except KeyError as exc:
        raise ValueError(f"index_restores entry missing field {exc}") from exc


def decode_merge_ledger_entry(raw: object) -> MergeLedgerEntry:
    """Parse one `merged_from` list item back into a `MergeLedgerEntry`,
    failing closed (`ValueError`) on any malformed or missing field -- a
    corrupt ledger entry must never be silently misread, since `unmerge`
    trusts it for byte-for-byte restoration.

    `schema` branches (design D1, extended for v3): V1 -> `relation_rewrites`
    and `provenance_rewrites` both default to `[]` regardless of whether the
    raw dict happens to carry either key (a genuine pre-slice-2a entry never
    has them at all -- spec: "Pre-slice-2a v1 ledger entry still unmerges
    exactly"); V2 -> the `relation_rewrites` key is REQUIRED, and
    `provenance_rewrites` defaults to `[]` (V2 entries predate that field);
    V3 -> BOTH `relation_rewrites` and `provenance_rewrites` keys are
    REQUIRED, and a missing key (or a malformed item within either) fails
    closed exactly like any other required field; V5 (#758) additionally
    REQUIRES `index_restores` and, uniquely, has NO `index_before`/
    `log_before` keys at all -- the catalog delta replaced them, so both
    decode to `""`; any other schema string is unsupported and rejected
    outright."""
    if not isinstance(raw, dict):
        raise ValueError(
            f"merged_from entry must be a mapping, got {type(raw).__name__}"
        )
    try:
        schema = str(raw["schema"])
        relation_rewrites: list[RelationRewrite]
        provenance_rewrites: list[ProvenanceRewrite]
        carried_content_ids: list[str] = []
        index_restores: list[CatalogLineRestore] = []
        if schema == MERGE_LEDGER_SCHEMA_V1:
            relation_rewrites = []
            provenance_rewrites = []
        elif schema == MERGE_LEDGER_SCHEMA_V2:
            relation_rewrites = [
                _decode_relation_rewrite(item) for item in raw["relation_rewrites"]
            ]
            provenance_rewrites = []
        elif schema == MERGE_LEDGER_SCHEMA_V3:
            relation_rewrites = [
                _decode_relation_rewrite(item) for item in raw["relation_rewrites"]
            ]
            provenance_rewrites = [
                _decode_provenance_rewrite(item) for item in raw["provenance_rewrites"]
            ]
        elif schema == MERGE_LEDGER_SCHEMA_V4:
            relation_rewrites = [
                _decode_relation_rewrite(item) for item in raw["relation_rewrites"]
            ]
            provenance_rewrites = [
                _decode_provenance_rewrite(item) for item in raw["provenance_rewrites"]
            ]
            raw_carried = raw["carried_content_ids"]
            if not isinstance(raw_carried, list):
                raise ValueError(
                    "carried_content_ids must be a list, got "
                    f"{type(raw_carried).__name__}"
                )
            carried_content_ids = [str(item) for item in raw_carried]
        elif schema == MERGE_LEDGER_SCHEMA_V5:
            relation_rewrites = [
                _decode_relation_rewrite(item) for item in raw["relation_rewrites"]
            ]
            provenance_rewrites = [
                _decode_provenance_rewrite(item) for item in raw["provenance_rewrites"]
            ]
            raw_carried = raw["carried_content_ids"]
            if not isinstance(raw_carried, list):
                raise ValueError(
                    "carried_content_ids must be a list, got "
                    f"{type(raw_carried).__name__}"
                )
            carried_content_ids = [str(item) for item in raw_carried]
            raw_restores = raw["index_restores"]
            if not isinstance(raw_restores, list):
                raise ValueError(
                    f"index_restores must be a list, got {type(raw_restores).__name__}"
                )
            index_restores = [
                _decode_catalog_line_restore(item) for item in raw_restores
            ]
        else:
            raise ValueError(f"unsupported merged_from schema version: {schema!r}")
        link_rewrites = [_decode_link_rewrite(item) for item in raw["link_rewrites"]]
        # V5 replaced the two whole-file catalog snapshots with the delta
        # above, so their keys are ABSENT rather than empty (#758); every
        # earlier schema still requires both.
        catalog_snapshots_stored = schema != MERGE_LEDGER_SCHEMA_V5
        return MergeLedgerEntry(
            schema=schema,
            merged_at=str(raw["merged_at"]),
            absorbed_id=str(raw["absorbed_id"]),
            absorbed_snapshot=str(raw["absorbed_snapshot"]),
            survivor_before=str(raw["survivor_before"]),
            index_before=str(raw["index_before"]) if catalog_snapshots_stored else "",
            log_before=str(raw["log_before"]) if catalog_snapshots_stored else "",
            link_rewrites=link_rewrites,
            sensitivity_before=str(raw["sensitivity_before"]),
            sensitivity_after=str(raw["sensitivity_after"]),
            relation_rewrites=relation_rewrites,
            provenance_rewrites=provenance_rewrites,
            carried_content_ids=carried_content_ids,
            index_restores=index_restores,
        )
    except KeyError as exc:
        raise ValueError(f"merged_from entry missing field {exc}") from exc
    except TypeError as exc:
        raise ValueError(f"merged_from entry malformed: {exc}") from exc


def decode_merged_from(metadata: dict[str, object]) -> list[MergeLedgerEntry]:
    """Read the `merged_from` ledger list off a survivor's `metadata`.

    Absent key returns `[]` (no prior merges). A present-but-non-list value
    fails closed (`ValueError`) -- a corrupt ledger key must never be
    silently ignored."""
    raw = metadata.get(MERGED_FROM_KEY)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(
            f"{MERGED_FROM_KEY!r} must be a list, got {type(raw).__name__}"
        )
    return [decode_merge_ledger_entry(item) for item in raw]


@dataclass(frozen=True)
class Relation:
    """One `relations:` list entry: a typed outbound edge from this document
    to `target` (spec: "`relations:` Frontmatter Field Shape"; design:
    SHAPE).

    `target` is the bundle-relative concept-id the edge points to, `.md`
    stripped -- byte-identical to how `provenance` (`sources/<slug>`) and
    `MergeLedgerEntry.absorbed_id` reference objects today (NOT a
    `/...md` link, NOT a bare slug). `type` is the edge's relation-type
    string: any non-empty, single-line value round-trips through this
    codec -- `model/relations.py::validate_relation_type`'s WARN-on-unknown
    gate is enforced by the `relate` CLI verb, not here; this layer only
    rejects an empty/whitespace value or one containing `\\n`/`\\r`."""

    target: str
    type: str


def _validate_relation_field(field_name: str, value: str) -> str:
    """Shared fail-closed guard for a `Relation` field: non-empty after
    stripping, and no embedded `\\n`/`\\r` (mirrors the existing index/log
    newline-injection guards -- spec: "Newline in target or type is
    rejected")."""
    if "\n" in value or "\r" in value:
        raise ValueError(f"relation {field_name} must not contain newlines")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"relation {field_name} must be non-empty")
    return stripped


def _normalize_relation_path(value: str) -> str:
    """Normalize a bundle-relative path-shaped string to its canonical
    relative-posix form (correction batch, finding 2): strip a leading `/`
    (a hand-authored target may mirror this codebase's own
    `[text](/id.md)` link style) and collapse redundant separators/`.`
    segments (e.g. `concepts//absorbed`, `./concepts/absorbed`) via
    `PurePosixPath`.

    Deliberately does NOT reject `..` traversal -- that is left as-is (it
    simply will not match any real node/target-id, same as today) rather
    than adding a new path-security layer in this batch; a future slice may
    add explicit rejection if a real need for one is found."""
    normalized = PurePosixPath(value.lstrip("/")).as_posix()
    return "" if normalized == "." else normalized


def _validate_relation_target(value: str) -> str:
    """Shared target-normalization guard: fail-closed field validation, then
    canonicalize the path shape (leading `/`, redundant separators) and
    strip a `.md` suffix (design: SHAPE), then re-check non-empty (a target
    that is non-empty only by virtue of its `.md` suffix, e.g. exactly
    ".md", must still be rejected).

    Shared by `encode_relation` and `decode_relation` so this normalization
    is symmetric on both sides of the codec: a stored non-canonical target
    (e.g. hand-edited with a leading `/`, or a `.md` suffix) always decodes
    to the same canonical form it would have been encoded to, keeping the
    codec round-trip stable and both `relate`'s idempotency dedup and the
    merge guard's/graph's raw string-equality target match correct
    regardless of how the `relations:` entry was produced."""
    target = _normalize_relation_path(
        _validate_relation_field("target", value)
    ).removesuffix(".md")
    if not target:
        raise ValueError("relation target must be non-empty")
    return target


def encode_relation(relation: Relation) -> dict[str, object]:
    """Turn one `Relation` into a plain-dict shape safe for
    `dump_frontmatter`, with `target`'s `.md` suffix stripped (design:
    SHAPE)."""
    target = _validate_relation_target(relation.target)
    rel_type = _validate_relation_field("type", relation.type)
    return {"target": target, "type": rel_type}


def encode_relations(relations: list[Relation]) -> list[dict[str, object]]:
    """Encode a full `relations:` list for assignment onto a document's
    frontmatter metadata dict before `dump_frontmatter`.

    Entries are SORTED by `(target, type)` (task 1.6) for deterministic
    re-emission and stable dedup, regardless of the order they were built
    in."""
    encoded = [encode_relation(relation) for relation in relations]
    return sorted(encoded, key=lambda entry: (entry["target"], entry["type"]))


def decode_relation(raw: object) -> Relation:
    """Parse one `relations:` list item back into a `Relation`, failing
    closed (`ValueError`) on anything malformed -- a corrupt or hand-edited
    `relations:` entry must never be silently misread."""
    if not isinstance(raw, dict):
        raise ValueError(f"relations entry must be a mapping, got {type(raw).__name__}")
    try:
        target = str(raw["target"])
        rel_type = str(raw["type"])
    except KeyError as exc:
        raise ValueError(f"relations entry missing field {exc}") from exc
    target = _validate_relation_target(target)
    rel_type = _validate_relation_field("type", rel_type)
    return Relation(target=target, type=rel_type)


def decode_relations(metadata: dict[str, object]) -> list[Relation]:
    """Read the `relations:` list off a document's `metadata`.

    Absent key returns `[]` (no relations -- spec: "Absent relations key is
    valid"). A present-but-non-list value fails closed (`ValueError`) -- a
    corrupt `relations:` key must never be silently ignored."""
    raw = metadata.get(RELATIONS_KEY)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{RELATIONS_KEY!r} must be a list, got {type(raw).__name__}")
    return [decode_relation(item) for item in raw]


def merge_relations(
    survivor_relations: list[Relation],
    absorbed_relations: list[Relation],
    *,
    survivor_id: str,
    absorbed_id: str,
) -> tuple[list[Relation], list[Relation], list[Relation]]:
    """Combine a survivor's and an absorbed object's `relations:` lists into
    the merged survivor's outbound edges (design D2; spec: "Reversible
    Typed-Relation Rewiring"): OUTBOUND move, SELF-LOOP drop, survivor-side
    DEDUPE -- the atomic pair with the guard removal (task 1.1-1.4). This is
    the ONLY place that computes the OUTBOUND-merge relation set; inbound
    third-party retargeting is a separate, later concern
    (`bundle/relations.py`, PR2).

    Every entry from `survivor_relations`, then every entry from
    `absorbed_relations`, is considered in turn (order-preserving, mirrors
    `_union_dedup`): an entry whose `target` equals `absorbed_id` is
    RETARGETED to `survivor_id` regardless of which side it came from -- the
    absorbed object's own edges move onto the survivor, and a survivor edge
    that already pointed at the soon-to-vanish absorbed id is redirected
    rather than left dangling.

    An entry is a RESULTING self-loop -- dropped, never emitted -- when its
    final target is `survivor_id` AND it came from the absorbed side (its
    source object is becoming the survivor, so any edge back at the
    survivor is now the survivor pointing at itself), OR it was retargeted
    from `absorbed_id` (the retarget itself produced the self-loop). A
    survivor-side entry that ALREADY targeted `survivor_id` before this
    merge (a pre-existing, unrelated self-loop) is left untouched -- that is
    not this merge's business to silently rewrite.

    An entry duplicating one already accepted into the merged list (by
    `(target, type)` equality) is a COLLISION -- dropped, reported, never
    duplicated.

    Returns `(merged, dropped_self_loops, deduped_collisions)`: the merged,
    order-preserving relation list (still to be re-emitted via
    `encode_relations` for its final `(target, type)` sort), plus the two
    non-silent drop reports a future preview/ledger consumes (PR3).
    """
    merged: list[Relation] = []
    dropped_self_loops: list[Relation] = []
    deduped_collisions: list[Relation] = []

    def _process(relation: Relation, *, from_absorbed: bool) -> None:
        was_retargeted = relation.target == absorbed_id
        retargeted = (
            Relation(target=survivor_id, type=relation.type)
            if was_retargeted
            else relation
        )
        if retargeted.target == survivor_id and (from_absorbed or was_retargeted):
            dropped_self_loops.append(retargeted)
            return
        if retargeted in merged:
            deduped_collisions.append(retargeted)
            return
        merged.append(retargeted)

    for relation in survivor_relations:
        _process(relation, from_absorbed=False)
    for relation in absorbed_relations:
        _process(relation, from_absorbed=True)

    return merged, dropped_self_loops, deduped_collisions


def _parse_timestamp(value: object) -> datetime | None:
    """Parse `value` as an ISO-8601 timestamp, returning `None` on anything
    unparseable (missing, non-string, or malformed) rather than raising --
    the freshness/timestamp merge rule fails closed to survivor-wins on any
    parse failure."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _absorbed_is_more_recent(
    survivor_timestamp: object, absorbed_timestamp: object
) -> bool:
    """True only if the absorbed side's `timestamp` is STRICTLY more recent
    than the survivor's. Fails closed to `False` (survivor wins) when either
    side is missing or unparseable, matching every other scalar's
    survivor-wins default -- and ALSO fails closed when both sides parse but
    are incomparable (one timezone-aware, one naive): stdlib `datetime`
    raises `TypeError` for that comparison rather than picking a winner, and
    this function must never assume a timezone to paper over it."""
    survivor_dt = _parse_timestamp(survivor_timestamp)
    absorbed_dt = _parse_timestamp(absorbed_timestamp)
    if survivor_dt is None or absorbed_dt is None:
        return False
    try:
        return absorbed_dt > survivor_dt
    except TypeError:
        return False


def _union_dedup(first: list[object], second: list[object]) -> list[object]:
    """Order-preserving dedup union: every item of `first`, then any item of
    `second` not already seen, each kept in first-seen order (spec:
    Frontmatter-Conflict Resolution, list fields).

    Uses equality-based `in` against the accumulated `result` list rather
    than a `set`, so this never calls `hash()` on an item -- a frontmatter
    list may hold UNHASHABLE items (e.g. a list of dicts, permitted by
    OKF's unknown-key tolerance), which would otherwise raise `TypeError`
    and crash a destructive merge on realistic input. These lists are tiny,
    so the resulting O(n^2) membership check is negligible. Every item of
    `first` is kept as-is (including any internal duplicates already
    present there); only `second`'s items are deduped against everything
    accumulated so far."""
    result: list[object] = list(first)
    for item in second:
        if item not in result:
            result.append(item)
    return result


_ABSORBED_HEADING_SHIFT: Final = 2
"""How many levels every absorbed heading moves down (#803, #811).

Two, because the delimiter the absorbed body is stacked under
(`## Merged content (<id>)`) is itself level 2, so the absorbed document's
own `# ` root has to land at level 3 to be a CHILD of it rather than a
sibling."""

_MAX_HEADING_LEVEL: Final = 6
"""Markdown's deepest heading. `####### x` is literal text, not a level-7
heading, so the shift clamps here instead of overflowing (#811)."""

_ATX_HEADING_RE: Final = re.compile(r"^(#{1,6})(\s.*)?$")
"""An ATX heading at column 0: one to six hashes, then whitespace or the
end of the line. Deliberately anchored, so an indented `#` (four spaces is
a code block) and a `#hashtag` with no space after it are both left alone.
"""

_FENCE_RE: Final = re.compile(r"^\s{0,3}(`{3,}|~{3,})[ \t]*(.*)$")
"""A fenced-code-block delimiter, either spelling, with the up-to-three
spaces of indentation CommonMark allows before it. Group 1 is the RUN --
its length matters, not just its character -- and group 2 is whatever
follows it on the line."""


def _demote_absorbed_headings(absorbed_body: str) -> str:
    """Shift EVERY heading in an absorbed body two levels down, so the
    whole document arrives as one subtree of the `## Merged content (<id>)`
    delimiter it is stacked under (issues #803 and #811).

    #803 demoted only the LEADING heading, which stopped the merged
    document asserting two document ROOTS. It left a second defect
    untouched: the absorbed body's own deeper sections kept their original
    levels, so a `## Related` came out a SIBLING of the level-2 delimiter
    rather than a child, and a hand-written `# Citations` outranked it
    outright. Read as markdown, the absorbed document's links belonged to
    the merged document's own Related section -- which is exactly the
    reading the delimiter exists to prevent.

    Shifting the whole tree by the same amount preserves the absorbed
    document's internal structure exactly while nesting all of it where the
    delimiter says it belongs. Nothing is folded, and that is deliberate:
    deduping two `## Related` bullet lists and renumbering `[N]` citation
    markers needs section-merging semantics no helper here provides, and
    doing it byte-wise would silently change meaning. Two Related sections
    that are correctly nested are honest; one section built by guessing is
    not. The #645 reconciliation pass is what actually folds two documents
    into one; this only fixes the unreconciled fallback's structure.

    `# Citations` is an OKF section-8 RESERVED heading, and moving it is
    safe here in a way folding it would not be: its `[N]` markers point at
    entries in the SAME subtree, which travels with it, so the shift
    renumbers nothing and breaks no reference.

    Two boundaries the shift respects. A `#` line inside a FENCED code
    block is a comment in whatever language the absorbed document quoted,
    never a heading, so fenced regions are skipped -- rewriting one would
    corrupt the quoted code. Closing a fence takes a run of the SAME
    character at least as long as the one that opened it, and carrying no
    info string, because a longer fence is exactly how markdown quotes a
    shorter one: a four-backtick block showing fenced markdown inside it
    would otherwise end at the first inner ``` and hand every line after it
    back to the rewriter. And a heading deep enough that the shift would
    overflow is CLAMPED to level 6 rather than emitted as `####### `, which
    markdown renders as literal text: two absorbed levels can collapse into
    one, which is a bounded loss of nesting and strictly better than
    turning a heading into a paragraph of hashes.

    Fails CLOSED, the same shape as before: a body with no ATX heading at
    column 0 comes back unchanged. This moves headings that exist; it never
    invents or relocates one.

    Presentation-only and reversible. Every consumer that reconstructs the
    absorbed document does so from the ledger's verbatim
    `absorbed_snapshot`/`survivor_before` bytes (`unmerge`), or finds the
    absorbed segment by the `## Merged content (` MARKER (`forget`/`purge`'s
    excision, `contradictions`' own-body cut, `plan_merge`'s
    `carried_content_ids`) -- never by matching the absorbed body's bytes.
    """
    lines = absorbed_body.split("\n")
    fence: tuple[str, int] | None = None
    out: list[str] = []
    for line in lines:
        fence_match = _FENCE_RE.match(line)
        if fence_match is not None:
            run, trailing = fence_match.group(1), fence_match.group(2)
            if fence is None:
                fence = (run[0], len(run))
            elif run[0] == fence[0] and len(run) >= fence[1] and not trailing.strip():
                fence = None
            out.append(line)
            continue
        if fence is not None:
            out.append(line)
            continue
        heading = _ATX_HEADING_RE.match(line)
        if heading is None:
            out.append(line)
            continue
        level = min(len(heading.group(1)) + _ABSORBED_HEADING_SHIFT, _MAX_HEADING_LEVEL)
        out.append("#" * level + (heading.group(2) or ""))
    return "\n".join(out)


def build_merged_document(
    survivor_metadata: dict[str, object],
    survivor_body: str,
    absorbed_metadata: dict[str, object],
    absorbed_body: str,
    absorbed_id: str,
    survivor_id: str,
) -> tuple[dict[str, object], str]:
    """Combine a survivor and an absorbed document into the merged survivor
    document's (metadata, body) -- the frontmatter-conflict, body-append,
    and OUTBOUND typed-relation rules (spec: Frontmatter-Conflict
    Resolution, Sensitivity High-Water-Mark Recomputation, Reversible
    Typed-Relation Rewiring). Does NOT touch the `merged_from` ledger --
    that is `bundle/merge.py::plan_merge`'s exclusive responsibility, so any
    pre-existing `merged_from` key on EITHER side is dropped here rather
    than propagated.

    Field-kind rules: a scalar present on both sides keeps the SURVIVOR's
    value; a scalar present on only one side fills the gap; a list-valued
    field (`tags`, `provenance`, or any other list) is unioned, deduped,
    order-preserving (survivor's items first). `type` is a SCALAR and
    follows this same survivor-wins rule EXPLICITLY, including when
    survivor and absorbed declare DIFFERENT OKF types (a cross-type merge,
    #437): the merged document's `type` is always the survivor's declared
    type, and the absorbed side's `type` is discarded via the generic
    `elif key not in merged` branch below -- it is never surfaced as a
    "conflict" requiring resolution, the same as any other scalar already
    present on the survivor. `type_alternative` is EXCLUDED from that
    generic branch (issue #803), so the absorbed side's value is never
    imported: it records ONE extraction's uncertainty about ONE document's
    classification, not a property of the entity, and importing it
    manufactures doubt no extraction ever expressed about the survivor --
    the reported `Marta Ruiz` came out of a merge newly flagged as possibly
    an `Organization`. Excluding it also removes a latent hazard:
    `build_concept` REFUSES `type_alternative == type`, while this path has
    no such check, so inheritance could leave a survivor carrying
    `type: X` + `type_alternative: X` -- a state the builder will not
    produce. A survivor that carries its OWN `type_alternative` keeps it,
    since `merged` starts as `dict(survivor_metadata)`.
    `sensitivity` is RECOMPUTED via
    `combine_sensitivity`, never copied; `freshness`+`timestamp` are
    taken TOGETHER from whichever side has the strictly more recent
    `timestamp` (`_absorbed_is_more_recent`), falling back to the
    survivor's own value when either timestamp is missing/unparseable.
    `relations:` is EXCLUDED from the generic list-union (which cannot tell
    a dangling `target: {absorbed_id}` edge or a resulting self-loop from
    any other list value) and instead computed via the dedicated
    `merge_relations` (design D2, `survivor_id` is required for its
    self-loop check): the merged document NEVER carries a relation
    targeting the now-absorbed id, nor a survivor->survivor self-loop
    introduced by this merge. An empty merged relation set omits the
    `relations:` key entirely, preserving "absent relations key is valid"
    through a merge with no edges on either side.

    Body: the survivor's body, then a delimited
    `## Merged content ({absorbed_id})` heading, then the absorbed body --
    an APPEND, never an overwrite, per the spec's "Successful merge"
    scenario. EVERY heading in the absorbed body is shifted two levels
    down on the way in (`_demote_absorbed_headings`, issues #803 and
    #811), so the merged document has ONE document root instead of two AND
    the absorbed document's own sections nest UNDER the delimiter rather
    than competing with it; its prose, and the relative structure of its
    headings, are otherwise stacked verbatim.
    """
    merged: dict[str, object] = dict(survivor_metadata)
    merged.pop(MERGED_FROM_KEY, None)

    if _absorbed_is_more_recent(
        survivor_metadata.get("timestamp"), absorbed_metadata.get("timestamp")
    ):
        merged["timestamp"] = absorbed_metadata.get("timestamp")
        merged["freshness"] = absorbed_metadata.get("freshness")
    else:
        merged["timestamp"] = survivor_metadata.get("timestamp")
        merged["freshness"] = survivor_metadata.get("freshness")

    _SPECIAL_KEYS = (
        "sensitivity",
        "freshness",
        "timestamp",
        MERGED_FROM_KEY,
        RELATIONS_KEY,
        TYPE_ALTERNATIVE_KEY,
    )
    for key, absorbed_value in absorbed_metadata.items():
        if key in _SPECIAL_KEYS:
            continue
        survivor_value = merged.get(key)
        if isinstance(absorbed_value, list) or isinstance(survivor_value, list):
            survivor_list = survivor_value if isinstance(survivor_value, list) else []
            absorbed_list = absorbed_value if isinstance(absorbed_value, list) else []
            merged[key] = _union_dedup(survivor_list, absorbed_list)
        elif key not in merged:
            merged[key] = absorbed_value
        # else: a scalar already present on the survivor wins -- no-op.

    merged["sensitivity"] = combine_sensitivity(
        survivor_metadata.get("sensitivity"), absorbed_metadata.get("sensitivity")
    )

    merged_relations, _dropped_self_loops, _deduped_collisions = merge_relations(
        decode_relations(survivor_metadata),
        decode_relations(absorbed_metadata),
        survivor_id=survivor_id,
        absorbed_id=absorbed_id,
    )
    if merged_relations:
        merged[RELATIONS_KEY] = encode_relations(merged_relations)
    else:
        merged.pop(RELATIONS_KEY, None)

    separator = f"{merged_content_heading(absorbed_id)}\n\n"
    merged_body = (
        survivor_body.rstrip("\n")
        + separator
        + _demote_absorbed_headings(absorbed_body)
    )
    if not merged_body.endswith("\n"):
        merged_body += "\n"

    return merged, merged_body


@dataclass(frozen=True)
class DocScan:
    """One `_iter_docs` result: a non-reserved `.md` file, scanned once.

    Exactly one of `metadata`, `read_error`, or `parse_error` is set (the
    other two are `None`) -- a successfully read AND parsed file has
    `metadata` populated (possibly `{}`) and both errors `None`; a file that
    could not be opened/decoded has `read_error` set and `metadata`/
    `parse_error` `None`; a file that was read but whose frontmatter did not
    parse has `parse_error` set and `metadata`/`read_error` `None`.
    """

    path: Path
    metadata: dict[str, object] | None
    read_error: OSError | UnicodeDecodeError | None
    parse_error: str | None


def concept_id_for(path: Path, bundle_dir: Path) -> str:
    """The concept id for `path` within `bundle_dir`: its bundle-relative
    POSIX path without the `.md` suffix, NFC-normalized (issue #430).

    THE ONE derivation. Eleven readers -- `lint`, `lifecycle`, `sensitivity`,
    `state/derived`, `state/fts`, `state/reindex`, `graph/sqlite_graph`,
    `bundle/listing`, `resolution/candidates`, `resolution/contradiction`,
    `cli/curate` -- each spelled it inline, and `graph/analysis.py` already
    flagged the duplication and asked for exactly this helper. One copy means
    the normalization decision below is made once rather than in eleven
    places that can drift.

    Ten sites spelled it `.relative_to(bundle_dir).with_suffix("").as_posix()`
    and `lint` spelled it `.as_posix().removesuffix(".md")`. This adopts the
    ten-site spelling, which is byte-identical for those ten and for `lint` on
    every input except ONE, disclosed here rather than glossed:

    A document named literally `.md` has no stem. `pathlib` treats such a name
    as a dotfile, so `Path("notes/.md").suffix` is `""` and `with_suffix("")`
    is a no-op yielding `notes/.md`, while `removesuffix(".md")` yields
    `notes/`. `_iter_docs` globs `*.md` and `rglob` DOES return such a file, so
    the input is reachable -- but not from openkos: `_slugify` can return `""`
    and every caller branches on that rather than writing an empty stem, so
    only a hand-authored file reaches it.

    `lint` is the one affected reader, and the effect reaches its OUTPUT: it
    rebuilds displayed paths as `f"{identity}.md"`, so a finding about such a
    file now reads `notes/.md.md` where it used to read `notes/.md`. That is
    the cost of this choice, taken deliberately: NO spelling leaves all eleven
    readers unchanged, since the two genuinely differ here, so the question is
    only which side absorbs it. `with_suffix("")` wins because the ten other
    readers use these ids as KEYS -- for graph nodes, FTS rows, manifest
    hashes -- and `notes/` is a keyless id naming a directory, while
    `notes/.md` at least names the file. A doubled suffix in one lint line
    about a document with no name is the cheaper defect.

    Pinned by `test_concept_id_for_leaves_a_bare_dot_md_name_unstripped` and,
    for the display consequence, by
    `test_lint_identity_for_a_bare_dot_md_name_doubles_the_suffix`.

    WHY NORMALIZE. APFS preserves whatever normalization it is handed; HFS+
    normalizes to NFD on write; SMB varies. So the same logical id can be
    spelled NFC in one document's `relations:` frontmatter and NFD when
    derived from a filename on the same machine, and a plain string comparison
    between the two fails. Every consequence is silent: graph edges are
    dropped because an edge target does not match any node id, `lint` reports
    orphans and dangling links that do not exist, and entity-resolution
    candidates are never nominated. This was unreachable while slugs were
    ASCII (which has no distinct NFD form) and #429 made it reachable.

    WHY NFC SPECIFICALLY, AND WHY THIS IS SAFE FOR THE ID-TO-PATH DIRECTION.
    `_slugify` normalizes its output to NFC, so openkos never WRITES a
    decomposed filename -- NFC is the canonical spelling by construction, not
    by preference. The volumes that nonetheless store NFD (HFS+, SMB) resolve
    lookups insensitively, so the ~7 sites that rebuild a path as
    `bundle_dir / f"{concept_id}.md"` still open the file there. And on a
    byte-exact volume the name is already NFC, where normalizing is a total
    no-op. The remaining case -- a decomposed name sitting on a byte-exact
    filesystem, e.g. a bundle authored on HFS+, committed, and cloned onto
    ext4 -- is what `concept_path_for` below exists for; making the BUNDLE
    consistent again (a rename migration) is deliberately left to the human,
    tracked as a follow-up.

    Pure: no I/O, and `path` need not exist."""
    relative = path.relative_to(bundle_dir).with_suffix("").as_posix()
    return unicodedata.normalize("NFC", relative)


def concept_path_for(concept_id: str, bundle_dir: Path, *, suffix: str = ".md") -> Path:
    """The `suffix` path `concept_id` names within `bundle_dir` -- the inverse
    of `concept_id_for`, and the reason that one is safe (issue #430).

    `suffix` defaults to `.md` (the concept-file case every existing caller
    exercises) and is otherwise the resolver `bundle.ledger` reuses for the
    merge-ledger sidecar tree (`bundle/.state/ledger/<concept_id>.ledger.okf`,
    durable-derived-state slice 1a): the normalization-fallback reasoning
    below is identical regardless of the trailing extension, so this is a
    generalization of the SAME resolver rather than a second implementation
    (design decision: do not invent a second id-to-path mapping).

    Making ids canonically NFC obliges this direction to accept that the NAME
    ON DISK may still be decomposed. `concept_id_for`'s own reasoning covers
    the volumes that normalize (HFS+, SMB resolve lookups insensitively, so the
    direct probe succeeds there) -- but not a decomposed name sitting on a
    BYTE-EXACT filesystem, which is reachable without anyone hand-authoring
    anything: a bundle written on HFS+ and committed carries decomposed
    filenames into git, and cloning it on ext4 reproduces them byte for byte.
    There `bundle_dir / f"{nfc_id}.md"` simply does not exist, and the callers
    that reconstruct a path this way degrade silently to an empty body -- they
    would ask the model to judge nothing at all, and report a verdict on it.

    So: probe the direct path first (the only branch that runs in the
    overwhelmingly common case, and the one that keeps this free), and only if
    that misses, resolve the id SEGMENT BY SEGMENT against the real directory
    entries, matching each non-ASCII segment by NFC-normalized name. Directory
    segments need this exactly as leaf names do: `concept_id_for` normalizes
    every segment of the id, so a document under an NFD-named directory gets
    an NFC id whose direct parent does not even exist on a byte-exact volume
    -- a leaf-only scan would raise on the missing parent and silently miss a
    file that is right there (found in review, R3-001). A miss at any segment
    returns the direct path unchanged rather than raising: this helper
    resolves a SPELLING, it does not assert existence, and every caller
    already owns its own absence handling.

    An unreadable parent directory degrades the same way. A scan is an
    optimization over a failed lookup, and it must never be what turns a
    silently-empty body into a crash.

    TWO GUARDS, both load-bearing:

    An ASCII id SKIPS the fallback entirely, and inside the fallback an ASCII
    segment is joined directly rather than scanned. ASCII has no distinct
    decomposed form, so a miss on one can never be a normalization mismatch
    and a scan could only ever confirm the miss. This is what keeps the cost
    honest: a dangling id is a documented, ordinary case -- `edge_typing.
    _load_doc` and `contradiction._load_doc` both reach here for an endpoint
    with no document at all -- and it is reached per candidate inside loops
    that drive `llm.chat`. Without this, every such miss would pay unbounded
    directory listings to learn nothing, and almost every real id is ASCII.

    The fallback admits ONLY a regular non-symlink file at the leaf and a
    non-symlink directory at every inner segment -- strictly LESS than the
    direct probe, which resolves an exactly-named symlink as it always has.
    The fallback is a GUESS keyed on normalization rather than an exact name
    the caller asked for, so it fails closed: `_resolve_concept_path` is a
    documented path-safety gate (`forget` deletes what it resolves) and every
    LLM verb reads the result into a prompt, so admitting any entry by
    normalized name would let a symlink planted under a decomposed spelling
    stand in for an absent concept and be read through to a file outside the
    bundle. The ASCII leaf of an id whose DIRECTORY was resolved by scan gets
    the same strict admission: once any segment is a guess, the whole path is.

    The fallback is deliberately silent -- no counter, no log line. It reports
    a SPELLING, and a caller that wants to know a bundle carries decomposed
    names should ask `lint`, which walks it anyway."""
    direct = bundle_dir / f"{concept_id}{suffix}"
    if direct.exists() or concept_id.isascii():
        return direct
    current = bundle_dir
    segments = concept_id.split("/")
    for index, segment in enumerate(segments):
        leaf = index == len(segments) - 1
        name = f"{segment}{suffix}" if leaf else segment
        if segment.isascii():
            exact = current / name
            if leaf and (exact.is_symlink() or not exact.is_file()):
                return direct
            current = exact
            continue
        wanted = unicodedata.normalize("NFC", name)
        found: Path | None = None
        try:
            for candidate in current.iterdir():
                if unicodedata.normalize("NFC", candidate.name) != wanted:
                    continue
                if candidate.is_symlink():
                    continue
                if not (candidate.is_file() if leaf else candidate.is_dir()):
                    continue
                found = candidate
                break
        except OSError:
            return direct
        if found is None:
            return direct
        current = found
    return current


def _iter_docs(bundle_dir: Path) -> Iterator[DocScan]:
    """Walk every non-reserved `.md` file under `bundle_dir` exactly once (D2).

    `sorted(rglob("*.md"))` is the SAME walk `check_conformance` used before
    this refactor, so both `check_conformance` and `survey_bundle` (Phase 2)
    observe files in identical order. A file that cannot be opened or
    decoded yields a `DocScan` with `read_error` set instead of raising --
    `check_conformance` re-raises it (preserving its documented raise
    contract); `survey_bundle` degrades it to a finding (D3). A file whose
    frontmatter does not parse, or that has no parseable frontmatter block,
    yields `parse_error` set to the SAME message text `check_conformance`
    has always produced for that case.
    """
    for path in sorted(bundle_dir.rglob("*.md")):
        if path.name in RESERVED_FILENAMES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            yield DocScan(path, None, exc, None)
            continue
        try:
            post = frontmatter.loads(text)
        except Exception as exc:  # broad: any parse failure is a rule-1 violation
            yield DocScan(path, None, None, f"no parseable frontmatter ({exc})")
            continue
        if post.handler is None:
            yield DocScan(path, None, None, "no parseable frontmatter")
        else:
            yield DocScan(path, post.metadata, None, None)


@dataclass(frozen=True)
class BundleSurvey:
    """Counts and §9 findings for one `_iter_docs` pass over a bundle (Phase 2/D2).

    `findings` is a SUPERSET of `check_conformance`'s violations: it adds a
    per-file "unreadable" line for a `read_error` (D3), which
    `check_conformance` instead raises, PLUS one "unreadable directory" line
    per subdirectory `_iter_docs`'s `rglob` walk could not descend into (its
    `OSError` is silently swallowed by `scandir()`, per stdlib `glob`
    behavior). A file contributing a finding is counted as NEITHER a source
    nor a concept; an unreadable subdirectory's contents are unknown, so it
    affects no count at all -- only `findings`.
    """

    sources: int
    concepts: int
    findings: list[str]
    by_type: dict[str, int] = field(default_factory=dict)
    """Count per raw `type` string for every counted doc, INCLUDING `Source`.
    `sources == by_type.get("Source", 0)` and `concepts` equals the sum of
    every non-`Source` entry; this field breaks that aggregate down by type
    so a caller can report Procedures, Decisions, etc. distinctly instead of
    folding them into "Concepts" (issue #133). Files that become a finding
    (read/parse error, missing `type`) contribute to no entry."""


def _walk_errors(bundle_dir: Path) -> list[OSError]:
    """Collect directory-scan `OSError`s that `_iter_docs`'s `rglob` walk
    would silently swallow, without yielding any file paths.

    `Path.rglob` never surfaces `scandir()` failures on a subdirectory it
    cannot descend into -- the subtree just vanishes from the walk with no
    signal. This walks the SAME tree with `os.walk`'s `onerror` hook solely
    to capture those errors as data (each has `.filename` set to the
    unreadable directory); `_iter_docs` and `check_conformance` are
    untouched and stay byte-identical.
    """
    errors: list[OSError] = []
    for _ in os.walk(bundle_dir, onerror=errors.append):
        pass
    return errors


def survey_bundle(bundle_dir: Path) -> BundleSurvey:
    """Survey `bundle_dir` for source/concept counts and §9-shaped findings (D2/D3).

    Consumes the SAME `_iter_docs` walk `check_conformance` uses, in one
    pass: `type == "Source"` counts as a source, any other non-empty `type`
    counts as a concept, every non-empty `type` also increments its own
    `by_type` entry (breakdown, issue #133), and every read error, parse
    error, or missing/empty `type` becomes a finding instead of a count --
    including a per-file read
    error, which `survey_bundle` degrades to a finding rather than raising
    (D3, Q3), unlike `check_conformance`. Directory-scan errors that
    `_iter_docs`'s walk silently drops (see `_walk_errors`) are appended as
    one finding per unreadable directory, sorted by path for determinism, so
    an unscanned subtree is never invisible to a caller reading `findings`
    alone -- it never affects `sources`/`concepts`, since that subtree's
    contents are unknown.
    """
    sources = 0
    concepts = 0
    by_type: dict[str, int] = {}
    findings: list[str] = []
    for scan in _iter_docs(bundle_dir):
        if scan.read_error is not None:
            findings.append(f"{scan.path}: unreadable ({scan.read_error})")
        elif scan.parse_error is not None:
            findings.append(f"{scan.path}: {scan.parse_error}")
        else:
            doc_type = (scan.metadata or {}).get("type")
            if not doc_type:
                findings.append(f"{scan.path}: missing non-empty 'type'")
            else:
                type_key = str(doc_type)
                by_type[type_key] = by_type.get(type_key, 0) + 1
                if type_key == "Source":
                    sources += 1
                else:
                    concepts += 1
    for walk_error in sorted(
        _walk_errors(bundle_dir), key=lambda exc: str(exc.filename)
    ):
        findings.append(f"{walk_error.filename}: unreadable directory ({walk_error})")
    return BundleSurvey(sources, concepts, findings, by_type)


def _has_frontmatter_fence(text: str) -> bool:
    """Detect a frontmatter block by FENCE PRESENCE, not parseability: `text`
    opens (after optional leading whitespace) with a `---` delimiter line and
    has a later closing `---` line.

    Deliberately does NOT reuse `_iter_docs`'s `frontmatter.loads` check
    (rule 1's "parseable frontmatter" mechanism): §6 forbids a nested
    `index.md` from carrying frontmatter AT ALL, so a malformed `---` block
    that fails to parse as YAML is still a frontmatter block for this rule,
    and must still be flagged.
    """
    lines = text.lstrip().splitlines()
    if not lines or lines[0].strip() != "---":
        return False
    return any(line.strip() == "---" for line in lines[1:])


def _iter_reserved(bundle_dir: Path) -> Iterator[Path]:
    """Walk every reserved `.md` file (`index.md`/`log.md`) under
    `bundle_dir` exactly once, in the SAME `sorted(rglob("*.md"))` order
    `_iter_docs` uses -- but filtering IN `RESERVED_FILENAMES` instead of
    excluding them."""
    for path in sorted(bundle_dir.rglob("*.md")):
        if path.name in RESERVED_FILENAMES:
            yield path


def _check_reserved_structure(bundle_dir: Path) -> list[str]:
    """§9 rule 3: check the fixed structure of every reserved file.

    `index.md` (§6 + §11 root exception): any `index.md` other than the
    bundle-root one (`path.parent == bundle_dir`) MUST NOT carry a
    frontmatter block, detected by `_has_frontmatter_fence`.

    `log.md` (§7): every `## ` heading MUST match `_ISO_DATE_RE`
    (`YYYY-MM-DD`, format only -- not calendar-validated).

    Reads via `path.read_text(encoding="utf-8")`, so an unreadable or
    undecodable reserved file raises `OSError`/`UnicodeDecodeError`, matching
    `check_conformance`'s documented raise contract for candidate files.
    """
    violations: list[str] = []
    for path in _iter_reserved(bundle_dir):
        text = path.read_text(encoding="utf-8")
        if path.name == "index.md":
            if path.parent != bundle_dir and _has_frontmatter_fence(text):
                violations.append(f"{path}: index.md must not contain frontmatter")
        else:  # log.md -- `_iter_reserved` only yields the two reserved names
            for heading in _LOG_HEADING_RE.findall(text):
                if not _ISO_DATE_RE.match(heading):
                    violations.append(
                        f"{path}: log.md heading must be an ISO-8601 date "
                        f"(YYYY-MM-DD), got '## {heading}'"
                    )
    return violations


def check_conformance(bundle_dir: Path) -> list[str]:
    """Check §9 rules 1-3 against `bundle_dir`.

    Rules 1-2 walk every non-reserved `.md` file (`_iter_docs`), checking for
    parseable frontmatter with a non-empty `type`. Rule 3 additively walks
    the reserved files themselves (`_check_reserved_structure`), checking
    `index.md`'s frontmatter ban (with the §11 bundle-root exception) and
    `log.md`'s ISO-8601 date headings; its violations are appended after
    rules 1-2's.

    An additive `relations:` shape rule (spec: "OKF §9 Conformance --
    `relations:` Field Shape") runs alongside rules 1-2, gated on
    `scan.metadata` containing a `relations` key: a malformed shape (per
    `decode_relations`) is appended as a violation in the SAME
    `f"{path}: {message}"` form. It is a strict ADD-ON -- a document without
    a `relations:` key produces the exact same rules 1-2 output as before
    this rule existed (regression-guarded by
    `tests/unit/model/test_okf.py::test_check_conformance_byte_identical_when_relations_absent`).

    An empty list means conformant; a fresh, empty bundle passes vacuously
    because there are no `.md` files to violate any rule.
    May raise `OSError` or `UnicodeDecodeError` when a candidate file cannot
    be read or decoded -- those are inspection failures, never reported as
    conformance violations. Consumes the shared `_iter_docs` walk (D2) and
    re-raises `read_error` to preserve this exact contract; the rule 1-2
    portion of the output is byte-identical to the pre-refactor
    implementation (regression-guarded by
    `tests/unit/model/test_okf.py::test_check_conformance_round_trip_regression`).
    """
    violations: list[str] = []
    for scan in _iter_docs(bundle_dir):
        if scan.read_error is not None:
            raise scan.read_error
        if scan.parse_error is not None:
            violations.append(f"{scan.path}: {scan.parse_error}")
        elif not (scan.metadata or {}).get("type"):
            violations.append(f"{scan.path}: missing non-empty 'type'")
        elif RELATIONS_KEY in (scan.metadata or {}):
            try:
                decode_relations(scan.metadata or {})
            except ValueError as exc:
                violations.append(f"{scan.path}: {exc}")
    violations += _check_reserved_structure(bundle_dir)
    return violations
