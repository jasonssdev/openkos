"""Unit tests for `fsio.rename_two_step` (issue #474 part 2,
`normalize-names`'s canonical-layer rename primitive, design D2).

Two-step, not one-step (`raw -> <unique temp sibling> -> NFC target`),
because APFS/HFS+ (and SMB shares) are normalization-*insensitive*: a
single `os.rename(nfd_path, nfc_path)` between canonically equivalent
names CAN be treated as a same-file no-op that leaves the on-disk spelling
untouched -- a silent failure this primitive exists to rule out (design
D2/D3). The macOS spike (design.md S1, Q2) observed a real one-step rename
SUCCEEDING on APFS + Python 3.13, so the one-step-fails property is
deliberately pinned here at the PRIMITIVE level, against an injected
normalization-insensitive `os.rename`, never as a claim about a real
platform.

Post-rename verification MUST be a byte-exact `os.listdir` comparison,
never `Path.exists()` -- spike Q4 observed `Path(nfc_target).exists()`
returning `True` with only the NFD name on disk, which is exactly the
silent-success failure mode design D2 rules out.
"""

import os
import sys
import unicodedata
from pathlib import Path

import pytest

from openkos import fsio

NFD_CAFE = unicodedata.normalize("NFD", "café")
NFC_CAFE = unicodedata.normalize("NFC", "café")


def test_happy_path_reaches_byte_exact_nfc_listing(tmp_path: Path) -> None:
    """A successful two-step rename leaves the parent directory listing
    containing the NFC target name byte-exactly, and neither the raw nor
    any temporary name (spec: "A successful rename passes through a
    temporary sibling")."""
    src = tmp_path / f"{NFD_CAFE}.md"
    src.write_text("body\n", encoding="utf-8")

    result = fsio.rename_two_step(src, f"{NFC_CAFE}.md")

    listing = os.listdir(tmp_path)  # noqa: PTH208 -- byte-exact listing check, matches the primitive
    assert f"{NFC_CAFE}.md" in listing
    assert f"{NFD_CAFE}.md" not in listing
    assert not any(name.startswith(fsio.RENAME_TEMP_PREFIX) for name in listing)
    assert result == tmp_path / f"{NFC_CAFE}.md"
    assert result.read_text(encoding="utf-8") == "body\n"


@pytest.mark.skipif(sys.platform != "darwin", reason="real APFS byte-exact behavior")
def test_real_apfs_listing_holds_the_byte_exact_nfc_name(tmp_path: Path) -> None:
    """On a REAL APFS volume (unpatched `os.rename`/`os.listdir`), the
    two-step primitive leaves the parent directory listing containing the
    byte-exact NFC target name -- the honest platform pin design.md's S1
    discrepancy note calls for: spike Q2 observed a one-step rename ALSO
    succeeding on real APFS, so this test deliberately does NOT assert
    that a one-step rename would fail (see the module docstring and the
    injected-primitive test below for that claim instead)."""
    src = tmp_path / f"{NFD_CAFE}.md"
    src.write_text("body\n", encoding="utf-8")

    fsio.rename_two_step(src, f"{NFC_CAFE}.md")

    listing = os.listdir(tmp_path)  # noqa: PTH208 -- byte-exact listing check
    assert f"{NFC_CAFE}.md" in listing
    assert f"{NFD_CAFE}.md" not in listing


def test_verification_failure_raises_and_leaves_no_temp_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A post-rename listing check that does NOT find the NFC target
    byte-exactly fails loudly (`OSError`) and leaves no
    `RENAME_TEMP_PREFIX`-named entry on disk afterward (spec: "Failed
    post-rename verification fails loudly and leaves no temp entry")."""
    src = tmp_path / f"{NFD_CAFE}.md"
    src.write_text("body\n", encoding="utf-8")
    real_listdir = os.listdir

    def fake_listdir(path: str) -> list[str]:
        return [name for name in real_listdir(path) if name != f"{NFC_CAFE}.md"]

    monkeypatch.setattr("openkos.fsio.os.listdir", fake_listdir)

    with pytest.raises(OSError, match="verification failed"):
        fsio.rename_two_step(src, f"{NFC_CAFE}.md")

    monkeypatch.setattr("openkos.fsio.os.listdir", real_listdir)
    listing = os.listdir(tmp_path)  # noqa: PTH208 -- byte-exact listing check, matches the primitive
    assert not any(name.startswith(fsio.RENAME_TEMP_PREFIX) for name in listing)


def test_temp_name_matches_rename_temp_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The intermediate sibling name used by the two-step primitive starts
    with `RENAME_TEMP_PREFIX` (design D3: machine-detectable, ASCII, so
    trivially NFC and never self-flagged by `scan_non_nfc_entries`)."""
    src = tmp_path / f"{NFD_CAFE}.md"
    src.write_text("body\n", encoding="utf-8")
    observed_temp_names: list[str] = []
    real_rename = os.rename

    def spying_rename(src_path: str | Path, dst_path: str | Path) -> None:
        dst_name = Path(dst_path).name
        if dst_name.startswith(fsio.RENAME_TEMP_PREFIX):
            observed_temp_names.append(dst_name)
        real_rename(src_path, dst_path)

    monkeypatch.setattr("openkos.fsio.os.rename", spying_rename)

    fsio.rename_two_step(src, f"{NFC_CAFE}.md")

    assert len(observed_temp_names) == 1
    assert observed_temp_names[0].startswith(fsio.RENAME_TEMP_PREFIX)


def test_double_fault_propagates_the_original_hop_two_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When hop 2 (`temp -> dst`) fails AND the best-effort restore
    (`temp -> original`) also fails, the ORIGINAL hop-2 `OSError`
    propagates -- never the restore's (PR #492 informational finding):
    the restore is suppressed exactly like the sibling
    verification-failure branch, so the caller's error report names the
    fault that actually stopped the rename. The entry MAY remain at the
    temp name in this double-fault case -- that is exactly what
    `lint.scan_stranded_rename_temps` reports at the next run (design
    D3), so detectability is preserved."""
    src = tmp_path / f"{NFD_CAFE}.md"
    src.write_text("body\n", encoding="utf-8")
    real_rename = os.rename

    def double_faulting_rename(src_path: str | Path, dst_path: str | Path) -> None:
        dst_name = Path(dst_path).name
        if dst_name.startswith(fsio.RENAME_TEMP_PREFIX):
            real_rename(src_path, dst_path)  # hop 1 succeeds
            return
        if dst_name == f"{NFC_CAFE}.md":
            raise OSError("hop two failed")
        raise OSError("restore failed")  # the best-effort restore also fails

    monkeypatch.setattr("openkos.fsio.os.rename", double_faulting_rename)

    with pytest.raises(OSError, match="hop two failed"):
        fsio.rename_two_step(src, f"{NFC_CAFE}.md")

    monkeypatch.setattr("openkos.fsio.os.rename", real_rename)
    listing = os.listdir(tmp_path)  # noqa: PTH208 -- byte-exact listing check, matches the primitive
    # The entry is stranded at the temp name -- machine-detectable by the
    # `RENAME_TEMP_PREFIX` scan, never silently lost.
    assert any(name.startswith(fsio.RENAME_TEMP_PREFIX) for name in listing)


def test_existing_destination_raises_and_restores_instead_of_overwriting(
    tmp_path: Path,
) -> None:
    """A destination that is already present byte-exactly in the parent
    listing when hop 2 is about to run refuses with `OSError` naming the
    collision, restores the source's original name, and leaves the
    destination's bytes untouched (PR #492 risk finding: a dst created
    between the caller's batched drift re-check and this entry's rename
    would otherwise be SILENTLY overwritten by `os.rename`).

    ASCII stand-in names, deliberately: on a normalization-insensitive
    lookup filesystem (macOS APFS, design.md S1 Q4) the raw and NFC
    spellings of one name cannot coexist as two byte-distinct entries, so
    a REAL on-disk collision can only be staged portably with names that
    are not canonically equivalent -- and the guard's contract is pure
    byte-exact `os.listdir` membership, indifferent to canonical
    equivalence."""
    src = tmp_path / "raw-entry.md"
    src.write_text("source body\n", encoding="utf-8")
    interloper = tmp_path / "target.md"
    interloper.write_text("interloper body\n", encoding="utf-8")

    with pytest.raises(OSError, match=r"target\.md"):
        fsio.rename_two_step(src, "target.md")

    listing = os.listdir(tmp_path)  # noqa: PTH208 -- byte-exact listing check, matches the primitive
    assert "raw-entry.md" in listing  # source restored to its original name
    assert not any(name.startswith(fsio.RENAME_TEMP_PREFIX) for name in listing)
    assert interloper.read_text(encoding="utf-8") == "interloper body\n"


def test_survives_a_normalization_insensitive_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Injected hostile primitive: `os.rename` no-ops whenever the source
    and destination NAMES are canonically equivalent (simulating a
    normalization-insensitive filesystem, e.g. HFS+/SMB) -- under which a
    single DIRECT `raw -> NFC` rename would leave the raw spelling on
    disk. The two-step scheme never crosses a canonically-equivalent pair
    directly (`raw -> ascii-temp` and `ascii-temp -> NFC` are each between
    NON-equivalent names), so it reaches the byte-exact NFC listing anyway
    (spec: "The two-step primitive converts a name even against a
    normalization-insensitive rename")."""
    src = tmp_path / f"{NFD_CAFE}.md"
    src.write_text("body\n", encoding="utf-8")
    real_rename = os.rename

    def hostile_rename(src_path: str | Path, dst_path: str | Path) -> None:
        src_name = Path(src_path).name
        dst_name = Path(dst_path).name
        if unicodedata.normalize("NFC", src_name) == unicodedata.normalize(
            "NFC", dst_name
        ):
            return  # simulated same-file no-op
        real_rename(src_path, dst_path)

    monkeypatch.setattr("openkos.fsio.os.rename", hostile_rename)

    fsio.rename_two_step(src, f"{NFC_CAFE}.md")

    listing = os.listdir(tmp_path)  # noqa: PTH208 -- byte-exact listing check, matches the primitive
    assert f"{NFC_CAFE}.md" in listing
    assert f"{NFD_CAFE}.md" not in listing
