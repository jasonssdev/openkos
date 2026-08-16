"""`evals/harness_report.py` renders the arm-identity line every harness
report carries, and this executes it (issue #742).

#738 made the generation ceiling and context window part of an arm's identity
and wrote them into each `runs-*.json`; #740 put them into the human-readable
reports too, because the artifact a person opens could not otherwise be told
apart from a pre-#738 run measured under unbounded conditions.

That #740 guard could only assert the two identifiers APPEARED somewhere in
the report-building slice of each runner's source. Nothing executed the
f-string, so an unresolved name or a malformed format string still passed the
suite and surfaced only after a paid eval run wrote the artifact. #742 called
the extraction below the real fix, and by then there were three harnesses
carrying the same untested shape.

WHY THIS MODULE CAN BE IMPORTED WHEN THE RUNNERS CANNOT. Every runner inserts
into `sys.path` at module scope and imports a bare `fixtures` module that only
resolves because of it -- side effects not worth bringing into the unit suite,
which is why #740 settled for reading their source as text. `harness_report`
exists to have neither: no `sys.path` surgery, no imports beyond the standard
library. It is loaded here straight from its path, so the suite does not gain
a new import root either.
"""

import importlib.machinery
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "evals" / "harness_report.py"


def _load() -> ModuleType:
    """The module from its path, without putting `evals/` on `sys.path`."""
    spec = importlib.util.spec_from_file_location("harness_report", _MODULE_PATH)
    assert spec is not None, _MODULE_PATH
    assert spec.loader is not None, _MODULE_PATH
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_each_setting_renders_under_its_own_label() -> None:
    """The whole point: rendered, and each value bound to ITS label.

    Asserting only that both numbers appear SOMEWHERE would pass against an
    implementation that swapped them -- and a swap is the one defect this
    extraction exists to make catchable, since a report naming the wrong
    ceiling is confidently wrong rather than obviously broken.

    Deliberately NOT the production constants: 8192 and 12288 are the shipped
    pair, so a test using them cannot tell a correct render apart from one
    that hard-codes the defaults and ignores its arguments."""
    line = _load().arm_identity_line(max_generation_tokens=111, context_window=222)

    assert "Generation ceiling `111`" in line
    assert "context window `222`" in line
    # Not the identifier names -- that is what the old guard could not tell
    # apart from a real value.
    assert "DEFAULT_MAX_GENERATION_TOKENS" not in line
    assert "DEFAULT_CONTEXT_WINDOW" not in line


def test_the_line_is_one_markdown_line() -> None:
    """It is spliced into a `lines` list that is later `"\\n".join`ed, so an
    embedded newline would silently become two report rows and break the
    caller's spacing."""
    line = _load().arm_identity_line(max_generation_tokens=111, context_window=222)

    assert "\n" not in line
    assert line.endswith(".")


def test_extra_segments_are_appended_in_order() -> None:
    """`evals/ingest_concurrency/` carries two more fields — the host and the
    server's `OLLAMA_NUM_PARALLEL`, without which one of its arms cannot be
    told apart from a silently serialized one."""
    line = _load().arm_identity_line(
        max_generation_tokens=8192,
        context_window=12288,
        extra=("host `h`", "**`OLLAMA_NUM_PARALLEL=2`**"),
    )

    assert line.index("12288") < line.index("host `h`") < line.index("PARALLEL")


def test_no_extra_segments_leaves_no_dangling_separator() -> None:
    """The two harnesses that pass no extras must not render a trailing ` · `.

    A separator-joined line is exactly where an empty tail shows up, and it
    would reach the stored report. Asserted as the whole rendered string,
    because that is the only form that also pins the separator itself."""
    line = _load().arm_identity_line(max_generation_tokens=111, context_window=222)

    # Pins the JOINED form, which also fixes a vacuous assertion this test
    # shipped with: the separator ends in a space, so `line.rstrip(".")` ended
    # with `"· "` and `.endswith("·")` could never be true. An implementation
    # joining with `""` would have passed every check here.
    assert line == "Generation ceiling `111` · context window `222`."


def test_an_empty_extra_segment_is_refused() -> None:
    """Fail loudly rather than render `a · · b`.

    A caller building segments conditionally is the likely source, and the
    damage lands in a measurement artifact nobody re-reads."""
    module = _load()
    with pytest.raises(ValueError, match="empty"):
        module.arm_identity_line(
            max_generation_tokens=8192, context_window=12288, extra=("host `h`", "")
        )


def test_a_bare_string_extra_is_refused() -> None:
    """A `str` SATISFIES `Sequence[str]`, so the type does not stop this.

    `extra="host \\`h\\`"` would iterate character by character and render one
    separator-joined segment per character — plausible-looking garbage in a
    stored measurement artifact. The empty-segment check cannot catch it,
    since no single character is blank."""
    module = _load()
    with pytest.raises(ValueError, match="bare string"):
        module.arm_identity_line(
            max_generation_tokens=8192,
            context_window=12288,
            # No `type: ignore` needed, and that IS the finding: mypy accepts
            # a `str` as a `Sequence[str]` without complaint, so the type
            # annotation provides no protection here at all.
            extra="host `h`",
        )


def test_a_newline_in_an_extra_segment_is_refused() -> None:
    """The one-line guarantee is only a guarantee if it is enforced.

    A newline here becomes two report rows the moment the caller joins its
    `lines` list, which is exactly the corruption this function documents
    itself as preventing."""
    module = _load()
    with pytest.raises(ValueError, match="line break"):
        module.arm_identity_line(
            max_generation_tokens=8192,
            context_window=12288,
            extra=("host `h`\nrogue row",),
        )


def test_a_single_pass_iterable_still_reaches_the_line() -> None:
    """`extra` is read twice — once to validate, once to render.

    A generator drained by the validation pass would splat to nothing and
    silently drop every caller-supplied field, leaving a line that looks
    correct and is missing the fields that distinguish the arm. Measured
    before the fix: the segment vanished and the render was
    `Generation ceiling \\`1\\` · context window \\`2\\`.`"""
    line = _load().arm_identity_line(
        max_generation_tokens=111,
        context_window=222,
        extra=(segment for segment in ["host `h`"]),
    )

    assert "host `h`" in line


def test_a_non_string_segment_raises_value_error_not_attribute_error() -> None:
    """The documented contract is `ValueError` for every malformed segment.

    Without an explicit type check `segment.strip()` raises `AttributeError`
    first, so `extra=(None,)` escaped the contract the docstring states — a
    caller catching `ValueError` around this would not catch it."""
    module = _load()
    with pytest.raises(ValueError, match="not str"):
        module.arm_identity_line(
            max_generation_tokens=111, context_window=222, extra=(None,)
        )


def test_a_lone_carriage_return_is_refused_too() -> None:
    """Python splits lines on a lone `\\r` as readily as on `\\n`.

    Checking only the line feed would let one through and still produce the
    two-row corruption the one-line guarantee exists to prevent."""
    module = _load()
    with pytest.raises(ValueError, match="line break"):
        module.arm_identity_line(
            max_generation_tokens=111,
            context_window=222,
            extra=("host `h`\rrogue row",),
        )


def test_the_name_the_runners_import_resolves_from_the_path_they_append() -> None:
    """The runners' own import line is never executed by the suite.

    Each of the three appends the evals root to `sys.path` and then does
    `from harness_report import arm_identity_line` at module scope. Nothing
    here runs that — importing a runner drags in its `sys.path` surgery and a
    bare `fixtures` module — so a renamed module or a dropped append would
    break every harness at the next paid run and no test would say so. This
    resolves the same name from the same directory, which is the part that
    can be checked without importing anything of theirs."""
    spec = importlib.machinery.PathFinder().find_spec(
        "harness_report", [str(_REPO_ROOT / "evals")]
    )

    assert spec is not None, (
        "`harness_report` does not resolve from the evals root, so every "
        "runner's module-scope import of it is broken"
    )
    assert spec.origin == str(_MODULE_PATH), (
        f"`harness_report` resolves to {spec.origin}, not {_MODULE_PATH}"
    )
    assert hasattr(_load(), "arm_identity_line"), (
        "`harness_report` no longer exports `arm_identity_line`, the name all "
        "three runners import"
    )
