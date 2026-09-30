"""The post-confirm drift check, as a pure function (issue #1138).

`describe_drift` is the decision `cli/main.py::_reject_drifted_targets`
used to make inline: it re-reads every target a verb is about to write or
unlink and compares the bytes with the Phase-A snapshot. It returns the
refusal message, or `None` when nothing drifted -- it neither prints nor
exits, so an adapter that is not the CLI (the ingest service, a daemon)
can turn the same decision into its own typed refusal. The CLI wrapper
keeps the printing and the exit code 3.
"""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from pathlib import Path

from openkos import config


def describe_drift(
    layout: config.WorkspaceLayout,
    expected: Mapping[Path, bytes],
    verb: str,
    *,
    deletes: AbstractSet[Path] = frozenset(),
    remedy: str | None = None,
    hint: str | None = None,
) -> str | None:
    """Describe the refusal a run owes (exit 3, nothing written, nothing deleted)
    when any target this run intends to WRITE or UNLINK changed on disk after
    the plan was computed from it (issues #306, #313, #319, #329).

    Every caller shares one shape: Phase A reads a snapshot, computes the
    ENTIRE plan from it -- each document's new bytes, plus the new
    `log.md` text in all of them, the new `index.md` text in the title
    backfill, and for the delete verbs WHICH files to unlink -- and only
    then prompts. Nothing re-read anything at write time, so an edit
    landing while the prompt waited was overwritten IN FULL by
    `fsio.write_atomic` -- or, on a delete target, destroyed outright by
    `fsio.remove_file`, which is strictly worse since nothing survives to
    recover from -- with no error, no signal that a newer version existed,
    and an `_autocommit` that then committed the result. Every caller
    therefore invokes this strictly AFTER its confirm gate and strictly
    BEFORE its first write -- and unconditionally, outside the gate's own
    `if`, because `--auto` and `review: false` skip the prompt but not the
    window: nothing pauses for a human there, which makes those runs the
    likeliest to race a second writer, not the least.

    Every mutating verb now calls this (#313's rollout is complete). A
    reader arriving from one call site does not need the roster --
    `grep _reject_drifted_targets` is exact and never goes stale -- only
    the assurance that every caller satisfies the same contract below. An
    enumeration here would be a second place to forget to update, which is
    how a previous version of this paragraph came to claim three callers
    while five existed.

    `expected` maps the ABSOLUTE `Path` a verb will actually hand to
    `fsio.write_atomic` (or delete) to the RAW BYTES that path held when
    Phase A read it, and the comparison is bytes-to-bytes. `Path` keys, not
    workspace-relative strings, are the point (issue #325): a string key is
    a SECOND construction of the target's identity, rebuilt by
    interpolation at each call site, and it protects the write only while
    the two constructions happen to agree -- a drive-anchored concept-id or
    an absolute `rel` out of an unmerge ledger made them diverge, and the
    guard then validated a path the verb was not going to write. Passing
    the one `Path` object both phases share leaves nothing to diverge.
    The workspace-relative POSIX spelling still appears in the refusal
    message, derived here via `relative_to(layout.root)`; a key that
    escapes the workspace entirely (`relative_to` raises) is drift BY
    DEFINITION, named by its raw path and refused before any byte of it is
    read -- an out-of-tree target is one the operator was never shown, so
    even byte-identical content must fail closed, explicitly rather than
    by accident of an unreadable join as before.

    Both sides MUST come from the same single observation: every caller
    obtains its snapshot bytes from the `_snapshot_read` call whose decoded
    text fed the plan, which is precisely what closes the #318 window (two
    reads made an edit landing between them the guard's own baseline).
    That helper returning BYTES for this guard alongside the text is not
    convenience but correctness, in both directions: comparing decoded
    text to decoded text misses a CRLF-only rewrite landing during the
    prompt, because universal-newline translation makes it equal its own
    LF snapshot, while `fsio.write_atomic` (opening with `newline=""`)
    then writes the LF plan over it; comparing raw bytes to a translated
    snapshot is worse, because a file that was ALREADY CRLF at rest,
    untouched by anyone, compares unequal on every run, so the verb
    refuses forever with a message naming a cause that never happened and
    a re-run that cannot clear it. Bytes on both sides is the only pairing
    with neither failure.

    Drift is not one situation but THREE, and the refusal reports them
    separately because each demands a different next step (#319; flattening
    them into one "changed on disk" sentence sent operators in circles):

    - CHANGED: the bytes differ. The benign bucket -- a plain re-run
      recomputes the plan over the current state and succeeds, so the
      default advice is exactly that re-run.
    - VANISHED: the read raised `OSError` (deleted, or unreadable).
      Re-creating or overwriting a file whose current state the operator
      can no longer be shown is the same silent revert, so it still
      refuses -- and a vanished DELETE target is not the run's own intent
      honored early (#329): the run promised to unlink exactly the bytes
      the operator previewed, and a path someone ELSE removed no longer
      supports that claim any more than a changed one does. A plain re-run
      reads the same missing path and -- for the delete verbs -- fails in
      Phase A before any prompt, so the advice must say the path has to be
      restored first, and ONLY that: the old "or confirm the deletion is
      intended" clause was a dead end (R4 wave 4), since no re-run reaches
      a confirmation while the path stays missing. Advising a bare re-run
      here was the #319 loop.
    - OUT-OF-TREE: the key escapes the workspace (`relative_to` raises).
      Nothing "changed" and nothing "vanished" -- the same inputs produce
      this refusal on EVERY run, deterministically, so a re-run cannot
      clear it and the message says so (this is also the wave-2 R4 fix:
      the flattened sentence blamed an edit that never happened).

    `deletes` names the subset of `expected`'s keys the verb will UNLINK
    rather than write -- `forget`'s purge set, `purge`'s root-plus-cascade,
    `merge`'s absorbed file. The distinction is reporting, not detection:
    every bucket applies to both kinds, but "refusing to write" on a path
    the verb was about to DESTROY understates what the operator just
    avoided, so each path is labeled a "write target" or a "delete target"
    by what Phase B would actually have done to it, and the fail-closed
    footer extends to "nothing was deleted" exactly when the plan had a
    delete half to fail closed on. The function's NAME stays
    target-kind-neutral on purpose (#329): with `deletes` in the signature
    and both kinds named in the message, "drifted targets" already covers
    writes and unlinks alike, and a rename would churn every call site for
    no contract gain.

    `hint` appends one EXTRA sentence after everything else, unconditionally,
    whenever this call refuses (okf-v02-migration Phase 6): `unmerge`'s own
    drift refusal uses it to name `openkos repair` when the bundle's
    `index.md` still declares a pre-0.2 `okf_version` -- the drift itself
    may be unrelated to the migration, but an unrepaired bundle is the more
    actionable fact for the operator to fix first. Unlike `remedy`, this
    is a pure addition, never a substitution, so it composes with every
    bucket's own advice rather than replacing any of it.

    `remedy` replaces the DEFAULT advice -- the changed bucket's re-run
    sentence -- when a verb's re-run is NOT a safe recovery: `unmerge`
    (#328), whose re-run would overwrite the very edit the guard just
    protected. Replacement is scoped to that one sentence, not wholesale
    (R3+R4 wave 5): the vanished and out-of-tree sentences are advisory
    FACTS about the refusal, not recovery advice a verb can substitute --
    a vanished target still has to be restored before anything proceeds,
    and an out-of-tree refusal is still deterministic -- so each is
    appended after whatever remedy is in effect whenever its bucket is
    non-empty. Under wholesale replacement, unmerge's copy-your-edit
    remedy talked about copying an edit that, for a vanished target, does
    not exist, and silently dropped the restore-first instruction.

    Exit code 3, and only here (#319): a drift refusal is the ONE failure a
    script may safely retry -- nothing was written, and when the message
    carries the re-run advice a retry genuinely recovers -- while every
    other failure keeps exit 1 and stays not-obviously-retryable. Scripts
    can now branch on `$? -eq 3` instead of parsing stderr; fail-closed
    semantics are unchanged (still non-zero, still before the first
    write). The retry contract is "safe WHEN the message says so": a
    vanished or out-of-tree refusal also exits 3, and its message is what
    tells the script's operator that a bare retry will not clear it.

    Refusal is whole-run, never per-path, because the plan is a unit. The
    title backfill's new `index.md` already encodes a relabel for every
    staged Source and its new `log.md` already names them, so skipping one
    drifted document would leave `index.md` asserting a relabel that never
    happened; `reconcile` shows the same thing on a smaller plan, where
    honouring one side of a symmetric pair while skipping the other leaves
    the two concepts disagreeing about their own resolution -- the one state
    its refuse-on-conflict gate exists to prevent. Neither case is special:
    every caller computes its plan as a whole from one snapshot, so a
    partial application asserts something that snapshot no longer supports.
    Recomputing after the prompt merely re-opens the same window.
    Refusing before the first write is the only fail-closed option, and it
    costs the operator one cheap re-run over fresh state.
    """
    changed: dict[bool, list[str]] = {False: [], True: []}
    vanished: dict[bool, list[str]] = {False: [], True: []}
    out_of_tree: dict[bool, list[str]] = {False: [], True: []}
    for path in expected:
        is_delete = path in deletes
        try:
            rel_path = path.relative_to(layout.root).as_posix()
        except ValueError:
            # Out-of-tree target: no workspace-relative spelling exists, so
            # the raw path is the entry, and no read is attempted -- see the
            # docstring for why matching bytes must not rescue it (#325).
            out_of_tree[is_delete].append(str(path))
            continue
        try:
            current = path.read_bytes()
        except OSError:
            vanished[is_delete].append(rel_path)
            continue
        if current != expected[path]:
            changed[is_delete].append(rel_path)
    if (
        not any(changed.values())
        and not any(vanished.values())
        and not any(out_of_tree.values())
    ):
        return None

    # One clause per non-empty (bucket, kind) pair, bucket-major, writes
    # before deletes -- so every path is named under the verb's ACTUAL
    # intent for it and under the ACTUAL observation that refused it.
    clauses: list[str] = []
    bucket_specs = [
        (changed, "changed on disk after this run computed its plan"),
        (vanished, "vanished from disk (deleted or unreadable)"),
        (out_of_tree, "resolve outside the workspace"),
    ]
    for bucket, cause in bucket_specs:
        for is_delete in (False, True):
            paths = sorted(bucket[is_delete])
            if not paths:
                continue
            kind = "delete target(s)" if is_delete else "write target(s)"
            clauses.append(f"{len(paths)} {kind} {cause}: {', '.join(paths)}")

    # Deliberately NOT Phase B's "No path was written." sentence: that one
    # reports a write that already began, this one reports a run that never
    # started writing, and #234 pinned that two messages a bug report might
    # quote must never read alike. The delete half appears exactly when the
    # plan HAD a delete half (`deletes` non-empty) -- claiming "nothing was
    # deleted" for a verb that deletes nothing would be noise.
    footer = (
        "Nothing was written, nothing was deleted."
        if deletes
        else "Nothing was written."
    )

    # A custom `remedy` replaces only the DEFAULT re-run advice (the
    # changed bucket's); the vanished/out-of-tree sentences are advisory
    # facts about the refusal itself and follow whichever remedy is in
    # effect, each scoped to its own bucket ("the vanished target(s)",
    # "the out-of-tree refusal") so a mixed refusal reads as a checklist,
    # not a contradiction -- see the docstring (R3+R4 wave 5).
    advice: list[str] = []
    if remedy is not None:
        advice.append(remedy)
    elif any(changed.values()):
        advice.append("Re-run to recompute over the current bundle.")
    if any(vanished.values()):
        advice.append(
            "A plain re-run will refuse again on the vanished target(s): "
            "restore them first."
        )
    if any(out_of_tree.values()):
        advice.append(
            "The out-of-tree refusal is deterministic -- the same inputs "
            "reproduce it on every run, and a re-run cannot clear it."
        )
    remedy = " ".join(advice)

    message = (
        f"openkos {verb}: refusing to write -- {'; '.join(clauses)}. {footer} {remedy}"
    )
    if hint:
        message = f"{message} {hint}"
    return message
