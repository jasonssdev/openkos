"""The optional native change-notification seam (#1213, ADR-0040).

A notifier only SIGNALS that something under the inbox changed; the polling
watch pass still does every settle and re-hash check. These tests drive the
seam with fakes, never with real OS events, whose timing is not ours to assume.
"""

import importlib.util
import time
from pathlib import Path

import pytest

from openkos.application import watch_notify


class _FakeNotifier:
    def __init__(self, *, fail_start: bool = False) -> None:
        self.fail_start = fail_start
        self.started: Path | None = None
        self.closed = False
        self.pending = False

    def start(self, inbox: Path) -> None:
        if self.fail_start:
            raise OSError("inotify watch limit reached")
        self.started = inbox

    def consume(self) -> bool:
        pending, self.pending = self.pending, False
        return pending

    def close(self) -> None:
        self.closed = True


def test_poll_opens_no_notifier(tmp_path: Path) -> None:
    opened = watch_notify.open_notifier(
        "poll", tmp_path, factory=lambda: pytest.fail("built")
    )

    assert opened.notifier is None
    assert opened.warning is None


def test_native_starts_the_notifier_on_the_inbox(tmp_path: Path) -> None:
    fake = _FakeNotifier()

    opened = watch_notify.open_notifier("native", tmp_path, factory=lambda: fake)

    assert opened.notifier is fake
    assert fake.started == tmp_path
    assert opened.warning is None


def test_native_without_the_extra_falls_back_to_poll_with_a_warning(
    tmp_path: Path,
) -> None:
    def missing() -> watch_notify.ChangeNotifier:
        raise ImportError("No module named 'watchdog'")

    opened = watch_notify.open_notifier("native", tmp_path, factory=missing)

    assert opened.notifier is None
    assert opened.warning is not None
    assert "openkos[watch]" in opened.warning
    assert "polling" in opened.warning


def test_a_notifier_that_cannot_start_falls_back_and_is_closed(
    tmp_path: Path,
) -> None:
    fake = _FakeNotifier(fail_start=True)

    opened = watch_notify.open_notifier("native", tmp_path, factory=lambda: fake)

    assert opened.notifier is None
    assert opened.warning is not None
    assert "polling" in opened.warning
    assert fake.closed


def test_native_with_no_inbox_opens_nothing() -> None:
    opened = watch_notify.open_notifier(
        "native", None, factory=lambda: pytest.fail("built")
    )

    assert opened.notifier is None
    assert opened.warning is None


def test_native_available_reflects_the_import_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert watch_notify.native_available() is False
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    assert watch_notify.native_available() is True


def test_real_watchdog_signals_a_change(tmp_path: Path) -> None:
    """One smoke test against the real backend: eventual, never timed tightly."""
    pytest.importorskip("watchdog")

    notifier = watch_notify.WatchdogNotifier()
    notifier.start(tmp_path)
    try:
        deadline = time.monotonic() + 20
        seen = False
        n = 0
        while time.monotonic() < deadline and not seen:
            (tmp_path / f"note{n}.md").write_text("x", encoding="utf-8")
            n += 1
            time.sleep(0.2)
            seen = notifier.consume()
        assert seen, "no native event within 20s"
    finally:
        notifier.close()
