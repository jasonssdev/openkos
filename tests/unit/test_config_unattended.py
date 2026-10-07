"""The `unattended:` section of `openkos.yaml` (budget and watch keys).

Validated when the config is read, following the `models:` precedent: an
unknown key is refused, a boolean is never an integer, ranges are enforced,
and `unattended.inbox` may not be (or sit inside) the engine's own trees.
"""

from pathlib import Path

import pytest

from openkos import config


def _write(root: Path, section: str) -> None:
    (root / "openkos.yaml").write_text(f"model: gemma3\n{section}", encoding="utf-8")


def test_absent_section_takes_every_default(tmp_path: Path) -> None:
    _write(tmp_path, "")

    unattended = config.read_config(tmp_path).unattended

    assert unattended.max_calls_per_pass == 100
    assert unattended.max_calls_per_day == 500
    assert unattended.max_sources_per_pass == 10
    assert unattended.job_deadline_seconds == 1800
    assert unattended.maintenance_interval_seconds == 86400
    assert unattended.quiet_seconds == 30
    assert unattended.inbox is None


def test_null_and_empty_section_take_every_default(tmp_path: Path) -> None:
    defaults = config.UnattendedConfig()
    for section in ("unattended:\n", "unattended: {}\n"):
        _write(tmp_path, section)
        assert config.read_config(tmp_path).unattended == defaults


def test_present_keys_are_read_and_the_rest_default(tmp_path: Path) -> None:
    _write(tmp_path, "unattended:\n  max_calls_per_pass: 7\n  quiet_seconds: 12\n")

    unattended = config.read_config(tmp_path).unattended

    assert unattended.max_calls_per_pass == 7
    assert unattended.quiet_seconds == 12
    assert unattended.max_calls_per_day == 500


def test_unknown_key_is_refused_naming_it(tmp_path: Path) -> None:
    _write(tmp_path, "unattended:\n  max_call_per_pass: 5\n")

    with pytest.raises(ValueError, match="max_call_per_pass"):
        config.read_config(tmp_path)


def test_section_must_be_a_mapping(tmp_path: Path) -> None:
    _write(tmp_path, "unattended: 5\n")

    with pytest.raises(ValueError, match="'unattended' must be a mapping"):
        config.read_config(tmp_path)


@pytest.mark.parametrize(
    "key",
    [
        "max_calls_per_pass",
        "max_calls_per_day",
        "max_sources_per_pass",
        "job_deadline_seconds",
        "maintenance_interval_seconds",
        "quiet_seconds",
    ],
)
@pytest.mark.parametrize("bad", ["true", "false", "'5'", "5.5", "[1]"])
def test_non_integers_are_refused_booleans_included(
    tmp_path: Path, key: str, bad: str
) -> None:
    _write(tmp_path, f"unattended:\n  {key}: {bad}\n")

    with pytest.raises(ValueError, match=f"unattended.{key}"):
        config.read_config(tmp_path)


@pytest.mark.parametrize(
    ("key", "floor"),
    [
        ("max_calls_per_pass", 0),
        ("max_calls_per_day", 0),
        ("max_sources_per_pass", 0),
        ("job_deadline_seconds", 60),
        ("maintenance_interval_seconds", 300),
        ("quiet_seconds", 1),
    ],
)
def test_ranges_accept_the_floor_and_refuse_one_below(
    tmp_path: Path, key: str, floor: int
) -> None:
    _write(tmp_path, f"unattended:\n  {key}: {floor}\n")
    assert getattr(config.read_config(tmp_path).unattended, key) == floor

    _write(tmp_path, f"unattended:\n  {key}: {floor - 1}\n")
    with pytest.raises(ValueError, match=f"unattended.{key}"):
        config.read_config(tmp_path)


# --- unattended.inbox ---------------------------------------------------------


def _inbox(root: Path, value: str) -> None:
    _write(root, f"unattended:\n  inbox: {value}\n")


def test_relative_inbox_resolves_against_the_workspace_root(tmp_path: Path) -> None:
    (tmp_path / "drop").mkdir()
    _inbox(tmp_path, "drop")

    assert (
        config.read_config(tmp_path).unattended.inbox == (tmp_path / "drop").resolve()
    )


def test_absolute_inbox_outside_the_workspace_is_accepted(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    _inbox(root, str(outside))

    assert config.read_config(root).unattended.inbox == outside.resolve()


@pytest.mark.parametrize(
    "value",
    [
        "raw",
        "raw/sub",
        "bundle",
        "bundle/.state",
        ".openkos",
        ".openkos/cache",
        ".",
        "./",
        "raw/../bundle",
    ],
)
def test_inbox_in_or_equal_to_an_engine_tree_or_the_root_is_refused(
    tmp_path: Path, value: str
) -> None:
    for sub in ("raw/sub", "bundle/.state", ".openkos/cache"):
        (tmp_path / sub).mkdir(parents=True)
    _inbox(tmp_path, value)

    with pytest.raises(ValueError, match=r"unattended\.inbox"):
        config.read_config(tmp_path)


def test_inbox_that_contains_the_workspace_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    (root / "raw").mkdir(parents=True)
    _inbox(root, "..")

    with pytest.raises(ValueError, match=r"unattended\.inbox"):
        config.read_config(root)


def test_inbox_symlinked_into_raw_is_refused(tmp_path: Path) -> None:
    (tmp_path / "raw").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "raw", target_is_directory=True)
    _inbox(tmp_path, "link")

    with pytest.raises(ValueError, match=r"unattended\.inbox"):
        config.read_config(tmp_path)


def test_missing_inbox_is_refused(tmp_path: Path) -> None:
    _inbox(tmp_path, "nope")

    with pytest.raises(ValueError, match=r"unattended\.inbox.*directory"):
        config.read_config(tmp_path)


def test_inbox_naming_a_file_is_refused(tmp_path: Path) -> None:
    (tmp_path / "afile").write_text("x", encoding="utf-8")
    _inbox(tmp_path, "afile")

    with pytest.raises(ValueError, match=r"unattended\.inbox.*directory"):
        config.read_config(tmp_path)


@pytest.mark.parametrize("bad", ["true", "5", "''", "'   '", "[a]"])
def test_inbox_must_be_a_non_blank_string(tmp_path: Path, bad: str) -> None:
    _inbox(tmp_path, bad)

    with pytest.raises(ValueError, match=r"unattended\.inbox"):
        config.read_config(tmp_path)


def test_null_inbox_means_watch_off(tmp_path: Path) -> None:
    _inbox(tmp_path, "null")

    assert config.read_config(tmp_path).unattended.inbox is None


def test_the_template_documents_the_section_as_provisional() -> None:
    template = (
        Path(config.__file__).parent / "templates" / "openkos.yaml.template"
    ).read_text(encoding="utf-8")

    assert "unattended" in template
    assert "provisional" in template.lower()
    for key in (
        "max_calls_per_pass",
        "max_calls_per_day",
        "max_sources_per_pass",
        "job_deadline_seconds",
        "maintenance_interval_seconds",
        "quiet_seconds",
        "inbox",
    ):
        assert key in template


def test_watch_backend_defaults_to_poll(tmp_path: Path) -> None:
    _write(tmp_path, "")

    assert config.read_config(tmp_path).unattended.watch_backend == "poll"


def test_watch_backend_native_is_read(tmp_path: Path) -> None:
    _write(tmp_path, "unattended:\n  watch_backend: native\n")

    assert config.read_config(tmp_path).unattended.watch_backend == "native"


@pytest.mark.parametrize("value", ["inotify", "", "Native", "true", "1"])
def test_watch_backend_outside_poll_or_native_is_refused(
    tmp_path: Path, value: str
) -> None:
    _write(tmp_path, f"unattended:\n  watch_backend: '{value}'\n")

    with pytest.raises(ValueError, match="watch_backend"):
        config.read_config(tmp_path)


def test_watch_backend_must_be_a_string(tmp_path: Path) -> None:
    _write(tmp_path, "unattended:\n  watch_backend: 5\n")

    with pytest.raises(ValueError, match="watch_backend"):
        config.read_config(tmp_path)


def test_a_tilde_inbox_expands_to_the_home_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    (home / "inbox").mkdir(parents=True)
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setenv("HOME", str(home))
    _inbox(root, "~/inbox")

    assert config.read_config(root).unattended.inbox == (home / "inbox").resolve()


def test_a_tilde_inbox_inside_the_engine_trees_is_still_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The expansion happens BEFORE the containment checks: a home that is the
    workspace itself must not smuggle the engine's own trees in as an inbox."""
    root = tmp_path / "ws"
    (root / "raw").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(root))
    _inbox(root, "~/raw")

    with pytest.raises(ValueError, match=r"unattended\.inbox.*raw/"):
        config.read_config(root)


def test_a_tilde_inbox_for_an_unknown_user_is_a_config_error(
    tmp_path: Path,
) -> None:
    _inbox(tmp_path, "~no-such-user-1334/inbox")

    with pytest.raises(ValueError, match=r"unattended\.inbox"):
        config.read_config(tmp_path)
