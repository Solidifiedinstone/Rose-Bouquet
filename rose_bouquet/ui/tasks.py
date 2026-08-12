"""Running slow things off the interface thread, and getting the answer back safely.

Scanning a library, searching YouTube Music, downloading a track and importing a
playlist all take long enough to freeze a window, and all of them need to report
progress while they run. This is the one piece of threading in the app, so every
one of them uses the same shape and no view has to know how it works.

Two details here are the difference between "works" and "corrupts the interface
at random", and both are easy to get wrong:

**Callbacks must arrive on the interface thread.** A Qt signal connected to a
plain Python function is delivered in whichever thread emitted it — so a worker
emitting `finished` runs your callback *on the worker*. Touching a widget or
starting a `QTimer` from there is undefined behaviour: it silently does nothing,
or it crashes, and which one you get depends on timing. So the callbacks are
slots on a `QObject` that lives on the interface thread, connected with an
explicit queued connection, which makes Qt post them across properly.

**A running job must be kept alive.** `QThreadPool.start` takes ownership of the
C++ runnable, but nothing holds the Python object — if it is garbage collected
mid-flight the signals it carries die with it and the result never arrives. Jobs
are held in a module-level set until they finish.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot

logger = logging.getLogger(__name__)

#: Jobs in flight. Without this they can be collected while running.
_running: set["Job"] = set()


class Signals(QObject):
    """A runnable cannot carry signals itself, so it borrows these."""

    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(object)


class Callbacks(QObject):
    """Where a job's results land — on the thread that created it.

    This exists so the connection has a `QObject` receiver with interface-thread
    affinity. That is what lets Qt queue the call instead of running it wherever
    the worker happened to be.
    """

    def __init__(
        self,
        on_done: Optional[Callable[[Any], None]],
        on_progress: Optional[Callable[[Any], None]],
        on_error: Optional[Callable[[str], None]],
        job: "Job",
    ) -> None:
        super().__init__()
        self._on_done = on_done
        self._on_progress = on_progress
        self._on_error = on_error
        self._job = job

    @Slot(object)
    def done(self, result: Any) -> None:
        _running.discard(self._job)
        if self._on_done is not None:
            self._on_done(result)

    @Slot(object)
    def progressed(self, update: Any) -> None:
        if self._on_progress is not None:
            self._on_progress(update)

    @Slot(str)
    def errored(self, message: str) -> None:
        _running.discard(self._job)
        if self._on_error is not None:
            self._on_error(message)
        else:
            logger.warning("job failed: %s", message)


class Job(QRunnable):
    """One piece of background work.

    The callable is given a `report` function to call with progress, if it asked
    for one, so simple jobs stay one-liners.
    """

    def __init__(self, work: Callable[..., Any], *args, wants_progress: bool = False, **kwargs) -> None:
        super().__init__()
        self.work = work
        self.args = args
        self.kwargs = kwargs
        self.wants_progress = wants_progress
        self.signals = Signals()
        self.callbacks: Optional[Callbacks] = None
        self.cancelled = False

    def cancel(self) -> None:
        """Ask the job to stop. Cooperative — the work has to check `cancelled`."""
        self.cancelled = True

    @Slot()
    def run(self) -> None:
        try:
            if self.wants_progress:
                result = self.work(*self.args, report=self.signals.progress.emit, **self.kwargs)
            else:
                result = self.work(*self.args, **self.kwargs)
        except Exception as exc:                  # noqa: BLE001 — the point is to not crash
            logger.exception("background job failed")
            self.signals.failed.emit(str(exc))
            return

        if not self.cancelled:
            self.signals.finished.emit(result)


def run(
    work: Callable[..., Any],
    *args,
    on_done: Optional[Callable[[Any], None]] = None,
    on_progress: Optional[Callable[[Any], None]] = None,
    on_error: Optional[Callable[[str], None]] = None,
    pool: Optional[QThreadPool] = None,
    **kwargs,
) -> Job:
    """Run `work` in the background and call back on the interface thread."""
    job = Job(work, *args, wants_progress=on_progress is not None, **kwargs)
    callbacks = Callbacks(on_done, on_progress, on_error, job)
    job.callbacks = callbacks

    queued = Qt.ConnectionType.QueuedConnection
    job.signals.finished.connect(callbacks.done, queued)
    job.signals.progress.connect(callbacks.progressed, queued)
    job.signals.failed.connect(callbacks.errored, queued)

    _running.add(job)
    (pool or QThreadPool.globalInstance()).start(job)
    return job


def downloads_pool() -> QThreadPool:
    """A separate, narrower pool for downloads.

    Three at a time: enough that a playlist import is not glacial, few enough
    that YouTube does not start refusing, and it leaves the global pool free for
    searches so the interface stays responsive mid-download.
    """
    pool = QThreadPool()
    pool.setMaxThreadCount(3)
    return pool


def in_flight() -> int:
    """How many jobs are running. Used by tests and the shutdown path."""
    return len(_running)
