"""The optional native change-notification seam for the inbox watch (ADR-0040).

A `ChangeNotifier` only SIGNALS that something under the inbox changed, so the
daemon's idle wait can end sooner than its poll interval. It never decides
anything: the polling watch pass still stats, settles and re-hashes every file
(ADR-0038), and the periodic poll keeps running underneath, so a missed or
coalesced OS event costs latency, never a file. Polling is the default and the
fallback.

The core stays synchronous (ADR-0021). A notifier's own thread does one thing:
set a flag. The synchronous loop reads it through `consume()`.

The only implementation is `WatchdogNotifier`, behind the optional
`openkos[watch]` extra; `watchdog` is imported lazily so the core never needs
it.
"""

import importlib.util
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol

log = logging.getLogger(__name__)

_JOIN_SECONDS: Final = 5.0


class ChangeNotifier(Protocol):
    """Internal seam; OpenKOS publishes no plugin API."""

    def start(self, inbox: Path) -> None:
        """Begin watching `inbox` (recursively). May raise `OSError`."""
        ...

    def consume(self) -> bool:
        """`True` if anything changed since the last call; clears the signal."""
        ...

    def close(self) -> None:
        """Stop watching. Idempotent."""
        ...


def native_available() -> bool:
    """Whether the optional `watchdog` dependency is importable."""
    return importlib.util.find_spec("watchdog") is not None


class WatchdogNotifier:
    """FSEvents / inotify / ReadDirectoryChangesW through `watchdog`."""

    def __init__(self) -> None:
        # Fail at construction, not at the first event, when the extra is absent.

        self._changed = threading.Event()
        self._observer: Any = None

    def start(self, inbox: Path) -> None:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        changed = self._changed

        class _Handler(FileSystemEventHandler):
            def on_any_event(self, event: object) -> None:
                changed.set()

        observer = Observer()
        observer.schedule(_Handler(), str(inbox), recursive=True)
        observer.start()
        self._observer = observer

    def consume(self) -> bool:
        if self._changed.is_set():
            self._changed.clear()
            return True
        return False

    def close(self) -> None:
        observer, self._observer = self._observer, None
        if observer is not None:
            observer.stop()
            observer.join(timeout=_JOIN_SECONDS)


def _default_factory() -> ChangeNotifier:
    return WatchdogNotifier()


@dataclass(frozen=True)
class OpenedNotifier:
    """`notifier` is `None` when polling alone drives the watch; `warning` says
    why a requested native backend is not in use."""

    notifier: ChangeNotifier | None = None
    warning: str | None = None


def open_notifier(
    backend: str,
    inbox: Path | None,
    *,
    factory: Callable[[], ChangeNotifier] | None = None,
) -> OpenedNotifier:
    """Start the configured notifier, or fall back to polling.

    Falling back (never refusing) is deliberate: polling is correct on its own,
    and an unattended daemon that exits on a missing optional extra would
    restart-loop under a service manager. The warning is visible instead."""
    if backend != "native" or inbox is None:
        return OpenedNotifier()
    build = factory if factory is not None else _default_factory
    notifier: ChangeNotifier | None = None
    try:
        notifier = build()
        notifier.start(inbox)
    except ImportError:
        return OpenedNotifier(
            warning=(
                "unattended.watch_backend is 'native' but the optional "
                "dependency is not installed (pip install 'openkos[watch]'); "
                "polling instead."
            )
        )
    except OSError as exc:
        if notifier is not None:
            notifier.close()
        return OpenedNotifier(
            warning=(
                "unattended.watch_backend is 'native' but the OS notification "
                f"backend could not start ({type(exc).__name__}); polling instead."
            )
        )
    return OpenedNotifier(notifier=notifier)
