"""Unit tests for `sensitivity.py`: the shared fail-closed sensitivity
predicate (sensitivity-fail-closed-filter, MVP-3 gap #8 · S3, Phase 1/PR1).

`sensitive_concept_ids` is the ONE shared predicate every `llm.chat`-calling
seam (query, contradictions, adjudicate, suggest-relations, suggest-
volatility) filters against before sending concept content to the LLM -- see
`openspec/changes/sensitivity-fail-closed-filter/design.md`. Fixture helper
mirrors `tests/unit/test_lifecycle.py::_write_doc`.
"""

import inspect
from collections.abc import Iterator
from pathlib import Path

import pytest

from openkos import sensitivity
from openkos.model import okf


def _write_doc(
    path: Path,
    *,
    sensitivity_value: str | None = None,
    sensitivity_raw: str | None = None,
    body: str = "",
) -> None:
    """Write a minimal concept `.md` file with an optional `sensitivity:`
    frontmatter field. `sensitivity_raw` overrides it with a hand-written
    frontmatter line (for malformed-shape cases); passing neither omits the
    key entirely (absent case)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: Concept", "title: Stub"]
    if sensitivity_raw is not None:
        lines.append(sensitivity_raw)
    elif sensitivity_value is not None:
        lines.append(f"sensitivity: {sensitivity_value}")
    lines.append("---")
    frontmatter = "\n".join(lines) + "\n"
    path.write_text(f"{frontmatter}{body}", encoding="utf-8")


def test_explicit_confidential_is_blocked(tmp_path: Path) -> None:
    """A concept with `sensitivity: confidential` resolves to blocked (spec:
    Explicit confidential is blocked)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "secret.md", sensitivity_value="confidential")

    blocked = sensitivity.sensitive_concept_ids(bundle_dir)

    assert blocked == frozenset({"concepts/secret"})


def test_missing_sensitivity_key_is_blocked(tmp_path: Path) -> None:
    """A concept with no `sensitivity` frontmatter field fails closed to
    blocked -- `okf._rank(None)` alone would return private, so this MUST
    NOT be delegated to `_rank` (spec: Missing, malformed, or unreadable
    fails closed)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "no-field.md")

    blocked = sensitivity.sensitive_concept_ids(bundle_dir)

    assert blocked == frozenset({"concepts/no-field"})


def test_blank_sensitivity_value_is_blocked(tmp_path: Path) -> None:
    """A blank/whitespace-only `sensitivity` value fails closed to blocked --
    `okf._rank("")` alone would return private, so this MUST NOT be delegated
    to `_rank` either (spec: Missing, malformed, or unreadable fails
    closed)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "blank.md", sensitivity_raw='sensitivity: "   "'
    )

    blocked = sensitivity.sensitive_concept_ids(bundle_dir)

    assert blocked == frozenset({"concepts/blank"})


def test_malformed_frontmatter_is_blocked(tmp_path: Path) -> None:
    """A document whose frontmatter fails to parse fails closed to blocked
    (spec: Missing, malformed, or unreadable fails closed)."""
    bundle_dir = tmp_path / "bundle"
    path = bundle_dir / "concepts" / "malformed.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\ntitle: [unterminated\n---\nbody\n", encoding="utf-8")

    blocked = sensitivity.sensitive_concept_ids(bundle_dir)

    assert blocked == frozenset({"concepts/malformed"})


def test_unreadable_file_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A document that raises on read (simulating an unreadable file) fails
    closed to blocked (spec: Missing, malformed, or unreadable fails
    closed)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "unreadable.md", sensitivity_value="private")

    original_iter_docs = okf._iter_docs

    def _raising_iter_docs(target_bundle_dir: Path) -> Iterator[okf.DocScan]:
        for scan in original_iter_docs(target_bundle_dir):
            yield okf.DocScan(
                path=scan.path,
                metadata=None,
                read_error=OSError("simulated unreadable file"),
                parse_error=None,
            )

    monkeypatch.setattr(okf, "_iter_docs", _raising_iter_docs)

    blocked = sensitivity.sensitive_concept_ids(bundle_dir)

    assert blocked == frozenset({"concepts/unreadable"})


def test_unknown_sensitivity_value_is_blocked(tmp_path: Path) -> None:
    """A concept with `sensitivity: top-secret` (not one of the three known
    ranks) fails closed to blocked (spec: Unknown sensitivity value fails
    closed)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "weird.md", sensitivity_value="top-secret")

    blocked = sensitivity.sensitive_concept_ids(bundle_dir)

    assert blocked == frozenset({"concepts/weird"})


def test_private_sensitivity_is_sent(tmp_path: Path) -> None:
    """A concept with `sensitivity: private` is NOT in the blocked set (spec:
    Private and public concepts reach llm.chat)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "priv.md", sensitivity_value="private")

    blocked = sensitivity.sensitive_concept_ids(bundle_dir)

    assert blocked == frozenset()


def test_public_sensitivity_is_sent(tmp_path: Path) -> None:
    """A concept with `sensitivity: public` is NOT in the blocked set (spec:
    Private and public concepts reach llm.chat)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "pub.md", sensitivity_value="public")

    blocked = sensitivity.sensitive_concept_ids(bundle_dir)

    assert blocked == frozenset()


def test_mixed_bundle_blocks_only_confidential_and_fallbacks(tmp_path: Path) -> None:
    """Triangulation: a bundle mixing all sensitivity states blocks exactly
    the confidential/missing/blank/unknown ones, leaving private/public
    live."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", sensitivity_value="confidential")
    _write_doc(bundle_dir / "concepts" / "b.md", sensitivity_value="private")
    _write_doc(bundle_dir / "concepts" / "c.md", sensitivity_value="public")
    _write_doc(bundle_dir / "concepts" / "d.md")  # absent
    _write_doc(bundle_dir / "concepts" / "e.md", sensitivity_value="top-secret")

    blocked = sensitivity.sensitive_concept_ids(bundle_dir)

    assert blocked == frozenset({"concepts/a", "concepts/d", "concepts/e"})


def test_all_public_private_bundle_returns_empty_frozenset(tmp_path: Path) -> None:
    """A bundle with no confidential/missing/malformed concept returns an
    empty set."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", sensitivity_value="private")
    _write_doc(bundle_dir / "concepts" / "b.md", sensitivity_value="public")

    blocked = sensitivity.sensitive_concept_ids(bundle_dir)

    assert blocked == frozenset()


# --- correction batch (post-4R-review), FIX 1: shared `blocks_llm_send` ----


def test_blocks_llm_send_blocks_none() -> None:
    """`blocks_llm_send(None)` is blocked -- an absent value MUST NOT be
    delegated to `okf._rank` alone (`okf._rank(None)` resolves to
    `"private"`, which would wrongly let it through)."""
    assert sensitivity.blocks_llm_send(None) is True


def test_blocks_llm_send_blocks_blank_and_whitespace() -> None:
    """`blocks_llm_send` blocks an empty string and a whitespace-only string
    -- same fail-closed reasoning as the `None` case."""
    assert sensitivity.blocks_llm_send("") is True
    assert sensitivity.blocks_llm_send("   ") is True


def test_blocks_llm_send_blocks_confidential_and_above() -> None:
    """`blocks_llm_send` blocks a present value that ranks at or above the
    threshold (default `"confidential"`)."""
    assert sensitivity.blocks_llm_send("confidential") is True


def test_blocks_llm_send_blocks_unknown_value() -> None:
    """`blocks_llm_send` blocks an unrecognized string -- `okf._rank` itself
    fails closed toward `"confidential"` on anything it does not
    recognize."""
    assert sensitivity.blocks_llm_send("top-secret") is True


def test_blocks_llm_send_allows_private_and_public() -> None:
    """`blocks_llm_send` does NOT block a present, non-blank value that ranks
    below the threshold."""
    assert sensitivity.blocks_llm_send("private") is False
    assert sensitivity.blocks_llm_send("public") is False


def test_blocks_llm_send_respects_custom_threshold() -> None:
    """A caller-supplied `threshold` changes the rank floor -- e.g. a
    `"private"` threshold blocks `"private"` itself too, not just
    `"confidential"`."""
    assert sensitivity.blocks_llm_send("private", threshold="private") is True
    assert sensitivity.blocks_llm_send("public", threshold="private") is False


# --- correction batch (post-4R-review, directory-walk-observability),
# FIX 1: centralized `should_block` metadata predicate --------------------


def test_should_block_blocks_confidential_metadata() -> None:
    """`should_block` blocks a `{"sensitivity": "confidential"}` mapping --
    the same decision every one of the 5 duplicated inline call sites made
    before centralization (query, contradictions, adjudicate,
    suggest-relations, suggest-volatility)."""
    assert sensitivity.should_block({"sensitivity": "confidential"}) is True


def test_should_block_allows_private_and_public_metadata() -> None:
    """`should_block` does NOT block `private`/`public` metadata."""
    assert sensitivity.should_block({"sensitivity": "private"}) is False
    assert sensitivity.should_block({"sensitivity": "public"}) is False


def test_should_block_blocks_missing_or_blank_metadata_fail_closed() -> None:
    """A metadata mapping missing the `sensitivity` key, or carrying a
    blank/whitespace value, fails closed to blocked -- `should_block`
    delegates to `blocks_llm_send`, which never lets an absent/blank signal
    through."""
    assert sensitivity.should_block({}) is True
    assert sensitivity.should_block({"sensitivity": "   "}) is True


def test_should_block_include_confidential_true_never_blocks() -> None:
    """`include_confidential=True` is the opt-in bypass every call site
    thread from its own CLI flag -- it short-circuits `should_block` to
    `False` regardless of the metadata's sensitivity value, restoring
    byte-identical pre-filter behavior."""
    assert (
        sensitivity.should_block(
            {"sensitivity": "confidential"}, include_confidential=True
        )
        is False
    )


# --- issue #240: the local-backend exemption -----------------------------


def test_should_block_local_exemption_defaults_to_false() -> None:
    """`local_exemption` defaults to `False`, so a caller that never heard of
    the exemption keeps today's blanket blocking (#240).

    This default is load-bearing, not cosmetic. `should_block` is reached
    from five independent seams; a `True` default would hand the exemption
    to every one of them that had not yet been taught to compute locality,
    turning "forgot to thread a parameter" into "sent a confidential
    concept off this machine". Pinned by inspecting the signature so the
    guarantee survives a refactor that stops taking the parameter
    positionally."""
    parameter = inspect.signature(sensitivity.should_block).parameters[
        "local_exemption"
    ]
    assert parameter.default is False
    # And behaviorally: omitting it blocks, exactly as before #240.
    assert sensitivity.should_block({"sensitivity": "confidential"}) is True


def test_should_block_local_exemption_true_never_blocks() -> None:
    """`local_exemption=True` means the caller PROVED the backend is this
    machine, so a `confidential` concept is not leaving anywhere and the
    gate has nothing to protect (#240)."""
    assert (
        sensitivity.should_block({"sensitivity": "confidential"}, local_exemption=True)
        is False
    )


def test_should_block_local_exemption_true_also_releases_fail_closed_values() -> None:
    """The exemption releases the fail-closed cases too -- absent, blank, and
    unrecognized `sensitivity` values (#240).

    Deliberate: those values fail closed because an unverifiable sensitivity
    might be `confidential`, and the WORST case is exactly the one the
    exemption already answers. Treating them more strictly than an explicit
    `confidential` would be incoherent."""
    assert sensitivity.should_block({}, local_exemption=True) is False
    assert (
        sensitivity.should_block({"sensitivity": "   "}, local_exemption=True) is False
    )
    assert (
        sensitivity.should_block({"sensitivity": "top-secret"}, local_exemption=True)
        is False
    )


def test_should_block_local_exemption_false_blocks_confidential() -> None:
    """An explicit `local_exemption=False` -- a non-local or unprovable
    backend -- keeps the gate closed (#240)."""
    assert (
        sensitivity.should_block({"sensitivity": "confidential"}, local_exemption=False)
        is True
    )


def test_should_block_either_escape_hatch_alone_releases() -> None:
    """The two hatches are a DISJUNCTION, not a conjunction: either one
    alone releases the block (#240).

    `--include-confidential` survives unchanged as the escape hatch on a
    remote backend, and the exemption applies without the flag on a local
    one."""
    metadata = {"sensitivity": "confidential"}
    assert sensitivity.should_block(metadata, include_confidential=True) is False
    assert sensitivity.should_block(metadata, local_exemption=True) is False
    assert (
        sensitivity.should_block(
            metadata, include_confidential=True, local_exemption=True
        )
        is False
    )


def test_blocks_llm_send_is_untouched_by_the_exemption() -> None:
    """`blocks_llm_send` keeps its fail-closed contract with NO exemption
    parameter of its own (#240).

    The exemption is a policy about a SEND, not about a value: a raw
    `sensitivity` string means the same thing regardless of where the
    backend runs. Keeping the value-level authority host-blind is what lets
    the non-send callers (the `ingest` extract floor gate, `lint`) keep
    reading it without accidentally inheriting a send-time policy."""
    assert (
        "local_exemption"
        not in inspect.signature(sensitivity.blocks_llm_send).parameters
    )
    assert sensitivity.blocks_llm_send(None) is True
    assert sensitivity.blocks_llm_send("confidential") is True
    assert sensitivity.blocks_llm_send("private") is False


def test_sensitive_concept_ids_local_exemption_defaults_to_false(
    tmp_path: Path,
) -> None:
    """`sensitive_concept_ids` also defaults `local_exemption` to `False`, so
    an un-updated caller still gets the full blocked set (#240)."""
    parameter = inspect.signature(sensitivity.sensitive_concept_ids).parameters[
        "local_exemption"
    ]
    assert parameter.default is False
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "c" / "secret.md", sensitivity_value="confidential")
    assert sensitivity.sensitive_concept_ids(bundle_dir) == frozenset({"c/secret"})


def test_sensitive_concept_ids_local_exemption_returns_empty_frozenset(
    tmp_path: Path,
) -> None:
    """With the exemption granted, `sensitive_concept_ids` returns an empty
    `frozenset` -- nothing is excluded (#240)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "c" / "secret.md", sensitivity_value="confidential")
    _write_doc(bundle_dir / "c" / "open.md", sensitivity_value="public")

    assert (
        sensitivity.sensitive_concept_ids(bundle_dir, local_exemption=True)
        == frozenset()
    )


def test_sensitive_concept_ids_escape_hatches_skip_the_walk_entirely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Either escape hatch short-circuits BEFORE `okf._iter_docs` runs, so
    the walk costs nothing (#240).

    The five call sites used to guard the call with their own
    `if not include_confidential:` precisely to avoid paying for a walk
    whose result they would discard. Centralizing the disjunction must not
    quietly reintroduce that cost, so the skip is pinned by proving the walk
    is never entered -- a `frozenset()` return alone could not tell the
    difference."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "c" / "secret.md", sensitivity_value="confidential")
    walked: list[Path] = []

    def _spy(directory: Path) -> Iterator[object]:
        walked.append(directory)
        yield from ()

    monkeypatch.setattr(okf, "_iter_docs", _spy)

    assert (
        sensitivity.sensitive_concept_ids(bundle_dir, local_exemption=True)
        == frozenset()
    )
    assert (
        sensitivity.sensitive_concept_ids(bundle_dir, include_confidential=True)
        == frozenset()
    )
    assert walked == []


def test_sensitive_concept_ids_include_confidential_defaults_to_false(
    tmp_path: Path,
) -> None:
    """`include_confidential` is the second hatch `sensitive_concept_ids`
    grew in #240 (the walk-skip used to be the caller's own `if`), and it
    defaults to `False` for the same fail-closed reason."""
    parameter = inspect.signature(sensitivity.sensitive_concept_ids).parameters[
        "include_confidential"
    ]
    assert parameter.default is False
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "c" / "secret.md", sensitivity_value="confidential")
    assert sensitivity.sensitive_concept_ids(bundle_dir) == frozenset({"c/secret"})


# ---------------------------------------------------------------------------
# `merged_content_blocked` -- the ledger-aware gate (surface-merged-body-
# contradictions, Correction 1). `sensitive_concept_ids` reads only the
# CURRENT on-disk sensitivity per file and cannot see this: a survivor
# absorbing a `confidential` document, later downgraded via
# `set-sensitivity --allow-downgrade`, still embeds the original
# confidential body inside `entry.absorbed_snapshot`.
# ---------------------------------------------------------------------------


def _entry(
    *,
    sensitivity_before: str = "private",
    sensitivity_after: str = "private",
    absorbed_id: str = "concepts/absorbed",
) -> okf.MergeLedgerEntry:
    """Minimal `MergeLedgerEntry` fixture -- only the three fields
    `merged_content_blocked` reads are varied; the rest are inert."""
    return okf.MergeLedgerEntry(
        schema=okf.MERGE_LEDGER_SCHEMA_V3,
        merged_at="2026-01-01T00:00:00Z",
        absorbed_id=absorbed_id,
        absorbed_snapshot="---\ntype: Concept\ntitle: Absorbed\n---\nBody.\n",
        survivor_before="---\ntype: Concept\ntitle: Survivor\n---\nBody.\n",
        index_before="",
        log_before="",
        link_rewrites=[],
        sensitivity_before=sensitivity_before,
        sensitivity_after=sensitivity_after,
        relation_rewrites=[],
        provenance_rewrites=[],
    )


def test_merged_content_blocked_all_private_is_not_blocked() -> None:
    entry = _entry(sensitivity_before="private", sensitivity_after="private")
    assert sensitivity.merged_content_blocked("private", entry) is False


def test_merged_content_blocked_current_confidential_blocks() -> None:
    entry = _entry(sensitivity_before="private", sensitivity_after="private")
    assert sensitivity.merged_content_blocked("confidential", entry) is True


def test_merged_content_blocked_sensitivity_before_confidential_blocks() -> None:
    """The survivor's CURRENT value is public, but the entry's own frozen
    `sensitivity_before` was confidential -- the ledger-aware gate must
    still block, since `sensitivity_before` is an independent floor."""
    entry = _entry(sensitivity_before="confidential", sensitivity_after="private")
    assert sensitivity.merged_content_blocked("public", entry) is True


def test_merged_content_blocked_sensitivity_after_confidential_blocks() -> None:
    """`sensitivity_after` already dominates the absorbed document's own
    original sensitivity by construction (`combine_sensitivity`) -- a
    confidential `sensitivity_after` alone must block."""
    entry = _entry(sensitivity_before="private", sensitivity_after="confidential")
    assert sensitivity.merged_content_blocked("public", entry) is True


def test_merged_content_blocked_survivor_downgraded_after_confidential_merge() -> None:
    """The exact Correction 1 scenario: a survivor absorbed a confidential
    document (recorded in the ledger at merge time), then a human later ran
    `set-sensitivity <survivor> public --allow-downgrade`. The CURRENT
    on-disk value is now `public`, but the frozen ledger entry still floors
    the gate at confidential."""
    entry = _entry(sensitivity_before="confidential", sensitivity_after="confidential")
    assert sensitivity.merged_content_blocked("public", entry) is True


def test_merged_content_blocked_evaluated_per_entry_not_once_per_survivor() -> None:
    """A later downgrade can have lowered the CURRENT value below what one
    specific historical entry established -- each entry is its own
    independent floor, so an old confidential-touching entry stays blocked
    even while a newer, fully-private entry on the SAME survivor does not."""
    old_confidential_entry = _entry(
        sensitivity_before="confidential", sensitivity_after="confidential"
    )
    new_private_entry = _entry(
        sensitivity_before="private", sensitivity_after="private"
    )

    assert sensitivity.merged_content_blocked("public", old_confidential_entry) is True
    assert sensitivity.merged_content_blocked("public", new_private_entry) is False


def test_merged_content_blocked_blank_sensitivity_before_sentinel_fails_closed() -> (
    None
):
    """`sensitivity_before` uses `""` as the sentinel for "survivor had no
    `sensitivity` key at merge time" -- treated fail-closed (as confidential),
    never silently as the `okf._rank` absent-default of private."""
    entry = _entry(sensitivity_before="", sensitivity_after="private")
    assert sensitivity.merged_content_blocked("private", entry) is True


def test_merged_content_blocked_include_confidential_short_circuits() -> None:
    """`include_confidential=True` releases the gate immediately, before any
    ledger entry field is inspected -- same contract as `should_block` /
    `sensitive_concept_ids`."""
    entry = _entry(sensitivity_before="confidential", sensitivity_after="confidential")
    assert (
        sensitivity.merged_content_blocked(
            "confidential", entry, include_confidential=True
        )
        is False
    )


def test_merged_content_blocked_local_exemption_short_circuits() -> None:
    entry = _entry(sensitivity_before="confidential", sensitivity_after="confidential")
    assert (
        sensitivity.merged_content_blocked("confidential", entry, local_exemption=True)
        is False
    )


def test_merged_content_blocked_custom_threshold() -> None:
    """A `threshold` of `"private"` blocks anything at or above private,
    same `okf._rank` semantics `blocks_llm_send` already exposes."""
    entry = _entry(sensitivity_before="private", sensitivity_after="private")
    assert (
        sensitivity.merged_content_blocked("public", entry, threshold="private") is True
    )
    assert (
        sensitivity.merged_content_blocked(
            "public",
            _entry(sensitivity_before="public", sensitivity_after="public"),
            threshold="private",
        )
        is False
    )
